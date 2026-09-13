from __future__ import annotations

import copy

import unittest

from unittest.mock import patch

from reading_pack.project import load_language_data

from reading_pack_producer.pipeline import Runner, _unseal, start_pipeline

from reading_pack_producer.pipeline_generation import generation_chunks, protection_reason

from reading_pack_producer.pipeline_reuse import import_preparation

from tests import test_pipeline as fixtures

Worker = fixtures.Worker

class GenerationContractTests(unittest.TestCase):
    def test_generation_projection_omits_only_uneditable_content_and_preserves_ids(self):
        from reading_pack_producer.pipeline_generation import compact_canonical, generation_context
        canonical = {'chapters': [{'id': 'CH-01', 'title': 'Gate', 'sections': [], 'summary': 'The gate opens.', 'terms': []}],
                     'claims': [{'id': 'CL-OLD', 'statement': 'Supplied claim.'}],
                     'policies': [{'id': 'POL-OLD', 'statement': 'Supplied policy.'}, {'id': 'POL-NEW', 'statement': 'Generated policy.'}]}
        before = copy.deepcopy(canonical)
        self.assertEqual(generation_context(canonical, {})[0], compact_canonical(canonical))
        projected, omitted = generation_context(canonical, {'claims': {'mode': 'provided', 'protected_ids': []},
                    'policy': {'mode': 'augment', 'protected_ids': ['POL-OLD']}})
        self.assertEqual(projected['claims'], [{'id': 'CL-OLD'}])
        self.assertEqual(projected['policies'][0], {'id': 'POL-OLD'})
        self.assertEqual(projected['policies'][1], canonical['policies'][1])
        self.assertEqual(projected['chapters'], canonical['chapters'])
        self.assertEqual(len(omitted['claims'][0]['sha256']), 64)
        self.assertEqual(canonical, before)

    def test_author_collections_and_fields(self):
        chapter={'id':'CH-01','title':'Gate','sections':[], 'summary':'Supplied', 'terms':[]}
        canonical={'chapters':[chapter]}
        for mode in ['provided','omit']:
            contract={'claims':{'mode':mode,'protected_ids':[]}}
            self.assertIsNotNone(protection_reason({'collection':'claims','record':{'id':'CL-NEW'}},canonical,contract))
        contract={'claims':{'mode':'augment','protected_ids':['CL-OLD']}}
        self.assertIsNotNone(protection_reason({'collection':'claims','record':{'id':'CL-OLD'}},canonical,contract))
        self.assertIsNone(protection_reason({'collection':'claims','record':{'id':'CL-NEW'}},canonical,contract))
        contract={'summaries':{'mode':'augment','protected_ids':['CH-01']}}
        self.assertIsNotNone(protection_reason({'collection':'chapters','record':{**chapter,'summary':'Changed'}},canonical,contract))
        self.assertIsNone(protection_reason({'collection':'chapters','record':chapter},canonical,contract))
        self.assertIsNotNone(protection_reason({'collection':'chapters','record':{**chapter,'sections':['Invented']}},canonical,{}))

    def test_generation_partition_covers_nonoverlap_source_and_preserves_offsets(self):
        text='abcdef' * 1300
        chunks=[{'id':'SRC-1-0','source_id':'SRC-1','role':'primary-book','source_sha256':'a'*64,'start':0,'end':len(text),'text_start':0,'text':text}]
        pieces=generation_chunks(chunks,2000)
        self.assertEqual(''.join(p['text'][p['start']-p['text_start']:p['end']-p['text_start']] for p in pieces),text)
        self.assertEqual(len({p['id'] for p in pieces}),len(pieces))
        self.assertTrue(all(p['end']-p['start']<=2000 for p in pieces))
        self.assertEqual(generation_chunks(chunks,9000),chunks)

    def test_missing_empty_page_is_restored_but_changed_page_is_rejected(self):
        from reading_pack_producer.pipeline_generation import hydrate_chapter_fields
        canonical={'chapters':[{'id':'CH-01','kind':'chapter','title':'Gate','pages':'','sections':[],'summary':''}]}
        item={'collection':'chapters','record':{k:v for k,v in canonical['chapters'][0].items() if k!='pages'}}
        self.assertEqual(hydrate_chapter_fields(item,canonical),['pages'])
        self.assertIsNone(protection_reason(item,canonical,{}))
        item['record']['pages']='100';self.assertEqual(hydrate_chapter_fields(item,canonical),[])
        self.assertEqual(protection_reason(item,canonical,{}),'chapter_structure_is_fixed')

    def test_batch_duplicate_case_is_not_silently_dropped(self):
        from reading_pack_producer.pipeline_evaluation import _by_id
        from reading_pack.errors import ReadingPackError
        with self.assertRaisesRegex(ReadingPackError,'coverage mismatch'):
            _by_id([{'case_id':'a'},{'case_id':'a'}],[{'id':'a'},{'id':'b'}])

    def test_batch_grade_cannot_borrow_another_cases_evidence(self):
        from types import SimpleNamespace
        from reading_pack_producer.pipeline_evaluation import evaluate_batches
        from reading_pack.errors import ReadingPackError
        cases=[{'id':k,'question':k,'requirements':['One condition'], 'evidence':[{'span_id':'SPAN-'+k*24,'source_id':'SRC-1','quote':k}]} for k in ['a','b']]
        def call(stage,payload,key):
            if stage=='answer':return {'answer':'Supported answer'}
            grade={'requirements_met':[True],'uncertain':False,'critical_errors':[], 'findings':[],'rationale':'Checked','answer_quotes':['Supported answer']}
            rows=[{'case_id':k,**copy.deepcopy(grade)} for k in ['a','b']]
            rows[0]['findings']=[{'evidence':[cases[1]['evidence'][0]]}]
            return {'grades':rows}
        runner=SimpleNamespace(recipe={'evaluation_batch_size':2,'answer_repetitions':1,'minimum_requirement_fraction':1},call=call,evidence=lambda _:None)
        with self.assertRaisesRegex(ReadingPackError,'another case'):
            evaluate_batches(runner,'Pack',cases,0,'development')

class PipelineGenerationTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def test_source_failure_is_repaired_before_any_reader_call(self):
        self.start(); worker = Worker()
        def adapter(command, request, **kwargs):
            result = worker(command, request, **kwargs)
            if request['stage'] == 'audit' and request['job'].startswith('0/'):
                result['result']['findings'] = [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                    'reason': 'Clarify the joint condition.', 'evidence': [{'source_id': 'SRC-1', 'span_id': request['payload']['evidence_spans'][0]['id']}]}]
            return result
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        reports = _unseal(self.run / 'state.json')['rounds']
        self.assertEqual(reports[0]['development']['scores'], {})
        self.assertFalse(reports[0]['development']['passed'])
        self.assertTrue(reports[1]['development']['passed'])
        self.assertTrue(all(not r['job'].startswith('0/') for r in worker.requests if r['stage'] == 'answer'))
        repair = next(r for r in worker.requests if r['stage'] == 'repair')
        self.assertTrue(repair['payload']['development_guidance']['cases'])
        self.assertTrue(all(c['id'].startswith('development-') for c in repair['payload']['development_guidance']['cases']))

    def test_source_regression_after_reader_failure_does_not_lose_previous_scores(self):
        self.recipe['max_rounds'] = 2; self.start(); worker = Worker(repair=True)
        def adapter(command, request, **kwargs):
            result = worker(command, request, **kwargs)
            if request['stage'] == 'audit' and request['job'].startswith('1/'):
                result['result']['findings'] = [{'category': 'missing_qualifier', 'record_ids': ['CH-01'],
                    'reason': 'Repair introduced a source defect.', 'evidence': [{'source_id': 'SRC-1', 'span_id': request['payload']['evidence_spans'][0]['id']}]}]
            return result
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'failed_quality', result)
        reports = _unseal(self.run / 'state.json')['rounds']
        self.assertTrue(reports[0]['development']['scores'])
        self.assertTrue(reports[1]['regression'])
        self.assertFalse(any('/holdout/' in r['job'] for r in worker.requests))

    def test_provided_candidate_is_isolated_before_review_and_application(self):
        self.recipe['profile']='general-navigation';self.start(); runner=Runner(self.run)
        project=self.root/'draft';runner.bootstrap(project)
        worker=Worker()
        def adapter(command,request,**kwargs):
            result=worker(command,request,**kwargs)
            if request['stage']=='generate':
                result['result']['candidates'].append({'collection':'claims','record':{'id':'CL-NEW','layer':'descriptive','kind':'observation','statement':'Entry needs the lamp.','chapter_ids':['CH-01'],'status':'draft'},'evidence':[{'span_id':request['payload']['evidence_spans'][0]['id']}]})
            return result
        with patch('reading_pack_producer.pipeline.author_contract',return_value={'claims':{'mode':'provided','protected_ids':[]}}),patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=adapter):
            rejected=runner.generate(project,0,[])
        self.assertTrue(any('author_input_provided' in f['reason'] for f in rejected))
        self.assertEqual(load_language_data(project,'en')['claims'],[])
        self.assertTrue(load_language_data(project,'en')['chapters'][0]['summary'])
        self.assertTrue(all(c['collection']!='claims' for r in worker.requests if r['stage']=='review' for c in r['payload']['candidates']))

    def test_reuse_preparation_rebinds_identical_contract_without_model_calls(self):
        self.recipe['profile']='general-navigation';self.start();worker=Worker();runner=Runner(self.run)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=worker):
            expected=runner.benchmark()
        destination=self.root/'next';recipe=copy.deepcopy(self.recipe);recipe['max_rounds']+=1
        start_pipeline(destination,self.source,recipe)
        receipt=import_preparation(self.run,destination)
        self.assertEqual(receipt['exchanges'],2)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=AssertionError('must reuse')):
            self.assertEqual(Runner(destination).benchmark(),expected)
        jobs=[_unseal(p) for p in (destination/'jobs').glob('*.json')]
        self.assertTrue(all(j.get('reused_from') and not j['attempts'] for j in jobs))
        # Reuse is not a free pass for a changed prompt, schema, or payload.
        from reading_pack_producer.pipeline_reuse import reused_response
        request=copy.deepcopy(jobs[0]['request']);request['prompt']+=' Changed contract.'
        self.assertIsNone(reused_response(destination,request))

    def test_batched_reader_and_judge_remain_separate_and_holdout_is_last(self):
        self.recipe['evaluation_batch_size']=4;self.recipe['answer_repetitions']=2
        supplement=self.root/'appendix.md';supplement.write_text(fixtures.SUPPLEMENT)
        self.start(supplements=[(supplement,'author-data')]);worker=BatchWorker()
        result=self.execute(worker);self.assertEqual(result['state'],'awaiting_author_approval',result)
        calls=[r for r in worker.requests if r['stage'] in {'answer','grade_batch'}]
        self.assertEqual([r['stage'] for r in calls],['answer','answer','grade_batch']*4)
        readers=[r for r in calls if r['stage']=='answer']
        self.assertTrue(all(set(r['payload']) == {'pack','question'} for r in readers))
        self.assertTrue(all(len(r['payload']['evaluation_cases']) == 2 for r in calls if r['stage']=='grade_batch'))
        self.assertTrue(all('/development/' in r['job'] for r in calls[:6]))
        self.assertTrue(all('/holdout/' in r['job'] for r in calls[6:]))
        self.assertEqual(len(set(r['request_id'] for r in calls)),len(calls))

class BatchWorker(Worker):
    def __call__(self, command, request, **kwargs):
        stage,payload=request['stage'],request['payload']
        if stage != 'grade_batch':
            return super().__call__(command,request,**kwargs)
        self.requests.append(copy.deepcopy(request))
        result={'grades':[{'case_id':q['case_id'],'requirements_met':[True]*len(q['case']['requirements']), 'critical_errors':[], 'findings':[], 'uncertain':False,'rationale':'Both necessary conditions are supported.','answer_quotes':['Entry needs both daylight']} for q in payload['evaluation_cases']]}
        return {'schema_version':1,'request_id':request['request_id'],'model':request['model'],'result':result}
