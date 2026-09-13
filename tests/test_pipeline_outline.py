from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from reading_pack.project import load_language_data, write_json
from reading_pack.validation import errors, validate_project
from reading_pack_producer.pipeline import Runner, _unseal, start_pipeline
from tests import test_pipeline as fixtures

SOURCE = '''# Orchard Notes

## First chapter
Entry depends on daylight and a green safety indication.
### The condition
The keeper checks both conditions.
#### A necessary qualifier
Daylight alone does not authorize entry.
## Afterword
This manual documents the entry procedure.
## Appendix
### Supporting information
The inspection lamp does not authorize entry.
'''


class TextOutlineTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp

    def prepare(self):
        self.source.write_text(SOURCE)
        self.recipe['profile'] = 'general-navigation'
        start_pipeline(self.run, self.source, self.recipe)
        initial = Runner(self.run)
        seed = self.root / 'seed'
        initial.bootstrap(seed)
        data = load_language_data(seed, 'en')
        data['chapters'] = data['chapters'][:2]
        data['chapters'][0]['sections'] = ['The condition']
        data['chapters'][1]['title'] = 'Afterword: The keeper reflects'
        for c in data['chapters']:
            c['summary'], c['terms'] = 'Supplied explanation retained without changes.', ['entry']
        write_json(seed / 'data/pack.en.json', data)
        new = self.root / 'restart'
        start_pipeline(new, self.source, self.recipe, project=seed)
        runner = Runner(new)
        project = new / 'working'
        runner.bootstrap(project)
        return runner, project, data

    def test_seed_gains_nested_headings_and_back_matter_without_losing_ids_or_prose(self):
        runner, project, before = self.prepare()
        runner.reconcile_source_structure(project)
        after = load_language_data(project, 'en')
        self.assertEqual(len(after['chapters']), 3)
        self.assertEqual(after['chapters'][0]['sections'], ['The condition', 'A necessary qualifier'])
        for old, new in zip(before['chapters'], after['chapters']):
            self.assertEqual([old[k] for k in ['id', 'summary', 'terms', 'title']],
                             [new[k] for k in ['id', 'summary', 'terms', 'title']])
        self.assertEqual(errors(validate_project(project)[2]), [])
        outline = _unseal(runner.root / 'source-text-outline.json')
        self.assertEqual(len(outline['headings']), 6)
        self.assertEqual(len([h for h in outline['headings'] if h.get('canonical_id')]), 3)
        self.assertTrue(outline['aliases'])
        # No agent decides away the newly discovered back matter in audit input.
        self.assertEqual(len(runner.layout_evidence(runner.chunks[0])['heading_units']), 6)

    def test_provided_outline_stays_intact_and_source_inventory_exposes_missing_headings(self):
        runner, project, before = self.prepare()
        with patch('reading_pack_producer.pipeline_outline.author_contract', return_value={
                'chapters': {'mode': 'provided', 'protected_ids': []}}):
            runner.reconcile_source_structure(project)
        self.assertEqual(load_language_data(project, 'en')['chapters'], before['chapters'])
        self.assertEqual(len(runner.layout_evidence(runner.chunks[0])['heading_units']), 6)

    def test_inventory_retains_sealed_title_aliases_in_old_and_new_checkpoints(self):
        from reading_pack.errors import ReadingPackError
        from reading_pack_producer.pipeline import _seal
        from reading_pack_producer.pipeline_audit import structural_counts
        from reading_pack_producer.pipeline_reuse import checked_checkpoint
        runner, project, _ = self.prepare()
        runner.reconcile_source_structure(project)
        data = load_language_data(project, 'en')
        self.assertEqual(structural_counts(runner, data)['matched_structure_records'], 6)
        runner.save('blocked_execution', 'synthetic interruption', round=0)
        _, receipt = checked_checkpoint(runner.root, 'working')
        self.assertIn('seed-reconciled-import-plan.json', receipt['source_artifacts'])
        # Legacy checkpoints kept only the source plan and explicit aliases.
        (runner.root / 'seed-reconciled-import-plan.json').unlink()
        self.assertEqual(structural_counts(runner, data)['matched_structure_records'], 6)
        data['chapters'][1]['title'] = 'An invented replacement'
        self.assertLess(structural_counts(runner, data)['matched_structure_records'], 6)
        outline = _unseal(runner.root / 'source-text-outline.json')
        outline['aliases'][0]['source_title'] = 'A title absent from the source'
        _seal(runner.root / 'source-text-outline.json', outline)
        with self.assertRaisesRegex(ReadingPackError, 'structure alias'):
            structural_counts(runner, data)

    def test_missing_summary_repair_is_bound_to_its_source_region(self):
        runner, project, _ = self.prepare()
        runner.reconcile_source_structure(project)
        worker = fixtures.Worker()
        def call(stage, payload, key):
            self.assertEqual(stage, 'audit')
            return {'expected_structure_records': 6, 'matched_structure_records': 6,
                    'source_attribution_errors': [], 'invented_record_ids': [], 'findings': []}
        with patch.object(runner, 'call', side_effect=call):
            _, failures, _ = runner.inspect(project, 0)
        missing = next(f for f in failures if f['reason'] == 'chapter summary is empty')
        self.assertTrue(missing['evidence'])
        runner.evidence(missing['evidence'])
        self.assertTrue(any('Appendix' in e['quote'] for e in missing['evidence']))
