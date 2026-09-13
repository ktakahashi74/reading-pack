from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data, write_json
from reading_pack_producer.pipeline import Runner, _inventory, _unseal, _seal, default_recipe, start_pipeline, resume_pipeline, finalize_pipeline, pipeline_status
from reading_pack_producer.pipeline_acceptance import inspect_artifact
from reading_pack_producer.pipeline_acceptance_records import verify_record
from reading_pack_producer.pipeline_acceptance_reassessment import reassess_artifact
from reading_pack_producer.pipeline_acceptance_resources import artifact_operating_plan
from tests.test_pipeline import Worker, SOURCE, SUPPLEMENT, SUMMARY
from tests.test_pipeline_acceptance_integration import ArtifactWorker


class FullWorker:
    def __init__(self, mode='pass'):
        self.generator = Worker()
        self.inspector = ArtifactWorker()
        self.mode = mode
        self.requests = []

    def __call__(self, command, request, **kwargs):
        self.requests.append(copy.deepcopy(request))
        if not request['stage'].startswith('artifact_'):
            return self.generator(command,request,**kwargs)
        response = self.inspector(command,request,**kwargs)
        checks = {c['id']:c for c in request['payload']['checks']}
        for value in response['result']['checks']:
            check = checks[value['check_id']]
            targeted = check['criterion']=='important_conditions' and check.get('metadata',{}).get('canonical_path')=='/chapters/0'
            defect = targeted and self.mode in {'repair','persistent'} and (self.mode=='persistent' or '/final/' not in request['job'])
            if self.mode=='contradiction' and request['stage']=='artifact_instructions': defect=True
            if defect:
                value.update(outcome='defect',reason='The summary omits the necessary safety condition.',reader_impact='Reader could infer permission without a safe lamp.')
            if targeted and self.mode in {'uncertain','resolve'} and (self.mode=='uncertain' or 'adjudicate-' not in request['job']):
                value.update(outcome='unresolved',reason='The condition needs one independent check.',reader_impact='The condition remains uncertain.')
        return response


class ArtifactWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'book.md';self.source.write_text(SOURCE)
        self.run=self.root/'run'
        self.recipe=default_recipe(['g'],['j'],['r'],generator_model='g',judge_model='j',reader_model='r')
        self.recipe.update(contract_version='artifact-acceptance-1',profile='general-navigation',max_rounds=2,max_attempts=1,artifact_inspection_batch_limit=16,artifact_adjudication_limit=1)

    def start(self,**kwargs):
        return start_pipeline(self.run,self.source,self.recipe,**kwargs)

    def execute(self,worker):
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=worker):
            return resume_pipeline(self.run)

    def test_source_only_and_supplement_reach_hash_bound_author_packet_without_answers(self):
        for supplemental in (False,True):
            with self.subTest(supplemental=supplemental):
                self.run=self.root/str(supplemental)
                sup=self.root/'supplement.md';sup.write_text(SUPPLEMENT)
                self.start(supplements=[(sup,'author-data')] if supplemental else None)
                worker=FullWorker();result=self.execute(worker)
                self.assertEqual(result['state'],'awaiting_author_approval',result)
                self.assertEqual(result['acceptance']['status'],'pass')
                self.assertFalse((self.run/'benchmark.json').exists())
                self.assertFalse({'answer','grade','benchmark','audit'} & {r['stage'] for r in worker.requests})
                record=verify_record(self.run,result['acceptance_record'])
                self.assertEqual(record['author_approval']['status'],'pending')
                self.assertEqual(record['model_diagnostics']['status'],'not_run')
                with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=AssertionError('must replay')):
                    self.assertEqual(resume_pipeline(self.run)['acceptance_record'],result['acceptance_record'])

    def test_one_repair_reinspects_all_and_persistent_defect_never_starts_second_repair(self):
        for mode,expected in [('repair','pass'),('persistent','fail')]:
            with self.subTest(mode=mode):
                self.run=self.root/mode;self.start();worker=FullWorker(mode);result=self.execute(worker)
                self.assertEqual(result['acceptance']['status'],expected,result)
                self.assertEqual(len([r for r in worker.requests if r['stage']=='repair']),1)
                self.assertTrue(any('/final/' in r['job'] for r in worker.requests))
                change=_unseal(self.run/'acceptance/repair-result.json')
                self.assertEqual(change['rounds'],1)
                self.assertNotEqual(change['before_sha256'],change['after_sha256'])
                initial=load_language_data(self.run/'acceptance/production/initial','en')
                self.assertNotIn('Both conditions must hold.',initial['chapters'][0]['summary'])

    def test_unresolved_gets_one_fixed_adjudication_without_repair_loop(self):
        for mode,expected in [('resolve','pass'),('uncertain','inconclusive')]:
            with self.subTest(mode=mode):
                self.run=self.root/mode;self.start();worker=FullWorker(mode);result=self.execute(worker)
                self.assertEqual(result['acceptance']['status'],expected,result)
                self.assertEqual(len([r for r in worker.requests if 'adjudicate-' in r['job']]),1)
                self.assertFalse(any(r['stage']=='repair' for r in worker.requests))

    def test_instruction_contradiction_is_defect_and_never_grants_template_repair(self):
        self.start();worker=FullWorker('contradiction');result=self.execute(worker)
        self.assertEqual(result['acceptance']['status'],'fail')
        self.assertEqual(result['author_approval']['status'],'needs_decision')
        self.assertFalse(any(r['stage']=='repair' for r in worker.requests))

    def test_approved_seed_content_is_preserved_and_collected_for_author(self):
        self.start();seed=self.run/'saved-seed';Runner(self.run).bootstrap(seed)
        data=load_language_data(seed,'en')
        data['chapters'][0].update(summary=SUMMARY,status='approved',source_locations=['book.md#normalized-text:0-100'])
        write_json(seed/'data/pack.en.json',data)
        before=(seed/'data/pack.en.json').read_bytes()
        self.run=self.root/'protected';self.start(project=seed)
        worker=FullWorker('persistent');result=self.execute(worker)
        self.assertEqual(result['acceptance']['status'],'fail')
        self.assertEqual(result['author_approval']['status'],'needs_decision')
        self.assertFalse(any(r['stage']=='repair' for r in worker.requests))
        self.assertEqual((seed/'data/pack.en.json').read_bytes(),before)

    def test_final_file_omitting_sys_fails_even_with_semantic_pass(self):
        self.start();seed=self.root/'seed';Runner(self.run).bootstrap(seed)
        data=load_language_data(seed,'en');data['chapters'][0].update(summary=SUMMARY,source_locations=['book.md#normalized-text:0-100']);write_json(seed/'data/pack.en.json',data)
        template=seed/'templates/pack.en.md';template.write_text(template.read_text().replace('{{SYS}}',''))
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=FullWorker()):
            result=inspect_artifact(self.run,seed,experimental=True)
        self.assertEqual(result['acceptance']['status'],'fail')
        self.assertTrue(any(r['check_id']=='delivery:final' for r in result['acceptance']['confirmed_defects']))

    def test_delivery_tamper_is_detected_on_author_decision(self):
        self.start();result=self.execute(FullWorker())
        record=verify_record(self.run,result['acceptance_record'])
        directory=(self.run/record['bindings']['delivery_manifest']['path']).parent/'project/dist'
        next(directory.glob('*.md')).write_text('changed')
        with self.assertRaisesRegex(ReadingPackError,'delivery'):
            finalize_pipeline(self.run)

    def test_author_approval_does_not_change_failed_quality_or_publish(self):
        self.start();result=self.execute(FullWorker('persistent'))
        decision=self.root/'decision.json'
        decision.write_text(json.dumps({'reviewer':'Synthetic test author','decided_at':'2026-09-12T12:00:00+09:00','decision':'approved','scope':'local candidate only','candidate_manifest_sha256':result['author_approval']['candidate_manifest_sha256']}))
        final=finalize_pipeline(self.run,decision)
        self.assertEqual(final['acceptance']['status'],'fail')
        self.assertEqual(final['author_approval']['status'],'approved')
        self.assertFalse((self.run/'release').exists())
        self.assertEqual(len(list((self.run/'acceptance/records').glob('*.json'))),2)

    def test_call_budget_stop_keeps_defect_and_pending_scope(self):
        self.recipe.update(max_calls=4)
        self.start();result=self.execute(FullWorker('persistent'))
        self.assertEqual(result['acceptance']['status'],'fail',result)
        self.assertEqual(result['acceptance']['coverage'],'incomplete')
        self.assertEqual(result['execution']['status'],'stopped')
        before=result['calls_reserved'];again=self.execute(FullWorker('persistent'))
        self.assertEqual(again['calls_reserved'],before)

    def test_frozen_inspection_capacity_stops_oversized_output_without_extra_calls(self):
        self.recipe.update(artifact_inspection_batch_limit=1)
        self.start();worker=FullWorker();result=self.execute(worker)
        self.assertEqual(result['acceptance']['status'],'inconclusive')
        self.assertEqual(result['execution']['stop_reasons'],['phase_budget_exhausted'])
        self.assertFalse(any(r['stage'].startswith('artifact_') for r in worker.requests))

    def test_reassessment_preserves_origin_and_starts_not_run(self):
        self.recipe['max_rounds']=1;self.start();self.execute(FullWorker())
        old=self.run;before=_inventory(old);seed=old/'acceptance/production/initial'
        self.run=self.root/'reassessed'
        result=reassess_artifact(old,self.run,seed,self.recipe)
        self.assertEqual(result['acceptance']['status'],'not_run')
        for path,digest in before.items():self.assertEqual(_inventory(old)[path],digest)
        original_calls=pipeline_status(old)['calls_reserved'];self.assertEqual(result['calls_reserved'],original_calls)
        final=self.execute(FullWorker());self.assertEqual(final['acceptance']['status'],'pass',final)
        self.assertEqual(final['author_approval']['status'],'pending')
        with self.assertRaisesRegex(ReadingPackError,'successor'):
            reassess_artifact(old,self.root/'branch',seed,self.recipe)

    def test_reassessment_refuses_envelope_change_before_creating_run(self):
        self.start();changed=copy.deepcopy(self.recipe);changed['max_calls']+=1
        with self.assertRaisesRegex(ReadingPackError,'cumulative call'):
            reassess_artifact(self.run,self.root/'bad',self.run/'absent',changed)
        self.assertFalse((self.root/'bad').exists())

    def test_status_never_displays_stale_pass_after_artifact_tamper(self):
        self.start();result=self.execute(FullWorker())
        record=verify_record(self.run,result['acceptance_record'])
        manifest=_unseal(self.run/record['bindings']['candidate_manifest']['path'])
        path=self.run/manifest['root']/'data/pack.en.json'
        path.write_text('{}')
        status=pipeline_status(self.run)
        self.assertEqual(status['acceptance']['status'],'inconclusive')
        self.assertEqual(status['execution']['stop_reasons'],['integrity_error'])
        self.assertEqual(_unseal(self.run/result['acceptance_record']['path'])['acceptance']['status'],'pass')

    def test_invalid_author_decision_candidate_does_not_append_a_record(self):
        self.start();result=self.execute(FullWorker())
        decision=self.root/'decision.json'
        decision.write_text(json.dumps({'reviewer':'Test','decided_at':'2026-09-12T00:00:00Z','decision':'approved','scope':'candidate','candidate_manifest_sha256':'a'*64}))
        with self.assertRaisesRegex(ReadingPackError,'another candidate'):
            finalize_pipeline(self.run,decision)
        self.assertEqual(len(list((self.run/'acceptance/records').glob('*.json'))),1)

    def test_reassessment_keeps_original_deadline_and_reservations(self):
        self.start();runner=Runner(self.run)
        caps=artifact_operating_plan(runner.manifest,runner.chunks)['minimum_phase_calls']
        self.recipe.update(max_calls=sum(caps.values()),timeout_seconds=1,operating_envelope={'mode':'production','phase_call_limits':caps,'max_wall_seconds':1000,'local_reserve_seconds':10,'max_cost_usd':100,'call_allowance_usd':0.1})
        self.run=self.root/'bounded';self.start();self.execute(FullWorker())
        old=self.run;ledger=_unseal(old/'resource-ledger.json')
        self.run=self.root/'reassessment'
        reassess_artifact(old,self.run,old/'acceptance/production/initial',self.recipe)
        self.execute(FullWorker())
        after=_unseal(self.run/'resource-ledger.json')
        self.assertEqual(after['deadline'],ledger['deadline'])
        self.assertEqual(after['reservations'][:len(ledger['reservations'])],ledger['reservations'])
        self.assertGreater(len(after['reservations']),len(ledger['reservations']))

    def test_study_accepts_finite_single_case_and_never_calls_synthetic_evidence_live(self):
        from reading_pack_producer.pipeline_qualification import register_trial, assess_qualification, workflow_signature, input_identity
        self.start();runner=Runner(self.run)
        caps=artifact_operating_plan(runner.manifest,runner.chunks)['minimum_phase_calls']
        self.recipe.update(max_calls=sum(caps.values()),timeout_seconds=1,operating_envelope={'mode':'qualification','phase_call_limits':caps,'max_wall_seconds':1000,'local_reserve_seconds':10,'max_cost_usd':100,'call_allowance_usd':0.1})
        self.run=self.root/'study';self.start();manifest=_unseal(self.run/'manifest.json')
        suite={'schema_version':2,'contract_version':'artifact-acceptance-1','workflow_signature':workflow_signature(manifest),'repetitions':1,
               'cases':[{'id':'one','book_id':'synthetic','input_identity':input_identity(manifest),'expectation':'complete','defect_class':'','record_ids':[]}]}
        path=self.root/'suite.json';path.write_text(json.dumps(suite));register_trial(self.run,path,'one',0)
        self.execute(FullWorker());report=assess_qualification(suite,[self.run])
        self.assertEqual(report['observed_completion_rate'],1)
        self.assertFalse(report['fresh_live_complete_study'])
        self.assertFalse(report['qualified'])
        self.assertFalse(report['pack_acceptance_gate'])
        absent=assess_qualification(suite,[])
        self.assertEqual(absent['observed_completion_rate'],0)
        self.assertEqual(len(absent['missing_trials']),1)
        with self.assertRaisesRegex(ReadingPackError,'duplicate'):
            assess_qualification(suite,[self.run,self.run])

    def test_planner_reserves_final_without_reader_questions_or_qualification_gate(self):
        self.start();runner=Runner(self.run)
        experimental=artifact_operating_plan(runner.manifest,runner.chunks)
        caps=experimental['minimum_phase_calls']
        self.recipe.update(max_calls=sum(caps.values()),timeout_seconds=1,operating_envelope={'mode':'production','phase_call_limits':caps,'max_wall_seconds':1000,'local_reserve_seconds':10,'max_cost_usd':100,'call_allowance_usd':0.1})
        self.run=self.root/'bounded';self.start();worker=FullWorker();result=self.execute(worker)
        self.assertEqual(result['acceptance']['status'],'pass',result)
        self.assertTrue((self.run/'resource-ledger.json').exists())
        self.assertFalse((self.run/'qualification.json').exists())
        self.assertEqual(_unseal(self.run/'operating-plan.json')['reader_questions'],0)
        self.assertEqual(result['resources']['unknown_cost_calls'],len(worker.requests))
