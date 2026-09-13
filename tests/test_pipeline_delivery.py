import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.delivery import prepare, recipe, run, status
from reading_pack_producer.pipeline import _seal, _unseal
from tests.support import cli, read_json

FIXTURE = Path(__file__).parent / 'fixtures' / 'delivery_adapter.py'
SOURCE = '# Test chapter\n\n## First section\nEvidence one.\n\n## Second section\nEvidence two.\n'


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'book.md'
        self.source.write_text(SOURCE)
        self.log = self.root / 'calls.log'
        self.run = self.root / 'delivery'

    def recipe(self, mode='normal'):
        command = [sys.executable, str(FIXTURE), mode, str(self.log)]
        return recipe(command, command, generator_model='generator', evaluator_model='evaluator',
                      max_cost_usd=3, call_allowance_usd=1, max_wall_seconds=300,
                      timeout_seconds=10, language='en', scope='Chapter excerpt only')

    def prepare(self, mode='normal', **kwargs):
        return prepare(self.run, self.source, self.recipe(mode), title='Test', chapter_level=1, **kwargs)

    def test_preparation_freezes_one_chapter_and_two_sections_without_calls(self):
        result = self.prepare()
        self.assertEqual((result['chapter_count'], result['section_count'], result['maximum_calls']), (1, 2, 3))
        self.assertFalse(self.log.exists())
        with self.assertRaisesRegex(ReadingPackError, 'overwrite'):
            self.prepare()

    def test_ambiguous_top_heading_requires_explicit_level_before_send(self):
        with self.assertRaisesRegex(ReadingPackError, 'ambiguous'):
            prepare(self.run, self.source, self.recipe())
        self.assertFalse(self.run.exists())
        self.assertFalse(self.log.exists())

    def test_budget_admission_uses_actual_chapter_count(self):
        with self.assertRaisesRegex(ReadingPackError, '5 calls'):
            prepare(self.run, self.source, self.recipe(), chapter_level=2)
        self.assertFalse(self.run.exists())

    def test_complete_low_scoring_pack_is_delivered_and_never_retried(self):
        self.prepare('low_scores')
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        self.assertFalse(report['quality_gate'])
        self.assertEqual(report['evaluation']['record_counts'], {'unsupported': 3})
        self.assertEqual(report['evaluation']['chapters']['CH-01']['dimensions']['coverage']['score'], 0)
        self.assertEqual(report['resources']['unknown_cost_calls'], 3)
        self.assertEqual(report['resources']['repairs'], 0)
        pack = (self.run / 'reading-pack.en.md').read_bytes()
        calls = self.log.read_text()
        run(self.run)
        self.assertEqual(self.log.read_text(), calls)
        self.assertEqual((self.run / 'reading-pack.en.md').read_bytes(), pack)
        self.assertEqual(len(calls.splitlines()), 3)
        self.assertEqual(report['user_adoption'], 'not_decided')

    def test_evaluation_failure_retains_pack_and_reports_missing_not_zero(self):
        self.prepare('evaluation_error')
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        self.assertEqual(report['evaluation']['records_evaluated'], 0)
        self.assertEqual(report['evaluation']['records_expected'], 3)
        self.assertEqual(report['evaluation']['chapters'], {})
        self.assertIsNone(report['evaluation']['global'])
        self.assertTrue((self.run / 'reading-pack.en.md').exists())

    def test_invalid_evaluation_identity_or_missing_record_is_not_a_score(self):
        for mode in ('wrong_identity', 'missing_record'):
            with self.subTest(mode=mode):
                self.run = self.root / mode
                self.prepare(mode)
                run(self.run)
                report = read_json(self.run / 'quality-report.json')
                self.assertEqual(report['evaluation']['records_evaluated'], 0)
                self.assertEqual(report['evaluation']['chapters'], {})

    def test_empty_model_content_is_measured_without_quality_gate(self):
        self.prepare('empty_content')
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        self.assertEqual(report['mechanical']['nonempty_content'], {'present': 0, 'expected': 3})

    def test_multiple_chapters_and_partial_generation_keep_all_denominators(self):
        self.source.write_text('# First\n## A\nEvidence.\n# Second\n## B\nEvidence.\n')
        value = self.recipe('partial_generation');value['max_cost_usd'] = value['cumulative_cost_limit_usd'] = 5
        prepare(self.run, self.source, value)
        self.assertEqual(run(self.run)['state'], 'delivered_partial')
        report = read_json(self.run / 'quality-report.json')
        self.assertEqual(report['generation'], {'completed_chapters': 1, 'total_chapters': 2})
        self.assertEqual(report['evaluation']['records_expected'], 4)
        self.assertEqual(report['evaluation']['records_evaluated'], 2)
        self.assertEqual(len(self.log.read_text().splitlines()), 4)

    def test_all_generation_failures_have_no_false_completion(self):
        self.prepare('generation_error')
        self.assertEqual(run(self.run)['state'], 'generation_failed')
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertTrue((self.run / 'quality-report.json').exists())

    def test_interrupted_unknown_call_is_not_resent(self):
        self.prepare()
        with patch('reading_pack_producer.delivery.run_local_adapter', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(self.run)
        with patch('reading_pack_producer.delivery.run_local_adapter', side_effect=AssertionError('must not send')):
            result = run(self.run)
        self.assertEqual(result['state'], 'generation_failed')
        report = read_json(self.run / 'quality-report.json')
        self.assertEqual(report['jobs']['generate/CH-01']['status'], 'outcome_unknown')

    def test_deadline_and_prior_cost_are_preserved(self):
        value = self.recipe();value.update(prior_cost_usd=99, cumulative_cost_limit_usd=100)
        with self.assertRaisesRegex(ReadingPackError, 'cumulative'):
            prepare(self.run, self.source, value, chapter_level=1)
        self.prepare()
        state = _unseal(self.run / 'delivery-state.json');state.update(state='running', started_at=1)
        _seal(self.run / 'delivery-state.json', state)
        with patch('reading_pack_producer.delivery.run_local_adapter', side_effect=AssertionError('must not send')):
            self.assertEqual(run(self.run)['state'], 'generation_failed')

    def test_source_and_delivered_output_tampering_are_detected(self):
        self.prepare();run(self.run)
        pack = self.run / 'reading-pack.en.md';pack.write_text('tampered')
        with self.assertRaisesRegex(ReadingPackError, 'output changed'):
            status(self.run)
        self.run = self.root / 'source-change';self.prepare()
        (self.run / 'source.txt').write_text('tampered')
        with self.assertRaisesRegex(ReadingPackError, 'source changed'):
            run(self.run)

    def test_nonfinite_budget_is_rejected_before_preparation(self):
        value = self.recipe();value['max_cost_usd'] = float('nan')
        with self.assertRaisesRegex(ReadingPackError, 'finite'):
            prepare(self.run, self.source, value, chapter_level=1)
        self.assertFalse(self.run.exists())

    def test_saved_exchange_tampering_is_detected_after_delivery(self):
        self.prepare();run(self.run)
        path = next((self.run / 'jobs').glob('*.json'));path.write_text('{}')
        with self.assertRaisesRegex(ReadingPackError, 'exchange changed'):
            status(self.run)

    def test_cli_deliver_prepare_plan_resume_status(self):
        path = self.root / 'recipe.json';path.write_text(json.dumps(self.recipe('low_scores')))
        result = cli('pipeline', 'deliver', str(self.source), '--run', str(self.run), '--recipe', str(path), '--chapter-level', '1', '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = cli('pipeline', 'plan', '--run', str(self.run))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        result = cli('pipeline', 'resume', '--run', str(self.run))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = cli('pipeline', 'status', '--run', str(self.run))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'delivered')

    def test_cli_recipe_and_unsafe_scope(self):
        for i, scope in enumerate(('Chapter only', 'chapter\nPACK | injected')):
            output = self.root / f'recipe-{i}.json'
            result = cli('pipeline', 'delivery-recipe', '--generator', '/bin/false', '--generator-model', 'g',
                         '--evaluator', '/bin/false', '--evaluator-model', 'e', '--scope', scope,
                         '--max-cost-usd', '3', '--call-allowance-usd', '1', '--max-wall-seconds', '1200', '--output', str(output))
            self.assertEqual(result.returncode == 0, i == 0, result.stderr)
            self.assertEqual(output.exists(), i == 0)

    def test_fenced_headings_and_org(self):
        self.source.write_text('# Chapter\n~~~\n# Not a chapter\n~~~\n## Real section\nText.\n')
        self.assertEqual(self.prepare()['section_count'], 1)
        self.source.write_text('#+TITLE: Book\n* First\n** A\nText.\n* Second\n** B\nText.\n')
        self.run = self.root / 'org'
        value = self.recipe();value['max_cost_usd'] = value['cumulative_cost_limit_usd'] = 5
        result = prepare(self.run, self.source, value, source_format='org')
        self.assertEqual((result['chapter_count'], result['section_count']), (2, 2))


EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'clockwork-garden'


class SeededDeliveryTests(unittest.TestCase):
    """A seed project's author-provided modules must survive delivery, measurably."""

    def setUp(self):
        from reading_pack.project import copy_project
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / 'seed-project'
        copy_project(EXAMPLE, self.seed)
        self.source = self.seed / 'manuscripts' / 'book.en.md'
        self.log = self.root / 'calls.log'
        self.run = self.root / 'delivery'

    def recipe(self, mode='normal'):
        command = [sys.executable, str(FIXTURE), mode, str(self.log)]
        return recipe(command, command, generator_model='generator', evaluator_model='evaluator',
                      max_cost_usd=5, call_allowance_usd=1, max_wall_seconds=600,
                      timeout_seconds=10, language='en', scope='Sample book')

    def seed_data(self):
        return read_json(self.seed / 'data' / 'pack.en.json')

    def write_seed_data(self, data):
        (self.seed / 'data' / 'pack.en.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def test_seeded_delivery_carries_author_modules_and_measures_completeness(self):
        from reading_pack.project import load_config
        result = prepare(self.run, self.source, self.recipe(), chapter_level=2, seed=self.seed)
        self.assertEqual((result['chapter_count'], result['section_count'], result['maximum_calls']), (2, 3, 5))
        self.assertEqual(result['seed']['policy'], 'preserve')
        self.assertFalse(self.log.exists())
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        c = report['completeness']
        self.assertEqual(c['lost_modules'], [])
        for name in ('misreadings', 'names', 'glossary', 'references', 'certainty'):
            self.assertEqual(c['modules'][name], {'seed': 1, 'output': 1, 'lost_ids': [], 'added_ids': []})
        self.assertEqual(c['modules']['claims'], {'seed': 1, 'output': 4, 'lost_ids': [],
                                                  'added_ids': ['CP-S01-01', 'CP-S01-02', 'CP-S02-01']})
        self.assertEqual(c['modules']['summaries'], {'seed': 2, 'output': 2, 'preserved': 2, 'generated': 0, 'empty': 0})
        self.assertTrue(c['seed']['workflow_reset_to_pending'])
        pack = (self.run / 'reading-pack.en.md').read_text(encoding='utf-8')
        self.assertIn('ENDPACK | chapters=2 | props=4 | mis=1 | names=1 | gloss=1 | ref=1', pack)
        self.assertIn('status=draft', pack.splitlines()[0])
        self.assertIn('MIS-01', pack)
        seed = self.seed_data()
        data = read_json(self.run / 'project' / 'data' / 'pack.en.json')
        self.assertEqual([ch['summary'] for ch in data['chapters']], [ch['summary'] for ch in seed['chapters']])
        self.assertEqual([ch['status'] for ch in data['chapters']], ['approved', 'approved'])
        self.assertEqual(data['misreadings'], seed['misreadings'])
        self.assertEqual(data['claims'][0], seed['claims'][0])
        self.assertTrue(all(cl['status'] == 'draft' for cl in data['claims'][1:]))
        config = load_config(self.run / 'project')
        self.assertEqual(config['status'], 'draft')
        self.assertTrue(config['version'].endswith('-draft'))
        self.assertTrue(all(v == 'pending' for v in config['workflow'].values()))
        for path in (self.run / 'jobs').glob('*.json'):
            request = read_json(path)['request']
            if request['stage'] == 'artifact_content':
                ids = [r['id'] for r in request['payload']['records']]
                self.assertNotIn('CL-01', ids)
                self.assertIn(request['payload']['chapter']['id'], ids)
        self.assertEqual(report['mechanical']['nonempty_content'], {'present': 5, 'expected': 5})
        self.assertTrue(all(x['resolved'] for x in report['mechanical']['source_locators']))
        self.assertEqual(report['evaluation']['records_evaluated'], 5)
        self.assertEqual([i['code'] for i in report['mechanical']['project_issues'] if i['severity'] == 'error'], [])
        self.assertEqual(c['author_input_conflicts'], [])
        self.assertIn('Module completeness', (self.run / 'quality-report.en.md').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ReadingPackError, 'seed snapshot changed'):
            (self.run / 'seed' / 'data' / 'pack.en.json').write_text('{}')
            status(self.run)

    def test_seed_structure_mismatches_are_rejected_before_any_call(self):
        data = self.seed_data()
        data['chapters'][1]['title'] = 'Renamed chapter'
        self.write_seed_data(data)
        with self.assertRaisesRegex(ReadingPackError, 'chapter map'):
            prepare(self.run, self.source, self.recipe(), chapter_level=2, seed=self.seed)
        self.assertFalse(self.run.exists())
        with self.assertRaisesRegex(ReadingPackError, 'unknown seed chapters'):
            prepare(self.run, self.source, self.recipe(), chapter_level=2, seed=self.seed, chapter_map={'CH-09': 'x'})
        result = prepare(self.run, self.source, self.recipe(), chapter_level=2, seed=self.seed,
                         chapter_map={'CH-02': 'The Garden Chooses'})
        self.assertEqual(result['chapter_count'], 2)
        self.assertEqual(_unseal(self.run / 'delivery-plan.json')['units'][1]['manuscript_title'], 'The Garden Chooses')
        data['chapters'][1]['title'] = 'The Garden Chooses'
        data['chapters'][0]['sections'] = ['The Blueprint']
        self.write_seed_data(data)
        with self.assertRaisesRegex(ReadingPackError, 'section titles'):
            prepare(self.root / 'sections', self.source, self.recipe(), chapter_level=2, seed=self.seed)
        data['chapters'][0]['sections'] = ['The Blueprint', 'First Germination']
        data['chapters'].append({**data['chapters'][1], 'id': 'CH-03', 'title': 'Extra'})
        self.write_seed_data(data)
        with self.assertRaisesRegex(ReadingPackError, 'declares 3 chapters'):
            prepare(self.root / 'count', self.source, self.recipe(), chapter_level=2, seed=self.seed)
        with self.assertRaisesRegex(ReadingPackError, 'only with --seed'):
            prepare(self.root / 'noseed', self.source, self.recipe(), chapter_level=2, seed_policy='regenerate')
        self.assertFalse(self.log.exists())

    def test_regenerate_policy_replaces_seed_summaries_as_drafts(self):
        prepare(self.run, self.source, self.recipe(), chapter_level=2, seed=self.seed, seed_policy='regenerate')
        self.assertEqual(run(self.run)['state'], 'delivered')
        data = read_json(self.run / 'project' / 'data' / 'pack.en.json')
        self.assertEqual([ch['summary'] for ch in data['chapters']], ['Synthetic chapter summary.'] * 2)
        self.assertEqual([ch['status'] for ch in data['chapters']], ['draft', 'draft'])
        self.assertTrue(all(ch['source_locations'][0].startswith('source.txt#normalized-text:') for ch in data['chapters']))
        report = read_json(self.run / 'quality-report.json')
        self.assertTrue(all(x['resolved'] for x in report['mechanical']['source_locators']))
        c = report['completeness']
        self.assertEqual(c['modules']['summaries'], {'seed': 2, 'output': 2, 'preserved': 0, 'generated': 2, 'empty': 0})
        self.assertEqual(c['modules']['misreadings']['lost_ids'], [])

    def test_partial_generation_keeps_seed_summary_for_failed_chapter(self):
        prepare(self.run, self.source, self.recipe('partial_generation'), chapter_level=2, seed=self.seed)
        self.assertEqual(run(self.run)['state'], 'delivered_partial')
        c = read_json(self.run / 'quality-report.json')['completeness']
        self.assertEqual(c['chapters']['CH-02'], {'summary': 'seed', 'terms': 'seed', 'status': 'approved'})
        self.assertEqual(c['modules']['claims']['added_ids'], ['CP-S01-01', 'CP-S01-02'])
        self.assertIn('MIS-01', (self.run / 'reading-pack.en.md').read_text(encoding='utf-8'))

    def test_seedless_delivery_reports_empty_auxiliary_modules_explicitly(self):
        source = self.root / 'book.md'
        source.write_text(SOURCE)
        value = self.recipe()
        prepare(self.run, source, value, title='Test', chapter_level=1)
        self.assertEqual(run(self.run)['state'], 'delivered')
        c = read_json(self.run / 'quality-report.json')['completeness']
        self.assertIsNone(c['seed'])
        self.assertIn('empty by construction', c['note'])
        self.assertEqual(c['modules']['misreadings'], {'seed': 0, 'output': 0, 'lost_ids': [], 'added_ids': []})
        self.assertIn('No seed', (self.run / 'quality-report.en.md').read_text(encoding='utf-8'))

    def test_cli_seeded_delivery_and_frozen_resume(self):
        path = self.root / 'recipe.json'
        path.write_text(json.dumps(self.recipe()))
        result = cli('pipeline', 'deliver', str(self.source), '--run', str(self.run), '--recipe', str(path),
                     '--chapter-level', '2', '--seed', str(self.seed), '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['seed']['policy'], 'preserve')
        self.assertFalse(self.log.exists())
        result = cli('pipeline', 'deliver', '--run', str(self.run), '--seed', str(self.seed))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        result = cli('pipeline', 'resume', '--run', str(self.run))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'delivered')
        self.assertIn('mis=1', (self.run / 'reading-pack.en.md').read_text(encoding='utf-8'))


class SuccessorDeliveryTests(unittest.TestCase):
    """A successor redoes only incomplete jobs and replays completed exchanges without sending."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'book.md'
        self.source.write_text('# First\n## A\nEvidence.\n# Second\n## B\nEvidence.\n')
        self.first = self.root / 'first'
        self.second = self.root / 'second'

    def recipe(self, mode, log, **extra):
        command = [sys.executable, str(FIXTURE), mode, str(self.root / log)]
        return recipe(command, command, generator_model='generator', evaluator_model='evaluator',
                      max_cost_usd=5, call_allowance_usd=1, max_wall_seconds=600,
                      timeout_seconds=10, language='en', scope='Two chapters', **extra)

    def test_failed_generation_is_regenerated_and_whole_pack_reevaluated(self):
        from reading_pack_producer.delivery import prepare_successor
        prepare(self.first, self.source, self.recipe('partial_generation', 'a.log'))
        self.assertEqual(run(self.first)['state'], 'delivered_partial')
        result = prepare_successor(self.second, self.first, self.recipe('normal', 'b.log'))
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(result['maximum_calls'], 3)
        self.assertEqual(result['carried_jobs'], ['evaluate/CH-01', 'generate/CH-01'])
        self.assertEqual(result['predecessor']['run'], str(self.first))
        self.assertFalse((self.root / 'b.log').exists())
        self.assertEqual(run(self.second)['state'], 'delivered')
        self.assertEqual((self.root / 'b.log').read_text().splitlines(), ['generate/CH-02', 'evaluate/CH-02', 'evaluate/global'])
        report = read_json(self.second / 'quality-report.json')
        self.assertEqual(report['generation'], {'completed_chapters': 2, 'total_chapters': 2})
        self.assertEqual(report['evaluation']['completed_chapters'], 2)
        self.assertEqual(report['resources']['model_calls_started'], 3)
        self.assertEqual(report['resources']['carried_jobs'], ['evaluate/CH-01', 'generate/CH-01'])
        self.assertEqual(report['jobs']['generate/CH-01']['carried'], _unseal(self.first / 'delivery-plan.json')['id'])
        self.assertEqual(status(self.second)['calls_started'], 3)
        self.assertIn('Successor run', (self.second / 'quality-report.en.md').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ReadingPackError, 'nothing to redo'):
            prepare_successor(self.root / 'third', self.second, self.recipe('normal', 'c.log'))

    def test_failed_evaluations_are_redone_on_an_identical_pack(self):
        from reading_pack_producer.delivery import prepare_successor
        prepare(self.first, self.source, self.recipe('evaluation_error', 'a.log'))
        self.assertEqual(run(self.first)['state'], 'delivered')
        first_pack = (self.first / 'reading-pack.en.md').read_bytes()
        result = prepare_successor(self.second, self.first, self.recipe('normal', 'b.log'))
        self.assertEqual(result['maximum_calls'], 3)
        self.assertEqual(result['carried_jobs'], ['generate/CH-01', 'generate/CH-02'])
        self.assertEqual(run(self.second)['state'], 'delivered')
        self.assertEqual((self.second / 'reading-pack.en.md').read_bytes(), first_pack)
        report = read_json(self.second / 'quality-report.json')
        self.assertEqual(report['evaluation']['records_evaluated'], 4)
        self.assertIsNotNone(report['evaluation']['global'])
        self.assertEqual(len((self.root / 'b.log').read_text().splitlines()), 3)

    def test_successor_guards(self):
        from reading_pack_producer.delivery import prepare_successor
        prepare(self.first, self.source, self.recipe('partial_generation', 'a.log'))
        with self.assertRaisesRegex(ReadingPackError, 'finished predecessor'):
            prepare_successor(self.second, self.first, self.recipe('normal', 'b.log'))
        run(self.first)
        other = self.recipe('normal', 'b.log');other['workers']['evaluator']['model'] = 'other'
        with self.assertRaisesRegex(ReadingPackError, 'models'):
            prepare_successor(self.second, self.first, other)
        other = self.recipe('normal', 'b.log');other['language'] = 'ja'
        with self.assertRaisesRegex(ReadingPackError, 'language'):
            prepare_successor(self.second, self.first, other)
        self.assertFalse(self.second.exists())
        path = next((self.first / 'jobs').glob('*.json'));path.write_text('{}')
        with self.assertRaisesRegex(ReadingPackError, 'exchange changed'):
            prepare_successor(self.second, self.first, self.recipe('normal', 'b.log'))
        self.assertFalse((self.root / 'b.log').exists())

    def test_seeded_predecessor_carries_seed_and_modules(self):
        from reading_pack.project import copy_project
        from reading_pack_producer.delivery import prepare_successor
        seed = self.root / 'seed-project';copy_project(EXAMPLE, seed)
        source = seed / 'manuscripts' / 'book.en.md'
        prepare(self.first, source, self.recipe('partial_generation', 'a.log'), chapter_level=2, seed=seed)
        self.assertEqual(run(self.first)['state'], 'delivered_partial')
        result = prepare_successor(self.second, self.first, self.recipe('normal', 'b.log'))
        self.assertEqual(result['seed']['policy'], 'preserve')
        self.assertEqual(run(self.second)['state'], 'delivered')
        c = read_json(self.second / 'quality-report.json')['completeness']
        self.assertEqual(c['lost_modules'], [])
        self.assertEqual(c['modules']['claims']['added_ids'], ['CP-S01-01', 'CP-S01-02', 'CP-S02-01'])
        self.assertIn('mis=1', (self.second / 'reading-pack.en.md').read_text(encoding='utf-8'))

    def test_global_allowance_and_evaluator_timeouts_are_reserved_and_sent(self):
        value = self.recipe('normal', 'a.log', global_call_allowance_usd=2, evaluator_timeout_seconds=20, global_timeout_seconds=30)
        with self.assertRaisesRegex(ReadingPackError, '5 calls / USD 6'):
            prepare(self.first, self.source, value)
        value['max_cost_usd'] = value['cumulative_cost_limit_usd'] = 6
        result = prepare(self.first, self.source, value)
        self.assertEqual(result['reserved_usd'], '6')
        self.assertEqual(run(self.first)['state'], 'delivered')
        plan = _unseal(self.first / 'delivery-plan.json');state = _unseal(self.first / 'delivery-state.json')
        self.assertEqual((state['jobs']['evaluate/global']['allowance_usd'], state['jobs']['evaluate/global']['timeout_seconds']), (2, 30))
        self.assertEqual((state['jobs']['evaluate/CH-01']['allowance_usd'], state['jobs']['evaluate/CH-01']['timeout_seconds']), (1, 20))
        self.assertEqual(state['jobs']['generate/CH-01']['timeout_seconds'], 10)
        exchange = read_json(self.first / 'jobs' / (state['jobs']['evaluate/global']['request_id'] + '.json'))
        self.assertEqual(exchange['request']['execution_budget'], {'call_allowance_usd': 2})
        tight = self.recipe('normal', 'a.log', global_timeout_seconds=600);tight['max_wall_seconds'] = 300
        with self.assertRaisesRegex(ReadingPackError, 'wall limit'):
            prepare(self.root / 'tight', self.source, tight)

    def test_cli_successor(self):
        prepare(self.first, self.source, self.recipe('evaluation_error', 'a.log'));run(self.first)
        path = self.root / 'recipe.json';path.write_text(json.dumps(self.recipe('normal', 'b.log')))
        result = cli('pipeline', 'deliver', '--run', str(self.second), '--predecessor', str(self.first), '--recipe', str(path), '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['maximum_calls'], 3)
        result = cli('pipeline', 'resume', '--run', str(self.second))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'delivered')
        result = cli('pipeline', 'deliver', str(self.source), '--run', str(self.root / 'x'), '--predecessor', str(self.first), '--recipe', str(path))
        self.assertNotEqual(result.returncode, 0)


class ExportTests(unittest.TestCase):
    """Deliverables leave the private run as a small hash-manifested set; evidence stays behind."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'book.md'
        self.source.write_text(SOURCE)
        self.log = self.root / 'calls.log'
        self.run = self.root / 'runs' / 'delivery'

    def recipe(self, mode='normal'):
        command = [sys.executable, str(FIXTURE), mode, str(self.log)]
        return recipe(command, command, generator_model='generator', evaluator_model='evaluator',
                      max_cost_usd=3, call_allowance_usd=1, max_wall_seconds=300,
                      timeout_seconds=10, language='en', scope='Chapter excerpt only')

    def test_run_exports_only_deliverables_to_a_sibling_folder_by_default(self):
        prepare(self.run, self.source, self.recipe(), title='Test', chapter_level=1)
        result = run(self.run)
        output = self.root / 'runs' / 'delivery-delivery'
        self.assertEqual(result['export'], {'output': str(output), 'unchanged': False,
                                            'files': ['pack.en.json', 'quality-report.en.md', 'quality-report.json', 'reading-pack.en.md']})
        self.assertEqual(sorted(p.name for p in output.iterdir()),
                         ['delivery-manifest.json', 'pack.en.json', 'quality-report.en.md', 'quality-report.json', 'reading-pack.en.md'])
        self.assertEqual((output / 'reading-pack.en.md').read_bytes(), (self.run / 'reading-pack.en.md').read_bytes())
        manifest = read_json(output / 'delivery-manifest.json')
        self.assertEqual(manifest['state'], 'delivered')
        self.assertEqual(manifest['run'], str(self.run))
        self.assertEqual(manifest['files']['reading-pack.en.md']['sha256'], _unseal(self.run / 'delivery-state.json')['outputs']['reading-pack.en.md'])
        self.assertIn('source.bin', manifest['withheld'])
        self.assertEqual(manifest['user_adoption'], 'not_decided')
        self.assertEqual(manifest['evaluation']['records_evaluated'], 3)
        for name in ('source.bin', 'source.txt', 'jobs', 'seed', 'delivery-plan.json'):
            self.assertFalse((output / name).exists(), name)
        from reading_pack_producer.delivery import export
        self.assertTrue(export(self.run)['unchanged'])
        (output / 'reading-pack.en.md').write_text('edited')
        with self.assertRaisesRegex(ReadingPackError, 'non-empty'):
            export(self.run)
        other = self.root / 'elsewhere'
        self.assertEqual(export(self.run, other)['output'], str(other))
        self.assertTrue((other / 'delivery-manifest.json').is_file())
        with self.assertRaisesRegex(ReadingPackError, 'outside the run'):
            export(self.run, self.run / 'inner')

    def test_explicit_output_is_frozen_in_the_plan_and_used_on_resume(self):
        output = self.root / 'handoff' / 'book-pack'
        result = prepare(self.run, self.source, self.recipe(), title='Test', chapter_level=1, output=output)
        self.assertEqual(result['output'], str(output))
        self.assertFalse(output.exists())
        run(self.run)
        self.assertTrue((output / 'delivery-manifest.json').is_file())
        from reading_pack_producer.delivery import export
        with self.assertRaisesRegex(ReadingPackError, 'finished delivery'):
            second = self.root / 'runs' / 'second'
            prepare(second, self.source, self.recipe(), title='Test', chapter_level=1)
            export(second)

    def test_cli_export_and_output_option(self):
        path = self.root / 'recipe.json';path.write_text(json.dumps(self.recipe()))
        output = self.root / 'out'
        result = cli('pipeline', 'deliver', str(self.source), '--run', str(self.run), '--recipe', str(path),
                     '--chapter-level', '1', '--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['export']['output'], str(output))
        self.assertTrue((output / 'reading-pack.en.md').is_file())
        result = cli('pipeline', 'export', '--run', str(self.run), '--output', str(self.root / 'again'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(json.loads(result.stdout)['files']), ['pack.en.json', 'quality-report.en.md', 'quality-report.json', 'reading-pack.en.md'])
        result = cli('pipeline', 'deliver', '--run', str(self.run), '--output', str(self.root / 'x'))
        self.assertNotEqual(result.returncode, 0)
