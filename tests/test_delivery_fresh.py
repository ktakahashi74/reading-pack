"""Regressions for silent old-source inheritance and unevaluated auxiliary modules."""
import json
from pathlib import Path
import tempfile
import unittest

from tests import test_pipeline_delivery as helpers
from tests.support import copy_sample, read_json
from reading_pack.errors import ReadingPackError
from reading_pack_producer.delivery import prepare, run


class FreshDeliveryTests(unittest.TestCase):
    setUp = helpers.DeliveryTests.setUp
    recipe = helpers.DeliveryTests.recipe
    prepare = helpers.DeliveryTests.prepare
    def test_seed_without_explicit_inheritance_is_rejected_before_sending(self):
        seed = copy_sample(self.root / 'old')
        with self.assertRaisesRegex(ReadingPackError, '--inherit-seed'):
            self.prepare(seed=seed)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.run.exists())
        self.prepare('fresh_modules', author='Test author')
        self.assertEqual(run(self.run)['state'], 'delivered')
        data = read_json(self.run / 'project/data/pack.en.json')
        self.assertNotIn('Clockwork Garden', json.dumps(data))
        self.assertFalse((self.run / 'seed').exists())
        self.assertEqual(data['book']['title'], 'Test')

    def test_every_generated_module_is_new_source_bound_and_evaluated(self):
        self.prepare('fresh_modules', author='Test author')
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        data = read_json(self.run / 'project/data/pack.en.json')
        self.assertEqual(report['content_mode'], 'fresh')
        self.assertEqual(report['evaluation']['records_evaluated'], 10)
        self.assertEqual(report['evaluation']['records_expected'], 10)
        self.assertEqual(report['mechanical']['nonempty_content'], {'present': 10, 'expected': 10})
        self.assertEqual(len(report['mechanical']['generator_evidence_quotes']), 9)
        self.assertTrue(all(x['exact_match'] for x in report['mechanical']['generator_evidence_quotes']))
        self.assertEqual(len(report['mechanical']['source_locators']), 10)
        self.assertTrue(all(x['resolved'] for x in report['mechanical']['source_locators']))
        self.assertEqual(len(report['evaluation']['chapters']['CH-01']['module_review']), 7)
        for name in ('certainty', 'misreadings', 'policies', 'names', 'glossary', 'references'):
            self.assertEqual(len(data[name]), 1)
            self.assertEqual(data[name][0]['status'], 'draft')
            self.assertTrue(data[name][0]['source_locations'][0].startswith('source.txt#normalized-text:'))
        errors = [i for i in report['mechanical']['project_issues'] if i['severity'] == 'error']
        self.assertEqual(errors, [])

    def test_omitted_module_and_unexplained_empty_are_incomplete_not_success(self):
        for mode in ('missing_module', 'unexplained_empty'):
            with self.subTest(mode=mode):
                self.run = self.root / mode
                self.prepare(mode)
                self.assertEqual(run(self.run)['state'], 'generation_failed')
                report = read_json(self.run / 'quality-report.json')
                self.assertEqual(report['completeness']['unprocessed_chapters'], ['CH-01'])
                self.assertEqual(report['evaluation']['records_evaluated'], 0)

    def test_plain_text_without_section_headings_can_generate_auxiliary_records(self):
        self.source = self.root / 'plain.txt'
        self.source.write_text('This text explains a new idea and a person.')
        prepare(self.run, self.source, self.recipe('fresh_modules'), title='Text', author='Author')
        self.assertEqual(run(self.run)['state'], 'delivered')
        report = read_json(self.run / 'quality-report.json')
        self.assertEqual(report['evaluation']['records_evaluated'], 8)
        self.assertTrue(all(x['exact_match'] for x in report['mechanical']['generator_evidence_quotes']))

    def test_unknown_failed_cli_cost_stops_later_dispatch(self):
        from unittest.mock import patch
        self.source.write_text('# First\n## A\nEvidence.\n# Second\n## B\nEvidence.\n')
        value = self.recipe('generation_error')
        value['max_cost_usd'] = value['cumulative_cost_limit_usd'] = 5
        prepare(self.run, self.source, value, chapter_level=1)
        with patch('reading_pack_producer.delivery.adapter_receipt', return_value={
                'actual_cost_usd': None, 'receipt_valid': False, 'usage_origin': 'claude-cli'}):
            self.assertEqual(run(self.run)['state'], 'generation_failed')
        self.assertEqual(self.log.read_text().splitlines(), ['generate/CH-01'])
        q = read_json(self.run / 'quality-report.json')
        self.assertEqual(q['jobs']['generate/CH-02']['status'], 'not_run')
        self.assertEqual(q['resources']['unknown_cost_calls'], 1)
        from reading_pack_producer.delivery import prepare_successor
        with self.assertRaisesRegex(ReadingPackError, 'unknown call'):
            prepare_successor(self.root / 'next', self.run, value)
        self.assertFalse((self.root / 'next').exists())
        self.assertEqual(self.log.read_text().splitlines(), ['generate/CH-01'])

    def test_empty_optional_uncertainty_is_omitted_without_inventing_content(self):
        from reading_pack_producer.delivery_fresh import module_records
        unit = {'id': 'CH-01', 'start': 0, 'end': 40, 'sections': []}
        item = {'kind': 'clarification', 'issue': 'Question', 'response': 'Answer',
                'remaining_uncertainty': '', 'section_id': 'CH-01', 'evidence_quote': 'Evidence'}
        records = module_records(unit, {'modules': {'misreadings': {'items': [item]}}})
        self.assertNotIn('remaining_uncertainty', records['misreadings'][0])
        self.assertEqual(records['misreadings'][0]['response'], 'Answer')
        self.assertEqual(item['remaining_uncertainty'], '')
