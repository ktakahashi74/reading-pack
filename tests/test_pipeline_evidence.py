import copy
import unittest
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.schema_validation import schema_document
from reading_pack_producer.pipeline import Runner, _unseal
from reading_pack_producer.pipeline_evidence import benchmark_contract, resolve_references, source_spans, worker_payload
from tests import test_pipeline as fixture
from tests.test_pipeline import Worker


class SpanTests(unittest.TestCase):
    def chunk(self, text):
        return {'source_id': 'SRC-1', 'source_sha256': 'a' * 64, 'text_start': 73, 'text': text}

    def test_unicode_offsets_and_exact_reconstruction(self):
        for text in ('（全角）と(半角)は原文のまま。\n' * 120, 'x' * 961, '短い本文。'):
            chunk = self.chunk(text)
            spans = source_spans(chunk)
            self.assertEqual(''.join(s['text'] for s in spans), text)
            self.assertTrue(all(len(s['text']) <= 500 for s in spans))
            for span in spans:
                self.assertEqual(text[span['start'] - 73:span['end'] - 73], span['text'])
            self.assertEqual(spans, source_spans(chunk))
            other = source_spans({**chunk, 'source_sha256': 'b' * 64})
            self.assertNotEqual(spans[0]['id'], other[0]['id'])

    def test_cross_document_and_out_of_scope_ids_are_rejected(self):
        span = source_spans(self.chunk('原文の十分な長さの文章。'))[0]
        for reference in ({'source_id': 'SRC-2', 'span_id': span['id']},
                          {'span_id': 'SPAN-' + '0' * 24}):
            with self.assertRaises(ReadingPackError):
                resolve_references(reference, {span['id']: span})

    def test_source_sent_once_and_input_unchanged(self):
        payload = {'source': self.chunk('Exact source evidence.'), 'language': 'en'}
        before = copy.deepcopy(payload)
        sent, registry = worker_payload(payload)
        self.assertEqual(payload, before)
        self.assertNotIn('text', sent['source'])
        self.assertEqual(len(sent['evidence_spans']), 1)
        self.assertEqual(set(sent['source']['span_ids']), set(registry))

    def test_contract_is_derived_from_schema(self):
        schema = copy.deepcopy(schema_document('pipeline-worker.schema.json')['$defs']['benchmark'])
        self.assertIn('2 to 4 atomic requirements', benchmark_contract(schema))
        schema['properties']['development']['items']['properties']['requirements']['maxItems'] = 3
        self.assertIn('2 to 3 atomic requirements', benchmark_contract(schema))


class BenchmarkRepairTests(unittest.TestCase):
    # Reuse the setup helpers without inheriting and rerunning unrelated pipeline tests.
    setUp = fixture.PipelineTests.setUp
    start = fixture.PipelineTests.start
    execute = fixture.PipelineTests.execute

    def test_schema_defect_and_semantic_defect_are_repaired_before_freezing(self):
        self.start()
        worker = Worker()
        attempts = 0
        reviews = 0
        def adapter(command, request, **kwargs):
            nonlocal attempts, reviews
            response = worker(command, request, **kwargs)
            if request['stage'] == 'benchmark':
                attempts += 1
                self.assertFalse((self.run / 'benchmark.json').exists())
                if attempts == 1:
                    response['result']['development'][0]['requirements'] += ['Third.', 'Fourth.', 'Fifth.']
                else:
                    self.assertIsNotNone(request['payload']['feedback'])
            if request['stage'] == 'benchmark_review':
                reviews += 1
                self.assertTrue(request['payload']['evidence_verified'])
                ref = request['payload']['cases']['development'][0]['evidence'][0]
                unit = next(s for s in request['payload']['evidence_spans'] if s['id'] == ref['span_id'])
                self.assertEqual(ref['quote'], unit['text'])
                if reviews == 1:
                    response['result'] = {'valid': False, 'reason': 'The requirements overlap; separate the conditions.'}
            return response
        result = self.execute(adapter)
        self.assertEqual(result['state'], 'awaiting_author_approval', result)
        self.assertEqual((attempts, reviews), (3, 2))
        audit = _unseal(self.run / 'benchmark-reviews.json')['attempts']
        self.assertEqual([a['review']['valid'] for a in audit], [False, False, True])
        saved = (self.run / 'benchmark.json').read_bytes()
        count = len(worker.requests)
        self.execute(adapter)
        self.assertEqual(count, len(worker.requests))
        self.assertEqual(saved, (self.run / 'benchmark.json').read_bytes())

    def test_invalid_span_is_repaired_without_sending_it_to_a_judge(self):
        self.start()
        worker = Worker()
        def adapter(command, request, **kwargs):
            result = worker(command, request, **kwargs)
            if request['stage'] == 'benchmark' and request['payload']['feedback'] is None:
                result['result']['development'][0]['evidence'][0]['span_id'] = 'SPAN-' + '0' * 24
            return result
        self.assertEqual(self.execute(adapter)['state'], 'awaiting_author_approval')
        self.assertEqual(sum(r['stage'] == 'benchmark_review' for r in worker.requests), 1)

    def test_repeated_failures_stop_at_fixed_limit_without_freezing_or_generating(self):
        self.start()
        worker = Worker()
        def adapter(command, request, **kwargs):
            result = worker(command, request, **kwargs)
            if request['stage'] == 'benchmark_review':
                result['result'] = {'valid': False, 'reason': 'Unsupported requirement.'}
            return result
        self.assertEqual(self.execute(adapter)['state'], 'failed_quality')
        self.assertEqual(sum(r['stage'] == 'benchmark' for r in worker.requests), self.recipe['max_rounds'])
        self.assertFalse((self.run / 'benchmark.json').exists())
        self.assertFalse(any(r['stage'] == 'generate' for r in worker.requests))

    def test_interruption_replays_prior_repair_attempts(self):
        self.start()
        worker = Worker()
        def adapter(command, request, **kwargs):
            if request['stage'] == 'benchmark' and request['payload']['feedback']:
                raise KeyboardInterrupt
            result = worker(command, request, **kwargs)
            if request['stage'] == 'benchmark_review':
                result['result'] = {'valid': False, 'reason': 'Separate requirements.'}
            return result
        from reading_pack_producer.pipeline import resume_pipeline
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=adapter):
            with self.assertRaises(KeyboardInterrupt):
                resume_pipeline(self.run)
        old_ids = {r['request_id'] for r in worker.requests}
        worker.requests.clear()
        with patch('reading_pack_producer.pipeline.run_local_adapter', side_effect=worker):
            result = resume_pipeline(self.run, retry_inflight=True)
        self.assertEqual(result['state'], 'awaiting_author_approval')
        self.assertFalse(old_ids & {r['request_id'] for r in worker.requests})
