"""Reader-model diagnostics with legacy evaluation-result compatibility."""
from __future__ import annotations

import copy
import hashlib

from reading_pack.errors import ReadingPackError
from .pipeline_answer_evidence import answer_quote_bindings
from .pipeline_contracts import ARTIFACT_CONTRACT_VERSION, manifest_contract
from .work_ledger import artifact_hash


def _by_id(records: list[dict], cases: list[dict]) -> dict:
    ids = [record['case_id'] for record in records]
    if len(ids) != len(cases) or set(ids) != {case['id'] for case in cases}:
        raise ReadingPackError('evaluation batch case coverage mismatch')
    return {record['case_id']: record for record in records}


def _contract_version(runner) -> str:
    # Small synthetic runners used by callers before contract selection have no
    # manifest. An absent contract is the legacy contract, just as it is in a
    # real legacy manifest.
    manifest = getattr(runner, 'manifest', {'recipe': runner.recipe})
    return manifest_contract(manifest)


def _identity(runner, pack: str, cases: list[dict]) -> dict:
    return {
        'kind': 'reader_diagnostics',
        'pack_sha256': hashlib.sha256(pack.encode('utf-8')).hexdigest(),
        'case_set_sha256': artifact_hash(cases),
        'recipe_sha256': artifact_hash(runner.recipe),
    }


def not_run_diagnostics(runner, pack: str, cases: list[dict], reason: str) -> dict:
    """Describe an intentionally unexecuted optional reader-model trial."""
    _contract_version(runner)
    return {
        **_identity(runner, pack, cases),
        'status': 'not_run',
        'reason': reason,
        'scores': {},
        'critical_errors': 0,
        'uncertain_grades': 0,
        'observations': [],
        'advisories': [],
        'answer_quote_bindings': [],
    }


class _Results:
    def __init__(self, runner, pack: str, cases: list[dict], contract_version: str):
        self.runner = runner
        self.pack = pack
        self.cases = cases
        self.contract_version = contract_version
        self.scores: dict[str, float] = {}
        self.failures: list[dict] = []
        self.critical = 0
        self.observations: list[dict] = []
        self.advisories: list[dict] = []
        self.quote_bindings: list[dict] = []

    @property
    def diagnostic(self) -> bool:
        return self.contract_version == ARTIFACT_CONTRACT_VERSION

    def add(self, case: dict, repetition: int, answer: str, grade: dict, *,
            restrict_to_case_evidence: bool = False) -> None:
        if len(grade['requirements_met']) != len(case['requirements']):
            raise ReadingPackError('grade requirement count mismatch')
        self.quote_bindings.extend(
            {'case_id': case['id'], 'repetition': repetition, **binding}
            for binding in answer_quote_bindings(answer, grade['answer_quotes'])
        )
        findings = grade['critical_errors'] + grade['findings']
        advisories = grade.get('advisories', [])
        checked = findings + advisories
        allowed = ({ref['span_id'] for ref in case['evidence']}
                   if restrict_to_case_evidence else None)
        for finding in checked:
            if restrict_to_case_evidence and any(
                    ref['span_id'] not in allowed for ref in finding['evidence']):
                raise ReadingPackError('grade cited evidence from another case')
            self.runner.evidence(finding['evidence'])

        self.advisories.extend(advisories)
        score = sum(grade['requirements_met']) / len(case['requirements'])
        self.scores[f'{case["id"]}/{repetition}'] = score
        self.critical += len(grade['critical_errors']) + int(grade['uncertain'])
        below_minimum = score < self.runner.recipe['minimum_requirement_fraction']

        if not self.diagnostic:
            self.failures.extend(findings)
            if below_minimum or grade['uncertain']:
                self.failures.append({
                    **self.runner.finding(
                        'missing_coverage', [], case['question'] + ': ' + grade['rationale']),
                    'evidence': case['evidence'],
                })
            return

        # A reader-model failure says that this trial had difficulty. It does
        # not identify a defect in the Pack. Preserve all signals for later
        # diagnosis without manufacturing a missing_coverage finding.
        if below_minimum or grade['uncertain'] or findings or advisories:
            finding_evidence = [
                copy.deepcopy(reference)
                for finding in findings + advisories
                for reference in finding['evidence']
            ]
            self.observations.append({
                'case_id': case['id'],
                'repetition': repetition,
                'score': score,
                'requirements_met': copy.deepcopy(grade['requirements_met']),
                'score_below_threshold': below_minimum,
                'uncertain': grade['uncertain'],
                'reason': grade['rationale'],
                'evidence': finding_evidence or copy.deepcopy(case['evidence']),
                'critical_errors': copy.deepcopy(grade['critical_errors']),
                'findings': copy.deepcopy(grade['findings']),
                'advisories': copy.deepcopy(advisories),
                'cause': 'undetermined',
            })

    def finish(self) -> dict:
        if not self.diagnostic:
            return {
                'scores': self.scores,
                'critical_errors': self.critical,
                'failures': self.failures,
                'advisories': self.advisories,
                'answer_quote_bindings': self.quote_bindings,
                'passed': bool(self.scores) and not self.failures and self.critical == 0 and
                          min(self.scores.values()) >= self.runner.recipe['minimum_requirement_fraction'],
            }
        return {
            **_identity(self.runner, self.pack, self.cases),
            'status': 'completed',
            'scores': self.scores,
            'critical_errors': sum(len(item['critical_errors']) for item in self.observations),
            'uncertain_grades': sum(int(item['uncertain']) for item in self.observations),
            'observations': self.observations,
            'advisories': self.advisories,
            'answer_quote_bindings': self.quote_bindings,
        }


def evaluate_single(runner, pack: str, cases: list[dict], number: int, split: str) -> dict:
    """Evaluate cases one at a time, retaining the legacy request sequence."""
    contract_version = _contract_version(runner)
    if contract_version == ARTIFACT_CONTRACT_VERSION and not cases:
        return not_run_diagnostics(runner, pack, cases, 'No evaluation cases were supplied.')
    results = _Results(runner, pack, cases, contract_version)
    for case in cases:
        for repetition in range(runner.recipe['answer_repetitions']):
            key = f'{number}/{split}/{case["id"]}/{repetition}'
            answer = runner.call(
                'answer', {'pack': pack, 'question': case['question']}, key + '/answer')['answer']
            payload = {'case': case, 'answer': answer, 'pack': pack}
            grade = runner.call('grade', payload, key + '/grade')
            if grade['uncertain']:
                grade = runner.call('grade', {**payload, 'adjudicate': True}, key + '/adjudicate')
            results.add(case, repetition, answer, grade)
    return results.finish()


def evaluate_batches(runner, pack: str, cases: list[dict], number: int, split: str) -> dict:
    contract_version = _contract_version(runner)
    if contract_version == ARTIFACT_CONTRACT_VERSION and not cases:
        return not_run_diagnostics(runner, pack, cases, 'No evaluation cases were supplied.')
    results = _Results(runner, pack, cases, contract_version)
    size = runner.recipe['evaluation_batch_size']
    # Repetitions use separate requests/contexts, never the same batched answer.
    for repetition in range(runner.recipe['answer_repetitions']):
        for start in range(0, len(cases), size):
            batch = cases[start:start + size]
            key = f'{number}/{split}/batch-{start}/{repetition}'
            # Every reader gets exactly one question in its own context. Sharing
            # reader questions could leak an answer through a different question.
            answers = {
                case['id']: runner.call(
                    'answer', {'pack': pack, 'question': case['question']},
                    key + '/' + case['id'] + '/answer')
                for case in batch
            }
            response = runner.call('grade_batch', {
                'pack': pack,
                'evaluation_cases': [
                    {'case_id': case['id'], 'case': case,
                     'answer': answers[case['id']]['answer']}
                    for case in batch
                ],
            }, key + '/grade')
            grades = _by_id(response['grades'], batch)
            for case in batch:
                answer, grade = answers[case['id']]['answer'], grades[case['id']]
                if grade['uncertain']:
                    grade = runner.call('grade', {
                        'case': case, 'answer': answer, 'pack': pack,
                        'adjudicate': True,
                    }, key + '/' + case['id'] + '/adjudicate')
                results.add(case, repetition, answer, grade,
                            restrict_to_case_evidence=True)
    return results.finish()
