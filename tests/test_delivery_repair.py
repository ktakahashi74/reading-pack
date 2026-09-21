"""One bounded, source-grounded repair pass with immutable baseline and fresh evaluation."""
import json
import unittest
from unittest.mock import patch
from tests import test_pipeline_delivery as helpers
from tests.support import read_json, copy_sample
from reading_pack.errors import ReadingPackError
from reading_pack_producer.delivery import prepare, prepare_successor, run
from reading_pack_producer.pipeline import _unseal
from reading_pack_producer.delivery_repair import findings


class RepairDeliveryTests(unittest.TestCase):
    setUp=helpers.DeliveryTests.setUp
    def recipe(self, mode='repair_one'):
        value=helpers.DeliveryTests.recipe(self,mode)
        value.update(repair_rounds=1,max_cost_usd=6,cumulative_cost_limit_usd=6,max_wall_seconds=600)
        return value
    def prepare(self,mode='repair_one',**kwargs):
        return prepare(self.run,self.source,self.recipe(mode),title='Test',author='Author',chapter_level=1,**kwargs)

    def test_full_indexes_can_append_missing_central_items_without_eviction(self):
        from reading_pack_producer.delivery_repair import apply_changes, targets
        from jsonschema import ValidationError
        import copy
        self.prepare('fresh_modules');run(self.run)
        plan=_unseal(self.run/'delivery-plan.json');state=_unseal(self.run/'delivery-state.json')
        job=state['jobs']['generate/CH-01']
        original=_unseal(self.run/'jobs'/(job['request_id']+'.json'))['response']['result']
        unit=plan['units'][0];text=(self.run/'source.txt').read_text()
        for name in ('names','glossary'):
            with self.subTest(module=name):
                raw=copy.deepcopy(original)
                item=copy.deepcopy(raw['modules'][name]['items'][0])
                raw['modules'][name]['items']=[copy.deepcopy(item) for _ in range(16)]
                target='/modules/'+name+'/items'
                issue={'id':'missing','allowed_targets':[target]}
                response={'changes':[{'target':target,'operation':'append','replacement':item,
                    'issue_ids':['missing'],'reason':'Missing central item','section_id':item['section_id'],
                    'source_quote':item['evidence_quote']}],
                    'dispositions':{'missing':{'action':'changed','reason':'Add source-grounded item'}}}
                result=apply_changes(unit,raw,response,[issue],targets(unit,raw),text)
                self.assertEqual(result['modules'][name]['items'][:16],raw['modules'][name]['items'])
                self.assertEqual(len(result['modules'][name]['items']),17)
                raw['modules'][name]['items']=[copy.deepcopy(item) for _ in range(24)]
                with self.assertRaises(ValidationError):
                    apply_changes(unit,raw,response,[issue],targets(unit,raw),text)

    def test_explicit_partial_repair_keeps_missing_chapter_and_denominators(self):
        self.source.write_text('# First\n## Evidence\nEvidence one.\n# Second\n## Other\nEvidence two.\n')
        v=self.recipe('partial_repair');v.update(max_cost_usd=10,cumulative_cost_limit_usd=10,repair_available_chapters=True)
        prepare(self.run,self.source,v,chapter_level=1)
        self.assertEqual(run(self.run)['state'],'delivered_partial')
        q=read_json(self.run/'quality-report.json')
        self.assertEqual(q['repair']['changed_chapters'],['CH-01'])
        self.assertEqual(q['repair']['deferred_chapters'],['CH-02'])
        self.assertIn('Deferred (generation or initial evaluation incomplete): CH-02',(self.run/'quality-report.en.md').read_text())
        self.assertFalse(q['repair']['complete'])
        self.assertEqual(q['generation'],{'completed_chapters':1,'total_chapters':2})
        self.assertEqual(q['evaluation']['total_chapters'],2)
        self.assertEqual(q['evaluation']['sections_expected'],2)
        data=read_json(self.run/'project/data/pack.en.json')
        self.assertEqual(len(data['chapters']),2)
        self.assertEqual(data['chapters'][1]['summary'],'')
        calls=self.log.read_text().splitlines()
        self.assertIn('repair/CH-01',calls);self.assertIn('reevaluate/CH-01',calls)
        self.assertNotIn('repair/CH-02',calls)
        self.assertFalse(q['publication']);self.assertEqual(q['user_adoption'],'not_decided')

    def test_partial_repair_does_not_repair_chapters_without_initial_evaluation(self):
        v=self.recipe('evaluation_error');v['repair_available_chapters']=True
        prepare(self.run,self.source,v,chapter_level=1)
        self.assertEqual(run(self.run)['state'],'delivered_partial')
        self.assertFalse(any(k.startswith('repair/') for k in self.log.read_text().splitlines()))

    def test_global_chapter_findings_allow_modules_but_record_findings_stay_narrow(self):
        self.prepare('fresh_modules');run(self.run)
        plan=_unseal(self.run/'delivery-plan.json');state=_unseal(self.run/'delivery-state.json')
        def result(key):
            j=state['jobs'][key]
            return _unseal(self.run/'jobs'/(j['request_id']+'.json'))['response']['result']
        g=result('evaluate/global')
        g['issues']=[{'target':'MAP terms and GLOSS','severity':'minor','reason':'CH-01 terms need a corresponding glossary item.'}, {'target':'CP-S01-01','severity':'minor','reason':'Section wording only.'}]
        issues,editable=findings(plan['units'][0],result('generate/CH-01'),result('evaluate/CH-01'),g,(self.run/'source.txt').read_text())
        indexed={i['id']:i for i in issues}
        self.assertIn('/modules/glossary/items/0',indexed['global:1']['allowed_targets'])
        self.assertEqual(indexed['global:2']['allowed_targets'],['/sections/S01-01'])
        self.assertTrue(all(p in editable for p in indexed['global:1']['allowed_targets']))

    def test_partial_reassessment_freezes_missing_chapter_and_initial_evidence(self):
        self.source.write_text('# First\n## Evidence\nEvidence one.\n# Second\n## Other\nEvidence two.\n')
        v=self.recipe('partial_repair_reeval_error');v.update(max_cost_usd=10,cumulative_cost_limit_usd=10,repair_available_chapters=True)
        prepare(self.run,self.source,v,chapter_level=1);run(self.run)
        first=(self.run/'first-pass-reading-pack.en.md').read_bytes()
        data=read_json(self.run/'project/data/pack.en.json')
        self.assertIn('Current generated coverage: 1/2 chapters',data['book']['contents_note'])
        self.assertIn('Ungenerated: CH-02',data['book']['contents_note'])
        self.log.write_text('')
        successor=self.root/'partial-next';v=self.recipe('normal');v.update(cumulative_cost_limit_usd=20,repair_available_chapters=True)
        prepare_successor(successor,self.run,v)
        self.assertEqual(run(successor)['state'],'delivered_partial')
        self.assertEqual(self.log.read_text().splitlines(),['reevaluate/CH-01','reevaluate/global'])
        self.assertEqual((successor/'first-pass-reading-pack.en.md').read_bytes(),first)
        plan=_unseal(successor/'delivery-plan.json')
        self.assertEqual(plan['deferred_generation'],['CH-02'])
        self.assertIn('evaluate/global',plan['carried'])
        self.assertNotIn('generate/CH-02',plan['redo'])

    def test_explicit_policy_reserves_both_passes_before_dispatch(self):
        v=self.recipe();v['max_cost_usd']=3
        with self.assertRaisesRegex(ReadingPackError,'6 calls'):
            prepare(self.run,self.source,v,chapter_level=1)
        self.assertFalse(self.run.exists());self.assertFalse(self.log.exists())
        result=self.prepare();self.assertEqual(result['maximum_calls'],6)
        self.assertEqual(result['repair_rounds'],1)

    def test_repairs_only_target_and_reevaluates_changed_artifact(self):
        self.prepare();self.assertEqual(run(self.run)['state'],'delivered')
        q=read_json(self.run/'quality-report.json');state=_unseal(self.run/'delivery-state.json')
        self.assertEqual(self.log.read_text().splitlines(),['generate/CH-01','evaluate/CH-01','evaluate/global','repair/CH-01','reevaluate/CH-01','reevaluate/global'])
        self.assertTrue(q['repair']['complete']);self.assertEqual(q['repair']['changed_chapters'],['CH-01'])
        self.assertEqual(q['resources']['repairs'],1);self.assertEqual(q['user_adoption'],'not_decided')
        before=read_json(self.run/'first-pass-pack.en.json');after=read_json(self.run/'project/data/pack.en.json')
        self.assertEqual(after['claims'][0]['statement'],'Evidence one.')
        for old,new in zip(before['claims'][1:],after['claims'][1:]):self.assertEqual(old,new)
        self.assertEqual(before['chapters'],after['chapters'])
        self.assertEqual(before['claims'][0]['status'],after['claims'][0]['status'])
        first=read_json(self.run/'first-pass-quality-report.json')
        self.assertEqual(first['evaluation']['record_counts']['partially_supported'],1)
        self.assertEqual(q['evaluation']['record_counts'],{'supported':3})
        request=_unseal(self.run/'jobs'/(state['jobs']['reevaluate/global']['request_id']+'.json'))['request']
        self.assertEqual(request['payload']['pack'],(self.run/'reading-pack.en.md').read_text())
        output=self.run.parent/(self.run.name+'-delivery')
        self.assertTrue((output/'first-pass-reading-pack.en.md').exists())
        self.assertTrue((output/'repair-report.json').exists())
        calls=self.log.read_text();run(self.run);self.assertEqual(self.log.read_text(),calls)

    def test_no_findings_skips_repair_and_reuses_only_unchanged_evaluations(self):
        self.prepare('normal');self.assertEqual(run(self.run)['state'],'delivered')
        q=read_json(self.run/'quality-report.json')
        self.assertEqual(q['repair']['changed_chapters'],[])
        self.assertEqual(q['resources']['repairs'],0)
        self.assertEqual(len(self.log.read_text().splitlines()),3)
        self.assertEqual((self.run/'reading-pack.en.md').read_bytes(),(self.run/'first-pass-reading-pack.en.md').read_bytes())

    def test_bad_quote_or_out_of_scope_patch_is_rejected_without_content_change(self):
        for mode in ('repair_bad_quote','repair_outside_scope'):
            with self.subTest(mode=mode):
                self.run=self.root/mode;self.prepare(mode)
                self.assertEqual(run(self.run)['state'],'delivered_partial')
                q=read_json(self.run/'quality-report.json')
                self.assertFalse(q['repair']['complete'])
                self.assertEqual(q['repair']['chapters']['CH-01']['status'],'rejected')
                self.assertEqual((self.run/'reading-pack.en.md').read_bytes(),(self.run/'first-pass-reading-pack.en.md').read_bytes())
                self.assertEqual(q['evaluation']['record_counts']['partially_supported'],1)
                state=_unseal(self.run/'delivery-state.json')
                self.assertEqual(state['jobs']['repair/CH-01']['status'],'repair_rejected')

    def test_missing_reassessment_is_not_replaced_with_first_pass_scores(self):
        self.prepare('reevaluation_error');self.assertEqual(run(self.run)['state'],'delivered_partial')
        q=read_json(self.run/'quality-report.json')
        self.assertEqual(q['evaluation']['completed_chapters'],0)
        self.assertIsNone(q['evaluation']['global'])
        self.assertEqual(q['evaluation']['records_evaluated'],0)
        self.assertIsNotNone(q['repair']['initial_global'])

    def test_declining_feedback_does_not_trigger_loop_or_score_gate(self):
        self.prepare('low_scores');self.assertEqual(run(self.run)['state'],'delivered')
        q=read_json(self.run/'quality-report.json')
        self.assertEqual(q['evaluation']['chapters']['CH-01']['dimensions']['coverage']['score'],0)
        self.assertEqual(q['repair']['changed_chapters'],[])
        self.assertEqual(len(self.log.read_text().splitlines()),4)
        self.assertFalse(q['quality_gate']);self.assertFalse(q['publication'])

    def test_unfinished_initial_pass_does_not_start_repair(self):
        self.prepare('evaluation_error');self.assertEqual(run(self.run)['state'],'delivered_partial')
        q=read_json(self.run/'quality-report.json')
        self.assertFalse(q['repair']['complete'])
        self.assertFalse(any(k.startswith('repair/') for k in self.log.read_text().splitlines()))

    def test_reassessment_successor_carries_repairs_but_preserves_initial_snapshot(self):
        self.prepare('reevaluation_error');run(self.run)
        initial=(self.run/'first-pass-reading-pack.en.md').read_bytes()
        next_run=self.root/'next';v=self.recipe('normal');v['cumulative_cost_limit_usd']=12
        prepare_successor(next_run,self.run,v)
        self.assertEqual(run(next_run)['state'],'delivered')
        self.assertEqual((next_run/'first-pass-reading-pack.en.md').read_bytes(),initial)
        q=read_json(next_run/'quality-report.json')
        self.assertEqual(q['resources']['model_calls_started'],2)
        self.assertEqual(q['resources']['repairs'],0)
        self.assertEqual(q['evaluation']['records_evaluated'],3)
        with self.assertRaisesRegex(ReadingPackError,'nothing to redo'):
            prepare_successor(self.root/'third',next_run,v)

    def test_rejected_repair_can_be_redone_only_by_explicit_successor(self):
        self.prepare('repair_bad_quote');run(self.run)
        next_run=self.root/'next';v=self.recipe('repair_one');v['cumulative_cost_limit_usd']=15
        prepare_successor(next_run,self.run,v)
        self.assertEqual(run(next_run)['state'],'delivered')
        q=read_json(next_run/'quality-report.json')
        self.assertEqual(q['resources']['model_calls_started'],3)
        self.assertEqual(q['resources']['cost_accounting']['chain_calls_started'],7)

    def test_cannot_change_round_policy_or_repair_inherited_seed(self):
        seed=copy_sample(self.root/'seed')
        with self.assertRaisesRegex(ReadingPackError,'fresh generation'):
            self.prepare(seed=seed,inherit_seed=True)
        self.prepare('evaluation_error');run(self.run)
        v=self.recipe();v['repair_rounds']=0
        with self.assertRaisesRegex(ReadingPackError,'repair round policy'):
            prepare_successor(self.root/'next',self.run,v)
