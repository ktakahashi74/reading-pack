from __future__ import annotations

import copy
import signal
import time
import unittest
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline import Runner, _seal, _unseal, resume_pipeline, pipeline_status
from reading_pack_producer.pipeline_reuse import restart_pipeline
from reading_pack_producer.pipeline_resources import (
    Resources, ResourceLimit, operating_plan, phase_for, selected_benchmark_chunks,
)
from tests import test_pipeline as fixtures


def envelope():
    return {'mode':'qualification','max_wall_seconds':60,'local_reserve_seconds':2,
            'max_cost_usd':4,'call_allowance_usd':.1,
            'phase_call_limits':{'preparation':8,'generation':4,'development':6,'repair':12,'final':3}}


class ResourceTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    start = fixtures.PipelineTests.start
    execute = fixtures.PipelineTests.execute

    def bounded(self):
        self.recipe.update(max_rounds=2,max_attempts=1,timeout_seconds=1,max_calls=40,
                           operating_envelope=envelope())
        self.start()
        return Runner(self.run)

    def test_complete_fixed_workflow_with_and_without_supplements_and_no_author_adoption(self):
        for extras in (False,True):
            with self.subTest(extras=extras):
                self.run=self.root/('with-extras' if extras else 'body-only')
                self.recipe.update(max_rounds=2,max_attempts=1,timeout_seconds=1,max_calls=60,
                    operating_envelope=envelope(),evaluation_batch_size=1)
                self.recipe['operating_envelope']['phase_call_limits']['final']=6
                note=self.root/'note.md';note.write_text(fixtures.SUPPLEMENT)
                self.start([(note,'author-data')] if extras else None)
                result=self.execute(fixtures.Worker())
                self.assertEqual(result['state'],'awaiting_author_approval',result)
                self.assertGreater(result['resources']['unknown_cost_calls'],0)
                self.assertLessEqual(float(result['resources']['reserved_usd']),4)
                self.assertFalse((self.run/'release').exists())
                before=_unseal(self.run/'resource-ledger.json')
                resume_pipeline(self.run)
                self.assertEqual(_unseal(self.run/'resource-ledger.json'),before)

    def test_unaffordable_or_unfinishable_plan_sends_nothing(self):
        runner=self.bounded()
        manifest=copy.deepcopy(runner.manifest)
        for field,value in [('max_cost_usd',.2),('max_wall_seconds',3)]:
            manifest['recipe']['operating_envelope'][field]=value
            self.assertFalse(operating_plan(manifest,runner.chunks)['admitted'])
            manifest=copy.deepcopy(runner.manifest)
        self.recipe['operating_envelope']['phase_call_limits']['final']=1
        self.run=self.root/'insufficient';self.start()
        worker=fixtures.Worker();result=self.execute(worker)
        self.assertEqual(result['state'],'input_outside_envelope')
        self.assertEqual(worker.requests,[])

    def test_repair_cannot_spend_final_allocation(self):
        runner=self.bounded();runner.resources.begin()
        for n in range(12):
            runner.resources.reserve('repair',f'1/repair/{n}',str(n))
        with self.assertRaises(ResourceLimit) as stop:
            runner.resources.reserve('repair','1/repair/extra','extra')
        self.assertEqual(stop.exception.state,'phase_budget_exhausted')
        self.assertIsNotNone(runner.resources.reserve('answer','1/holdout/q/answer','final'))

    def test_failure_and_unknown_outcome_consume_the_full_allowance(self):
        runner=self.bounded();runner.resources.begin()
        first=runner.resources.reserve('profile','profile','first')
        runner.resources.finish_call(first,'execution_error')
        ledger=_unseal(self.run/'resource-ledger.json')
        self.assertEqual(ledger['reservations'][0]['allowance_usd'],'0.1')
        self.assertIsNone(ledger['reservations'][0]['actual_cost_usd'])
        self.assertEqual(pipeline_status(self.run)['resources']['reserved_usd'],'0.1')

    def test_provider_allowance_breach_is_operational_failure(self):
        runner=self.bounded();runner.resources.begin()
        item=runner.resources.reserve('profile','profile','first')
        with self.assertRaises(ResourceLimit) as stop:
            runner.resources.finish_call(item,'returned',{'actual_cost_usd':.11})
        self.assertEqual(stop.exception.state,'cost_allowance_exceeded')
        self.assertEqual(pipeline_status(self.run)['resources']['known_reported_usd'],'0.11')

    def test_final_evaluation_time_is_reserved_and_timeout_shrinks(self):
        runner=self.bounded();runner.resources.begin()
        ledger=_unseal(self.run/'resource-ledger.json')
        with patch('reading_pack_producer.pipeline_resources._now',return_value=ledger['deadline']-4):
            with self.assertRaises(ResourceLimit):runner.resources.reserve('repair','1/repair/x','x')
            final=runner.resources.reserve('answer','1/holdout/x','h')
            self.assertLessEqual(final['timeout_seconds'],1)

    def test_resume_and_restart_do_not_reset_deadline_or_cost(self):
        runner=self.bounded();runner.resources.begin()
        runner.resources.reserve('profile','profile','first')
        before=_unseal(self.run/'resource-ledger.json')
        Runner(self.run).resources.begin()
        self.assertEqual(_unseal(self.run/'resource-ledger.json')['deadline'],before['deadline'])
        new=self.root/'restart';restart_pipeline(self.run,new,self.recipe)
        resources=Runner(new).resources;resources.begin()
        self.assertEqual(_unseal(new/'resource-ledger.json'),before)
        resources.reserve('profile','profile2','second')
        self.assertEqual(pipeline_status(new)['resources']['reserved_usd'],'0.2')
        changed=copy.deepcopy(self.recipe);changed['operating_envelope']['max_cost_usd']=9
        with self.assertRaisesRegex(ReadingPackError,'reset|enlarge|transferred'):
            restart_pipeline(self.run,self.root/'enlarged',changed)

    def test_wall_deadline_also_stops_local_work_and_restores_the_process_timer(self):
        runner=self.bounded();runner.resources.begin()
        ledger=_unseal(self.run/'resource-ledger.json');ledger['deadline']=time.time()+.02
        _seal(self.run/'resource-ledger.json',ledger)
        previous=signal.getsignal(signal.SIGALRM)
        with self.assertRaises(ResourceLimit):
            with runner.resources.deadline_guard():time.sleep(.08)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM),previous)

    def test_unqualified_production_never_dispatches(self):
        self.recipe.update(max_rounds=2,max_attempts=1,timeout_seconds=1,max_calls=40,
                           operating_envelope=envelope())
        self.recipe['operating_envelope']['mode']='production';self.start()
        worker=fixtures.Worker();result=self.execute(worker)
        self.assertEqual(result['state'],'workflow_unqualified')
        self.assertEqual(worker.requests,[])

    def test_bad_source_preflight_rolls_back_without_dispatch(self):
        self.recipe.update(operating_envelope=envelope())
        self.source.write_text('No reliable headings.')
        with self.assertRaises(ReadingPackError):
            self.start()
        self.assertFalse(self.run.exists())

    def test_author_changes_do_not_open_a_second_automatic_spending_envelope(self):
        from reading_pack_producer.pipeline import finalize_pipeline
        from reading_pack_review.review_session import build_author_review_session
        from tests.test_assisted_review import _set_overrides, _sign
        self.bounded();self.execute(fixtures.Worker())
        candidate=self.run/'candidate';directory=candidate/'.reading-pack/reviews/pipeline-author'
        review=candidate/'.reading-pack/reviews/pipeline-author.review.md'
        unit=build_author_review_session(candidate,directory)['records'][0]['unit_id']
        body=f'''### {unit}
- `decision`: `revise`
- `comment`: Clarify the joint condition.
#### `summary`
- `operation`: `set`
<!-- RP_VALUE_START -->
{fixtures.SUMMARY} Both conditions must hold.
<!-- RP_VALUE_END -->'''
        review.write_text(_sign(_set_overrides(review.read_text(),body),submitted=True,final_signoff=False))
        before=_unseal(self.run/'resource-ledger.json')
        with patch('reading_pack_producer.pipeline.run_local_adapter') as dispatch:
            self.assertEqual(finalize_pipeline(self.run)['state'],'revision_requested')
            dispatch.assert_not_called()
        self.assertEqual(_unseal(self.run/'resource-ledger.json'),before)
        self.assertFalse((self.run/'revisions').exists())

    def test_phase_classification_and_role_preserving_question_cap(self):
        self.assertEqual(phase_for('audit_adjudicate','0/audit-adjudicate/a'),'development')
        self.assertEqual(phase_for('grade_batch','1/holdout/batch-0/0/grade'),'final')
        self.assertEqual(phase_for('review','1/review/a'),'repair')
        chunks=[{'id':str(n),'source_id':'SRC-1','role':'primary-book'} for n in range(10)]
        chunks.append({'id':'note','source_id':'SRC-2','role':'author-data'})
        selected=selected_benchmark_chunks(chunks,4)
        self.assertEqual(len(selected),4)
        self.assertIn(chunks[-1],selected)
        self.assertIn(chunks[0],selected)
        self.assertIn(chunks[9],selected)
        with self.assertRaisesRegex(ReadingPackError,'every source'):
            selected_benchmark_chunks(chunks,1)
