import copy
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import file_hash
from reading_pack_producer.claude_adapter import assess, cli_arguments, cli_schema, cli_input, execute

MODEL = 'synthetic-model-v1'
SCHEMA = {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object',
          'properties': {'schema_version': {'const': 1}, 'model': {'const': MODEL},
                         'request_id': {'type': 'string'}, 'result': {'type': 'object',
                         'properties': {'valid': {'type': 'boolean'}}, 'required': ['valid'],
                         'additionalProperties': False}},
          'required': ['schema_version', 'model', 'request_id', 'result'], 'additionalProperties': False}
RESPONSE = {'schema_version': 1, 'request_id': 'a' * 64, 'model': MODEL, 'result': {'valid': False}}


def events_for(outputs):
    events = []
    for index, value in enumerate(outputs):
        events.extend([
            {'type': 'assistant', 'message': {'model': MODEL, 'content': [
                {'type': 'tool_use', 'id': f'output-{index}', 'name': 'StructuredOutput', 'input': value}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': f'output-{index}', 'is_error': index < len(outputs) - 1}]}}
        ])
    events.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'tool_use',
                   'modelUsage': {MODEL: {'canonicalModel': MODEL}}, 'total_cost_usd': 0.25,
                   'structured_output': outputs[-1], 'result': json.dumps(outputs[-1])})
    return events


class ClaudeAuditTests(unittest.TestCase):
    def test_cli_input_preserves_complete_request_and_shares_pack_prefix(self):
        request = {'job': 'first', 'model': MODEL, 'payload': {'question': 'First question?', 'pack': 'A stable Japanese context: 条件。' * 1000},
                   'prompt': 'Answer using the Pack.', 'request_id': 'a' * 64, 'response_schema': SCHEMA,
                   'schema_version': 1, 'stage': 'answer'}
        other = copy.deepcopy(request)
        other.update(job='second', request_id='b' * 64)
        other['payload']['question'] = 'Second question?'
        first, second = cli_input(request), cli_input(other)
        self.assertEqual(json.loads(first), request)
        self.assertEqual(json.loads(second), other)
        split = first.index(b'"question":')
        self.assertGreater(split, len(request['payload']['pack'].encode('utf-8')))
        self.assertEqual(first[:split], second[:split])
        self.assertEqual(cli_input(request), first)

    def test_native_output_and_verified_invalid_schema_retry(self):
        invalid = copy.deepcopy(RESPONSE)
        invalid['result']['valid_note'] = None
        for outputs in ([RESPONSE], [invalid, RESPONSE], [invalid, invalid, RESPONSE]):
            _, audit = assess(events_for(outputs), 0, SCHEMA, MODEL)
            self.assertTrue(audit['valid'], audit)
            self.assertEqual(audit['native_schema_retries'], len(outputs) - 1)

    def test_valid_judgment_cannot_be_replaced_even_after_error_notification(self):
        accepted = copy.deepcopy(RESPONSE)
        accepted['result']['valid'] = True
        _, audit = assess(events_for([RESPONSE, accepted]), 0, SCHEMA, MODEL)
        self.assertIn('unverified_schema_retry', audit['issues'])

    def test_tool_mismatch_missing_error_reordering_and_foreign_model_fail(self):
        invalid = copy.deepcopy(RESPONSE)
        invalid['result']['extra'] = None
        for corruption in ('operational_tool', 'missing_error', 'missing_result', 'order', 'model', 'output', 'four', 'duplicate', 'invalid_json'):
            events = events_for([invalid, RESPONSE])
            if corruption == 'operational_tool':
                events[0]['message']['content'][0]['name'] = 'Bash'
            elif corruption == 'missing_error':
                events[1]['message']['content'][0]['is_error'] = False
            elif corruption == 'missing_result':
                del events[3]
            elif corruption == 'order':
                events[1], events[2] = events[2], events[1]
            elif corruption == 'model':
                events[-1]['modelUsage'][MODEL]['canonicalModel'] = 'different'
            elif corruption == 'output':
                events[-1]['structured_output'] = {'changed': True}
            elif corruption == 'four':
                events = events_for([invalid, invalid, invalid, RESPONSE])
            elif corruption == 'duplicate':
                events.insert(2, copy.deepcopy(events[1]))
            else:
                events[-1]['result'] = events[-1]['result'][:-1]
            with self.subTest(corruption=corruption):
                self.assertFalse(assess(events, 0, SCHEMA, MODEL)[1]['valid'])

    def test_cli_schema_compatibility_removes_only_declaration(self):
        schema = copy.deepcopy(SCHEMA)
        argv = cli_arguments(Path('/synthetic/claude'), {'model': MODEL, 'response_schema': schema},
                             effort='medium', call_budget=5)
        sent = json.loads(argv[argv.index('--json-schema') + 1])
        self.assertEqual(sent, {k: v for k, v in SCHEMA.items() if k != '$schema'})
        self.assertEqual(schema, SCHEMA)
        self.assertEqual(argv[argv.index('--tools') + 1], '')

    def test_property_dependencies_keep_identical_validation_in_cli_dialect(self):
        from jsonschema import Draft7Validator, Draft202012Validator
        schema = {'type': 'object', 'properties': {'url': {'type': 'string'}, 'checked': {'type': 'boolean'}},
                  'dependentRequired': {'url': ['checked']}, 'additionalProperties': False,
                  'allOf': [{'maxProperties': 2}]}
        adapted = cli_schema(schema)
        self.assertNotIn('dependentRequired', adapted)
        for value in ({}, {'url': 'example'}, {'url': 'example', 'checked': True}, {'checked': False}, {'url': 'x', 'extra': 1}):
            self.assertEqual(Draft202012Validator(schema).is_valid(value), Draft7Validator(adapted).is_valid(value))


class ClaudeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / 'synthetic-cli'
        binary.write_text('Synthetic pinned CLI; subprocess is mocked.')
        self.args = Namespace(cli=binary, cli_sha256=file_hash(binary.read_bytes()), model=MODEL,
            effort='medium', audit_dir=self.root / 'audit', max_calls=3, budget_usd=10.0,
            call_budget_usd=5.0, timeout_seconds=10)
        self.request = {'model': MODEL, 'request_id': RESPONSE['request_id'], 'response_schema': SCHEMA}
        self.raw = json.dumps(self.request).encode()

    def fake_process(self, argv, **kwargs):
        kwargs['stdout'].write(('\n'.join(json.dumps(e) for e in events_for([RESPONSE]))).encode())
        kwargs['stdout'].flush()
        class Process:
            pid = 123456
            returncode = 0
            def communicate(self, raw, timeout):
                pass
        return Process()

    def test_exact_request_replays_without_second_send_and_detects_mutation(self):
        with patch('reading_pack_producer.claude_adapter.subprocess.Popen', side_effect=self.fake_process) as send:
            self.assertEqual(execute(self.request, self.raw, self.args), RESPONSE)
            self.assertEqual(execute(self.request, self.raw, self.args), RESPONSE)
            self.assertEqual(send.call_count, 1)

            directory = self.args.audit_dir / self.request['request_id']
            self.assertEqual((directory / 'input.json').read_bytes(), self.raw)
            sent = (directory / 'cli-input.json').read_bytes()
            self.assertEqual(json.loads(sent), self.request)
            self.assertEqual(json.loads((directory / 'request.json').read_text())['cli_input_sha256'], file_hash(sent))
            response = self.args.audit_dir / self.request['request_id'] / 'response.json'
            response.write_text('{}')
            with self.assertRaisesRegex(ReadingPackError, 'response changed'):
                execute(self.request, self.raw, self.args)
            self.assertEqual(send.call_count, 1)

    def test_pipeline_allowance_reaches_the_provider_cli_without_enlarging_adapter_limits(self):
        request={**self.request,'execution_budget':{'call_allowance_usd':.5}}
        with patch('reading_pack_producer.claude_adapter.subprocess.Popen',side_effect=self.fake_process) as send:
            execute(request,json.dumps(request).encode(),self.args)
        argv=send.call_args.args[0]
        self.assertEqual(float(argv[argv.index('--max-budget-usd')+1]),.5)
        self.assertEqual(self.args.call_budget_usd,5)

    def test_unknown_attempts_reserve_call_budget_and_configuration_cannot_change(self):
        self.args.audit_dir.mkdir()
        for name in ('unknown-1', 'unknown-2'):
            path = self.args.audit_dir / name
            path.mkdir()
            (path / 'input.json').write_text('{}')
        with patch('reading_pack_producer.claude_adapter.subprocess.Popen') as send:
            with self.assertRaisesRegex(ReadingPackError, 'budget exhausted'):
                execute(self.request, self.raw, self.args)
            self.assertFalse(send.called)
            self.args.budget_usd = 100
            with self.assertRaisesRegex(ReadingPackError, 'configuration changed'):
                execute(self.request, self.raw, self.args)

    def test_saved_invalid_judgment_is_not_resampled(self):
        def broken(argv, **kwargs):
            process = self.fake_process(argv, **kwargs)
            process.returncode = 1
            return process
        with patch('reading_pack_producer.claude_adapter.subprocess.Popen', side_effect=broken) as send:
            for _ in range(2):
                with self.assertRaisesRegex(ReadingPackError, 'audit failed'):
                    execute(self.request, self.raw, self.args)
            self.assertEqual(send.call_count, 1)


class SalvageTests(unittest.TestCase):
    """A final attempt rejected only for undeclared keys is accepted after recorded pruning."""

    def rejected_events(self, output, subtype='error_max_budget_usd'):
        return [
            {'type': 'assistant', 'message': {'model': MODEL, 'content': [
                {'type': 'tool_use', 'id': 'output-0', 'name': 'StructuredOutput', 'input': output}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'output-0', 'is_error': True}]}},
            {'type': 'result', 'subtype': subtype, 'is_error': True, 'stop_reason': 'tool_use',
             'modelUsage': {MODEL: {'canonicalModel': MODEL}}, 'total_cost_usd': 1.5, 'result': ''}]

    def test_undeclared_keys_are_pruned_and_recorded(self):
        noisy = copy.deepcopy(RESPONSE)
        noisy['result']['score_note'] = 'ignored'
        result, audit = assess(self.rejected_events(noisy), 1, SCHEMA, MODEL)
        self.assertTrue(audit['valid'], audit)
        self.assertEqual(audit['salvaged_additional_properties'], ['/result/score_note'])
        self.assertEqual(result['structured_output'], RESPONSE)
        self.assertEqual(audit['total_cost_usd'], 1.5)

    def test_invalid_declared_values_are_never_salvaged(self):
        wrong = copy.deepcopy(RESPONSE)
        wrong['result']['score_note'] = 'ignored'
        wrong['result']['valid'] = 'yes'
        _, audit = assess(self.rejected_events(wrong), 1, SCHEMA, MODEL)
        self.assertFalse(audit['valid'])
        self.assertNotIn('salvaged_additional_properties', audit)
        clean = copy.deepcopy(RESPONSE)
        _, audit = assess(self.rejected_events(clean), 1, SCHEMA, MODEL)
        self.assertFalse(audit['valid'])  # nothing to prune: the rejection stands
