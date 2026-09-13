from __future__ import annotations

import copy
import unittest

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data
from reading_pack_producer.pipeline import Runner, _seal, _unseal
from reading_pack_producer.pipeline_audit import run_contract
from reading_pack_producer.pipeline_purpose import READER_UTILITY_CONTRACT
from reading_pack_producer.pipeline_qualification import workflow_signature
from reading_pack_producer.pipeline_reuse import restart_pipeline
from tests import test_pipeline as fixtures


class ReaderPurposeTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def test_probe_details_do_not_become_pack_quotas_but_probe_changes_remain_detectable(self):
        self.start()
        worker = fixtures.Worker()
        self.execute(worker)
        contract = _unseal(self.run / 'audit-contract.json')
        self.assertFalse(contract['reader_questions_define_coverage'])
        self.assertTrue(contract['coverage_requirements'])
        self.assertTrue(all(r['id'].startswith(('chapter:', 'module:')) for r in contract['coverage_requirements']))
        suite = _unseal(self.run / 'benchmark.json')
        self.assertNotIn(suite['holdout'][0]['question'], str(contract))
        suite['development'][0]['requirements'].append('Recall the incidental percentage 17.3%.')
        _seal(self.run / 'benchmark.json', suite)
        runner = Runner(self.run)
        runner.recipe['profile'] = runner.state['effective_profile']
        with self.assertRaisesRegex(ReadingPackError, 'coverage contract changed'):
            run_contract(runner, load_language_data(self.run / 'candidate', 'en'))

    def test_scope_is_fixed_before_answers_and_shared_with_all_judges_not_readers(self):
        self.start()
        frozen = _unseal(self.run / 'manifest.json')['reader_utility_contract']
        self.assertEqual(frozen, READER_UTILITY_CONTRACT)
        worker = fixtures.Worker()
        self.execute(worker)
        for stage in ('benchmark', 'benchmark_review', 'generate', 'audit', 'grade'):
            requests = [r for r in worker.requests if r['stage'] == stage]
            self.assertTrue(requests, stage)
            self.assertTrue(all(r['payload']['reader_utility_contract'] == frozen for r in requests))
        for request in worker.requests:
            if request['stage'] == 'answer':
                self.assertEqual(set(request['payload']), {'pack', 'question'})

    def test_material_condition_failure_still_blocks_author_handoff(self):
        # The worker is synthetic; this tests the gate, not actual judge quality.
        self.recipe['max_rounds'] = 1
        self.start()
        result = self.execute(fixtures.Worker(always_fail=True))
        self.assertEqual(result['state'], 'failed_quality')
        report = _unseal(self.run / 'rounds/0/report.json')
        self.assertTrue(any(f['category'] == 'missing_qualifier' for f in report['failures']))
        self.assertFalse((self.run / 'candidate').exists())

    def test_legacy_restart_does_not_reinterpret_frozen_questions(self):
        self.start()
        manifest = _unseal(self.run / 'manifest.json')
        manifest.pop('reader_utility_contract')
        manifest['benchmark_selection_contract'] = {'legacy': 'Every requirement is mandatory coverage.'}
        _seal(self.run / 'manifest.json', manifest)
        destination = self.root / 'restart'
        restart_pipeline(self.run, destination, self.recipe)
        restarted = _unseal(destination / 'manifest.json')
        self.assertNotIn('reader_utility_contract', restarted)
        self.assertEqual(restarted['benchmark_selection_contract'], manifest['benchmark_selection_contract'])

    def test_changing_purpose_invalidates_workflow_qualification_identity(self):
        self.start()
        original = _unseal(self.run / 'manifest.json')
        changed = copy.deepcopy(original)
        changed['reader_utility_contract']['detail_rule'] = 'Require every source number.'
        self.assertNotEqual(workflow_signature(original), workflow_signature(changed))


if __name__ == '__main__':
    unittest.main()
