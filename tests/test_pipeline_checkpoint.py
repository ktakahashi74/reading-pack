from __future__ import annotations
import copy
import unittest
from unittest.mock import patch
from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data, write_json
from reading_pack_producer.pipeline import _unseal, _seal, resume_pipeline, Runner
from reading_pack_producer.pipeline_generation import chapter_review_scope
from reading_pack_producer.pipeline_reuse import restart_pipeline
from tests import test_pipeline as fixtures

class ReviewScopeTests(unittest.TestCase):
    def test_only_equal_structure_is_attested_and_content_still_needs_review(self):
        old={'id':'CH-01','title':'A gate','pages':[],'sections':['Before','After'], 'summary':'','terms':[]}
        candidate={'candidate_id':'C-1','collection':'chapters','record':{**old,'summary':'A supported overview.','terms':['gate'],'status':'draft'}}
        before=copy.deepcopy(candidate)
        scope=chapter_review_scope([candidate],{'chapters':[old]})['C-1']
        self.assertEqual(scope['fields_requiring_source_review'],['summary','terms'])
        self.assertIn('sections',scope['unchanged_fields'])
        self.assertNotIn('summary',scope['unchanged_fields'])
        self.assertEqual(candidate,before)
        candidate['record']['spoiler_scope']='whole_book'
        self.assertIn('spoiler_scope',chapter_review_scope([candidate],{'chapters':[old]})['C-1']['fields_requiring_source_review'])
        candidate['record']['sections']=['An invented heading']
        self.assertEqual(chapter_review_scope([candidate],{'chapters':[old]}),{})

class CheckpointTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    start=fixtures.PipelineTests.start
    execute=fixtures.PipelineTests.execute

    def stopped_round(self):
        self.recipe['max_attempts']=1
        self.source.write_text(fixtures.SOURCE+'\n## Closing the gate\nThe keeper closes the entrance before darkness.\n')
        self.start();worker=fixtures.Worker()
        def stop(command,request,**kwargs):
            if request['stage']=='repair':raise KeyboardInterrupt()
            return worker(command,request,**kwargs)
        with self.assertRaises(KeyboardInterrupt):self.execute(stop)
        self.assertTrue((self.run/'rounds/0/inventory.json').exists())
        data=load_language_data(self.run/'rounds/0/project','en')
        self.assertEqual(sum(bool(c['summary']) for c in data['chapters']),1)
        self.assertGreater(len(data['chapters']),1)
        return data

    def test_working_checkpoint_retains_generation_before_any_complete_round(self):
        self.recipe['max_attempts']=1;self.start();worker=fixtures.Worker()
        def stop(command,request,**kwargs):
            if request['stage']=='audit':raise KeyboardInterrupt()
            return worker(command,request,**kwargs)
        with self.assertRaises(KeyboardInterrupt):self.execute(stop)
        self.assertFalse((self.run/'rounds/0/report.json').exists())
        before=load_language_data(self.run/'working','en');destination=self.root/'working-checkpoint'
        restart_pipeline(self.run,destination,self.recipe,working_checkpoint=True)
        self.assertEqual(load_language_data(destination/'seed','en'),before)
        receipt=_unseal(destination/'manifest.json')['restart_origin']['checkpoint']
        self.assertEqual(receipt['kind'],'unverified-working-draft')
        self.assertIsNone(receipt['report_sha256'])
        self.assertFalse(receipt['author_approval'])
        requests=[]
        def boundary(command,request,**kwargs):
            requests.append(request);raise ReadingPackError('offline boundary')
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=boundary):resume_pipeline(destination)
        self.assertEqual([r['stage'] for r in requests],['audit'])

    def test_working_checkpoint_cannot_skip_validation_or_choose_two_inputs(self):
        self.stopped_round();destination=self.root/'invalid'
        with self.assertRaisesRegex(ReadingPackError,'either'):
            restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0,working_checkpoint=True)
        data=load_language_data(self.run/'working','en');data['chapters'][0]['summary']=False
        write_json(self.run/'working/data/pack.en.json',data)
        with self.assertRaisesRegex(ReadingPackError,'validation'):
            restart_pipeline(self.run,destination,self.recipe,working_checkpoint=True)
        self.assertFalse(destination.exists())

    def test_checkpoint_keeps_partial_draft_and_reuses_matching_audit_and_questions(self):
        before=self.stopped_round();destination=self.root/'checkpoint'
        restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0)
        self.assertEqual(load_language_data(destination/'seed','en'),before)
        requests=[]
        def boundary(command,request,**kwargs):
            requests.append(request);raise ReadingPackError('offline boundary')
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=boundary):result=resume_pipeline(destination)
        self.assertEqual(result['state'],'blocked_execution')
        self.assertEqual([r['stage'] for r in requests],['repair'])
        self.assertEqual(_unseal(destination/'benchmark.json'),_unseal(self.run/'benchmark.json'))
        self.assertEqual(load_language_data(destination/'working','en'),before)
        self.assertEqual(_unseal(destination/'rounds/0/report.json')['pack_sha256'],_unseal(self.run/'rounds/0/report.json')['pack_sha256'])
        self.assertTrue(any(j.get('reused_from') and j['request']['stage']=='audit' for p in (destination/'jobs').glob('*.json') if (j:=_unseal(p))))

    def test_checkpoint_rejects_modified_project_before_creating_destination(self):
        self.stopped_round();project=self.run/'rounds/0/project';data=load_language_data(project,'en')
        data['chapters'][0]['summary']='Unreviewed replacement.';write_json(project/'data/pack.en.json',data)
        destination=self.root/'changed'
        with self.assertRaisesRegex(ReadingPackError,'checkpoint project changed'):
            restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0)
        self.assertFalse(destination.exists())

    def test_checkpoint_preserves_missing_modules_for_bounded_repair(self):
        self.recipe['profile']='nonfiction-reading';self.stopped_round()
        destination=self.root/'missing-modules'
        restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0)
        requests=[]
        def boundary(command,request,**kwargs):
            requests.append(request);raise ReadingPackError('offline boundary')
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=boundary):resume_pipeline(destination)
        self.assertEqual([r['stage'] for r in requests],['repair'])
        self.assertTrue(any(f['reason'].startswith('missing profile module:') for f in _unseal(destination/'rounds/0/report.json')['failures']))

    def test_checkpoint_rejects_stale_preflight_before_creating_destination(self):
        self.stopped_round();plan=_unseal(self.run/'source-import-plan.json');plan['plan_id']='altered';_seal(self.run/'source-import-plan.json',plan)
        destination=self.root/'changed'
        with self.assertRaisesRegex(ReadingPackError,'preflight changed'):
            restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0)
        self.assertFalse(destination.exists())

    def test_checkpoint_cannot_rebind_a_modified_source(self):
        self.stopped_round();manifest=_unseal(self.run/'manifest.json')
        (self.run/manifest['sources'][0]['path']).write_text('Changed original source.')
        destination=self.root/'changed-source'
        with self.assertRaisesRegex(ReadingPackError,'source snapshot changed'):
            restart_pipeline(self.run,destination,self.recipe,checkpoint_round=0)
        self.assertFalse(destination.exists())

    def test_checkpoint_does_not_bypass_holdout_or_invalid_round_guards(self):
        self.stopped_round()
        with self.assertRaisesRegex(ReadingPackError,'nonnegative'):
            restart_pipeline(self.run,self.root/'negative',self.recipe,checkpoint_round=-1)
        _seal(self.run/'jobs/held-out-started.json',{'request':{'job':'0/holdout/H-01/answer'}})
        with self.assertRaisesRegex(ReadingPackError,'held-out'):
            restart_pipeline(self.run,self.root/'holdout',self.recipe,checkpoint_round=0)

    def test_scoped_review_does_not_force_acceptance_of_unsupported_new_content(self):
        self.recipe['max_rounds']=1;self.start();worker=fixtures.Worker()
        def reject(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='review':
                self.assertTrue(request['payload']['chapter_review_scope'])
                for d in response['result']['decisions']:d['decision']='reject'
            return response
        result=self.execute(reject)
        self.assertEqual(result['state'],'failed_quality')
        self.assertFalse(load_language_data(self.run/'working','en')['chapters'][0]['summary'])
        self.assertFalse(any(r['stage']=='answer' for r in worker.requests))


    def test_working_checkpoint_keeps_interrupted_round_and_does_not_reset_limit(self):
        before=self.stopped_round();destination=self.root/'same-round'
        self.assertEqual(_unseal(self.run/'state.json')['round'],1)
        restart_pipeline(self.run,destination,self.recipe,working_checkpoint=True)
        requests=[]
        def boundary(command,request,**kwargs):
            requests.append(request);raise ReadingPackError('offline boundary')
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=boundary):resume_pipeline(destination)
        self.assertEqual([r['job'] for r in requests],['1/audit/SRC-1-0'])
        self.assertEqual(load_language_data(destination/'working','en'),before)
        limited={**self.recipe,'max_rounds':1};second=self.root/'exhausted-rounds'
        restart_pipeline(self.run,second,limited,working_checkpoint=True)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=AssertionError('no new calls')):
            result=resume_pipeline(second)
        self.assertEqual(result['state'],'failed_quality')
        self.assertIn('round limit',result['reason'])
