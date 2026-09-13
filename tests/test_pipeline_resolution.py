from __future__ import annotations
import copy
import unittest
from unittest.mock import patch
from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import _unseal
from reading_pack_producer.pipeline_resolution import consolidate_findings,lookup_context
from tests import test_pipeline as fixtures
from tests import test_pipeline_audit as audit_fixtures

class ResolutionTests(unittest.TestCase):
    def test_repeated_issue_keeps_all_observations_and_distinct_criteria(self):
        a={'category':'misattributed','criterion':'consistency','classification':'blocking','record_ids':['NAME-A','NAME-B'],
           'reason':'First source identifies the contradiction.','evidence':[{'source_id':'SRC-1','quote':'First.'}]}
        b={**a,'category':'contradictory','record_ids':['NAME-B','NAME-A'],'reason':'Second source confirms it.',
           'evidence':[{'source_id':'SRC-2','quote':'Second.'}]}
        other={**a,'criterion':'attribution','reason':'A different issue.'}
        groups=consolidate_findings([a,b,other])
        self.assertEqual(len(groups),2)
        self.assertEqual(groups[0]['observations'],[a,b])
        self.assertEqual(len(groups[0]['evidence']),2)
        self.assertIn(b['reason'],groups[0]['reason'])
        self.assertNotIn('observations',a)

class LookupTests(unittest.TestCase):
    setUp=audit_fixtures.AuditContextTests.setUp
    def test_requested_query_fetches_only_frozen_sources_and_stays_bounded(self):
        result=lookup_context(self.runner,self.data,self.finding,[{'source_id':'SRC-2','query':'daylight'}])
        self.assertEqual(result['retrieval']['followup_searches'][0]['match_count'],1)
        self.assertTrue(any('daylight' in c['text'] and c['role']=='author-qa' for c in result['samples']))
        self.assertLessEqual(result['retrieval']['characters'],32000)
        with self.assertRaises(ReadingPackError):
            lookup_context(self.runner,self.data,self.finding,[{'source_id':'/tmp/private','query':'secret'}])
        self.runner.recipe['audit_context_characters']=500
        result=lookup_context(self.runner,self.data,self.finding,[{'source_id':'SRC-2','query':'daylight'}])
        self.assertLessEqual(result['retrieval']['characters'],500)
        self.assertFalse(result['retrieval']['complete'])

class RecoveryPipelineTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    start=fixtures.PipelineTests.start
    execute=fixtures.PipelineTests.execute

    def adapter(self,*,persist=False,no_target=False):
        worker=fixtures.Worker();requests=[]
        def call(command,request,**kwargs):
            requests.append(copy.deepcopy(request));stage=request['stage'];payload=request['payload']
            if stage=='audit_lookup':
                result={'requests':[{'source_id':'SRC-1','query':'safety lamp'}], 'reason':'Check the stated joint entry condition.'}
                return {'schema_version':1,'request_id':request['request_id'],'model':request['model'],'result':result}
            response=worker(command,request,**kwargs)
            if stage=='audit' and (persist or 'Both conditions must hold.' not in payload['canonical']['chapters'][0]['summary']):
                span=payload['evidence_spans'][0]
                response['result']['findings']=[{'category':'misattributed','record_ids':[] if no_target else ['CH-01'],
                    'reason':'Clarify whether the described opening condition also requires the safety indication.',
                    'evidence':[{'source_id':span['source_id'],'span_id':span['id']}]}]
            if stage=='audit_adjudicate':
                for d in response['result']['decisions']:
                    corrected = not persist and 'Both conditions must hold.' in payload['canonical']['chapters'][0]['summary']
                    d.update(classification='dismissed' if corrected else 'unresolved',repair_scope='none',
                             criterion='none' if corrected else 'qualification')
            return response
        return requests,call

    def test_uncertain_attribution_is_clarified_reviewed_and_reaudited_before_readers(self):
        self.start();requests,adapter=self.adapter()
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        repair=next(r for r in requests if r['stage']=='repair')
        self.assertIn('uncertainty_clarification',repair['payload'])
        self.assertEqual(repair['payload']['allowed_replacement_ids'],['CH-01'])
        self.assertTrue(any(r['stage']=='audit_lookup' for r in requests))
        self.assertTrue(any('audit-adjudicate/resolution-' in r['job'] for r in requests))
        self.assertTrue(any(r['stage']=='review' and r['job'].startswith('1/') for r in requests))
        self.assertFalse(any(r['stage']=='answer' and r['job'].startswith('0/') for r in requests))
        report=_unseal(self.run/'rounds/0/report.json')
        self.assertEqual(report['failures'][0]['classification'],'unresolved')
        self.assertEqual(_unseal(self.run/'rounds/1/report.json')['failures'],[])

    def test_persistent_uncertainty_never_passes_and_uses_only_existing_rounds(self):
        self.start();requests,adapter=self.adapter(persist=True)
        result=self.execute(adapter)
        self.assertEqual(result['state'],'blocked_source_evidence',result)
        self.assertEqual(len([r for r in requests if r['stage']=='repair']),self.recipe['max_rounds']-1)
        self.assertFalse(any(r['stage']=='answer' for r in requests))
        self.assertFalse((self.run/'candidate').exists())

    def test_fresh_question_selection_is_reviewed_for_reader_purpose(self):
        self.start();worker=fixtures.Worker();self.execute(worker)
        generation=next(r for r in worker.requests if r['stage']=='benchmark')
        review=next(r for r in worker.requests if r['stage']=='benchmark_review')
        self.assertEqual(generation['payload']['selection_contract'],review['payload']['selection_contract'])
        self.assertIn('do not create mandatory Pack coverage obligations',str(review['payload']['selection_contract']))

    def test_audit_silence_cannot_erase_an_unrepaired_confirmed_finding_after_restart(self):
        from reading_pack_producer.pipeline import resume_pipeline
        from reading_pack_producer.pipeline_reuse import restart_pipeline
        self.recipe['profile']='general-navigation';self.recipe['max_rounds']=1;self.start()
        worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='repair':response['result']['candidates']=[]
            if request['stage']=='audit' and request['job'].startswith('0/'):
                span=request['payload']['evidence_spans'][0]
                response['result']['findings']=[{'category':'missing_qualifier','record_ids':['CH-01'],
                    'reason':'Clarify the required condition.','evidence':[{'source_id':span['source_id'],'span_id':span['id']}]}]
            return response
        self.assertEqual(self.execute(adapter)['state'],'failed_quality')
        destination=self.root/'restarted';recipe=copy.deepcopy(self.recipe);recipe['max_rounds']=2
        restart_pipeline(self.run,destination,recipe,checkpoint_round=0)
        self.assertTrue(_unseal(destination/'manifest.json')['restart_origin']['known_findings'])
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=adapter):
            result=resume_pipeline(destination)
        self.assertEqual(result['state'],'failed_quality',result)
        self.assertTrue(result['rounds'][-1]['failures'])
        self.assertFalse(any(r['stage']=='answer' for r in worker.requests))
        self.assertTrue(_unseal(destination/'audit-report-1.json')['known_findings_rechecked'])
