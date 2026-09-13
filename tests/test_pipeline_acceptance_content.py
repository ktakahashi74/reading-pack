from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline_acceptance_content import (
    CONTENT_PROMPT,
    build_content_plan,
    validate_content_results,
)
from reading_pack_producer.pipeline_acceptance_ledger import freeze_inventory


SOURCE = """# Tiny Book

## First Gate

Alpha is 10, while Beta is 5. Alpha is therefore twice Beta under the stated test condition.

### Limits

The comparison applies only in dry weather.

## Second Gate

The second gate requires both hinges to be inspected.
"""

SUPPLEMENT = "Author note: the dry-weather qualification remains in force.\n"


def _chunks(source_id, role, digest, text, size=55):
    result = []
    for start in range(0, len(text), size):
        end = min(start + size, len(text))
        left = max(0, start - 12)
        right = min(len(text), end + 12)
        result.append({
            'id': f'{source_id}-{start}', 'source_id': source_id, 'role': role,
            'source_sha256': digest, 'start': start, 'end': end,
            'text_start': left, 'text': text[left:right],
        })
    return result


class ContentPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'inputs').mkdir()
        self.primary = self.root / 'inputs' / 'book.md'
        self.primary.write_text(SOURCE, encoding='utf-8')
        self.supplement = self.root / 'inputs' / 'note.txt'
        self.supplement.write_text(SUPPLEMENT, encoding='utf-8')
        self.primary_hash = hashlib.sha256(SOURCE.encode()).hexdigest()
        self.supplement_hash = hashlib.sha256(SUPPLEMENT.encode()).hexdigest()
        self.runner = SimpleNamespace(
            root=self.root,
            manifest={'sources': [
                {'id': 'SRC-1', 'name': 'book.md', 'path': 'inputs/book.md',
                 'format': 'markdown', 'role': 'primary-book', 'sha256': self.primary_hash},
                {'id': 'SRC-2', 'name': 'note.txt', 'path': 'inputs/note.txt',
                 'format': 'text', 'role': 'author-supplement', 'sha256': self.supplement_hash},
            ]},
            chunks=(_chunks('SRC-1', 'primary-book', self.primary_hash, SOURCE) +
                    _chunks('SRC-2', 'author-supplement', self.supplement_hash, SUPPLEMENT)),
            recipe={'audit_context_characters': 10000,
                    'chapter_review_context_characters': 10000},
        )
        self.canonical = {
            'schema_version': 1,
            'language': 'en',
            'source': {'format': 'markdown', 'name': 'book.md', 'sha256': self.primary_hash},
            'book': {'title': 'Tiny Book', 'author': 'A. Writer'},
            # Deliberately thin: the source-derived Second Gate is absent.
            'chapters': [{
                'id': 'CH-FIRST', 'kind': 'chapter', 'title': 'First Gate',
                'sections': ['Limits'], 'summary': 'Alpha is twice Beta when dry.',
                'terms': ['Alpha', 'Beta'], 'status': 'draft',
                'extra_field': {'comparison': [10, 5]},
            }],
            'claims': [{
                'id': 'CL-RATIO', 'statement': 'Alpha is twice Beta.',
                'chapter_ids': ['CH-FIRST'], 'status': 'draft',
                'source_locations': ['book.md#normalized-text:30-63'],
            }],
            'certainty': [],
            'custom_note': {'text': 'Dry-weather condition applies.', 'priority': 2,
                            'source_locations': [f'note.txt#normalized-text:0-{len(SUPPLEMENT)}']},
            'score_hint': 10,
            'reading_instructions': 'Use the source roles.',
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_whole_inventory_source_roles_and_missing_source_chapter(self):
        plan = build_content_plan(self.runner, self.canonical)
        top_paths = {target['canonical_path'] for target in plan['targets']
                     if target['kind'] in {'canonical_field', 'canonical_collection'}}
        self.assertEqual(top_paths, {'/' + field for field in self.canonical})

        record = next(target for target in plan['targets']
                      if target.get('record_id') == 'CH-FIRST')
        self.assertEqual(record['record'], self.canonical['chapters'][0])
        self.assertEqual(record['all_fields_in_scope'], sorted(self.canonical['chapters'][0]))
        criteria = {check['criterion'] for check in plan['checks']
                    if check['target_id'] == record['id']}
        self.assertEqual(criteria, {'source_fidelity', 'important_conditions'})

        chapters = [target for target in plan['targets'] if target['kind'] == 'source_chapter']
        self.assertEqual([target['title'] for target in chapters], ['First Gate', 'Second Gate'])
        missing = next(target for target in chapters if target['title'] == 'Second Gate')
        self.assertFalse(missing['candidate_present'])
        missing_batch = next(batch for batch in plan['batches']
                             if batch['checks'][0]['target_id'] == missing['id'])
        self.assertIsNone(missing_batch['payload']['source_requirement']['candidate_record_id'])
        self.assertEqual(missing_batch['payload']['canonical']['chapters'],
                         self.canonical['chapters'])
        self.assertEqual(missing_batch['payload']['canonical']['claims'],
                         self.canonical['claims'])
        self.assertTrue(missing_batch['payload']['source_context']['complete'])

        claim_batch = next(batch for batch in plan['batches']
                           if batch['payload'].get('canonical', {}).get('claims'))
        roles = {sample['role'] for sample in claim_batch['payload']['samples']}
        # The locator is expanded to its complete primary chapter. The supplement
        # remains available through small-source fallback only where all sources
        # are the selected complete context.
        self.assertIn('primary-book', roles)
        note_batch = next(batch for batch in plan['batches']
                          if 'custom_note' in batch['payload'].get('canonical', {}))
        self.assertEqual({sample['role'] for sample in note_batch['payload']['samples']},
                         {'author-supplement'})
        self.assertIn('supplement', CONTENT_PROMPT.lower())
        self.assertTrue(all({'role', 'source_sha256', 'start', 'end',
                             'text_start', 'text'} <= set(sample)
                            for batch in plan['batches'] for sample in batch['payload']['samples']))
        self.assertTrue(any(check['criterion'] == 'global_consistency' for check in plan['checks']))
        deferred = next(check for check in plan['checks']
                        if check['criterion'] == 'instruction_integrity')
        self.assertEqual(deferred['method'], 'deferred')
        checked = {check['target_id'] for check in plan['checks']}
        self.assertEqual(checked, {target['id'] for target in plan['targets']})
        frozen = freeze_inventory(plan['targets'], plan['checks'], {'candidate_sha256': 'b' * 64})
        self.assertEqual(frozen['targets'], plan['targets'])

    def test_chapter_alias_maps_existing_candidate_without_false_absence(self):
        self.canonical['chapters'][0]['title'] = 'Entry'
        self.canonical['chapters'][0]['aliases'] = ['First Gate']
        plan = build_content_plan(self.runner, self.canonical)
        first = next(target for target in plan['targets']
                     if target['kind'] == 'source_chapter' and target['title'] == 'First Gate')
        self.assertTrue(first['candidate_present'])
        self.assertEqual(first['candidate_record_id'], 'CH-FIRST')

    def test_large_unlocated_content_and_global_check_are_blocked_not_truncated(self):
        self.runner.recipe['audit_context_characters'] = 50
        plan = build_content_plan(self.runner, self.canonical)
        book_target = next(target for target in plan['targets']
                           if target.get('canonical_path') == '/book')
        ids = {check['id'] for check in plan['checks'] if check['target_id'] == book_target['id']}
        self.assertTrue(ids)
        self.assertTrue(ids <= {result['check_id'] for result in plan['blocked']})
        global_check = next(check for check in plan['checks']
                            if check['criterion'] == 'global_consistency')
        self.assertIn(global_check['id'], {result['check_id'] for result in plan['blocked']})
        self.assertFalse(any(batch['checks'][0]['id'] in ids for batch in plan['batches']))

    def test_untrusted_text_structure_leaves_chapter_orientation_incomplete(self):
        self.runner.manifest['sources'][0]['format'] = 'text'
        plan = build_content_plan(self.runner, self.canonical)
        inventory = next(target for target in plan['targets']
                         if target['kind'] == 'source_chapter_inventory')
        check = next(check for check in plan['checks'] if check['target_id'] == inventory['id'])
        result = next(result for result in plan['blocked'] if result['check_id'] == check['id'])
        self.assertEqual((result['status'], result['outcome']), ('incomplete', 'unresolved'))


class ContentResultValidationTests(unittest.TestCase):
    def setUp(self):
        digest = 'a' * 64
        self.batch = {
            'checks': [
                {'id': 'CHECK-A', 'target_id': 'TARGET-A', 'criterion': 'source_fidelity',
                 'method': 'semantic', 'source_ranges': [
                     {'source_id': 'SRC-1', 'source_sha256': digest, 'start': 2, 'end': 12}]},
                {'id': 'CHECK-B', 'target_id': 'TARGET-B', 'criterion': 'important_conditions',
                 'method': 'semantic', 'source_ranges': [
                     {'source_id': 'SRC-1', 'source_sha256': digest, 'start': 2, 'end': 12}]},
            ],
            'payload': {
                'samples': [
                    {'source_id': 'SRC-1', 'source_sha256': digest, 'role': 'primary-book',
                     'start': 0, 'end': 7, 'text_start': 0, 'text': 'xxAlpha'},
                    {'source_id': 'SRC-1', 'source_sha256': digest, 'role': 'primary-book',
                     'start': 7, 'end': 14, 'text_start': 7, 'text': ' 10 > 5'},
                ],
                'canonical': {'claims': [{'id': 'CL-1'}]},
                'source_context': {'complete': True},
            },
        }
        self.evidence = {'source_id': 'SRC-1', 'source_sha256': digest,
                         'start': 2, 'end': 12, 'quote': 'Alpha 10 >', 'span_id': 'SPAN-X'}

    def _result(self, check_id='CHECK-A', **updates):
        value = {'check_id': check_id, 'status': 'complete', 'outcome': 'pass',
                 'reason': 'The numeric comparison and its direction match the supplied source.',
                 'evidence': [copy.deepcopy(self.evidence)], 'reader_impact': ''}
        value.update(updates)
        return value

    def test_exact_number_comparison_and_cross_chunk_evidence(self):
        result = self._result()
        self.assertEqual(validate_content_results(self.batch, {'checks': [result]}), [result])

    def test_omitted_check_remains_pending(self):
        result = self._result()
        validated = validate_content_results(self.batch, {'checks': [result]})
        self.assertEqual([value['check_id'] for value in validated], ['CHECK-A'])

    def test_unknown_duplicate_wrong_quote_and_out_of_scope_are_rejected(self):
        cases = [
            {'checks': [self._result('CHECK-Z')]},
            {'checks': [self._result(), self._result()]},
            {'checks': [self._result(evidence=[{**self.evidence, 'quote': 'Alpha 11 >'}])]},
            {'checks': [self._result(evidence=[{**self.evidence, 'start': 0, 'end': 2,
                                                'quote': 'xx'}])]},
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(ReadingPackError):
                validate_content_results(self.batch, response)

    def test_incomplete_context_cannot_complete_and_unresolved_needs_specific_fields(self):
        incomplete = copy.deepcopy(self.batch)
        incomplete['payload']['source_context']['complete'] = False
        with self.assertRaises(ReadingPackError):
            validate_content_results(incomplete, {'checks': [self._result()]})
        unresolved = self._result(status='incomplete', outcome='unresolved', evidence=[],
                                  reason='', reader_impact='Cannot assess the reader effect.')
        with self.assertRaises(ReadingPackError):
            validate_content_results(self.batch, {'checks': [unresolved]})
        unresolved['reason'] = 'The required note was not supplied.'
        self.assertEqual(validate_content_results(self.batch, {'checks': [unresolved]}), [unresolved])

        complete_unresolved = self._result(outcome='unresolved',
            reason='The supplied passages support two readings that remain unresolved.',
            reader_impact='The reader cannot tell which scope the claim uses.')
        self.assertEqual(validate_content_results(
            self.batch, {'checks': [complete_unresolved]}), [complete_unresolved])

    def test_pass_and_defect_need_verified_evidence(self):
        for outcome in ('pass', 'defect'):
            value = self._result(outcome=outcome, evidence=[], reader_impact='Reader receives a reversed comparison.')
            with self.subTest(outcome=outcome), self.assertRaises(ReadingPackError):
                validate_content_results(self.batch, {'checks': [value]})


if __name__ == '__main__':
    unittest.main()
