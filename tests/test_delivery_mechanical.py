"""Offline regressions for source bindings, evidence, replay and whole-chain costs."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.rendering import _location_rule
from reading_pack_producer.delivery import prepare, prepare_successor, run
from reading_pack_producer.delivery_mechanical import freeze_inputs, quote_check
from reading_pack_producer.pipeline import _unseal
from tests import test_pipeline_delivery as helpers
from tests.support import read_json, cli


class QuoteTests(unittest.TestCase):
    def test_whitespace_recovery_is_unique_range_bound_and_preserves_original(self):
        text = 'A B. xx AB. outside'
        item = quote_check(text, 'AB', 0, 4)
        self.assertEqual(item['status'], 'whitespace_restored')
        self.assertEqual(item['original_quote'], 'AB')
        self.assertEqual(item['effective_quote'], 'A B')
        self.assertEqual(text[item['start']:item['end']], 'A B')
        self.assertFalse(item['exact_match'])
        self.assertEqual(quote_check('A B A\nB', 'AB', 0, 7)['status'], 'ambiguous_whitespace_match')
        self.assertEqual(quote_check(text, 'outside', 0, 4)['status'], 'exact_outside_declared_range')
        self.assertEqual(quote_check(text, 'AC', 0, len(text))['status'], 'unmatched')
        self.assertEqual(quote_check(text, ' ', 0, 0)['status'], 'unmatched')
        self.assertFalse(quote_check(text, '', 0, len(text))['restored'])


class MechanicalDeliveryTests(unittest.TestCase):
    setUp = helpers.DeliveryTests.setUp
    recipe = helpers.DeliveryTests.recipe

    def inputs(self):
        return {'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                'section_pages': [{'section_id': 'S01-01', 'printed_page': 10}],
                'names': [{'entity_id': 'PERSON-1', 'canonical': 'Canonical Person',
                           'aliases': ['Synthetic evidence for protocol tests.']}]}

    def test_pages_names_evidence_and_evaluation_use_same_canonical_records(self):
        self.source.write_text(self.source.read_text() + '\nCanonical Person\n')
        inputs = self.inputs()
        prepare(self.run, self.source, self.recipe('fresh_modules'), title='Test', author='A',
                chapter_level=1, mechanical_inputs=inputs)
        inputs['section_pages'][0]['printed_page'] = 999  # Caller changes cannot affect the plan.
        self.assertEqual(run(self.run)['state'], 'delivered')
        q = read_json(self.run/'quality-report.json')
        self.assertFalse(any(i['severity']=='error' for i in q['mechanical']['project_issues']))
        data = read_json(self.run/'project/data/pack.en.json')
        self.assertEqual(data['names'][0]['name'], 'Canonical Person')
        self.assertEqual(data['names'][0]['aliases'], ['Synthetic evidence for protocol tests.'])
        self.assertEqual(data['names'][0]['status'], 'draft')
        self.assertEqual(data['section_pages'][0]['printed_page'], 10)
        pack = (self.run/'reading-pack.en.md').read_text()
        self.assertIn('section start page=10', pack)
        self.assertIn('Section starts are not exact record pages', pack)
        state = _unseal(self.run/'delivery-state.json')
        ev = _unseal(self.run/'jobs'/(state['jobs']['evaluate/CH-01']['request_id']+'.json'))
        name = next(x for x in ev['request']['payload']['records'] if x['id'].startswith('NAME-'))
        self.assertEqual(name['name'], 'Canonical Person')
        gen = _unseal(self.run/'jobs'/(state['jobs']['generate/CH-01']['request_id']+'.json'))
        self.assertEqual(gen['response']['result']['modules']['names']['items'][0]['name'], 'Synthetic evidence for protocol tests.')
        self.assertEqual(q['mechanical']['name_normalization']['applied'][0]['canonical'], 'Canonical Person')
        global_request = _unseal(self.run/'jobs'/(state['jobs']['evaluate/global']['request_id']+'.json'))
        self.assertEqual(global_request['request']['payload']['pack'], pack)
        self.assertIn('section start page=10', global_request['request']['payload']['pack'])

    def test_bad_source_section_or_conflicting_alias_rejected_before_any_send(self):
        self.source.write_text(self.source.read_text() + '\nCanonical Person\n')
        for kind in ('hash', 'section', 'duplicate_section', 'alias', 'missing_canonical'):
            with self.subTest(kind=kind):
                inputs = self.inputs()
                if kind=='hash': inputs['source_sha256']='0'*64
                elif kind=='section': inputs['section_pages'][0]['section_id']='wrong'
                elif kind=='duplicate_section': inputs['section_pages']*=2
                elif kind=='alias': inputs['names']*=2
                else: inputs['names'][0]['canonical']='Absent Person'
                with self.assertRaises(ReadingPackError):
                    prepare(self.run, self.source, self.recipe(), chapter_level=1, mechanical_inputs=inputs)
                self.assertFalse(self.run.exists())
                self.assertFalse(self.log.exists())

    def test_cli_wires_source_bound_mechanical_inputs(self):
        self.source.write_text(self.source.read_text() + '\nCanonical Person\n')
        inputs=self.root/'inputs.json'; inputs.write_text(json.dumps(self.inputs()))
        recipe=self.root/'recipe.json'; recipe.write_text(json.dumps(self.recipe()))
        result=cli('pipeline','deliver',str(self.source),'--run',str(self.run),'--recipe',str(recipe),
                   '--chapter-level','1','--mechanical-inputs',str(inputs),'--prepare-only')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(_unseal(self.run/'delivery-plan.json')['mechanical_inputs'],self.inputs())
        self.assertFalse(self.log.exists())

    def test_no_pages_means_no_promise_in_both_languages(self):
        for lang in ('en','ja'):
            rule = _location_rule(lang, {'chapters': []})
            self.assertIn('not included' if lang=='en' else '未収録', rule)

    def test_evaluation_successor_never_renders_and_preserves_hashes(self):
        prepare(self.run,self.source,self.recipe('evaluation_error'),chapter_level=1)
        run(self.run)
        before=(self.run/'reading-pack.en.md').read_bytes()
        next_run=self.root/'next';v=self.recipe();v['cumulative_cost_limit_usd']=10
        prepare_successor(next_run,self.run,v)
        with patch('reading_pack_producer.delivery._render',side_effect=AssertionError('must not render')):
            self.assertEqual(run(next_run)['state'],'delivered')
        self.assertEqual(before,(next_run/'reading-pack.en.md').read_bytes())
        self.assertEqual((self.run/'project/data/pack.en.json').read_bytes(),(next_run/'project/data/pack.en.json').read_bytes())
        q=read_json(next_run/'quality-report.json')
        self.assertEqual(q['resources']['cost_accounting']['chain_calls_started'],5)
        self.assertEqual(q['resources']['cost_accounting']['chain_unknown_cost_calls'],5)
        self.assertEqual(q['resources']['cost_accounting']['booked_cumulative_usd'],'5')

    def test_changed_frozen_pack_stops_before_evaluation(self):
        prepare(self.run,self.source,self.recipe('evaluation_error'),chapter_level=1);run(self.run)
        next_run=self.root/'next';v=self.recipe();v['cumulative_cost_limit_usd']=10
        prepare_successor(next_run,self.run,v)
        pack=next_run/'reading-pack.en.md';pack.write_text(pack.read_text()+'changed')
        calls=self.log.read_text()
        with self.assertRaisesRegex(ReadingPackError,'frozen evaluation artifact'):
            run(next_run)
        self.assertEqual(self.log.read_text(),calls)

    def test_successor_admission_counts_failed_known_cost_and_unknown_reserve(self):
        v=self.recipe('evaluation_error');v['unknown_call_reserve_usd']=1.5
        v['max_cost_usd']=v['cumulative_cost_limit_usd']=8
        prepare(self.run,self.source,v,chapter_level=1)
        receipts=[{'actual_cost_usd':.2}, {'actual_cost_usd':.3}, {'actual_cost_usd':None}]
        with patch('reading_pack_producer.delivery.adapter_receipt',side_effect=receipts):run(self.run)
        q=read_json(self.run/'quality-report.json')['resources']['cost_accounting']
        self.assertEqual(q['chain_known_usd'],'0.5')
        self.assertEqual(q['chain_unknown_reserve_usd'],'1.5')
        next_run=self.root/'next';v=self.recipe()
        with self.assertRaisesRegex(ReadingPackError,'cumulative'):
            prepare_successor(next_run,self.run,v)
        self.assertFalse(next_run.exists())
        v['cumulative_cost_limit_usd']=8
        prepare_successor(next_run,self.run,v)
        self.assertEqual(_unseal(next_run/'delivery-plan.json')['recipe']['prior_cost_usd'],2)
        with patch('reading_pack_producer.delivery.adapter_receipt',return_value={'actual_cost_usd':.25}):run(next_run)
        q=read_json(next_run/'quality-report.json')['resources']['cost_accounting']
        self.assertEqual(q['chain_known_usd'],'1.00')
        self.assertEqual(q['chain_unknown_reserve_usd'],'1.5')
        self.assertEqual(q['chain_calls_started'],5)
        self.assertEqual(q['booked_cumulative_usd'],'2.50')

    def test_recovered_output_with_unknown_cli_cost_still_stops_dispatch(self):
        prepare(self.run,self.source,self.recipe(),chapter_level=1)
        with patch('reading_pack_producer.delivery.adapter_receipt',return_value={
            'actual_cost_usd':None,'receipt_valid':True,'usage_origin':'claude-cli',
            'recovery':{'method':'unchanged_schema_valid_structured_output'}}):run(self.run)
        self.assertEqual(self.log.read_text().splitlines(),['generate/CH-01'])
        q=read_json(self.run/'quality-report.json')
        self.assertEqual(q['resources']['unknown_cost_calls'],1)
        self.assertEqual(q['resources']['recovered_output_jobs'],['generate/CH-01'])

    def test_additional_prior_reserve_survives_two_successors_without_double_counting(self):
        prepare(self.run,self.source,self.recipe('evaluation_error'),chapter_level=1);run(self.run)
        second=self.root/'second';v=self.recipe('evaluation_error')
        v.update(prior_cost_usd=10,cumulative_cost_limit_usd=30)
        prepare_successor(second,self.run,v);run(second)
        third=self.root/'third';v=self.recipe();v['cumulative_cost_limit_usd']=30
        prepare_successor(third,second,v)
        self.assertEqual(_unseal(third/'delivery-plan.json')['recipe']['prior_cost_usd'],12)
        run(third)
        c=read_json(third/'quality-report.json')['resources']['cost_accounting']
        self.assertEqual(c['chain_calls_started'],7)
        self.assertEqual(c['additional_prior_budget_usd'],'7.0')
        self.assertEqual(c['booked_cumulative_usd'],'14.0')
