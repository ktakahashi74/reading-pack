"""M1 wire-contract checks; no production runner or model calls."""
import copy
import itertools
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'docs/contracts/artifact-acceptance-1'


class ArtifactAcceptanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((CONTRACT / 'report.schema.json').read_text())
        cls.examples = json.loads((CONTRACT / 'examples.json').read_text())
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())

    def valid(self, record):
        return self.validator.is_valid(record)

    def test_schema_and_all_saved_examples(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual(len(self.examples), 17)
        self.assertEqual(len({r['record_id'] for r in self.examples.values()}), 17)
        for name, record in self.examples.items():
            with self.subTest(example=name):
                self.validator.validate(record)

    def test_acceptance_precedence_matrix(self):
        # Independent truth table: defect evidence dominates uncertainty.
        expected = {
            ('not_run', False, False): 'not_run',
            ('not_run', False, True): None,
            ('not_run', True, False): None,
            ('not_run', True, True): None,
            ('incomplete', False, False): 'inconclusive',
            ('incomplete', False, True): 'inconclusive',
            ('incomplete', True, False): 'fail',
            ('incomplete', True, True): 'fail',
            ('complete', False, False): 'pass',
            ('complete', False, True): 'inconclusive',
            ('complete', True, False): 'fail',
            ('complete', True, True): 'fail',
        }
        for (coverage, defect, unresolved), result in expected.items():
            for status in ('not_run', 'inconclusive', 'fail', 'pass'):
                record = copy.deepcopy(self.examples['pass'])
                record['acceptance'].update(
                    status=status, coverage=coverage,
                    confirmed_defect_ids=['D-1'] if defect else [],
                    unresolved_ids=['U-1'] if unresolved else [],
                    report=None if coverage == 'not_run' else record['acceptance']['report'])
                with self.subTest(coverage=coverage, defect=defect, unresolved=unresolved, status=status):
                    self.assertEqual(self.valid(record), status == result)

    def test_model_outcome_never_selects_acceptance(self):
        self.assertEqual(self.examples['reader-wrong-pack-pass']['acceptance']['status'], 'pass')
        self.assertEqual(self.examples['reader-correct-pack-fail']['acceptance']['status'], 'fail')
        for name, diagnostic in itertools.product(
                ('pass', 'defect-complete', 'unresolved-complete'),
                ('not_run', 'incomplete', 'completed')):
            record = copy.deepcopy(self.examples[name])
            record['model_diagnostics'] = {
                'status': diagnostic,
                'report': None if diagnostic == 'not_run' else {'path': 'diagnostics.json', 'sha256': 'b' * 64}}
            self.assertTrue(self.valid(record), (name, diagnostic))

    def test_execution_stop_does_not_erase_quality(self):
        for name in ('pass', 'defect-complete', 'unresolved-complete', 'not-run'):
            record = copy.deepcopy(self.examples[name])
            record['execution'] = {'status': 'stopped', 'stop_reasons': ['budget_exhausted']}
            self.assertTrue(self.valid(record), name)
            record['execution']['stop_reasons'] = []
            self.assertFalse(self.valid(record))
            record['execution'] = {'status': 'completed', 'stop_reasons': ['budget_exhausted']}
            self.assertFalse(self.valid(record))

    def test_author_review_gates_and_evidence(self):
        record = copy.deepcopy(self.examples['awaiting-author'])
        record['acceptance'] = copy.deepcopy(self.examples['defect-complete']['acceptance'])
        self.assertFalse(self.valid(record))
        record = copy.deepcopy(self.examples['protected-content-question'])
        record['author_approval']['question_ids'] = []
        self.assertFalse(self.valid(record))
        for name in ('approved', 'author-changes-requested', 'approved-later-defect'):
            record = copy.deepcopy(self.examples[name])
            self.assertTrue(self.valid(record))
            record['author_approval']['decision'] = None
            self.assertFalse(self.valid(record))

    def test_pass_requires_bound_candidate_delivery_and_report(self):
        for field in ('candidate_manifest', 'delivery_manifest'):
            record = copy.deepcopy(self.examples['pass'])
            record['bindings'][field] = None
            self.assertFalse(self.valid(record))
        record = copy.deepcopy(self.examples['pass'])
        record['acceptance']['report'] = None
        self.assertFalse(self.valid(record))

    def test_contract_shape_rejects_ambiguous_records(self):
        changes = [('contract_version', 'reader-utility-1'), ('created_at', 'yesterday')]
        for field, value in changes:
            record = copy.deepcopy(self.examples['pass'])
            record[field] = value
            self.assertFalse(self.valid(record))
        record = copy.deepcopy(self.examples['pass'])
        record['status'] = 'approved'
        self.assertFalse(self.valid(record))
        for path in ('/tmp/report.json', '../report.json', 'old/../../report.json', 'https://example.org/r', 'C:\\r.json'):
            record = copy.deepcopy(self.examples['pass'])
            record['acceptance']['report']['path'] = path
            self.assertFalse(self.valid(record), path)
        record = copy.deepcopy(self.examples['defect-complete'])
        record['acceptance']['confirmed_defect_ids'] = ['D-1', 'D-1']
        self.assertFalse(self.valid(record))

    def test_legacy_reassessment_preserves_budget_identity(self):
        old = self.examples['not-run']
        new = self.examples['legacy-reassessment-not-run']
        self.assertIsNotNone(new['reassessment_of'])
        self.assertEqual(new['acceptance']['status'], 'not_run')
        self.assertEqual(new['resources']['budget_scope_id'], old['resources']['budget_scope_id'])
        self.assertEqual(new['author_approval']['status'], 'not_requested')


if __name__ == '__main__':
    unittest.main()
