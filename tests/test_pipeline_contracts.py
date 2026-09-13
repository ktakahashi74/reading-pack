"""Contract selection, historical compatibility, and preparation-only isolation."""
import copy
import json
import unittest
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import (
    Runner, _inventory, _seal, _unseal, finalize_pipeline,
    pipeline_status, resume_pipeline, start_pipeline,
)
from reading_pack_producer.pipeline_contracts import (
    ARTIFACT_CONTRACT_VERSION as NEW, LEGACY_CONTRACT_VERSION as OLD,
)
from reading_pack_producer.pipeline_qualification import workflow_signature
from reading_pack_producer.pipeline_resources import operating_plan
from reading_pack_producer.pipeline_reuse import import_preparation, restart_pipeline
from reading_pack_producer.work_ledger import artifact_hash
from tests import test_pipeline as fixtures
from tests.support import cli
from tests.test_pipeline_resources import envelope


class PipelineContractTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def test_new_runs_pin_the_selected_contract_without_rewriting_recipe(self):
        for choice in (None, OLD, NEW):
            self.run = self.root / str(choice)
            recipe = copy.deepcopy(self.recipe)
            if choice is not None:
                recipe['contract_version'] = choice
            result = start_pipeline(self.run, self.source, recipe)
            manifest = _unseal(self.run / 'manifest.json')
            self.assertEqual(manifest['contract_version'], choice or OLD)
            self.assertEqual(manifest['recipe'], recipe)
            self.assertEqual(result['contract_version'], choice or OLD)
            self.assertEqual(result['contract_execution_supported'], True)
            self.assertEqual(result['calls_reserved'], 0)

    def test_historical_manifest_resume_does_not_add_or_change_contracts(self):
        self.start()
        manifest = _unseal(self.run / 'manifest.json')
        manifest.pop('contract_version')
        _seal(self.run / 'manifest.json', manifest)
        before = (self.run / 'manifest.json').read_bytes()
        self.assertEqual(pipeline_status(self.run)['contract_version'], OLD)
        result = self.execute(fixtures.Worker())
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual((self.run / 'manifest.json').read_bytes(), before)
        self.assertNotIn('contract_version', _unseal(self.run / 'manifest.json'))

    def test_unknown_and_mismatched_contracts_fail_before_dispatch(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['contract_version'] = 'unknown'
        with self.assertRaises(ReadingPackError):
            start_pipeline(self.run, self.source, recipe)
        self.assertFalse(self.run.exists())
        self.start()
        original = _unseal(self.run / 'manifest.json')
        for selected in (NEW, 'unknown'):
            manifest = copy.deepcopy(original)
            manifest['contract_version'] = selected
            _seal(self.run / 'manifest.json', manifest)
            before = _inventory(self.run)
            with patch('reading_pack_producer.pipeline.run_local_adapter') as adapter:
                for action in (pipeline_status, resume_pipeline):
                    with self.assertRaises(ReadingPackError):
                        action(self.run)
                adapter.assert_not_called()
            self.assertEqual(_inventory(self.run), before)
        manifest = copy.deepcopy(original)
        manifest.pop('contract_version')
        manifest['recipe']['contract_version'] = NEW
        _seal(self.run / 'manifest.json', manifest)
        with self.assertRaisesRegex(ReadingPackError, 'differ'):
            pipeline_status(self.run)

    def test_new_contract_rejects_inadequate_resource_plan_without_calls(self):
        self.recipe.update(contract_version=NEW, operating_envelope=envelope())
        self.start()
        with patch('reading_pack_producer.pipeline.run_local_adapter') as adapter:
            result = resume_pipeline(self.run)
            adapter.assert_not_called()
        self.assertEqual(result['state'], 'artifact_stopped')
        self.assertEqual(result['acceptance']['status'], 'not_run')
        self.assertFalse((self.run / 'resource-ledger.json').exists())
        with self.assertRaisesRegex(ReadingPackError, 'legacy evaluation'):
            Runner(self.run).call('benchmark', {}, 'benchmark')

    def test_restart_rejects_contract_migration_before_creating_destination(self):
        self.start()
        before = _inventory(self.run)
        recipe = copy.deepcopy(self.recipe)
        recipe['contract_version'] = NEW
        destination = self.root / 'forbidden'
        with self.assertRaisesRegex(ReadingPackError, 'cannot change contract_version'):
            restart_pipeline(self.run, destination, recipe)
        self.assertFalse(destination.exists())
        self.assertEqual(_inventory(self.run), before)

    def test_restart_inherits_new_contract_and_keeps_historical_absence(self):
        for version in (None, NEW):
            recipe = copy.deepcopy(self.recipe)
            if version:
                recipe['contract_version'] = version
            old = self.root / ('old-' + str(version))
            start_pipeline(old, self.source, recipe)
            manifest = _unseal(old / 'manifest.json')
            if version is None:
                manifest.pop('contract_version')
                _seal(old / 'manifest.json', manifest)
            before = _inventory(old)
            destination = self.root / ('restart-' + str(version))
            restart_pipeline(old, destination, self.recipe)
            new = _unseal(destination / 'manifest.json')
            self.assertEqual('contract_version' in new, version is not None)
            self.assertEqual(pipeline_status(destination)['contract_version'], version or OLD)
            for field in ('reader_utility_contract', 'benchmark_selection_contract', 'audit_policy', 'created_at', 'inputs'):
                self.assertEqual(new[field], manifest[field])
            self.assertEqual(_inventory(old), before)

    def test_cross_contract_response_reuse_is_rejected(self):
        self.start()
        recipe = copy.deepcopy(self.recipe)
        recipe['contract_version'] = NEW
        other = self.root / 'other'
        start_pipeline(other, self.source, recipe)
        with self.assertRaisesRegex(ReadingPackError, 'cannot change contract_version'):
            import_preparation(self.run, other, include_work=True)
        self.assertFalse((other / 'reused-preparation.json').exists())

    def test_workflow_identity_keeps_historical_signature_and_separates_new_contract(self):
        self.start()
        manifest = _unseal(self.run / 'manifest.json')
        manifest.pop('contract_version')
        expected = artifact_hash({'engine': manifest['engine_sha256'], 'recipe': manifest['recipe'],
            'adapters': manifest['adapter_files'], 'reader_utility_contract': manifest.get('reader_utility_contract'),
            'benchmark_selection_contract': manifest.get('benchmark_selection_contract')})
        self.assertEqual(workflow_signature(manifest), expected)
        manifest['contract_version'] = NEW
        manifest['recipe']['contract_version'] = NEW
        self.assertNotEqual(workflow_signature(manifest), expected)

    def test_public_cli_prepares_and_reports_invalid_new_execution_plan(self):
        self.recipe['contract_version'] = NEW
        recipe = self.root / 'recipe.json'
        recipe.write_text(json.dumps(self.recipe))
        result = cli('pipeline', 'start', str(self.source), '--run', str(self.run),
                     '--recipe', str(recipe), '--experimental', '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['contract_version'], NEW)
        before = _inventory(self.run)
        for command in ('status', 'plan', 'resume'):
            args = ['pipeline', command, '--run', str(self.run)]
            if command == 'resume': args.append('--experimental')
            result = cli(*args)
            self.assertEqual(result.returncode == 0, command == 'status', result.stderr)
            if command == 'resume':
                self.assertEqual(json.loads(result.stdout)['acceptance']['status'], 'not_run')
        self.assertEqual(_unseal(self.run / 'manifest.json')['recipe'], self.recipe)


if __name__ == '__main__':
    unittest.main()
