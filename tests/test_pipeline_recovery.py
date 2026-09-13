from __future__ import annotations
import copy
import unittest
from unittest.mock import patch
from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data
from reading_pack_producer.pipeline import _unseal, resume_pipeline
from reading_pack_producer.pipeline_generation import dependency_closed_candidates
from reading_pack_producer.pipeline_reuse import restart_pipeline
from tests import test_pipeline as fixtures

class RecoveryTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def test_dependency_closure_is_transitive_and_keeps_existing_targets(self):
        records=[{'collection':'claims','record':{'id':'CL-A','certainty_id':'CERT-A'}},
                 {'collection':'misreadings','record':{'id':'MIS-A','claim_ids':['CL-A']}},
                 {'collection':'chapters','record':{'id':'CH-01'}}]
        kept,rejected=dependency_closed_candidates(records,{})
        self.assertEqual([r['record']['id'] for r in kept],['CH-01'])
        self.assertEqual([r['record_id'] for r in rejected],['CL-A','MIS-A'])
        self.assertEqual(len(records),3)
        self.assertEqual(dependency_closed_candidates(records,{'certainty':[{'id':'CERT-A'}]})[0],records)

    def test_rejected_certainty_does_not_break_the_accepted_project(self):
        self.start();worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            result=worker(command,request,**kwargs)
            if request['stage']=='generate':
                evidence=copy.deepcopy(result['result']['candidates'][0]['evidence'])
                result['result']['candidates'] += [
                    {'collection':'claims','record':{'id':'CL-A','layer':'descriptive','kind':'observation','statement':'Admission requires a checked safety indication.','chapter_ids':['CH-01'],'certainty_id':'CERT-A','status':'draft'},'evidence':evidence},
                    {'collection':'certainty','record':{'id':'CERT-A','label':'Test label','definition':'An unsupported confidence classification.','status':'draft'},'evidence':evidence}]
            if request['stage']=='review':
                rejected={c['candidate_id'] for c in request['payload']['candidates'] if c['collection']=='certainty'}
                for d in result['result']['decisions']:
                    if d['candidate_id'] in rejected:d['decision']='reject'
            return result
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        canonical=load_language_data(self.run/'candidate','en')
        self.assertEqual(canonical['claims'],[])
        self.assertTrue(canonical['chapters'][0]['summary'])
        self.assertTrue(list(self.run.glob('candidate-dependency-rejections-*.json')))

    def test_restart_replays_generation_and_review_with_original_candidate_identity(self):
        self.start();worker=fixtures.Worker()
        def interrupted(command,request,**kwargs):
            if request['stage']=='audit':raise KeyboardInterrupt()
            return worker(command,request,**kwargs)
        with self.assertRaises(KeyboardInterrupt):self.execute(interrupted)
        old=_unseal(self.run/'manifest.json')
        destination=self.root/'restart';recipe=copy.deepcopy(self.recipe);recipe['max_calls']+=1
        restart_pipeline(self.run,destination,recipe)
        manifest=_unseal(destination/'manifest.json')
        self.assertEqual(manifest['created_at'],old['created_at'])
        self.assertIn('restarted_at',manifest)
        next_worker=fixtures.Worker()
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=next_worker):
            result=resume_pipeline(destination)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        self.assertNotIn('generate',[r['stage'] for r in next_worker.requests])
        self.assertNotIn('review',[r['stage'] for r in next_worker.requests])
        jobs=[_unseal(p) for p in (destination/'jobs').glob('*.json')]
        self.assertTrue(any(j.get('reused_from') and j['request']['stage']=='review' for j in jobs))
        self.assertEqual(load_language_data(destination/'candidate','en')['chapters'][0]['status'],'draft')

    def test_restart_cannot_repair_after_holdout_begins(self):
        self.start();self.execute(fixtures.Worker())
        destination=self.root/'forbidden'
        with self.assertRaisesRegex(ReadingPackError,'held-out'):
            restart_pipeline(self.run,destination,self.recipe)
        self.assertFalse(destination.exists())

    def test_malformed_unhashable_id_is_quarantined_without_poisoning_valid_siblings(self):
        self.start();worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='generate':
                response['result']['candidates'].append({'collection':'claims','record':{'id':['CL-X'],'status':'draft'},'evidence':copy.deepcopy(response['result']['candidates'][0]['evidence'])})
            return response
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        self.assertEqual(load_language_data(self.run/'candidate','en')['claims'],[])
