from __future__ import annotations

import copy
import json
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import _seal, _unseal, Runner, resume_pipeline
from reading_pack_producer.pipeline_qualification import (
    assess_qualification, attach_qualification, input_identity, register_trial, validate_suite, workflow_signature, wilson_lower,
)
from tests import test_pipeline as fixtures
from tests.test_pipeline_resources import envelope


class QualificationTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def prepared(self):
        self.recipe.update(max_rounds=2,max_attempts=1,timeout_seconds=1,max_calls=40,
                           operating_envelope=envelope())
        self.start()
        manifest=_unseal(self.run/'manifest.json')
        self.suite={'schema_version':1,'workflow_signature':workflow_signature(manifest),
            'repetitions':2,'minimum_distinct_books':3,'minimum_defect_classes':3,
            'minimum_completion_rate':1,
            'cases':[{'id':'clean','book_id':'orchard','input_identity':input_identity(manifest),
                      'expectation':'complete','defect_class':'','record_ids':[]}]}
        self.suite_path=self.root/'suite.json';self.suite_path.write_text(json.dumps(self.suite))

    def test_preregistration_rejects_changed_source_and_posthoc_registration(self):
        self.prepared()
        changed=copy.deepcopy(self.suite);changed['cases'][0]['input_identity']='other'
        wrong=self.root/'wrong.json';wrong.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ReadingPackError,'input identity'):
            register_trial(self.run,wrong,'clean',0)
        self.execute(fixtures.Worker())
        with self.assertRaisesRegex(ReadingPackError,'before any worker call'):
            register_trial(self.run,self.suite_path,'clean',0)

    def test_fully_passing_synthetic_pipeline_is_not_real_quality_qualification(self):
        self.prepared();register_trial(self.run,self.suite_path,'clean',0)
        result=self.execute(fixtures.Worker())
        self.assertEqual(result['state'],'awaiting_author_approval')
        report=assess_qualification(self.suite,[self.run])
        self.assertFalse(report['qualified'])
        self.assertTrue(report['trials'][0]['complete'])
        self.assertFalse(report['trials'][0]['fresh_live_measurement'])
        self.assertTrue(any('incomplete' in r for r in report['reasons']))
        self.assertEqual(report['supported_input_modes'],{})

    def test_duplicate_trial_and_engine_changes_cannot_produce_a_certificate(self):
        self.prepared();register_trial(self.run,self.suite_path,'clean',0)
        self.execute(fixtures.Worker())
        with self.assertRaisesRegex(ReadingPackError,'duplicate'):
            assess_qualification(self.suite,[self.run,self.run])
        manifest=_unseal(self.run/'manifest.json');manifest['engine_sha256']='new-engine'
        _seal(self.run/'manifest.json',manifest)
        with self.assertRaisesRegex(ReadingPackError,'manifest changed|implementation changed'):
            assess_qualification(self.suite,[self.run])

    def test_operational_failure_is_retained_and_not_counted_as_defect_detection(self):
        self.prepared();self.suite['cases'][0].update(expectation='detect-defect',defect_class='attribution',record_ids=['CH-01'])
        self.suite_path.write_text(json.dumps(self.suite));register_trial(self.run,self.suite_path,'clean',0)
        def unavailable(*args,**kwargs):raise ReadingPackError('simulated unavailable provider')
        result=self.execute(unavailable)
        self.assertEqual(result['state'],'blocked_execution')
        report=assess_qualification(self.suite,[self.run])
        self.assertFalse(report['trials'][0]['defect_detected'])
        self.assertFalse(report['qualified'])
        self.assertEqual(report['defect_detection_rate'],0)

    def test_no_or_too_few_trials_cannot_establish_the_requested_reliability(self):
        self.prepared()
        report=assess_qualification(self.suite,[])
        self.assertFalse(report['qualified'])
        self.assertIsNone(report['completion_rate'])
        self.assertEqual(wilson_lower(0,0),0)
        self.assertLess(wilson_lower(3,3),.75)
        self.assertGreater(wilson_lower(9,9),.75)
        self.assertLess(wilson_lower(8,9),wilson_lower(9,9))

    def test_profile_model_effort_and_limits_are_part_of_workflow_identity(self):
        self.prepared();original=_unseal(self.run/'manifest.json')
        signature=workflow_signature(original)
        equivalent=copy.deepcopy(original);equivalent['recipe']['operating_envelope']['mode']='production'
        self.assertEqual(workflow_signature(equivalent),signature)
        for change in ('profile','model','allowance','questions'):
            altered=copy.deepcopy(original)
            if change=='profile':altered['recipe']['profile']='academic-argument'
            elif change=='model':altered['recipe']['workers']['reader']['model']='another-model'
            elif change=='allowance':altered['recipe']['operating_envelope']['max_cost_usd']=100
            else:altered['recipe']['benchmark_chunk_limit']=4
            self.assertNotEqual(workflow_signature(altered),signature)

    def test_restart_cannot_be_registered_as_a_fresh_trial(self):
        from reading_pack_producer.pipeline_reuse import restart_pipeline
        self.prepared();new=self.root/'new';restart_pipeline(self.run,new,self.recipe)
        with self.assertRaisesRegex(ReadingPackError,'start fresh'):
            register_trial(new,self.suite_path,'clean',0)

    def test_complete_certificate_path_and_production_scope_with_simulated_receipts(self):
        # Exercise the admission protocol, not empirical model quality. Every
        # worker and usage receipt below is explicitly simulated and temporary.
        self.recipe.update(max_rounds=1,max_attempts=1,timeout_seconds=1,max_calls=40,
                           operating_envelope=envelope())
        cases=[];runs=[]
        classes=['missing_qualifier','misattributed','unsupported_absence']
        for index in range(5):
            source=self.root/f'book-{index}.md'
            source.write_text(fixtures.SOURCE + f'\nCatalogue entry {index}.\n')
            for repetition in range(2):
                self.run=self.root/f'trial-{index}-{repetition}';self.source=source
                for role,worker in self.recipe['workers'].items():
                    worker['command']=[f'test-{role}','--audit-dir',str(self.root/f'raw-{index}-{repetition}')]
                self.start()
                manifest=_unseal(self.run/'manifest.json')
                if repetition==0:
                    cases.append({'id':str(index),'book_id':str(index),
                        'input_identity':input_identity(manifest),
                        'expectation':'complete' if index<2 else 'detect-defect',
                        'defect_class':'' if index<2 else classes[index-2],
                        'record_ids':[] if index<2 else ['CH-01']})
                runs.append((self.run,index,repetition))
        suite={'schema_version':1,'workflow_signature':workflow_signature(manifest),'repetitions':2,
               'minimum_distinct_books':2,'minimum_defect_classes':3,'minimum_completion_rate':1,'cases':cases}
        suite_path=self.root/'cohort.json';suite_path.write_text(json.dumps(suite))
        receipts=self.root/'receipts'
        def simulated_receipt(command,request_id):
            path=receipts/request_id/'audit.json';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('{"simulated_for_unit_test":true}')
            return {'actual_cost_usd':.01,'receipt_path':str(path),
                'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'receipt_valid':True}
        with patch('reading_pack_producer.pipeline.adapter_receipt',side_effect=simulated_receipt), \
             patch('reading_pack_producer.pipeline_resources.adapter_receipt',side_effect=simulated_receipt):
            for root,index,repetition in runs:
                register_trial(root,suite_path,str(index),repetition)
                worker=fixtures.Worker(always_fail=index>=2)
                def answer(command,request,**kwargs):
                    response=worker(command,request,**kwargs)
                    if request['stage']=='grade' and index>=2:
                        for finding in response['result']['findings']:
                            finding['category']=classes[index-2]
                    return response
                self.run=root;self.execute(answer)
            report=assess_qualification(suite,[r[0] for r in runs])
            self.assertTrue(report['qualified'],report['reasons'])
            self.assertEqual(report['completion_rate'],1)
            self.assertEqual(report['defect_detection_rate'],1)
            report_path=self.root/'certificate.json';_seal(report_path,report)
            self.recipe['operating_envelope']['mode']='production'
            for role,worker in self.recipe['workers'].items():
                worker['command']=[f'test-{role}','--audit-dir',str(self.root/'production-raw')]
            self.source=self.root/'small.md';self.source.write_text(fixtures.SOURCE)
            self.run=self.root/'production';self.start();attach_qualification(self.run,report_path)
            self.assertEqual(self.execute(fixtures.Worker())['state'],'awaiting_author_approval')
            self.source=self.root/'large.md';self.source.write_text(fixtures.SOURCE+'Background. '*500)
            self.run=self.root/'too-large';self.start();attach_qualification(self.run,report_path)
            worker=fixtures.Worker()
            self.assertEqual(self.execute(worker)['state'],'input_outside_envelope')
            self.assertEqual(worker.requests,[])


if __name__=='__main__':unittest.main()
