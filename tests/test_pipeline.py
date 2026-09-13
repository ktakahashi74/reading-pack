from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data
from reading_pack_producer.pipeline import (
    _read, _unseal, default_recipe, finalize_pipeline, pipeline_status, resume_pipeline, start_pipeline,
)
from tests.support import cli

SOURCE = '''# The Orchard Manual

## Opening the gate
The keeper opens the orchard entrance at sunrise only when the safety lamp is green.
When the lamp is red, the keeper leaves the entrance closed even after sunrise.
The visitor waits outside until the keeper has checked both daylight and the lamp.
This chapter describes the morning entry procedure and its necessary conditions.
'''
SUPPLEMENT = '''# Author's additional note
A blue lamp is used only during scheduled inspection. It does not authorize entry.
'''
SUMMARY = 'Morning access depends jointly on daylight and a positive safety indication; waiting continues whenever the safety condition is unmet.'


class Worker:
    def __init__(self, *, repair=False, holdout_fail=False, always_fail=False):
        self.requests = []
        self.repair = repair
        self.holdout_fail = holdout_fail
        self.always_fail = always_fail

    def __call__(self, command, request, **kwargs):
        self.requests.append(copy.deepcopy(request))
        stage, payload = request['stage'], request['payload']
        source = payload.get('source', {})
        source_id = source.get('source_id', 'SRC-1')
        quote = ('A blue lamp is used only during scheduled inspection.' if source_id != 'SRC-1'
                 else 'The keeper opens the orchard entrance at sunrise only when the safety lamp is green.')
        evidence = [{'source_id': source_id, 'quote': quote}]
        if stage == 'profile':
            result = {'profile': 'general-navigation', 'reason': 'Procedural navigation.', 'evidence': [{'source_id': 'SRC-1', 'quote': 'The keeper opens the orchard entrance at sunrise only when the safety lamp is green.'}]}
        elif stage == 'benchmark':
            result = {split: [{'question': (f'{source_id}: Under which conditions may entry begin?' if split == 'development' else f'{source_id}: Can the keeper admit a visitor after sunrise while the lamp is red?'),
                               'requirements': ['Daylight is necessary.', 'A safe indication is necessary.'],
                               'evidence': evidence}] for split in ('development', 'holdout')}
        elif stage == 'benchmark_review':
            result = {'valid': True, 'reason': 'The synthetic source supports the requirements.'}
        elif stage in {'generate', 'repair'}:
            canonical = payload['canonical']
            if source_id != 'SRC-1':
                result = {'candidates': [{'collection': 'claims', 'record': {
                    'id': 'CL-INSPECTION', 'layer': 'descriptive', 'kind': 'observation',
                    'statement': 'Inspection signalling grants no access permission.',
                    'chapter_ids': [canonical['chapters'][0]['id']], 'status': 'draft'},
                    'evidence': [{'snippet': 'A blue lamp is used only during scheduled inspection. It does not authorize entry.'}]}]}
            else:
                record = copy.deepcopy(canonical['chapters'][0])
                record['summary'] = SUMMARY + (' Both conditions must hold.' if stage == 'repair' else '')
                record['terms'] = ['safety lamp']
                record['status'] = 'draft'
                result = {'candidates': [{'collection': 'chapters', 'record': record,
                                         'evidence': [{'snippet': quote}]}]}
        elif stage == 'review':
            result = {'decisions': [{'candidate_id': c['candidate_id'], 'decision': 'accept',
                                     'reason': 'The source states the condition.', 'evidence': evidence}
                                    for c in payload['candidates']]}
        elif stage == 'audit':
            count = sum(1 + len(c['sections']) for c in payload['canonical']['chapters'])
            result = {'expected_structure_records': count if source_id == 'SRC-1' else 0,
                      'matched_structure_records': count if source_id == 'SRC-1' else 0,
                      'source_attribution_errors': [], 'invented_record_ids': [], 'findings': []}
        elif stage == 'answer':
            result = {'answer': 'Entry needs both daylight and a green safety indication.'}
        elif stage == 'audit_adjudicate':
            span = payload['evidence_spans'][0]
            repaired = '/audit-adjudicate/known-' in request['job'] and 'Both conditions must hold.' in payload['canonical']['chapters'][0]['summary']
            result = {'decisions': [{'finding_id': f['finding_id'], 'classification': 'blocking',
                'criterion': 'qualification', 'reader_impact': 'The reader may miss a necessary entry condition.',
                'reason': f['reason'], 'repair_scope': 'pack',
                'evidence': [{'source_id':span['source_id'],'span_id':span['id']}]} for f in payload['findings']]}
            if repaired:
                for decision in result['decisions']:
                    decision.update(classification='dismissed',criterion='none',repair_scope='none',reason='The repaired summary now states both required conditions.')
        elif stage == 'grade':
            fail = self.always_fail or (self.repair and request['job'].startswith('0/')) or (self.holdout_fail and '/holdout/' in request['job'])
            findings = []
            if fail:
                findings = [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                             'reason': 'The joint condition needs clarification.',
                             'evidence': [{'source_id': 'SRC-1', 'span_id': request['payload']['evidence_spans'][0]['id']}]}]
            result = {'requirements_met': [not fail] * len(payload['case']['requirements']), 'critical_errors': [], 'findings': findings,
                      'uncertain': False, 'rationale': 'Both conditions were checked.',
                      'answer_quotes': ['Entry needs both daylight']}
        else:
            raise AssertionError(stage)
        def select_spans(value):
            if isinstance(value, dict):
                if 'snippet' in value or ('quote' in value and 'source_id' in value):
                    quote = value.get('quote', value.get('snippet'))
                    unit = next(s for s in payload['evidence_spans'] if quote in s['text']
                                and s['source_id'] == value.get('source_id', source_id))
                    return {**({'source_id': unit['source_id']} if 'source_id' in value else {}), 'span_id': unit['id']}
                return {k: select_spans(v) for k, v in value.items()}
            if isinstance(value, list):
                return [select_spans(v) for v in value]
            return value
        return {'schema_version': 1, 'request_id': request['request_id'], 'model': request['model'], 'result': select_spans(result)}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'book.md'
        self.source.write_text(SOURCE)
        self.run = self.root / 'run'
        self.recipe = default_recipe(['test-generator'], ['test-judge'], ['test-reader'],
                                     generator_model='generator-v1', judge_model='judge-v1', reader_model='reader-v1')
        self.recipe['answer_repetitions'] = 1
        self.recipe['max_rounds'] = 3

    def start(self, supplements=None):
        return start_pipeline(self.run, self.source, self.recipe, supplements=supplements)

    def execute(self, worker):
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            return resume_pipeline(self.run)


    def test_unusable_structure_stops_before_profile_or_benchmark_calls(self):
        self.source.write_text('No unambiguous headings in this manuscript.')
        self.start()
        worker = Worker()
        result = self.execute(worker)
        self.assertEqual(result['state'], 'blocked_execution')
        self.assertEqual(worker.requests, [])
        self.assertEqual(result['calls_reserved'], 0)

    def _prepare_synthetic_layout(self):
        from reading_pack_producer.pipeline import Runner, _seal
        self.start()
        runner = Runner(self.run)
        runner.prepare_source_structure()
        layout = {'schema_version': 1, 'method': 'test-layout',
                  'source_sha256': runner.manifest['sources'][0]['sha256'],
                  'chapters': [{'id': 'CH-01', 'title': 'Opening the gate',
                    'toc_title': 'Old heading', 'toc_sections': ['obsolete section'],
                    'pdf_page': 1, 'source_start': 0, 'source_text': SOURCE,
                    'candidates': [{'id': 'LH-' + 'a'*20, 'title': 'Entry conditions',
                                    'order': 0, 'source_offset': 0, 'pdf_page': 1,
                                    'context': SOURCE}]}]}
        _seal(self.run / 'source-layout.json', layout)
        preflight = _unseal(self.run / 'source-preflight.json')
        from reading_pack_producer.work_ledger import artifact_hash
        preflight['artifacts']['source-layout.json'] = artifact_hash(layout)
        _seal(self.run / 'source-preflight.json', preflight)

    def test_layout_selection_review_and_benchmark_order_and_draft_boundary(self):
        self._prepare_synthetic_layout()
        worker = Worker()
        original = worker.__call__
        requests = []
        def adapter(command, request, **kwargs):
            requests.append(copy.deepcopy(request))
            if request['stage'] == 'structure_select':
                value = {'selected_candidate_ids': ['LH-' + 'a'*20], 'rationale': 'Body heading.'}
            elif request['stage'] == 'structure_review':
                value = {'valid': True, 'reason': 'Complete inventory.', 'missing_candidate_ids': [], 'spurious_candidate_ids': []}
            else:
                return original(command, request, **kwargs)
            return {'schema_version': 1, 'request_id': request['request_id'], 'model': request['model'], 'result': value}
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        stages = [r['stage'] for r in requests]
        self.assertLess(stages.index('structure_review'), stages.index('benchmark'))
        data = load_language_data(self.run / 'candidate', 'en')
        self.assertEqual(data['chapters'][0]['sections'], ['Entry conditions'])
        self.assertEqual(data['chapters'][0]['status'], 'draft')
        self.assertFalse(_unseal(self.run / 'structure-reviews.json')['author_approval'])

    def test_failed_layout_review_stops_before_generation_and_benchmark(self):
        self._prepare_synthetic_layout()
        stages = []
        def adapter(command, request, **kwargs):
            stages.append(request['stage'])
            if request['stage'] == 'profile':
                return Worker()(command, request, **kwargs)
            if request['stage'] == 'structure_select':
                result = {'selected_candidate_ids': [], 'rationale': 'Uncertain.'}
            else:
                self.assertEqual(request['stage'], 'structure_review')
                result = {'valid': False, 'reason': 'Missing a body heading.',
                          'missing_candidate_ids': ['LH-' + 'a'*20], 'spurious_candidate_ids': []}
            return {'schema_version': 1, 'request_id': request['request_id'], 'model': request['model'], 'result': result}
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertNotIn('benchmark', stages)
        self.assertNotIn('generate', stages)
        self.assertEqual(stages.count('structure_select'), self.recipe['max_rounds'])
        self.assertFalse((self.run / 'candidate').exists())

    def test_layout_selection_cannot_invent_a_heading_id(self):
        self._prepare_synthetic_layout()
        def adapter(command, request, **kwargs):
            if request['stage'] == 'profile':
                return Worker()(command, request, **kwargs)
            result = {'selected_candidate_ids': ['LH-' + 'f'*20], 'rationale': 'Invented ID.'}
            return {'schema_version': 1, 'request_id': request['request_id'], 'model': request['model'], 'result': result}
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'blocked_execution', result)
        self.assertIn('invented', result['reason'])

    def test_missing_body_heading_is_added_then_independently_rechecked(self):
        from reading_pack_producer.pipeline import _seal
        from reading_pack_producer.work_ledger import artifact_hash
        self._prepare_synthetic_layout()
        layout = _unseal(self.run / 'source-layout.json')
        chapter = layout['chapters'][0]
        chapter['body_pages'] = [{'pdf_page': 1, 'start': 0, 'text': SOURCE}]
        _seal(self.run / 'source-layout.json', layout)
        preflight = _unseal(self.run / 'source-preflight.json')
        preflight['artifacts']['source-layout.json'] = artifact_hash(layout)
        _seal(self.run / 'source-preflight.json', preflight)
        requests = []
        def adapter(command, request, **kwargs):
            requests.append(copy.deepcopy(request))
            stage, payload = request['stage'], request['payload']
            if stage == 'structure_select':
                result = {'selected_candidate_ids': [c['id'] for c in payload['candidates']],
                          'rationale': 'Select source candidates.'}
            elif stage == 'structure_review':
                missing = len(payload['candidates']) == 1
                result = {'valid': not missing, 'reason': 'Check body heading completeness.',
                          'missing_candidate_ids': [], 'spurious_candidate_ids': [],
                          'missing_headings': [{'pdf_page': 1, 'title': 'Opening the gate',
                             'evidence_quote': '## Opening the gate'}] if missing else []}
            else:
                return Worker()(command, request, **kwargs)
            return {'schema_version': 1, 'request_id': request['request_id'],
                    'model': request['model'], 'result': result}
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual(sum(r['stage'] == 'structure_review' for r in requests), 2)
        reviews = _unseal(self.run / 'structure-reviews.json')
        self.assertEqual(len(reviews['selected']), 2)
        added = reviews['reports'][0]['added_candidates'][0]
        audit = next(r for r in requests if r['stage'] == 'audit')
        self.assertIn(added['id'], [c['id'] for c in audit['payload']['layout_evidence']['body_candidates']])
        self.assertFalse(reviews['author_approval'])
        count = len(requests)
        self.assertEqual(self.execute(adapter)['state'], result['state'])
        self.assertEqual(len(requests), count)

    def test_bad_quote_quarantines_only_its_candidate(self):
        from reading_pack_producer.pipeline import Runner
        self.recipe['profile'] = 'general-navigation'
        self.start()
        runner = Runner(self.run)
        project = self.root / 'draft'
        runner.bootstrap(project)
        def adapter(command, request, **kwargs):
            result = Worker()(command, request, **kwargs)
            if request['stage'] == 'generate':
                candidate = copy.deepcopy(result['result']['candidates'][0])
                candidate['evidence'] = [{'span_id': 'SPAN-' + '0' * 24}]
                result['result']['candidates'].append(candidate)
            return result
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=adapter):
            failures = runner.generate(project, 0, [])
        self.assertTrue(any('evidence_not_in_source_chunk' in f['reason'] for f in failures))
        self.assertEqual(load_language_data(project, 'en')['chapters'][0]['summary'], SUMMARY)
        self.assertTrue(list(self.run.glob('candidate-evidence-rejections-*.json')))

    def test_worker_schema_constrains_new_ids_lengths_and_benchmark_counts(self):
        from jsonschema import Draft202012Validator
        self.start()
        def adapter(command, request, **kwargs):
            response = Worker()(command, request, **kwargs)
            validator = Draft202012Validator(request['response_schema'])
            self.assertTrue(validator.is_valid(response))
            if request['stage'] == 'generate':
                claim = {'collection': 'claims', 'record': {'id': 'AX-9', 'layer': 'descriptive',
                    'kind': 'observation', 'statement': 'A synthetic statement.',
                    'chapter_ids': [request['payload']['canonical']['chapters'][0]['id']], 'status': 'draft'},
                    'evidence': [{'span_id': request['payload']['evidence_spans'][0]['id']}]}
                invalid = copy.deepcopy(response)
                invalid['result']['candidates'] = [claim]
                admission = Draft202012Validator(request['payload']['candidate_record_schemas']['claims'])
                self.assertTrue(validator.is_valid(invalid))
                self.assertFalse(admission.is_valid(claim['record']))
                claim['record']['id'] = 'CL-NEW'
                self.assertTrue(admission.is_valid(claim['record']))
                claim['record']['statement'] = 'a' * 1001
                self.assertFalse(admission.is_valid(claim['record']))
            if request['stage'] == 'benchmark':
                invalid = copy.deepcopy(response)
                invalid['result']['development'] *= 2
                self.assertFalse(validator.is_valid(invalid))
            return response
        self.assertEqual(self.execute(adapter)['state'], 'awaiting_author_approval')

    def test_manuscript_only_reaches_author_without_approving_records(self):
        self.start()
        worker = Worker()
        result = self.execute(worker)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        data = load_language_data(self.run / 'candidate', 'en')
        self.assertTrue(all(c['status'] == 'draft' for c in data['chapters']))
        self.assertTrue(list((self.run / 'candidate').rglob('*.review.md')))
        before = len(worker.requests)
        self.assertEqual(self.execute(worker)['state'], result['state'])
        self.assertEqual(len(worker.requests), before)
        for request in worker.requests:
            if request['stage'] in {'generate', 'repair'}:
                self.assertNotIn('holdout', json.dumps(request['payload']))
            if request['stage'] == 'answer':
                self.assertEqual(set(request['payload']), {'pack', 'question'})

    def test_supplement_is_used_with_its_own_provenance(self):
        supplement = self.root / 'note.md'
        supplement.write_text(SUPPLEMENT)
        self.start([(supplement, 'author-data')])
        result = self.execute(Worker())
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        claim = load_language_data(self.run / 'candidate', 'en')['claims'][0]
        self.assertEqual(claim['provenance_source_id'], 'SRC-2')
        self.assertEqual(claim['status'], 'draft')

    def test_quality_failure_is_repaired_automatically(self):
        self.start()
        worker = Worker(repair=True)
        result = self.execute(worker)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual(len(result['rounds']), 2)
        self.assertTrue(any(r['stage'] == 'repair' for r in worker.requests))

    def test_holdout_failure_stops_without_repairing_on_test(self):
        self.start()
        worker = Worker(holdout_fail=True)
        result = self.execute(worker)
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertNotIn('repair', [r['stage'] for r in worker.requests])
        self.assertFalse((self.run / 'candidate').exists())

    def test_unmet_quality_exhausts_fixed_rounds(self):
        self.start()
        result = self.execute(Worker(always_fail=True))
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertEqual(len(result['rounds']), 3)

    def test_budget_reserves_calls_before_dispatch(self):
        self.recipe['max_calls'] = 1
        self.start()
        worker = Worker()
        self.assertEqual(self.execute(worker)['state'], 'budget_exhausted')
        self.assertEqual(len(worker.requests), 1)
        self.execute(worker)
        self.assertEqual(len(worker.requests), 1)

    def test_interrupted_call_requires_explicit_bounded_retry(self):
        self.start()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                resume_pipeline(self.run)
        worker = Worker()
        self.assertEqual(self.execute(worker)['state'], 'blocked_execution')
        self.assertEqual(worker.requests, [])
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            result = resume_pipeline(self.run, retry_inflight=True)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual(result['calls_reserved'], len(worker.requests) + 1)

    def test_stale_source_and_recipe_tampering_are_rejected(self):
        self.start()
        (self.run / 'inputs' / '0' / 'book.md').write_text('changed')
        with self.assertRaisesRegex(ReadingPackError, 'snapshot changed'):
            self.execute(Worker())

    def test_foreign_model_is_not_accepted_or_resampled(self):
        self.start()
        worker = Worker()
        def wrong(*args, **kwargs):
            result = worker(*args, **kwargs)
            result['model'] = 'wrong'
            return result
        self.assertEqual(self.execute(wrong)['state'], 'blocked_execution')
        self.assertEqual(self.execute(wrong)['state'], 'blocked_execution')
        self.assertEqual(len(worker.requests), 1)


    def test_author_signoff_builds_a_release_without_mutating_the_draft(self):
        from reading_pack_review.review_session import build_author_review_session
        from tests.test_assisted_review import AssistedAuthorReviewTests
        self.start()
        self.assertEqual(self.execute(Worker())['state'], 'awaiting_author_approval')
        candidate = self.run / 'candidate'
        review = candidate / '.reading-pack/reviews/pipeline-author.review.md'
        session = build_author_review_session(candidate, candidate / '.reading-pack/reviews/pipeline-author')
        # This is an explicitly synthetic human signature, not a production worker.
        helper = AssistedAuthorReviewTests()
        review.write_text(helper._complete_release_text(review, session))
        result = finalize_pipeline(self.run)
        self.assertEqual(result['state'], 'release_ready', result)
        self.assertEqual(load_language_data(candidate, 'en')['chapters'][0]['status'], 'draft')
        self.assertEqual(load_language_data(self.run / 'release', 'en')['chapters'][0]['status'], 'approved')
        self.assertEqual(finalize_pipeline(self.run)['state'], 'release_ready')

    def test_unsigned_author_form_cannot_finalize(self):
        self.start()
        self.execute(Worker())
        with self.assertRaisesRegex(ReadingPackError, 'not been submitted'):
            finalize_pipeline(self.run)
        self.assertFalse((self.run / 'release').exists())

    def test_candidate_edit_invalidates_evaluation(self):
        self.start()
        self.execute(Worker())
        path = self.run / 'candidate' / 'data' / 'pack.en.json'
        data = _read(path)
        data['chapters'][0]['summary'] += ' Unexpected later modification.'
        path.write_text(json.dumps(data))
        result = self.execute(Worker())
        self.assertEqual(result['state'], 'blocked_execution')
        self.assertIn('candidate changed', result['reason'])

    def test_later_interruption_replays_completed_jobs_without_resending(self):
        self.start()
        worker = Worker()
        def interrupt(command, request, **kwargs):
            if request['stage'] == 'grade':
                raise KeyboardInterrupt
            return worker(command, request, **kwargs)
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                resume_pipeline(self.run)
        earlier = [r['request_id'] for r in worker.requests]
        worker.requests.clear()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            result = resume_pipeline(self.run, retry_inflight=True)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertFalse(set(earlier) & {r['request_id'] for r in worker.requests})

    def test_foreign_evidence_and_answer_quotes_fail_closed(self):
        for stage in ('benchmark', 'grade'):
            with self.subTest(stage=stage):
                if self.run.exists():
                    import shutil
                    shutil.rmtree(self.run)
                self.start()
                worker = Worker()
                def corrupt(command, request, **kwargs):
                    response = worker(command, request, **kwargs)
                    if request['stage'] == stage:
                        if stage == 'benchmark':
                            response['result']['development'][0]['evidence'][0]['span_id'] = 'SPAN-' + '0' * 24
                        else:
                            response['result']['answer_quotes'] = ['invented answer quote']
                    return response
                result = self.execute(corrupt)
                self.assertEqual(result['state'], 'failed_quality' if stage == 'benchmark' else 'blocked_execution', result)
                self.assertFalse((self.run / 'candidate').exists())

    def test_live_protocol_errors_stop_without_rewriting_or_resampling(self):
        for corruption in ('stage_wrapper', 'chunk_id_as_source'):
            with self.subTest(corruption=corruption):
                if self.run.exists():
                    import shutil
                    shutil.rmtree(self.run)
                self.start()
                worker = Worker()
                def corrupt(command, request, **kwargs):
                    response = worker(command, request, **kwargs)
                    if request['stage'] == 'benchmark':
                        if corruption == 'stage_wrapper':
                            response['result'] = {'benchmark': response['result']}
                        else:
                            response['result']['development'][0]['evidence'][0]['source_id'] = request['payload']['source']['id']
                    return response
                result = self.execute(corrupt)
                self.assertEqual(result['state'], 'failed_quality' if corruption == 'chunk_id_as_source' else 'blocked_execution', result)
                calls = result['calls_reserved']
                saved = {p.name: p.read_bytes() for p in (self.run / 'jobs').glob('*.json')}
                worker.requests.clear()
                resumed = self.execute(worker)
                self.assertEqual(resumed['calls_reserved'], calls)
                self.assertFalse(worker.requests)
                self.assertEqual(saved, {p.name: p.read_bytes() for p in (self.run / 'jobs').glob('*.json')})
                self.assertFalse((self.run / 'candidate').exists())

    def test_stage_schema_exposes_direct_result_and_document_ids(self):
        from jsonschema import Draft202012Validator
        self.start()
        worker = Worker()
        def checked(command, request, **kwargs):
            schema = request['response_schema']
            self.assertNotIn('$defs', schema)
            self.assertNotIn('$ref', schema['properties']['result'])
            response = worker(command, request, **kwargs)
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(response)), [])
            if request['stage'] == 'benchmark':
                invalid = copy.deepcopy(response)
                invalid['result']['development'][0]['evidence'][0]['source_id'] = request['payload']['source']['id']
                self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
            return response
        self.assertEqual(self.execute(checked)['state'], 'awaiting_author_approval')

    def test_quarantine_is_reported_but_unapplied_record_does_not_poison_valid_pack(self):
        self.recipe['max_rounds'] = 1
        self.start()
        worker = Worker()
        def malformed(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'generate':
                response['result']['candidates'].append({'collection': 'claims',
                    'record': {'id': 'invalid lowercase id', 'layer': 'descriptive', 'kind': 'observation',
                               'statement': 'The gate depends on the lamp.', 'chapter_ids': ['CH-01'], 'status': 'draft'},
                    'evidence': [{'span_id': request['payload']['evidence_spans'][0]['id']}]})
            return response
        result = self.execute(malformed)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        report = _unseal(self.run / 'rounds' / '0' / 'report.json')
        self.assertTrue(any('invalid_record_id' in f['reason'] for f in report['candidate_rejections']))
        self.assertEqual(load_language_data(self.run / 'candidate', 'en')['claims'], [])

    def test_unexpected_controller_error_does_not_leave_running_state(self):
        self.start()
        with patch('reading_pack_producer.pipeline.Runner.generate', side_effect=KeyError('record')):
            with self.assertRaises(KeyError):
                self.execute(Worker())
        self.assertEqual(pipeline_status(self.run)['state'], 'blocked_execution')
        self.assertIn('KeyError', pipeline_status(self.run)['reason'])

    def test_real_subprocess_adapter_and_cli_end_to_end(self):
        import sys
        adapter = Path(__file__).parent / 'fixtures' / 'pipeline_adapter.py'
        for worker in self.recipe['workers'].values():
            worker['command'] = [sys.executable, str(adapter)]
        recipe = self.root / 'recipe.json'
        recipe.write_text(json.dumps(self.recipe))
        result = cli('pipeline', 'start', str(self.source), '--experimental', '--recipe', str(recipe), '--run', str(self.run))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['state'], 'awaiting_author_approval')

    def test_failed_repair_cannot_replace_an_unrelated_record(self):
        self.start()
        worker = Worker(always_fail=True)
        def wrong_scope(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'grade':
                response['result']['findings'][0]['record_ids'] = ['CL-OTHER']
            return response
        result = self.execute(wrong_scope)
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertTrue(all(r['pack_sha256'] == result['rounds'][0]['pack_sha256'] for r in result['rounds']))
        self.assertFalse(any('/holdout/' in r['job'] for r in worker.requests))
        rejected = list(self.run.glob('candidate-evidence-rejections-*.json'))
        self.assertTrue(rejected)
        self.assertTrue(all(row['reason'] == 'unrelated_record_replacement'
                            for p in rejected for row in _unseal(p)['rejections']))

    def test_submitted_worker_cannot_approve_its_candidate(self):
        self.start()
        worker = Worker()
        def approved(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'generate':
                response['result']['candidates'][0]['record']['status'] = 'approved'
            return response
        self.assertEqual(self.execute(approved)['state'], 'blocked_execution')


    def test_author_corrections_start_fresh_tests_before_another_signoff(self):
        from reading_pack_review.review_session import build_author_review_session
        from tests.test_assisted_review import _set_overrides, _sign
        self.start()
        self.execute(Worker())
        candidate = self.run / 'candidate'
        review = candidate / '.reading-pack/reviews/pipeline-author.review.md'
        session = build_author_review_session(candidate, candidate / '.reading-pack/reviews/pipeline-author')
        unit = session['records'][0]['unit_id']
        text = _set_overrides(review.read_text(), f"""### {unit}
- `decision`: `revise`
- `comment`: Make the joint condition explicit.
#### `summary`
- `operation`: `set`
<!-- RP_VALUE_START -->
{SUMMARY} Both conditions must be satisfied.
<!-- RP_VALUE_END -->""")
        review.write_text(_sign(text, submitted=True, final_signoff=False))
        worker = Worker()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            result = finalize_pipeline(self.run)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertIn('revisions', result['run'])
        self.assertTrue(any(r['stage'] == 'benchmark' for r in worker.requests))
        self.assertTrue(any(r['stage'] == 'grade' for r in worker.requests))
        self.assertEqual(load_language_data(candidate, 'en')['chapters'][0]['summary'], SUMMARY)
        self.assertFalse((self.run / 'release').exists())

    def test_inapplicable_web_profile_fails_before_author_approval(self):
        self.recipe['public_base_url'] = 'https://example.org/packs'
        self.start()
        result = self.execute(Worker())
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertTrue(any('delivery preflight' in f['reason'] for f in result['rounds'][0]['failures']))

    def test_delivery_build_uses_existing_valid_profile(self):
        from tests.support import copy_sample
        from reading_pack_producer.pipeline import build_pipeline_delivery
        project = copy_sample(self.root)
        self.recipe['public_base_url'] = 'https://example.org/packs'
        build_pipeline_delivery(project, self.recipe, 'en')
        self.assertTrue(list((project / 'dist/delivery').rglob('manifest.json')))

    def test_benchmark_rejection_stops_before_pack_generation(self):
        self.start()
        worker = Worker()
        def reject(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'benchmark_review':
                response['result']['valid'] = False
            return response
        result = self.execute(reject)
        self.assertEqual(result['state'], 'failed_quality')
        self.assertNotIn('generate', [r['stage'] for r in worker.requests])

    def test_oversized_pack_cannot_reach_author_approval(self):
        self.recipe['max_pack_bytes'] = 1000
        self.recipe['max_rounds'] = 1
        self.start()
        result = self.execute(Worker())
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertTrue(any(f['category'] == 'too_large' for f in result['rounds'][0]['failures']))


    def test_regressing_round_is_not_used_as_the_next_baseline(self):
        self.recipe['answer_repetitions'] = 2
        self.start()
        worker = Worker()
        def regression(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'repair':
                if request['job'].startswith('1/'):
                    response['result']['candidates'][0]['record']['summary'] += ' First revision.'
                elif request['job'].startswith('2/'):
                    self.assertEqual(request['payload']['canonical']['chapters'][0]['summary'], SUMMARY)
            if request['stage'] == 'grade':
                key = request['job']
                fail = ('/development/' in key and ((key.startswith('0/') and '/development-1/1/' in key) or (key.startswith('1/') and '/development-1/0/' in key)))
                if fail:
                    response['result']['requirements_met'] = [False] * len(request['payload']['case']['requirements'])
                    response['result']['findings'] = [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                        'reason': 'The condition needs correction.', 'evidence': [{'source_id':'SRC-1',
                        'span_id': request['payload']['evidence_spans'][0]['id']}]}]
            return response
        result = self.execute(regression)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertTrue(result['rounds'][1]['regression'])
        self.assertFalse(result['rounds'][1]['improved'])
        self.assertEqual(len(result['rounds']), 3)

    def test_unresolved_grading_uncertainty_cannot_pass(self):
        self.recipe['max_rounds'] = 1
        self.start()
        worker = Worker()
        def uncertain(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'grade':
                response['result']['uncertain'] = True
            return response
        result = self.execute(uncertain)
        self.assertEqual(result['state'], 'failed_quality', result)
        self.assertEqual(sum(r['stage'] == 'grade' for r in worker.requests), 2)

    def test_adapter_execution_errors_have_a_fixed_retry_limit(self):
        self.start()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=ReadingPackError('timeout')) as adapter:
            result = resume_pipeline(self.run)
            self.assertEqual(result['state'], 'blocked_execution')
            self.assertEqual(adapter.call_count, self.recipe['max_attempts'])
            resume_pipeline(self.run)
            self.assertEqual(adapter.call_count, self.recipe['max_attempts'])

    def test_bilingual_seed_retains_originals_and_provided_primary_prose(self):
        from reading_pack.project import create_project, write_json
        from reading_pack.importers import import_manuscript
        from reading_pack_producer.pipeline import _inventory
        seed = self.root / 'seed'
        create_project(seed, title='Supplied Book', author='Author', languages=['en','ja'], primary_language='en')
        import_manuscript(seed, self.source, lang='en')
        data = load_language_data(seed,'en')
        data['chapters'][0]['summary'] = SUMMARY + ' Supplied wording.'
        data['chapters'][0]['status'] = 'approved'
        write_json(seed/'data/pack.en.json', data)
        before = _inventory(seed)
        start_pipeline(self.run, self.source, self.recipe, project=seed)
        result = self.execute(Worker())
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual(_inventory(seed), before)
        candidate = load_language_data(self.run/'candidate','en')['chapters'][0]
        self.assertEqual(candidate, data['chapters'][0])
        self.assertFalse((self.run/'candidate/dist/reading-pack.ja.md').exists())

    def test_saved_candidate_can_recover_after_interrupted_state_commit(self):
        from reading_pack_producer.pipeline import Runner
        self.start()
        original = Runner.save
        def interrupt_after_rename(runner, state, *args, **kwargs):
            if state == 'awaiting_author_approval':
                raise KeyboardInterrupt
            return original(runner, state, *args, **kwargs)
        worker = Worker()
        with patch.object(Runner, 'save', interrupt_after_rename), patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            with self.assertRaises(KeyboardInterrupt):
                resume_pipeline(self.run)
        worker.requests.clear()
        self.assertEqual(self.execute(worker)['state'], 'awaiting_author_approval')
        self.assertEqual(worker.requests, [])

    def test_cli_recipe_and_prepare_only_do_not_call_a_model(self):
        recipe = self.root / 'recipe.json'
        result = cli('pipeline', 'recipe', '--experimental', '--output', str(recipe), '--generator', '/bin/false',
                     '--judge', '/bin/false', '--reader', '/bin/false', '--generator-model', 'g',
                     '--judge-model', 'j', '--reader-model', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = cli('pipeline', 'start', str(self.source), '--experimental', '--recipe', str(recipe), '--run', str(self.run), '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pipeline_status(self.run)['calls_reserved'], 0)


if __name__ == '__main__':
    unittest.main()
