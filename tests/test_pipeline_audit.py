from __future__ import annotations

import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data, write_json
from reading_pack.hashing import file_hash
from reading_pack_producer.pipeline import Runner, _seal, _unseal
from reading_pack_producer.pipeline_audit import AUDIT_POLICY, audit_contract, adjudicate_audit, evidence_context, structural_counts, run_contract, protected_conflicts
from reading_pack_producer.pipeline_evidence import source_spans
from tests import test_pipeline as fixtures
from tests import test_assisted_review as author_fixtures


class AuditContextTests(unittest.TestCase):
    def setUp(self):
        texts=[('book.md','primary-book','The gate opens only after inspection.\n'+'Background. '*80),
               ('appendix.md','author-qa','# gate-condition\nThe inspection requires a green lamp and daylight.\n'+'Explanation. '*80)]
        self.chunks=[];sources=[]
        for number,(name,role,text) in enumerate(texts,1):
            sid=f'SRC-{number}';digest=str(number)*64
            sources.append({'id':sid,'name':name,'role':role,'sha256':digest})
            for start in range(0,len(text),500):
                end=min(len(text),start+500);left=max(0,start-50);right=min(len(text),end+50)
                self.chunks.append({'id':f'{sid}-{start}','source_id':sid,'role':role,'source_sha256':digest,
                    'start':start,'end':end,'text_start':left,'text':text[left:right]})
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.runner=SimpleNamespace(root=Path(temp.name),chunks=self.chunks,
            manifest={'sources':sources,'recipe':{'profile':'general-navigation'}},
            recipe={'profile':'general-navigation'},lang='en')
        self.data={'chapters':[], 'claims':[{'id':'CL-GATE','statement':'The gate opens after inspection.',
                    'source_locations':['appendix.md#normalized-text:0-60'],'anchor':'gate-condition'}]}
        s=source_spans(self.chunks[0])[0]
        self.finding={'category':'missing_qualifier','record_ids':['CL-GATE'],'reason':'The condition may be missing.',
            'evidence':[{'source_id':s['source_id'],'span_id':s['id'],'source_sha256':s['source_sha256'],
                'start':s['start'],'end':s['end'],'quote':s['text']}]}

    def test_retrieves_exact_cross_source_support_and_preserves_roles(self):
        result=evidence_context(self.runner,self.data,[self.finding])
        self.assertTrue(result['retrieval']['complete'])
        self.assertEqual({c['source_id'] for c in result['samples']},{'SRC-1','SRC-2'})
        self.assertTrue(any('green lamp and daylight' in c['text'] and c['role']=='author-qa' for c in result['samples']))
        self.assertLessEqual(result['retrieval']['characters'],32000)
        self.assertFalse(any('path' in c for c in result['samples']))

    def test_fragment_locator_retrieves_only_the_registered_source(self):
        record = self.data['claims'][0]
        record.pop('anchor')
        record['source_locations'] = ['appendix.md#gate-condition']
        result = evidence_context(self.runner, self.data, [self.finding])
        self.assertTrue(result['retrieval']['complete'])
        self.assertTrue(any('green lamp and daylight' in c['text'] and c['role'] == 'author-qa' for c in result['samples']))
        for locator in ['appendix.md#missing-anchor', '/tmp/private.md#gate-condition', 'appendix.md#gate-cond']:
            record['source_locations'] = [locator]
            result = evidence_context(self.runner, self.data, [self.finding])
            self.assertIn('unresolved locator: ' + locator, result['retrieval']['unresolved'])

    def test_attribution_retrieves_distant_manuscript_mentions_without_inferring_absence(self):
        text = 'Padding. ' * 600 + 'Ada described the inspection rule.\n' + 'Padding. ' * 600
        self.runner.chunks = [c for c in self.chunks if c['source_id'] != 'SRC-1'] + [{
            'id': 'SRC-1-0', 'source_id': 'SRC-1', 'role': 'primary-book', 'source_sha256': '1'*64,
            'start': 0, 'end': len(text), 'text_start': 0, 'text': text}]
        record = self.data['claims'][0]
        record.update(name='Ada', aliases=['NoSuchName'])
        finding = {**self.finding, 'category': 'misattributed', 'evidence': []}
        result = evidence_context(self.runner, self.data, [finding])
        self.assertTrue(result['retrieval']['complete'])
        self.assertTrue(any('Ada described' in c['text'] and c['role'] == 'primary-book' for c in result['samples']))
        counts = {row['query']: row['match_count'] for row in result['retrieval']['literal_searches']}
        self.assertEqual(counts, {'Ada': 1, 'NoSuchName': 0})
        self.assertIn('do not prove semantic absence', result['retrieval']['literal_search_note'])
        self.assertLessEqual(result['retrieval']['characters'], 32000)

    def test_invalid_source_hash_or_quote_cannot_be_used_as_evidence(self):
        for key,value in [('source_sha256','9'*64),('quote','Fabricated quotation.')]:
            f=copy.deepcopy(self.finding);f['evidence'][0][key]=value
            with self.assertRaises(ReadingPackError):evidence_context(self.runner,self.data,[f])

    def test_unavailable_locator_is_visible_and_does_not_read_a_path(self):
        self.data['claims'][0]['source_locations']=['/tmp/private.md#normalized-text:0-30']
        context=evidence_context(self.runner,self.data,[self.finding])
        self.assertFalse(context['retrieval']['complete'])
        self.assertIn('unresolved locator',context['retrieval']['unresolved'][0])

    def test_limit_does_not_silently_omit_evidence_or_allow_dismissal(self):
        self.runner.recipe['audit_context_characters']=600
        def judge(stage,payload,key):
            return {'decisions':[{'finding_id':payload['findings'][0]['finding_id'],'classification':'advisory',
                'criterion':'none','reader_impact':'','reason':'Optional detail.','repair_scope':'none','evidence':[]}]}
        self.runner.call=judge
        audit={'findings':[self.finding],'source_attribution_errors':[],'invented_record_ids':[]}
        with patch('reading_pack_producer.pipeline_audit.author_contract',return_value={}):
            result=adjudicate_audit(self.runner,None,self.data,audit,self.chunks[0],0)
        self.assertEqual(result['blocking'][0]['classification'],'unresolved')
        self.assertEqual(result['advisories'],[])

    def test_frozen_policy_does_not_require_every_footnote_name(self):
        contract=audit_contract('academic-argument');contract['selection_rules'].clear()
        self.assertTrue(AUDIT_POLICY['selection_rules'])
        self.assertIn('Names mentioned only in bibliographic/footnote attribution need no separate person record.',AUDIT_POLICY['selection_rules'])

    def test_overfull_batch_splits_before_judge_calls_without_raising_context_cap(self):
        self.runner.recipe['audit_context_characters']=1200
        first=copy.deepcopy(self.finding);first['record_ids']=[]
        second=copy.deepcopy(first)
        span=source_spans(next(c for c in self.chunks if c['source_id']=='SRC-2'))[0]
        second['evidence']=[{'source_id':span['source_id'],'span_id':span['id'],'source_sha256':span['source_sha256'],
                             'start':span['start'],'end':span['end'],'quote':span['text']}]
        calls=[]
        def judge(stage,payload,key):
            calls.append((payload,key))
            self.assertTrue(payload['retrieval']['complete'])
            self.assertLessEqual(payload['retrieval']['characters'],1200)
            self.assertEqual(len(payload['findings']),1)
            return {'decisions':[{'finding_id':payload['findings'][0]['finding_id'],'classification':'advisory',
                'criterion':'none','reader_impact':'','reason':'Optional detail.','repair_scope':'none','evidence':[]}]}
        self.runner.call=judge
        audit={'findings':[first,second],'source_attribution_errors':[],'invented_record_ids':[]}
        with patch('reading_pack_producer.pipeline_audit.author_contract',return_value={}):
            result=adjudicate_audit(self.runner,None,self.data,audit,self.chunks[0],0)
        self.assertEqual(len(calls),2);self.assertNotEqual(calls[0][1],calls[1][1])
        self.assertEqual(len(result['advisories']),2);self.assertEqual(result['blocking'],[])

    def test_coverage_ids_are_frozen_and_holdout_requirements_are_never_exposed(self):
        _seal(self.runner.root/'benchmark.json',{'development':[{'id':'D-1','requirements':['Daylight is necessary.']}],
              'holdout':[{'id':'H-1','requirements':['Hidden condition.']}]})
        contract=run_contract(self.runner,self.data)
        self.assertTrue(any(r['id']=='development:D-1:0' for r in contract['coverage_requirements']))
        self.assertNotIn('Hidden condition',str(contract));self.assertNotIn('H-1',str(contract))
        _seal(self.runner.root/'benchmark.json',{'development':[{'id':'D-1','requirements':['Changed requirement.']}],'holdout':[]})
        with self.assertRaisesRegex(ReadingPackError,'coverage contract changed'):run_contract(self.runner,self.data)


class AuditGateTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    start=fixtures.PipelineTests.start
    execute=fixtures.PipelineTests.execute

    def adapter(self,classification='advisory',*,direct_advice=False,reader_advice=False):
        worker=fixtures.Worker()
        def run(command,request,**kwargs):
            if request['stage']=='audit_lookup':
                return {'schema_version':1,'request_id':request['request_id'],'model':request['model'],
                        'result':{'requests':[],'reason':'No additional literal query is available.'}}
            response=worker(command,request,**kwargs);stage=request['stage']
            if stage=='audit' or (reader_advice and stage=='grade'):
                span=request['payload']['evidence_spans'][0]
                f={'category':'other','record_ids':['CH-01'],'reason':'A longer explanation would be helpful; no error is established.',
                   'evidence':[{'source_id':span['source_id'],'span_id':span['id']}]}
                response['result']['advisories' if direct_advice or stage=='grade' else 'findings']=[f]
            if stage=='audit_adjudicate':
                for decision in response['result']['decisions']:
                    decision.update(classification=classification,criterion='none' if classification in {'advisory','dismissed'} else 'qualification',
                                    repair_scope='none' if classification in {'advisory','dismissed'} else 'pack')
            return response
        return worker,run

    def test_optional_audit_advice_is_saved_and_readers_run_without_repair(self):
        self.start();worker,adapter=self.adapter()
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        self.assertNotIn('repair',[r['stage'] for r in worker.requests])
        self.assertIn('answer',[r['stage'] for r in worker.requests])
        report=_unseal(self.run/'rounds/0/report.json')
        self.assertTrue(report['advisories']);self.assertFalse(report['failures'])
        self.assertTrue(_unseal(self.run/'audit-report-0.json')['decisions'])

    def test_question_requirement_namespace_is_not_mistaken_for_a_missing_pack_module(self):
        self.start();worker,adapter=self.adapter('dismissed')
        def scope_checked(command,request,**kwargs):
            if request['stage']=='audit_adjudicate':
                payload=request['payload']
                self.assertNotIn('development',payload['canonical'])
                self.assertFalse(any(r['id'].startswith('development:') for r in payload['audit_contract']['coverage_requirements']))
                scope=payload['canonical_scope']
                self.assertTrue(scope['complete_pack_records'])
                self.assertIn('no development module exists or is required',scope['instruction'])
                self.assertNotIn('holdout',str(scope))
            return adapter(command,request,**kwargs)
        result=self.execute(scope_checked)
        self.assertEqual(result['state'],'awaiting_author_approval',result)

    def test_direct_advice_needs_no_adjudication_call_and_grade_advice_does_not_fail(self):
        self.start();worker,adapter=self.adapter(direct_advice=True,reader_advice=True)
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        self.assertNotIn('audit_adjudicate',[r['stage'] for r in worker.requests])
        self.assertTrue(_unseal(self.run/'final-evaluation.json')['advisories'])

    def test_confirmed_material_violation_still_blocks_readers(self):
        self.recipe['max_rounds']=1;self.start();worker,adapter=self.adapter('blocking')
        result=self.execute(adapter)
        self.assertEqual(result['state'],'failed_quality',result)
        self.assertNotIn('answer',[r['stage'] for r in worker.requests])
        self.assertTrue(_unseal(self.run/'rounds/0/report.json')['failures'])

    def test_unresolved_evidence_without_an_editable_target_stops_before_speculative_repairs(self):
        self.start();worker,adapter=self.adapter('unresolved')
        def no_target(command,request,**kwargs):
            response=adapter(command,request,**kwargs)
            if request['stage']=='audit':
                response['result']['findings'][0]['record_ids']=[]
            return response
        result=self.execute(no_target)
        self.assertEqual(result['state'],'blocked_source_evidence',result)
        self.assertNotIn('repair',[r['stage'] for r in worker.requests])
        self.assertTrue(_unseal(self.run/'unresolved-source-evidence.json')['findings'])
        count=len(worker.requests);self.execute(no_target);self.assertEqual(len(worker.requests),count)

    def test_adjudicator_cannot_invent_or_duplicate_finding_ids(self):
        for mode in ['invent','duplicate']:
            with self.subTest(mode=mode):
                self.setUp();self.start();worker,adapter=self.adapter()
                def wrong(command,request,**kwargs):
                    response=adapter(command,request,**kwargs)
                    if request['stage']=='audit_adjudicate':
                        if mode=='invent':response['result']['decisions'][0]['finding_id']='unknown'
                        else:response['result']['decisions']*=2
                    return response
                result=self.execute(wrong)
                self.assertEqual(result['state'],'blocked_execution',result)
                self.assertNotIn('answer',[r['stage'] for r in worker.requests])

    def test_changed_audit_policy_is_rejected_before_any_call(self):
        self.start();manifest=_unseal(self.run/'manifest.json');manifest['audit_policy']['criteria'].clear();_seal(self.run/'manifest.json',manifest)
        with self.assertRaisesRegex(ReadingPackError,'audit policy'):Runner(self.run)

    def test_coverage_cannot_gain_a_new_requirement_during_audit(self):
        self.start();worker,adapter=self.adapter('blocking')
        def wrong(command,request,**kwargs):
            response=adapter(command,request,**kwargs)
            if request['stage']=='audit_adjudicate':
                for d in response['result']['decisions']:
                    d.update(criterion='required_coverage',requirement_ids=['invented:all-footnote-authors'])
            return response
        result=self.execute(wrong)
        self.assertEqual(result['state'],'blocked_execution',result)
        self.assertNotIn('answer',[r['stage'] for r in worker.requests])

    def test_wrong_evidence_cannot_dismiss_a_real_suspicion(self):
        self.start();worker,adapter=self.adapter('dismissed')
        def wrong(command,request,**kwargs):
            response=adapter(command,request,**kwargs)
            if request['stage']=='audit_adjudicate':response['result']['decisions'][0]['evidence'][0]['span_id']='SPAN-'+'f'*24
            return response
        result=self.execute(wrong)
        self.assertEqual(result['state'],'blocked_execution',result)
        self.assertNotIn('answer',[r['stage'] for r in worker.requests])

    def test_blocking_decisions_require_material_impact_and_frozen_coverage_ids(self):
        for missing in ['impact','coverage_id']:
            with self.subTest(missing=missing):
                self.setUp();self.start();worker,adapter=self.adapter('blocking')
                def invalid(command,request,**kwargs):
                    response=adapter(command,request,**kwargs)
                    if request['stage']=='audit_adjudicate':
                        for d in response['result']['decisions']:
                            if missing=='impact':d['reader_impact']=' '
                            else:d.update(criterion='required_coverage',requirement_ids=[])
                    return response
                result=self.execute(invalid)
                self.assertEqual(result['state'],'blocked_execution',result)
                self.assertNotIn('answer',[r['stage'] for r in worker.requests])

    def test_protected_repair_stops_once_without_changing_author_input(self):
        from reading_pack_producer.pipeline import start_pipeline
        from reading_pack_producer.pipeline_drafts import authorize_draft_revisions
        from reading_pack_review.author_input import load_author_input_state
        from reading_pack_review.author_review import load_author_review_state
        seed=author_fixtures._author_project(self.root)
        data=load_language_data(seed,'en');data['source']={'format':'markdown','name':self.source.name,'sha256':file_hash(self.source.read_bytes())}
        data['chapters'][0].update(title='Opening the gate',pages='',sections=[])
        write_json(seed/'data/pack.en.json',data)
        start_pipeline(self.run,self.source,self.recipe,project=seed)
        worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='audit':
                span=request['payload']['evidence_spans'][0]
                response['result']['findings']=[{'category':'contradictory','record_ids':['NAME-ADA'],
                    'reason':'The supplied record attributes the contrary position.',
                    'evidence':[{'source_id':span['source_id'],'span_id':span['id']}]}]
            if request['stage']=='audit_adjudicate':
                for d in response['result']['decisions']:d.update(criterion='attribution',repair_scope='record')
            return response
        result=self.execute(adapter)
        self.assertEqual(result['state'],'needs_author_input',result)
        self.assertNotIn('repair',[r['stage'] for r in worker.requests])
        working=self.run/'working'
        self.assertEqual(load_language_data(working,'en')['names'],data['names'])
        self.assertEqual(load_author_input_state(working),load_author_input_state(seed))
        state=load_author_review_state(working)
        self.assertFalse(state and state.get('reviews'))
        failures=_unseal(self.run/'rounds/0/report.json')['failures']
        self.assertTrue(protected_conflicts(working,'en',load_language_data(working,'en'),failures))
        uncertain=copy.deepcopy(failures)
        for finding in uncertain:finding['classification']='unresolved'
        self.assertEqual(protected_conflicts(working,'en',load_language_data(working,'en'),uncertain),[])
        authorize_draft_revisions(working,'en',['NAME-ADA'],origin='synthetic explicit authorization')
        self.assertEqual(protected_conflicts(working,'en',load_language_data(working,'en'),failures),[])

    def test_model_count_noise_does_not_override_source_bound_inventory(self):
        self.start();worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='audit':
                response['result']['expected_structure_records']+=1
                response['result']['matched_structure_records']+=1
            return response
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        audit=_unseal(self.run/'audit-report-0.json')
        self.assertEqual(audit['model_structure_counts'][0]['expected'],2)
        self.assertEqual(audit['structural_inventory']['expected_structure_records'],1)

    def test_real_missing_or_reordered_headings_are_not_dismissed_as_count_noise(self):
        self.recipe['profile']='general-navigation';self.source.write_text(fixtures.SOURCE+'\n### First condition\nInspect the hinges.\n### Second condition\nInspect the latch.\n')
        self.start();runner=Runner(self.run);project=self.run/'working';runner.bootstrap(project);runner.reconcile_source_structure(project)
        data=load_language_data(project,'en');expected=structural_counts(runner,data)
        self.assertEqual(expected['matched_structure_records'],3)
        data['chapters'][0]['sections'].reverse()
        self.assertEqual(structural_counts(runner,data)['matched_structure_records'],1)
        data['chapters'][0]['sections'].pop()
        counts=structural_counts(runner,data);self.assertNotEqual(counts['expected_structure_records'],counts['observed_structure_records'])
