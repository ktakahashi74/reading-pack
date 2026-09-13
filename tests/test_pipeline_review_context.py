import copy
import unittest
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data
from reading_pack_producer.pipeline import Runner, _unseal
from reading_pack_producer.pipeline_evidence import worker_payload, resolve_references
from reading_pack_producer.pipeline_review_context import chapter_review_source, candidate_review_context, candidate_review_batches
from tests import test_pipeline as fixtures


class ChapterReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.text = ''.join(f'Paragraph {i:04d} provides an independent condition.\n' for i in range(100))
        self.chunks = []
        for start in range(0, len(self.text), 500):
            end = min(start + 500, len(self.text))
            left, right = max(0, start - 50), min(len(self.text), end + 50)
            self.chunks.append({'id': f'SRC-1-{start}', 'source_id': 'SRC-1', 'role': 'primary-book',
                'source_sha256': 'a'*64, 'start': start, 'end': end, 'text_start': left,
                'text': self.text[left:right]})
        self.old = {'id': 'CH-01', 'kind': 'chapter', 'title': 'Conditions', 'pages': '', 'sections': [], 'summary': '', 'terms': []}
        self.canonical = {'chapters': [self.old]}
        self.candidates = [{'candidate_id': 'C-1', 'collection': 'chapters',
                            'record': {**self.old, 'summary': 'Several conditions apply.'}}]

    def test_neighboring_support_is_available_with_exact_offsets_and_a_fixed_cap(self):
        original = self.chunks[3]
        result = chapter_review_source(original, self.candidates, self.canonical, self.chunks, 2000)
        self.assertEqual(result['text'], self.text[result['text_start']:result['end']])
        self.assertEqual(len(result['text']), 2000)
        self.assertLess(result['text_start'], original['text_start'])
        self.assertGreater(result['end'], original['text_start']+len(original['text']))
        payload, registry = worker_payload({'source': result})
        span = next(s for s in payload['evidence_spans'] if s['start'] > original['end'] + 50)
        reference = resolve_references({'span_id': span['id']}, registry)
        self.assertEqual(reference['quote'], self.text[reference['start']:reference['end']])
        runner = object.__new__(Runner);runner.chunks=self.chunks
        runner.evidence([reference],chunk=result)
        with self.assertRaises(ReadingPackError):runner.evidence([reference],chunk=original)
        with self.assertRaises(ReadingPackError):resolve_references({'span_id':'SPAN-outside'},registry)

    def test_unchanged_chapter_and_nonchapter_batches_do_not_expand(self):
        for candidates in [[{**self.candidates[0], 'record': self.old}],
                           [{'candidate_id':'C-2','collection':'claims','record':{'id':'CL-1'}}]]:
            self.assertEqual(chapter_review_source(self.chunks[3],candidates,self.canonical,self.chunks,2000),self.chunks[3])

    def test_other_documents_are_never_added(self):
        other={**self.chunks[0],'source_id':'SRC-2','text':'Unapproved unrelated document.'}
        result=chapter_review_source(self.chunks[0],self.candidates,self.canonical,[other]+self.chunks,2000)
        self.assertEqual(result['text'],self.text[:2000])
        self.assertNotIn('Unapproved',result['text'])

    def test_inconsistent_hash_gaps_and_original_overlap_are_rejected(self):
        for mutation in ['hash','gap','text']:
            chunks=copy.deepcopy(self.chunks)
            if mutation=='hash':chunks[3]['source_sha256']='b'*64
            elif mutation=='gap':chunks.pop(3)
            else:chunks[3]['text']='!'*len(chunks[3]['text'])
            with self.assertRaises(ReadingPackError):
                chapter_review_source(self.chunks[3],self.candidates,self.canonical,chunks,2000)

    def test_existing_context_is_not_truncated_when_it_already_exceeds_cap(self):
        self.assertEqual(chapter_review_source(self.chunks[3],self.candidates,self.canonical,self.chunks,500),self.chunks[3])


class ChapterReviewContextPipelineTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start

    def test_review_splits_before_dispatch_when_joint_retrieval_hides_one_records_source(self):
        from types import SimpleNamespace
        primary={'id':'SRC-1-0','source_id':'SRC-1','source_sha256':'a'*64,'role':'primary-book',
                 'start':0,'end':100,'text_start':0,'text':'x'*100}
        chunks=[primary];sources=[{'id':'SRC-1','name':'book.md','sha256':'a'*64,'role':'primary-book'}]
        candidates=[]
        for n in [2,3]:
            chunks.append({**primary,'id':f'SRC-{n}-0','source_id':f'SRC-{n}','source_sha256':str(n)*64,
                           'role':'author-data','end':1600,'text':str(n)*1600})
            sources.append({'id':f'SRC-{n}','name':f'note-{n}.md','sha256':str(n)*64,'role':'author-data'})
            candidates.append({'candidate_id':f'C-{n}','collection':'names',
                'record':{'id':f'NAME-{n}','name':f'Name {n}',
                          'source_locations':[f'note-{n}.md#normalized-text:0-1600']}})
        runner=SimpleNamespace(chunks=chunks,manifest={'sources':sources},recipe={'audit_context_characters':2000})
        canonical={'names':[], 'chapters':[]}
        together=candidate_review_context(runner,primary,candidates,canonical,[])
        self.assertFalse(together['source_context']['complete'])
        batches=list(candidate_review_batches(runner,primary,candidates,canonical,[]))
        self.assertEqual([offset for offset,_,_ in batches],[0,1])
        for (offset,batch,payload),sid in zip(batches,['SRC-2','SRC-3']):
            self.assertEqual(len(batch),1)
            self.assertTrue(payload['source_context']['complete'])
            self.assertLessEqual(payload['source_context']['characters'],2000)
            self.assertEqual({s['source_id'] for s in payload['supporting_sources']},{sid})
        runner.recipe['audit_context_characters']=500
        single=list(candidate_review_batches(runner,primary,candidates[:1],canonical,[]))
        self.assertEqual(len(single),1)
        self.assertFalse(single[0][2]['source_context']['complete'])

    def test_manuscript_candidate_review_can_verify_its_supplement_qualification(self):
        self.recipe['profile'] = 'general-navigation'
        supplement = self.root / 'appendix.md'
        supplement.write_text(fixtures.SUPPLEMENT)
        self.start(supplements=[(supplement, 'author-data')])
        runner = Runner(self.run); runner.prepare_source_structure()
        worker = fixtures.Worker()
        def adapter(command, request, **kwargs):
            response = worker(command, request, **kwargs)
            if request['stage'] == 'generate':
                if request['payload']['source']['source_id'] != 'SRC-1':
                    response['result']['candidates'] = []
                else:
                    record = response['result']['candidates'][0]['record']
                    record['summary'] += ' The author note adds that blue inspection lamps grant no entry.'
                    record['source_locations'] = [f'appendix.md#normalized-text:0-{len(fixtures.SUPPLEMENT)}']
            if request['stage'] == 'review':
                payload = request['payload']
                self.assertEqual({s['source_id'] for s in payload['evidence_spans']}, {'SRC-1','SRC-2'})
                self.assertEqual(payload['supporting_sources'][0]['role'], 'author-data')
                spans = [next(s for s in payload['evidence_spans'] if s['source_id'] == sid)
                         for sid in ['SRC-1','SRC-2']]
                for d in response['result']['decisions']:
                    d['evidence'] = [{'span_id':s['id'],'source_id':s['source_id']} for s in spans]
            return response
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=adapter):
            project = runner.root/'working'; runner.bootstrap(project)
            self.assertEqual(runner.generate(project, 0, []), [])
        chapter = load_language_data(project,'en')['chapters'][0]
        self.assertIn('blue inspection', chapter['summary'])
        self.assertEqual(chapter['status'], 'draft')

    def test_supporting_context_keeps_related_records_bound_and_rejects_unprovided_spans(self):
        supplement = self.root/'appendix.md'; supplement.write_text(fixtures.SUPPLEMENT)
        self.start(supplements=[(supplement,'author-data')])
        runner=Runner(self.run)
        old={'id':'CL-OLD','statement':'The note is discussed here.',
             'source_locations':[f'appendix.md#normalized-text:0-{len(fixtures.SUPPLEMENT)}']}
        candidate={'collection':'claims','record':{'id':'CL-NEW','statement':'See CL-OLD.'}}
        payload=candidate_review_context(runner, runner.chunks[0], [candidate], {'claims':[old]}, [])
        self.assertEqual(payload['related_records'],[old])
        self.assertLessEqual(payload['source_context']['characters'],32000)
        sent,registry=worker_payload(payload)
        self.assertNotIn('text',sent['supporting_sources'][0])
        appendix=next(s for s in sent['evidence_spans'] if s['source_id']=='SRC-2')
        self.assertEqual(resolve_references({'span_id':appendix['id']},registry)['quote'],fixtures.SUPPLEMENT)
        _,primary_registry=worker_payload({'source':runner.chunks[0]})
        with self.assertRaises(ReadingPackError):
            resolve_references({'span_id':appendix['id']},primary_registry)
        with self.assertRaises(ReadingPackError):
            resolve_references({'source_id':'SRC-1','span_id':appendix['id']},registry)

    def test_review_can_cite_neighboring_text_without_granting_author_approval(self):
        marker='The final condition is an inspection of the hinges.'
        self.source.write_text(fixtures.SOURCE+'\n'+('Background detail.\n'*50)+'\n'+marker+'\n')
        self.recipe['generation_chunk_characters']=500
        self.recipe['profile']='general-navigation'
        self.start()
        runner=Runner(self.run);runner.prepare_source_structure()
        worker=fixtures.Worker()
        def adapter(command,request,**kwargs):
            if request['stage']=='generate' and request['payload']['source']['start']>0:
                return {'schema_version':1,'request_id':request['request_id'],'model':request['model'],'result':{'candidates':[]}}
            response=worker(command,request,**kwargs)
            if request['stage']=='review':
                span=next(s for s in request['payload']['evidence_spans'] if marker in s['text'])
                for decision in response['result']['decisions']:
                    decision['evidence']=[{'source_id':'SRC-1','span_id':span['id']}]
            return response
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=adapter):
            project=runner.root/'working'
            runner.bootstrap(project)
            self.assertEqual(runner.generate(project,0,[]),[])
        chapter=load_language_data(project,'en')['chapters'][0]
        self.assertTrue(chapter['summary'])
        self.assertEqual(chapter['status'],'draft')
