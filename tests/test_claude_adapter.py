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
    def test_referenced_record_extra_key_recovery_preserves_declared_values(self):
        schema=copy.deepcopy(SCHEMA)
        schema['$defs']={'finding':{'type':'object','properties':{'valid':{'type':'boolean'}},
            'patternProperties':{'^note_':{'type':'string'}},'required':['valid'],'additionalProperties':False}}
        schema['properties']['result']={'$ref':'#/$defs/finding'}
        value=copy.deepcopy(RESPONSE);value['result'].update(note_kept='evidence',verdter='')
        events=events_for([value]);before=copy.deepcopy(events)
        result,audit=assess(events,0,schema,MODEL)
        self.assertTrue(audit['valid'])
        self.assertEqual(audit['salvaged_additional_properties'],['/result/verdter'])
        self.assertEqual(result['structured_output']['result'],{'valid':False,'note_kept':'evidence'})
        self.assertEqual(events,before)
        value['result']['valid']='not a boolean'
        _,audit=assess(events_for([value]),0,schema,MODEL)
        self.assertFalse(audit['valid'])
        self.assertNotIn('salvaged_additional_properties',audit)

    def test_unambiguous_array_wrapper_recovery_preserves_values_and_raw_events(self):
        schema=copy.deepcopy(SCHEMA)
        schema['properties']['result']={'type':'object','properties':{'limitations':{'type':'array','items':{'type':'string'}}},'required':['limitations'],'additionalProperties':False}
        response=copy.deepcopy(RESPONSE);response['result']={'limitations':{'items':['First.', 'Second.']}}
        events=events_for([response]);before=copy.deepcopy(events)
        result,audit=assess(events,0,schema,MODEL)
        self.assertTrue(audit['valid'])
        self.assertFalse(audit['native_schema_valid'])
        self.assertEqual(audit['normalized_array_wrappers'],['/result/limitations'])
        self.assertEqual(result['structured_output']['result']['limitations'],['First.', 'Second.'])
        self.assertEqual(events,before)

    def test_array_wrapper_recovery_rejects_extra_keys_and_invalid_elements(self):
        schema=copy.deepcopy(SCHEMA)
        schema['properties']['result']={'type':'object','properties':{'limitations':{'type':'array','items':{'type':'string'}}},'required':['limitations'],'additionalProperties':False}
        for wrapped in ({'items':['a'],'note':'extra'}, {'items':[False]}, {'items':'a'}):
            response=copy.deepcopy(RESPONSE);response['result']={'limitations':wrapped}
            _,audit=assess(events_for([response]),0,schema,MODEL)
            self.assertFalse(audit['valid']);self.assertNotIn('normalized_array_wrappers',audit)

    def test_array_wrapper_recovery_cannot_bypass_identity_or_execution_failure(self):
        schema=copy.deepcopy(SCHEMA)
        schema['properties']['result']={'type':'object','properties':{'limitations':{'type':'array','items':{'type':'string'}}},'required':['limitations'],'additionalProperties':False}
        response=copy.deepcopy(RESPONSE);response['result']={'limitations':{'items':['a']}}
        for code,model in ((1,MODEL),(0,'foreign-model')):
            _,audit=assess(events_for([response]),code,schema,model)
            self.assertFalse(audit['valid']);self.assertNotIn('normalized_array_wrappers',audit)

    def test_indexed_array_recovery_preserves_explicit_numeric_order(self):
        schema=copy.deepcopy(SCHEMA)
        schema['properties']['result']={'type':'object','properties':{'limitations':{'type':'array','items':{'type':'string'}}},'required':['limitations'],'additionalProperties':False}
        response=copy.deepcopy(RESPONSE);response['result']={'limitations':{'1':'Second.','0':'First.'}}
        result,audit=assess(events_for([response]),0,schema,MODEL)
        self.assertTrue(audit['valid']);self.assertFalse(audit['native_schema_valid'])
        self.assertEqual(result['structured_output']['result']['limitations'],['First.','Second.'])
        for value in ({'1':'a'}, {'0':'a','2':'b'}, {'00':'a'}, {}, {'0':'a','meta':'b'}):
            response['result']['limitations']=value
            _,audit=assess(events_for([response]),0,schema,MODEL)
            self.assertFalse(audit['valid'])

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

    def test_cli_schema_projection_preserves_envelope_and_declared_fields(self):
        schema = copy.deepcopy(SCHEMA)
        argv = cli_arguments(Path('/synthetic/claude'), {'model': MODEL, 'response_schema': schema},
                             effort='medium', call_budget=5)
        sent = json.loads(argv[argv.index('--json-schema') + 1])
        expected = {k: copy.deepcopy(v) for k, v in SCHEMA.items() if k != '$schema'}
        expected['properties']['result'] = {'type': 'object', 'additionalProperties': True}
        self.assertEqual(sent, expected)
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


class ResultTransportTests(unittest.TestCase):
    def test_native_transport_is_open_but_controller_schema_stays_closed(self):
        from reading_pack_producer.delivery_fresh import full_schema
        from reading_pack_producer.delivery_contract import obj
        schema = obj({'result': full_schema({'id': 'CH-01', 'sections': [{'id': 'S01-01'}]})})
        before = copy.deepcopy(schema)
        args = cli_arguments(Path('/bin/false'), {'model': MODEL, 'stage': 'generate', 'response_schema': schema}, effort='low', call_budget=1)
        native = json.loads(args[args.index('--json-schema')+1])
        self.assertFalse(native['additionalProperties'])
        self.assertEqual(native['properties']['result'], {'type': 'object', 'additionalProperties': True})
        self.assertEqual(json.loads(cli_input({'model': MODEL, 'response_schema': schema}))['response_schema'], before)
        self.assertEqual(schema, before)

    def test_evaluation_transport_is_open_with_original_constraints_preserved(self):
        before = copy.deepcopy(SCHEMA)
        args = cli_arguments(Path('/bin/false'), {'model': MODEL, 'stage': 'source_support', 'response_schema': SCHEMA}, effort='low', call_budget=1)
        native = json.loads(args[args.index('--json-schema')+1])
        self.assertFalse(native['additionalProperties'])
        self.assertTrue(native['properties']['result']['additionalProperties'])
        self.assertEqual(native['properties']['result']['type'], 'object')
        self.assertEqual(json.loads(cli_input({'model': MODEL, 'response_schema': SCHEMA}))['response_schema'], before)
        self.assertEqual(SCHEMA, before)

    def test_extra_keys_pruned_but_invalid_declared_values_rejected(self):
        noisy = copy.deepcopy(RESPONSE);noisy['result']['decoder_dummy'] = ''
        parsed, audit = assess(events_for([noisy]), 0, SCHEMA, MODEL)
        self.assertTrue(audit['valid'], audit)
        self.assertEqual(parsed['structured_output'], RESPONSE)
        self.assertEqual(audit['salvaged_additional_properties'], ['/result/decoder_dummy'])
        noisy['result']['valid'] = 'wrong type'
        self.assertFalse(assess(events_for([noisy]), 0, SCHEMA, MODEL)[1]['valid'])


class StoppedOutputTests(unittest.TestCase):
    def stopped(self, response=RESPONSE):
        events=events_for([copy.deepcopy(response)])
        events[-1].update(subtype='error_max_budget_usd',is_error=True)
        events[-1].pop('structured_output');events[-1].pop('result')
        del events[1]  # CLI stops before the tool result is acknowledged.
        return events

    def test_budget_stop_recovers_exact_complete_output_without_call_success(self):
        result,audit=assess(self.stopped(),1,SCHEMA,MODEL)
        self.assertTrue(audit['valid'],audit)
        self.assertEqual(result['structured_output'],RESPONSE)
        self.assertFalse(audit['native_execution_completed'])
        self.assertTrue(result['is_error'])
        self.assertFalse(audit['recovery']['content_changed'])
        self.assertEqual(audit['total_cost_usd'],.25)

    def test_timeout_complete_output_recovers_but_cost_remains_unknown(self):
        result,audit=assess(self.stopped()[:-1],-9,SCHEMA,MODEL,interrupted=True)
        self.assertTrue(audit['valid'],audit)
        self.assertIsNone(audit['total_cost_usd'])
        self.assertEqual(result['structured_output'],RESPONSE)
        self.assertFalse(assess(self.stopped()[:-1],-9,SCHEMA,MODEL)[1]['valid'])

    def test_recovery_refuses_invalid_ambiguous_foreign_or_rejected_output(self):
        for kind in ('malformed','extra','two','model','wrong_request','denied','tool','rejected','duplicate_result'):
            with self.subTest(kind=kind):
                events=self.stopped();schema=copy.deepcopy(SCHEMA)
                schema['properties']['request_id']={'const':RESPONSE['request_id']}
                call=events[0]['message']['content'][0]
                if kind=='malformed':call['input']['result']='{"valid":true'
                elif kind=='extra':call['input']['result']['extra']='x'
                elif kind=='two':events.insert(1,copy.deepcopy(events[0]))
                elif kind=='model':events[-1]['modelUsage'][MODEL]['canonicalModel']='other'
                elif kind=='wrong_request':call['input']['request_id']='b'*64
                elif kind=='denied':events[-1]['permission_denials']=['denied']
                elif kind=='tool':call['name']='Bash'
                elif kind=='duplicate_result':events.append(copy.deepcopy(events[-1]))
                else:events.insert(1,{'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'output-0','is_error':True}]}})
                self.assertFalse(assess(events,1,schema,MODEL)[1]['valid'])
