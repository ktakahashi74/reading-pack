from __future__ import annotations

import copy
import hashlib
import unittest

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline_evaluation import (
    evaluate_batches,
    evaluate_single,
    not_run_diagnostics,
)
from reading_pack_producer.work_ledger import artifact_hash


LEGACY = 'legacy-reader-evaluation-1'
ARTIFACT = 'artifact-acceptance-1'


def evaluation_case(case_id: str, requirement_count: int = 1) -> dict:
    return {
        'id': case_id,
        'question': f'Question {case_id}?',
        'requirements': [f'Requirement {index}' for index in range(requirement_count)],
        'evidence': [{
            'source_id': 'SRC-1',
            'span_id': f'SPAN-{case_id * 24}',
            'quote': f'evidence {case_id}',
        }],
    }


def grade(case: dict, *, met: list[bool] | None = None, uncertain: bool = False,
          critical_errors: list[dict] | None = None,
          findings: list[dict] | None = None,
          advisories: list[dict] | None = None,
          rationale: str = 'Synthetic rationale.', answer_quotes: list[str] | None = None) -> dict:
    return {
        'requirements_met': met if met is not None else [True] * len(case['requirements']),
        'uncertain': uncertain,
        'critical_errors': critical_errors or [],
        'findings': findings or [],
        'advisories': advisories or [],
        'rationale': rationale,
        'answer_quotes': answer_quotes or [],
    }


def issue(category: str, case: dict, reason: str = 'Observed issue.') -> dict:
    return {
        'category': category,
        'record_ids': [],
        'reason': reason,
        'evidence': copy.deepcopy(case['evidence']),
    }


class SyntheticRunner:
    def __init__(self, contract: str, handler, *, batch_size: int = 1,
                 repetitions: int = 1):
        self.recipe = {
            'contract_version': contract,
            'answer_repetitions': repetitions,
            'evaluation_batch_size': batch_size,
            'minimum_requirement_fraction': 1.0,
            'workers': {
                'reader': {'command': ['reader'], 'model': 'reader-synthetic'},
                'judge': {'command': ['judge'], 'model': 'judge-synthetic'},
            },
        }
        self.manifest = {'recipe': copy.deepcopy(self.recipe), 'contract_version': contract}
        self.handler = handler
        self.calls: list[tuple[str, str]] = []
        self.evidence_calls: list[list[dict]] = []
        self.finding_calls: list[tuple[str, list[str], str]] = []

    def call(self, stage: str, payload: dict, key: str) -> dict:
        self.calls.append((stage, key))
        return self.handler(stage, payload, key)

    def evidence(self, references: list[dict]) -> None:
        self.evidence_calls.append(copy.deepcopy(references))
        for reference in references:
            if reference['source_id'] != 'SRC-1' or not reference['span_id'].startswith('SPAN-'):
                raise ReadingPackError('worker invented or misattributed source evidence')

    def finding(self, category: str, record_ids: list[str], reason: str) -> dict:
        self.finding_calls.append((category, record_ids, reason))
        return {'category': category, 'record_ids': record_ids, 'reason': reason}


class PipelineDiagnosticTests(unittest.TestCase):
    def test_legacy_single_preserves_result_and_request_order(self):
        case = evaluation_case('a')
        observed = issue('missing_qualifier', case)

        def handler(stage, payload, key):
            if stage == 'answer':
                return {'answer': 'Synthetic answer.'}
            return grade(case, met=[False], findings=[copy.deepcopy(observed)],
                         rationale='A condition was absent.')

        runner = SyntheticRunner(LEGACY, handler, repetitions=2)
        result = evaluate_single(runner, 'Pack', [case], 7, 'development')
        generated = {
            'category': 'missing_coverage',
            'record_ids': [],
            'reason': 'Question a?: A condition was absent.',
            'evidence': case['evidence'],
        }
        self.assertEqual(result, {
            'scores': {'a/0': 0.0, 'a/1': 0.0},
            'critical_errors': 0,
            'failures': [observed, generated, observed, generated],
            'advisories': [],
            'answer_quote_bindings': [],
            'passed': False,
        })
        self.assertEqual(runner.calls, [
            ('answer', '7/development/a/0/answer'),
            ('grade', '7/development/a/0/grade'),
            ('answer', '7/development/a/1/answer'),
            ('grade', '7/development/a/1/grade'),
        ])

    def test_legacy_batch_preserves_jobs_scoring_and_missing_coverage(self):
        cases = [evaluation_case('a'), evaluation_case('b')]

        def handler(stage, payload, key):
            if stage == 'answer':
                return {'answer': 'Synthetic answer.'}
            return {'grades': [
                {'case_id': 'b', **grade(cases[1])},
                {'case_id': 'a', **grade(cases[0], met=[False], rationale='Wrong answer.')},
            ]}

        runner = SyntheticRunner(LEGACY, handler, batch_size=2)
        result = evaluate_batches(runner, 'Pack', cases, 3, 'holdout')
        self.assertEqual(result['scores'], {'a/0': 0.0, 'b/0': 1.0})
        self.assertEqual(result['failures'], [{
            'category': 'missing_coverage', 'record_ids': [],
            'reason': 'Question a?: Wrong answer.', 'evidence': cases[0]['evidence'],
        }])
        self.assertFalse(result['passed'])
        self.assertEqual(runner.calls, [
            ('answer', '3/holdout/batch-0/0/a/answer'),
            ('answer', '3/holdout/batch-0/0/b/answer'),
            ('grade_batch', '3/holdout/batch-0/0/grade'),
        ])

    def test_artifact_single_records_wrong_uncertain_and_critical_without_pack_failure(self):
        case = evaluation_case('a', 2)
        critical = issue('contradiction', case, 'The answer contradicted the expected condition.')
        finding = issue('missing_qualifier', case, 'The answer omitted a condition.')
        advisory = issue('wording', case, 'Optional wording improvement.')

        def handler(stage, payload, key):
            if stage == 'answer':
                return {'answer': '**Synthetic** answer.'}
            return grade(
                case, met=[False, True], uncertain=True,
                critical_errors=[copy.deepcopy(critical)],
                findings=[copy.deepcopy(finding)], advisories=[copy.deepcopy(advisory)],
                rationale='The trial answer was incomplete; its cause is not established.',
                answer_quotes=['Synthetic answer.'],
            )

        runner = SyntheticRunner(ARTIFACT, handler)
        result = evaluate_single(runner, '日本語 Pack', [case], 2, 'diagnostic')
        self.assertEqual(result['kind'], 'reader_diagnostics')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['pack_sha256'], hashlib.sha256('日本語 Pack'.encode('utf-8')).hexdigest())
        self.assertEqual(result['case_set_sha256'], artifact_hash([case]))
        self.assertEqual(result['recipe_sha256'], artifact_hash(runner.recipe))
        self.assertNotIn('passed', result)
        self.assertNotIn('failures', result)
        self.assertEqual(result['scores'], {'a/0': 0.5})
        self.assertEqual(result['critical_errors'], 1)
        self.assertEqual(result['uncertain_grades'], 1)
        self.assertEqual(result['advisories'], [advisory])
        observation = result['observations'][0]
        self.assertEqual(observation['case_id'], 'a')
        self.assertEqual(observation['repetition'], 0)
        self.assertTrue(observation['uncertain'])
        self.assertTrue(observation['score_below_threshold'])
        self.assertEqual(observation['reason'],
                         'The trial answer was incomplete; its cause is not established.')
        self.assertEqual(observation['critical_errors'], [critical])
        self.assertEqual(observation['findings'], [finding])
        self.assertEqual(observation['cause'], 'undetermined')
        self.assertNotIn('missing_coverage', repr(result))
        self.assertEqual(runner.calls, [
            ('answer', '2/diagnostic/a/0/answer'),
            ('grade', '2/diagnostic/a/0/grade'),
            ('grade', '2/diagnostic/a/0/adjudicate'),
        ])
        self.assertEqual(len(result['answer_quote_bindings']), 1)

    def test_artifact_batch_records_each_problem_without_pass_or_failures(self):
        cases = [evaluation_case('a'), evaluation_case('b')]
        problem = issue('unsupported', cases[1])
        advisory = issue('wording', cases[0], 'Optional wording improvement.')

        def handler(stage, payload, key):
            if stage == 'answer':
                return {'answer': f"Answer {payload['question'][-2]}"}
            return {'grades': [
                {'case_id': 'a', **grade(cases[0], advisories=[advisory])},
                {'case_id': 'b', **grade(cases[1], met=[False], findings=[problem],
                                         rationale='Reader answer was unsupported.')},
            ]}

        runner = SyntheticRunner(ARTIFACT, handler, batch_size=2)
        result = evaluate_batches(runner, 'Pack', cases, 1, 'diagnostic')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['scores'], {'a/0': 1.0, 'b/0': 0.0})
        self.assertEqual([item['case_id'] for item in result['observations']], ['a', 'b'])
        self.assertEqual(result['observations'][0]['repetition'], 0)
        self.assertEqual(result['observations'][0]['advisories'], [advisory])
        self.assertEqual(result['observations'][1]['findings'], [problem])
        self.assertNotIn('passed', result)
        self.assertNotIn('failures', result)

    def test_artifact_empty_single_and_batch_are_not_run_without_calls(self):
        def no_call(stage, payload, key):
            self.fail('empty diagnostic must not call a worker')

        for function, batch_size in ((evaluate_single, 1), (evaluate_batches, 4)):
            with self.subTest(function=function.__name__):
                runner = SyntheticRunner(ARTIFACT, no_call, batch_size=batch_size)
                result = function(runner, 'Pack', [], 0, 'diagnostic')
                self.assertEqual(result['status'], 'not_run')
                self.assertEqual(result['scores'], {})
                self.assertEqual(result['observations'], [])
                self.assertEqual(runner.calls, [])

    def test_explicit_not_run_binds_inputs_and_reason(self):
        case = evaluation_case('a')
        runner = SyntheticRunner(ARTIFACT, None)
        result = not_run_diagnostics(runner, 'Pack', [case], 'Optional trial omitted.')
        self.assertEqual(result['kind'], 'reader_diagnostics')
        self.assertEqual(result['status'], 'not_run')
        self.assertEqual(result['reason'], 'Optional trial omitted.')
        self.assertEqual(result['pack_sha256'], hashlib.sha256(b'Pack').hexdigest())
        self.assertEqual(result['case_set_sha256'], artifact_hash([case]))
        self.assertNotIn('passed', result)
        self.assertNotIn('failures', result)

    def test_invalid_evidence_and_grade_shape_raise_instead_of_completing(self):
        case = evaluation_case('a')
        bad = issue('unsupported', case)
        bad['evidence'][0]['source_id'] = 'SRC-INVENTED'

        def invalid_evidence(stage, payload, key):
            if stage == 'answer':
                return {'answer': 'Answer'}
            return grade(case, findings=[bad])

        with self.assertRaisesRegex(ReadingPackError, 'invented or misattributed'):
            evaluate_single(SyntheticRunner(ARTIFACT, invalid_evidence), 'Pack', [case], 0, 'diagnostic')

        def invalid_shape(stage, payload, key):
            if stage == 'answer':
                return {'answer': 'Answer'}
            return grade(case, met=[])

        with self.assertRaisesRegex(ReadingPackError, 'requirement count mismatch'):
            evaluate_single(SyntheticRunner(ARTIFACT, invalid_shape), 'Pack', [case], 0, 'diagnostic')

    def test_batch_rejects_evidence_borrowed_from_another_case(self):
        cases = [evaluation_case('a'), evaluation_case('b')]
        borrowed = issue('unsupported', cases[1])

        def handler(stage, payload, key):
            if stage == 'answer':
                return {'answer': 'Answer'}
            return {'grades': [
                {'case_id': 'a', **grade(cases[0], findings=[borrowed])},
                {'case_id': 'b', **grade(cases[1])},
            ]}

        runner = SyntheticRunner(ARTIFACT, handler, batch_size=2)
        with self.assertRaisesRegex(ReadingPackError, 'another case'):
            evaluate_batches(runner, 'Pack', cases, 0, 'diagnostic')


if __name__ == '__main__':
    unittest.main()
