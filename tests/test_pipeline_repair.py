from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from reading_pack.hashing import file_hash
from reading_pack.project import load_language_data, write_json
from reading_pack_producer.pipeline import Runner, _unseal
from reading_pack_producer.pipeline_repair import (
    AUDIT_ERROR_SUMMARY, audit_error_guidance, local_repair_failures, protected_repair_context, coverage_repair_targets, repair_findings_with_requirements,
)
from tests import test_pipeline as fixtures
from tests import test_assisted_review as author_fixtures


class RepairScopeTests(unittest.TestCase):
    def test_coverage_repair_reaches_the_distant_requirement_footnote(self):
        body = {'source_id':'SRC-1','start':0,'end':100,'text':'A controlled experiment is described.'}
        note = {'source_id':'SRC-1','start':1000,'end':1100,'text':'Without a signal 71%; with it 12%.'}
        proof = {'source_id':'SRC-1','quote':note['text'],'start':1000,'end':1036,'source_sha256':'frozen'}
        failure = {'category':'missing_coverage','classification':'blocking','record_ids':['CL-A'],
            'requirement_ids':['development:development-1:0'],
            'evidence':[{'source_id':'SRC-1','quote':body['text']}],
            'source_scope':{k:body[k] for k in ('source_id','start','end')}}
        development = [{'id':'development-1','requirements':['Retain both conditions.'],'evidence':[proof]}]
        before=copy.deepcopy(failure)
        self.assertEqual(local_repair_failures([failure],note),[])
        repaired=repair_findings_with_requirements([failure],development)
        self.assertEqual(local_repair_failures(repaired,note),repaired)
        self.assertEqual(local_repair_failures(repaired,body),repaired)
        self.assertIn(proof,repaired[0]['evidence'])
        self.assertEqual(repaired[0]['repair_requirement_ids'],['development:development-1:0'])
        self.assertEqual(failure,before)
        self.assertEqual(repair_findings_with_requirements(repaired,development),repaired)

    def test_requirement_repair_context_never_guesses_or_uses_holdout_ids(self):
        cases=[{'id':'development-1','requirements':['Explicit condition.'],
                'evidence':[{'source_id':'SRC-2','quote':'A fixed supplement.'}]}]
        failures=[{'record_ids':['CL-A'],'reason':'development-1 was mentioned in prose','evidence':[]},
                  {'record_ids':['CL-B'],'requirement_ids':['holdout:holdout-1:0','development:development-1:8'], 'evidence':[]}]
        self.assertEqual(repair_findings_with_requirements(failures,cases),failures)
        valid={'record_ids':['CL-C'],'requirement_ids':['development:development-1:0'],'evidence':[]}
        enriched=repair_findings_with_requirements([valid],cases)[0]
        self.assertEqual(enriched['evidence'],cases[0]['evidence'])
        self.assertEqual(valid['evidence'],[])

    def test_mixed_coverage_targets_do_not_lose_the_authorized_destination(self):
        data={'names':[{'id':'NAME-A','name':'Ada'}],
              'misreadings':[{'id':'MIS-A','response':'Editable response.'},
                             {'id':'MIS-LOCKED','response':'Protected response.'}],
              'chapters':[{'id':'CH-01','title':'Entry'},{'id':'CH-02','title':'Other'}]}
        contract={'qa':{'mode':'provided','protected_ids':['MIS-A','MIS-LOCKED'],'draft_revision_ids':['MIS-A']}}
        findings=[{'category':'misattributed','record_ids':['NAME-A'],'reason':'Fix the attribution.'},
                  {'category':'missing_coverage','criterion':'required_coverage','record_ids':[],
                   'reason':'The condition belongs in MIS-A. MIS-LOCKED is original author text.'}]
        self.assertEqual(coverage_repair_targets(data,contract,findings),{'MIS-A'})
        self.assertEqual(coverage_repair_targets(data,contract,[findings[0]]),set())
        unnamed=[{'category':'missing_coverage','record_ids':[],'reason':'Add a source-supported condition.'}]
        self.assertNotIn('MIS-LOCKED',coverage_repair_targets(data,contract,unnamed))

    def test_summary_does_not_create_an_unrelated_model_call(self):
        summary = {'category': 'misattributed', 'record_ids': [], 'reason': AUDIT_ERROR_SUMMARY, 'evidence': []}
        real = {'category': 'missing_qualifier', 'record_ids': ['CL-A'], 'reason': 'A source condition is missing.',
                'evidence': [{'source_id': 'SRC-1', 'quote': 'necessary condition'}]}
        chunk = {'source_id': 'SRC-1', 'start': 0, 'end': 100, 'text': 'This is a necessary condition.'}
        self.assertEqual(local_repair_failures([real, summary], chunk), [real])
        self.assertEqual(local_repair_failures([real, summary], {**chunk, 'text': 'Unrelated discussion.'}), [])
        self.assertEqual(local_repair_failures([summary], chunk), [])

    def test_metadata_only_audit_errors_keep_details_and_their_source_scope(self):
        chunk = {'source_id': 'SRC-1', 'start': 100, 'end': 200, 'text': 'Source excerpt'}
        audit = {'source_attribution_errors': ['CL-A has a wrong attribution; CL-AB is a different ID.'], 'invented_record_ids': []}
        findings = audit_error_guidance(audit, chunk, {'claims': [{'id': 'CL-A'}, {'id': 'CL-AB'}, {'id': 'CL-B'}]})
        self.assertEqual(findings[0]['record_ids'], ['CL-A', 'CL-AB'])
        self.assertEqual(findings[0]['source_audit_errors'], audit)
        self.assertEqual(findings[0]['evidence'], [])  # Scope is not invented quotation evidence.
        self.assertEqual(local_repair_failures(findings, chunk), findings)
        self.assertEqual(local_repair_failures(findings, {**chunk, 'start': 200, 'end': 300}), [])
        self.assertEqual(local_repair_failures(findings, {**chunk, 'source_id': 'SRC-2'}), [])

    def test_adjudicated_cross_source_evidence_routes_beyond_initial_audit_scope(self):
        original = {'source_id': 'SRC-1', 'start': 0, 'end': 100, 'text': 'Earlier suspicion.'}
        actual = {'source_id': 'SRC-2', 'start': 0, 'end': 100, 'text': 'The missing condition is daylight.'}
        finding = {'category': 'misattributed', 'record_ids': ['CL-A'], 'classification': 'blocking',
            'source_scope': {k: original[k] for k in ('source_id', 'start', 'end')},
            'evidence': [{'source_id': 'SRC-2', 'quote': 'The missing condition is daylight.'}]}
        self.assertEqual(local_repair_failures([finding], original), [])
        self.assertEqual(local_repair_failures([finding], actual), [finding])
        self.assertEqual(local_repair_failures([finding], {**actual, 'text': 'Unrelated.'}), [])

    def test_existing_source_backed_attribution_finding_keeps_its_narrow_scope(self):
        audit = {'source_attribution_errors': ['CL-A: wrong attribution.'], 'invented_record_ids': [],
                 'findings': [{'category': 'misattributed', 'record_ids': ['CL-A'], 'evidence': [{'source_id': 'SRC-1', 'quote': 'Actual attribution.'}]}]}
        self.assertEqual(audit_error_guidance(audit, {'source_id': 'SRC-1', 'start': 0, 'end': 50000}, {'claims': [{'id': 'CL-A'}]}), [])
        self.assertEqual(audit['source_attribution_errors'], ['CL-A: wrong attribution.'])

    def test_protected_context_expands_only_related_editable_targets(self):
        chapter = {'id': 'CH-01', 'title': 'Gate', 'sections': [], 'summary': 'Entry conditions.', 'terms': []}
        data = {'chapters': [chapter, {**chapter, 'id': 'CH-02'}],
                'claims': [{'id': 'CL-A', 'statement': 'Provided statement.', 'chapter_ids': ['CH-01']}],
                'misreadings': [{'id': 'MIS-A', 'response': 'Provided response.', 'claim_ids': ['CL-A']}],
                'glossary': [{'id': 'TERM-A', 'term': 'entry', 'chapter_id': 'CH-01'},
                             {'id': 'TERM-B', 'term': 'other', 'chapter_id': 'CH-02'}],
                'names': [{'id': 'NAME-A', 'name': 'Ada', 'chapter_id': 'CH-01'}]}
        before = copy.deepcopy(data)
        contract = {m: {'mode': 'provided', 'protected_ids': []} for m in ['claims', 'qa', 'names']}
        related, context = protected_repair_context(data, contract, [{'record_ids': ['MIS-A']}])
        self.assertEqual(related, {'CH-01', 'TERM-A'})
        self.assertEqual(context, [{'collection': 'misreadings', 'record': data['misreadings'][0]}])
        self.assertEqual(data, before)
        contract['chapters'] = {'mode': 'provided', 'protected_ids': []}
        self.assertEqual(protected_repair_context(data, contract, [{'record_ids': ['CL-A']}])[0], {'TERM-A'})


class RepairRoutingTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def test_generator_receives_the_newly_routed_footnote_and_its_development_guidance(self):
        from reading_pack_producer.pipeline import _seal
        self.recipe['profile']='general-navigation';self.start();runner=Runner(self.run)
        project=self.run/'working';runner.bootstrap(project)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=fixtures.Worker()):
            runner.benchmark()
        body={**runner.chunks[0],'id':'SRC-1-0','start':0,'end':100,'text_start':0,'text':'A controlled experiment.'}
        note={**body,'id':'SRC-1-1000','start':1000,'end':1100,'text_start':1000,'text':'No signal 71%; clear signal 12%.'}
        suite=_unseal(self.run/'benchmark.json')
        suite['development'][0]['evidence']=[{'source_id':'SRC-1','quote':note['text']}]
        _seal(self.run/'benchmark.json',suite)
        case=suite['development'][0]
        failure={'category':'missing_coverage','classification':'blocking','record_ids':['CH-01'],
            'requirement_ids':[f"development:{case['id']}:0"], 'reason':'Retain both conditions.',
            'evidence':[{'source_id':'SRC-1','quote':body['text']}]}
        with patch('reading_pack_producer.pipeline.generation_chunks',return_value=[body,note]), \
             patch.object(runner,'call',return_value={'candidates':[]}) as worker:
            runner.generate(project,1,[failure])
        sent={call.args[2]:call.args[1] for call in worker.call_args_list}
        self.assertEqual(set(sent),{'1/repair/SRC-1-0','1/repair/SRC-1-1000'})
        self.assertEqual(sent['1/repair/SRC-1-1000']['development_guidance']['cases'],[case])
        self.assertIn(case['evidence'][0],sent['1/repair/SRC-1-1000']['failures'][0]['evidence'])

    def test_mixed_missing_coverage_and_named_failure_allow_only_the_named_destinations(self):
        self.recipe['profile']='general-navigation';self.start();runner=Runner(self.run)
        project=self.run/'working';runner.bootstrap(project)
        data=load_language_data(project,'en')
        for n in [2,3]:data['chapters'].append({**copy.deepcopy(data['chapters'][0]),'id':f'CH-0{n}','title':f'Other {n}','summary':'Keep this.'})
        write_json(project/'data/pack.en.json',data)
        failures=[{'category':'missing_qualifier','record_ids':['CH-01'],'reason':'Joint condition missing.','evidence':[]},
                  {'category':'missing_coverage','criterion':'required_coverage','record_ids':[],
                   'reason':'The frozen condition is missing from CH-02.','evidence':[]}]
        worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='repair':
                self.assertEqual(request['payload']['allowed_replacement_ids'],['CH-01','CH-02'])
                self.assertIn('prior_candidate_rejections',request['payload'])
                self.assertEqual(request['payload']['admission_feedback_contract']['max_contiguous_source_characters'],160)
                extra=copy.deepcopy(response['result']['candidates'][0])
                extra['record'].update(id='CH-02',title='Other 2')
                response['result']['candidates'].append(extra)
            return response
        feedback=[{'category':'other','record_ids':['CH-02'],'reason':'candidate quarantined: chapters: source_copy_risk','evidence':[]}]
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=adapter):
            runner.benchmark();self.assertEqual(runner.generate(project,1,failures,feedback=feedback),[])
        after=load_language_data(project,'en')
        self.assertNotEqual(after['chapters'][1],data['chapters'][1])
        self.assertEqual(after['chapters'][2],data['chapters'][2])

    def test_admission_rejection_reaches_the_next_existing_repair_round(self):
        self.recipe['profile']='general-navigation';self.start();worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='generate':
                response['result']['candidates'][0]['record']['summary']=fixtures.SOURCE.split('## Opening the gate\n')[1].strip()
            if request['stage']=='repair':
                feedback=request['payload']['prior_candidate_rejections']
                self.assertTrue(any('source_copy_risk' in f['reason'] for f in feedback))
            return response
        result=self.execute(adapter)
        self.assertEqual(result['state'],'awaiting_author_approval',result)
        self.assertTrue(any(r['stage']=='repair' for r in worker.requests))

    def test_completed_checkpoint_retains_rejection_feedback_for_the_remaining_round(self):
        from reading_pack_producer.pipeline import resume_pipeline
        from reading_pack_producer.pipeline_reuse import restart_pipeline
        self.recipe['profile']='general-navigation';self.recipe['max_rounds']=1;self.start()
        worker=fixtures.Worker()
        def initial(command,request,**kwargs):
            response=worker(command,request,**kwargs)
            if request['stage']=='generate':
                response['result']['candidates'][0]['record']['summary']=fixtures.SOURCE.split('## Opening the gate\n')[1].strip()
            return response
        self.assertEqual(self.execute(initial)['state'],'failed_quality')
        destination=self.root/'restarted';recipe=copy.deepcopy(self.recipe);recipe['max_rounds']=2
        restart_pipeline(self.run,destination,recipe,checkpoint_round=0)
        def finish(command,request,**kwargs):
            if request['stage']=='repair':
                self.assertTrue(any('source_copy_risk' in f['reason'] for f in request['payload']['prior_candidate_rejections']))
            return worker(command,request,**kwargs)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=finish):
            result=resume_pipeline(destination)
        self.assertEqual(result['state'],'awaiting_author_approval',result)

    def test_targeted_repairs_group_source_independently_of_initial_generation(self):
        self.recipe['profile'] = 'general-navigation'
        self.recipe['chunk_characters'] = 50000
        self.start(); runner = Runner(self.run)
        project = author_fixtures._author_project(self.root)
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=fixtures.Worker()):
            runner.benchmark()
        text = fixtures.SOURCE * 40
        runner.chunks = [{'id': 'SRC-1-0', 'source_id': 'SRC-1', 'start': 0,
                          'end': len(text), 'text_start': 0, 'text': text}]
        failures = [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                     'reason': 'The joint condition is missing.', 'evidence': []}]
        with patch.object(runner, 'call', return_value={'candidates': []}) as worker:
            runner.generate(project, 0, [])
            self.assertGreater(worker.call_count, 1)
            self.assertTrue(all(c.args[1]['source']['end'] - c.args[1]['source']['start'] <= 12000
                                for c in worker.call_args_list))
            worker.reset_mock()
            runner.generate(project, 1, failures)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(worker.call_args.args[1]['source']['text'], text)
            worker.reset_mock()
            runner.recipe['repair_chunk_characters'] = 2000
            runner.generate(project, 2, failures)
            self.assertGreater(worker.call_count, 1)
            self.assertTrue(all(c.args[1]['source']['end'] - c.args[1]['source']['start'] <= 2000
                                for c in worker.call_args_list))

    def test_related_chapter_can_be_repaired_while_real_author_input_stays_intact(self):
        self.recipe['profile'] = 'general-navigation'; self.start(); runner = Runner(self.run)
        project = author_fixtures._author_project(self.root)
        before = load_language_data(project, 'en')
        before['source'] = {'format': 'markdown', 'name': self.source.name, 'sha256': file_hash(self.source.read_bytes())}
        write_json(project / 'data/pack.en.json', before)
        worker = fixtures.Worker()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            runner.benchmark()
            problems = runner.generate(project, 1, [{'category': 'missing_qualifier', 'record_ids': ['NAME-ADA'],
                'reason': 'Clarify the source condition in the related chapter.', 'evidence': []}])
        self.assertEqual(problems, [])
        after = load_language_data(project, 'en')
        self.assertEqual(after['names'], before['names'])
        self.assertNotEqual(after['chapters'][0]['summary'], before['chapters'][0]['summary'])
        request = next(r for r in worker.requests if r['stage'] == 'repair')
        self.assertIn('CH-01', request['payload']['allowed_replacement_ids'])
        self.assertEqual(request['payload']['protected_repair_context']['records'][0]['record']['id'], 'NAME-ADA')

    def test_unrelated_replacement_is_quarantined_while_valid_sibling_is_reviewed(self):
        self.recipe['profile'] = 'general-navigation'; self.start(); runner = Runner(self.run)
        project = self.run / 'working'; runner.bootstrap(project)
        data = load_language_data(project, 'en')
        other = {**copy.deepcopy(data['chapters'][0]), 'id': 'CH-02', 'title': 'Other chapter', 'summary': 'Keep this.'}
        data['chapters'].append(other)
        write_json(project / 'data/pack.en.json', data)
        worker = fixtures.Worker()
        def adapter(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'repair':
                extra = copy.deepcopy(response['result']['candidates'][0])
                extra['record'].update(id='CH-02', title='Other chapter', summary='Unauthorized replacement.')
                response['result']['candidates'].insert(0, extra)
            return response
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=adapter):
            runner.benchmark()
            problems = runner.generate(project, 1, [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                'reason': 'A joint condition needs clarification.', 'evidence': []}])
        after = load_language_data(project, 'en')
        self.assertEqual(after['chapters'][1], other)
        self.assertNotEqual(after['chapters'][0]['summary'], data['chapters'][0]['summary'])
        reviewed = [c for r in worker.requests if r['stage'] == 'review' for c in r['payload']['candidates']]
        self.assertEqual([c['record_id'] for c in reviewed], ['CH-01'])
        self.assertEqual(problems, [])  # A discarded extra is not a new defect in the Pack.
        rejected = _unseal(self.run / 'candidate-evidence-rejections-1-SRC-1-0.json')['rejections']
        self.assertEqual([(r['record_id'], r['reason']) for r in rejected], [('CH-02', 'unrelated_record_replacement')])

    def test_metadata_only_source_error_blocks_readers_until_the_repair_is_audited(self):
        self.start(); worker = fixtures.Worker()
        def adapter(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'audit' and request['job'].startswith('0/'):
                response['result']['source_attribution_errors'] = ['CH-01 needs corrected source attribution.']
            return response
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        first = _unseal(self.run / 'rounds/0/report.json')
        self.assertFalse(first['development']['passed'])
        self.assertTrue(first['counts']['source_attribution_errors'])
        repair = next(r for r in worker.requests if r['stage'] == 'repair')
        self.assertTrue(any(f.get('source_scope') and f.get('source_audit_errors') for f in repair['payload']['failures']))
        self.assertTrue(all(not r['job'].startswith('0/') for r in worker.requests if r['stage'] == 'answer'))
