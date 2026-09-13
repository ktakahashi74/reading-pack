"""New standard production does not turn reader trials into acceptance gates."""
import copy
import unittest
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import Runner, _inventory, _seal, _unseal
from reading_pack_producer.pipeline_audit import run_contract
from reading_pack_producer.pipeline_purpose import READER_UTILITY_CONTRACT
from reading_pack_producer.pipeline_reuse import restart_pipeline
from tests import test_pipeline as fixtures


class DiagnosticIntegrationTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start

    def test_standard_new_contract_skips_both_reader_splits_and_question_generation(self):
        self.recipe['contract_version'] = 'artifact-acceptance-1'
        self.start()
        runner = Runner(self.run)
        before = _inventory(self.run)
        with patch.object(runner, 'call', side_effect=AssertionError('unexpected worker call')):
            self.assertEqual(runner.benchmark(), {'development': [], 'holdout': []})
            for split in ('development', 'holdout'):
                result = runner.evaluate('An accurate Pack.', [{'id': 'D-1', 'question': 'Probe'}], 0, split)
                self.assertEqual(result['kind'], 'reader_diagnostics')
                self.assertEqual(result['status'], 'not_run')
                self.assertNotIn('passed', result)
                self.assertNotIn('failures', result)
        self.assertEqual(_inventory(self.run), before)
        self.assertFalse((self.run / 'benchmark.json').exists())
        self.assertFalse((self.run / 'candidate').exists())

    def test_new_audit_contract_is_independent_of_reader_questions_and_results(self):
        self.recipe.update(contract_version='artifact-acceptance-1', profile='general-navigation')
        self.start()
        runner = Runner(self.run)
        canonical = {'chapters': [{'id': 'CH-1', 'title': 'Opening'}]}
        first = run_contract(runner, canonical)
        before = (self.run / 'audit-contract.json').read_bytes()
        _seal(self.run / 'benchmark.json', {'development': [{'id': 'D-1', 'requirements': ['New incidental detail']}], 'holdout': []})
        _seal(self.run / 'final-evaluation.json', {'passed': False, 'failures': [{'category': 'missing_coverage'}]})
        second = run_contract(runner, canonical)
        self.assertEqual(second, first)
        self.assertEqual((self.run / 'audit-contract.json').read_bytes(), before)
        self.assertFalse(first['reader_questions_define_coverage'])
        self.assertNotIn('development_suite_sha256', first)
        self.assertTrue(all(r['id'].startswith(('module:', 'chapter:')) for r in first['coverage_requirements']))

    def test_frozen_purpose_separates_new_acceptance_but_preserves_old_scope(self):
        self.start()
        legacy = _unseal(self.run / 'manifest.json')
        self.assertEqual(legacy['reader_utility_contract'], READER_UTILITY_CONTRACT)
        self.recipe['contract_version'] = 'artifact-acceptance-1'
        self.run = self.root / 'artifact'
        self.start()
        new = _unseal(self.run / 'manifest.json')
        self.assertEqual(new['reader_utility_contract']['version'], 'reader-utility-artifact-1')
        for key in ('essential_outcomes', 'detail_rule', 'routing_rule', 'fidelity_rule'):
            self.assertEqual(new['reader_utility_contract'][key], legacy['reader_utility_contract'][key])
        for old, destination in ((self.root / 'run', self.root / 'legacy-restart'), (self.run, self.root / 'artifact-restart')):
            original = (old / 'manifest.json').read_bytes()
            recipe = copy.deepcopy(_unseal(old / 'manifest.json')['recipe'])
            restart_pipeline(old, destination, recipe)
            self.assertEqual(_unseal(destination / 'manifest.json')['reader_utility_contract'],
                             _unseal(old / 'manifest.json')['reader_utility_contract'])
            self.assertEqual((old / 'manifest.json').read_bytes(), original)

    def test_new_contract_cannot_enter_legacy_handoff_with_reader_success(self):
        self.recipe['contract_version'] = 'artifact-acceptance-1'
        self.start()
        runner = Runner(self.run)
        before = _inventory(self.run)
        with self.assertRaisesRegex(ReadingPackError, 'own evidence-bound author packet'):
            runner.handoff(self.root / 'nonexistent', {}, {'final': {'passed': True}})
        self.assertEqual(_inventory(self.run), before)


if __name__ == '__main__':
    unittest.main()
