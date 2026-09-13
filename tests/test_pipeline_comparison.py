import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import default_recipe, start_pipeline, _seal, _unseal
from reading_pack_producer.pipeline_comparison import register_comparison, run_comparison, report_comparison, completion_comparisons
from tests.test_pipeline import SOURCE
from tests.test_pipeline_acceptance_workflow import FullWorker


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'book.md';self.source.write_text(SOURCE)
        self.definition={'schema_version':1,'variants':{'a':'g-a','b':'g-b'},'cases':{'one':'book'},'repetitions':1,
            'book_budgets':{'book':{'prior_cost_usd':1,'max_cost_usd':101,'basis':'test fixture'}},
            'max_cost_usd':100,'max_wall_seconds':2000,'minimum_completion_rate':0.9,'equivalence_margin':0.05,'trials':[]}
        for variant,model in self.definition['variants'].items():
            recipe=default_recipe(['g'],['j'],['r'],generator_model=model,judge_model='judge',reader_model='reader')
            caps={'preparation':0,'generation':25,'development':17,'repair':0,'final':0}
            recipe.update(contract_version='artifact-acceptance-1',profile='general-navigation',max_rounds=1,max_attempts=1,
                artifact_inspection_batch_limit=16,artifact_adjudication_limit=1,max_calls=42,timeout_seconds=1,
                operating_envelope={'mode':'qualification','phase_call_limits':caps,'max_wall_seconds':1000,'local_reserve_seconds':10,'max_cost_usd':50,'call_allowance_usd':1})
            run=self.root/variant;start_pipeline(run,self.source,recipe)
            self.definition['trials'].append({'variant':variant,'case':'one','repetition':0,'run':str(run)})
        self.comparison=self.root/'comparison'

    def test_finite_execution_missing_failed_and_synthetic_not_saturation(self):
        register_comparison(self.definition,self.comparison)
        before=report_comparison(self.comparison)
        self.assertEqual(before['variants']['a']['completion_rate'],0)
        worker=FullWorker()
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=worker):
            result=run_comparison(self.comparison)
        self.assertEqual(result['variants']['a']['completion_rate'],1,result)
        self.assertEqual(result['variants']['b']['completion_rate'],1,result)
        self.assertEqual(result['comparisons'][0]['status'],'inconclusive')
        self.assertEqual(result['quality_saturation'],'not_established')
        self.assertFalse(result['trials'][0]['fresh_live_measurement'])
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=AssertionError('no retry')):
            again=run_comparison(self.comparison)
        self.assertEqual(len(again['execution']['attempts']),2)
        self.assertFalse(any(r['stage']=='repair' for r in worker.requests))

    def test_cumulative_budget_rejects_all_trials_before_calls(self):
        self.definition['book_budgets']['book']['max_cost_usd']=100
        result=register_comparison(self.definition,self.comparison)
        self.assertFalse(result['admitted'])
        self.assertIn('remaining cumulative',result['reasons'][0])
        self.assertFalse((self.root/'a/qualification-trial.json').exists())
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=AssertionError('no calls')):
            self.assertFalse(run_comparison(self.comparison)['admitted'])

    def test_duplicate_or_missing_slots_rejected(self):
        for change in ('missing','duplicate'):
            definition=copy.deepcopy(self.definition)
            if change=='missing':definition['trials'].pop()
            else:definition['trials'].append(definition['trials'][0])
            with self.assertRaises(ReadingPackError):register_comparison(definition,self.comparison)
        self.assertFalse(self.comparison.exists())

    def test_common_judge_and_equal_limits_required(self):
        path=self.root/'b/manifest.json';manifest=_unseal(path)
        manifest['recipe']['workers']['judge']['model']='different'
        _seal(path,manifest)
        with self.assertRaisesRegex(ReadingPackError,'changed input, judge'):
            register_comparison(self.definition,self.comparison)

    def test_changed_source_invalidates_report(self):
        register_comparison(self.definition,self.comparison)
        path=self.root/'a/manifest.json';manifest=_unseal(path);manifest['recipe']['workers']['generator']['model']='changed';_seal(path,manifest)
        with self.assertRaises(ReadingPackError):report_comparison(self.comparison)

    def test_interrupted_trial_never_retried_automatically(self):
        register_comparison(self.definition,self.comparison)
        import time
        _seal(self.comparison/'execution.json',{'started_at':time.time(),'attempts':[{'index':0,'status':'inflight'}]})
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=FullWorker()):
            result=run_comparison(self.comparison)
        self.assertEqual(result['variants']['a']['completion_rate'],0)
        self.assertEqual(result['variants']['b']['completion_rate'],1)

    def test_equal_low_quality_or_small_samples_are_not_equivalence(self):
        for passed,n in ((False,10000),(True,2),(True,10000)):
            rows=[{'variant':v,'case':'c','repetition':i,'completed':passed,'fresh_live_measurement':True} for v in ('a','b') for i in range(n)]
            result=completion_comparisons(rows,['a','b'],0.05,0.9)[0]
            self.assertEqual(result['status'],'within_margin' if passed and n==10000 else 'inconclusive')

    def test_failure_stays_in_denominator_and_has_inspection_evidence(self):
        register_comparison(self.definition,self.comparison)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=FullWorker('persistent')):
            result=run_comparison(self.comparison)
        self.assertEqual(result['variants']['a']['completion_rate'],0)
        self.assertEqual(result['variants']['a']['acceptance_counts'],{'fail':1})
        self.assertTrue(result['trials'][0]['inspection']['confirmed_defect_ids'])

    def test_finalize_cli_success_is_not_quality_or_release_approval(self):
        import contextlib,io,json
        from argparse import Namespace
        from reading_pack_producer.pipeline_commands import command_pipeline
        register_comparison(self.definition,self.comparison)
        with patch('reading_pack_producer.pipeline.run_local_adapter',side_effect=FullWorker('persistent')):
            run_comparison(self.comparison)
        run=self.root/'a';state=_unseal(run/'state.json')
        decision=self.root/'decision.json'
        decision.write_text(json.dumps({'reviewer':'Test only','decided_at':'2026-09-13T00:00:00Z','decision':'approved','scope':'synthetic candidate',
            'candidate_manifest_sha256':state['author_approval']['candidate_manifest_sha256']}))
        with contextlib.redirect_stdout(io.StringIO()):
            code=command_pipeline(Namespace(pipeline_command='finalize',run=run,review=decision))
        self.assertEqual(code,0)
        state=_unseal(run/'state.json')
        self.assertEqual(state['state'],'artifact_completed')
        self.assertEqual(state['acceptance']['status'],'fail')
        self.assertFalse((run/'release').exists())

    def test_report_cli_preserves_budget_rejection(self):
        import json
        from tests.support import cli
        self.definition['max_cost_usd']=1
        definition=self.root/'definition.json';definition.write_text(json.dumps(self.definition))
        result=cli('pipeline','comparison-register','--definition',str(definition),'--output',str(self.comparison))
        self.assertEqual(result.returncode,1,result.stderr)
        output=self.root/'result.json'
        result=cli('pipeline','comparison-run','--comparison',str(self.comparison),'--output',str(output))
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(_unseal(output)['admitted'])
