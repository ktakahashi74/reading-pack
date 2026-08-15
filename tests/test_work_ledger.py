from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from reading_pack_producer.candidates import create_candidate_run, load_candidate_run
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.work_ledger import (
    adjudicate_semantic_findings,
    coverage_report,
    create_semantic_review,
    create_work_ledger,
    load_semantic_findings,
    load_semantic_review,
    load_work_ledger,
    load_work_results,
    reconcile_work_results,
    write_semantic_review,
    write_work_ledger,
)
from tests.support import cli


class WorkLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.source = self.root / "book.txt"
        self.source.write_text(
            "The gate opens at dawn. The second gate stays closed at noon.\n",
            encoding="utf-8",
        )
        create_project(
            self.project,
            title="Gate Book",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        data = load_language_data(self.project, "en")
        data["source"] = {
            "format": "text",
            "name": self.source.name,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }
        data["chapters"] = [
            {
                "id": "CH-01",
                "title": "First Gate",
                "pages": "1-2",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            },
            {
                "id": "CH-02",
                "title": "Second Gate",
                "pages": "3-4",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            },
        ]
        write_json(self.project / "data" / "pack.en.json", data)
        self.data = data
        self.ledger = create_work_ledger(
            language="en",
            canonical_data=data,
            modules=["certainty", "claims"],
        )
        run_path = create_candidate_run(
            self.root / "candidate-run",
            source_path=self.source,
            responses=[
                {
                    "collection": "claims",
                    "record": {
                        "id": "CL-GATE",
                        "layer": "descriptive",
                        "kind": "observation",
                        "statement": "The first gate opens at dawn.",
                        "chapter_ids": ["CH-01"],
                        "status": "draft",
                    },
                    "evidence": [{"snippet": "The gate opens at dawn"}],
                }
            ],
            language="en",
            canonical_data=data,
            project_data_by_lang={"en": data},
            known_chapter_ids={"CH-01", "CH-02"},
            run_id="work-ledger-test",
            created_at="2026-08-13T00:00:00+00:00",
        )
        self.run_path = run_path
        self.run = load_candidate_run(run_path)
        self.candidate = self.run["candidates"][0]
        self.assertEqual(self.candidate["candidate_state"], "ready_for_review")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _work_item(self, module: str, chapter_id: str | None = None) -> dict:
        for item in self.ledger["items"]:
            if item["module"] != module:
                continue
            if chapter_id is None or item["scope"].get("chapter_id") == chapter_id:
                return item
        self.fail(f"missing work item {module} {chapter_id}")

    def _write_results(self, results: list[dict]) -> dict:
        path = self.root / "results.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plan_id": self.ledger["plan_id"],
                    "run_id": self.run["run_id"],
                    "results": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return load_work_results(
            path,
            ledger=self.ledger,
            run_id=self.run["run_id"],
        )

    def _reconciled(self) -> dict:
        results = self._write_results(
            [
                {
                    "work_id": self._work_item("claims", "CH-01")["work_id"],
                    "status": "complete",
                    "reason_code": "",
                    "candidate_ids": [self.candidate["candidate_id"]],
                },
                {
                    "work_id": self._work_item("claims", "CH-02")["work_id"],
                    "status": "no_supported_candidate",
                    "reason_code": "no_explicit_claim",
                    "candidate_ids": [],
                },
                {
                    "work_id": next(
                        item["work_id"]
                        for item in self.ledger["items"]
                        if item["module"] == "claims" and item["scope"] == {"kind": "book"}
                    ),
                    "status": "no_supported_candidate",
                    "reason_code": "no_book_wide_claim",
                    "candidate_ids": [],
                },
                {
                    "work_id": self._work_item("certainty")["work_id"],
                    "status": "skipped",
                    "reason_code": "author_input_required",
                    "candidate_ids": [],
                },
            ]
        )
        return reconcile_work_results(self.ledger, results, self.run)

    def test_plan_is_deterministic_and_uses_module_appropriate_scopes(self) -> None:
        again = create_work_ledger(
            language="en",
            canonical_data=self.data,
            modules=["claims", "certainty", "claims"],
        )
        self.assertEqual(again, self.ledger)
        self.assertEqual(self.run["canonical"]["data_sha256"], self.ledger["canonical_data_sha256"])
        self.assertEqual(self.ledger["modules"], ["certainty", "claims"])
        self.assertEqual(self.ledger["summary"], {
            "total": 4,
            "pending": 4,
            "complete": 0,
            "no_supported_candidate": 0,
            "failed": 0,
            "skipped": 0,
        })
        self.assertEqual(self._work_item("certainty")["scope"], {"kind": "book"})
        self.assertEqual(
            [
                item["scope"]["chapter_id"]
                for item in self.ledger["items"]
                if item["module"] == "claims" and item["scope"]["kind"] == "chapter"
            ],
            ["CH-01", "CH-02"],
        )

    def test_cli_plans_and_reports_without_granting_approval(self) -> None:
        path = self.root / "cli-ledger.json"
        planned = cli(
            "work",
            "plan",
            "--project",
            str(self.project),
            "--lang",
            "en",
            "--module",
            "claims",
            "--output",
            str(path),
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        reported = cli("work", "report", str(path), "--json")
        self.assertEqual(reported.returncode, 0, reported.stderr)
        report = json.loads(reported.stdout)
        self.assertEqual(report["generation_state"], "incomplete")
        self.assertEqual(report["summary"]["pending"], 3)
        self.assertFalse(report["approval_granted"])

    def test_reconcile_distinguishes_generation_coverage_outcomes(self) -> None:
        reconciled = self._reconciled()
        report = coverage_report(reconciled)
        self.assertEqual(report["generation_state"], "fully_accounted")
        self.assertEqual(report["semantic"]["state"], "not_assessed")
        self.assertFalse(report["approval_granted"])
        self.assertEqual(reconciled["summary"]["complete"], 1)
        self.assertEqual(reconciled["summary"]["no_supported_candidate"], 2)
        self.assertEqual(reconciled["summary"]["skipped"], 1)

    def test_reconcile_rejects_candidate_bound_to_the_wrong_chapter(self) -> None:
        results = self._write_results(
            [
                {
                    "work_id": self._work_item("claims", "CH-02")["work_id"],
                    "status": "complete",
                    "reason_code": "",
                    "candidate_ids": [self.candidate["candidate_id"]],
                }
            ]
        )
        with self.assertRaisesRegex(ReadingPackError, "does not match work item"):
            reconcile_work_results(self.ledger, results, self.run)

    def test_ledger_tampering_and_conflicting_reconciliation_are_rejected(self) -> None:
        path = self.root / "ledger.json"
        write_work_ledger(path, self.ledger)
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["items"][0]["status"] = "skipped"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "reason_code|integrity"):
            load_work_ledger(path)

        reconciled = self._reconciled()
        conflict = {
            "schema_version": 1,
            "plan_id": reconciled["plan_id"],
            "run_id": self.run["run_id"],
            "results": [
                {
                    "work_id": self._work_item("claims", "CH-02")["work_id"],
                    "status": "failed",
                    "reason_code": "adapter_failure",
                    "candidate_ids": [],
                }
            ],
        }
        with self.assertRaisesRegex(ReadingPackError, "conflicting terminal outcome"):
            reconcile_work_results(reconciled, conflict, self.run)

    def test_semantic_findings_are_bound_adjudicated_and_excerpt_free(self) -> None:
        reconciled = self._reconciled()
        finding_input_path = self.root / "semantic-input.json"
        finding_input_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plan_id": reconciled["plan_id"],
                    "run_id": self.run["run_id"],
                    "assessment": {
                        "status": "complete",
                        "assessor": "Independent semantic reviewer",
                        "assessed_candidate_ids": [self.candidate["candidate_id"]],
                    },
                    "findings": [
                        {
                            "work_id": self._work_item("claims", "CH-01")["work_id"],
                            "candidate_id": self.candidate["candidate_id"],
                            "category": "missing_qualifier",
                            "severity": "error",
                            "reason_code": "dawn_scope_omitted",
                            "evidence_ref_ids": [self.candidate["evidence_refs"][0]["id"]],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        findings = load_semantic_findings(
            finding_input_path,
            ledger=reconciled,
            run_id=self.run["run_id"],
        )
        review = create_semantic_review(
            ledger=reconciled,
            candidate_run=self.run,
            findings_input=findings,
        )
        review_path = self.root / "semantic-review.json"
        write_semantic_review(review_path, review)
        self.assertEqual(stat.S_IMODE(review_path.stat().st_mode), 0o600)
        self.assertNotIn("The gate opens at dawn", review_path.read_text(encoding="utf-8"))
        self.assertEqual(coverage_report(reconciled, review)["semantic"]["state"], "blocked")

        finding_id = review["findings"][0]["finding_id"]
        adjudicate_semantic_findings(
            review_path,
            [finding_id],
            decision="confirmed",
            reviewer="Semantic Reviewer",
            reviewed_at="2026-08-13T01:00:00+00:00",
        )
        confirmed = load_semantic_review(review_path)
        self.assertEqual(confirmed["summary"]["confirmed"], 1)
        self.assertEqual(confirmed["summary"]["blocking_errors"], 1)
        self.assertEqual(coverage_report(reconciled, confirmed)["semantic"]["state"], "blocked")

    def test_complete_zero_finding_semantic_review_is_clear(self) -> None:
        reconciled = self._reconciled()
        review = create_semantic_review(
            ledger=reconciled,
            candidate_run=self.run,
            findings_input={
                "schema_version": 1,
                "plan_id": reconciled["plan_id"],
                "run_id": self.run["run_id"],
                "assessment": {
                    "status": "complete",
                    "assessor": "Independent semantic reviewer",
                    "assessed_candidate_ids": [self.candidate["candidate_id"]],
                },
                "findings": [],
            },
        )
        report = coverage_report(reconciled, review)
        self.assertEqual(report["semantic"]["state"], "clear")
        self.assertFalse(report["approval_granted"])

    def test_dismissed_error_is_clear_but_does_not_grant_approval(self) -> None:
        reconciled = self._reconciled()
        raw = {
            "schema_version": 1,
            "plan_id": reconciled["plan_id"],
            "run_id": self.run["run_id"],
            "assessment": {
                "status": "complete",
                "assessor": "Independent semantic reviewer",
                "assessed_candidate_ids": [self.candidate["candidate_id"]],
            },
            "findings": [
                {
                    "work_id": self._work_item("claims", "CH-01")["work_id"],
                    "candidate_id": self.candidate["candidate_id"],
                    "category": "unsupported",
                    "severity": "error",
                    "reason_code": "false_positive",
                    "evidence_ref_ids": [],
                }
            ],
        }
        review = create_semantic_review(
            ledger=reconciled,
            candidate_run=self.run,
            findings_input=raw,
        )
        path = self.root / "dismiss.json"
        write_semantic_review(path, review)
        adjudicate_semantic_findings(
            path,
            [review["findings"][0]["finding_id"]],
            decision="dismissed",
            reviewer="Human Reviewer",
            reviewed_at="2026-08-13T02:00:00+00:00",
        )
        report = coverage_report(reconciled, load_semantic_review(path))
        self.assertEqual(report["semantic"]["state"], "clear")
        self.assertFalse(report["approval_granted"])

    def test_semantic_finding_cannot_reference_foreign_evidence(self) -> None:
        reconciled = self._reconciled()
        raw = {
            "schema_version": 1,
            "plan_id": reconciled["plan_id"],
            "run_id": self.run["run_id"],
            "assessment": {
                "status": "complete",
                "assessor": "Independent semantic reviewer",
                "assessed_candidate_ids": [self.candidate["candidate_id"]],
            },
            "findings": [
                {
                    "work_id": self._work_item("claims", "CH-01")["work_id"],
                    "candidate_id": self.candidate["candidate_id"],
                    "category": "unsupported",
                    "severity": "warning",
                    "reason_code": "evidence_mismatch",
                    "evidence_ref_ids": ["EV-0000000000000000"],
                }
            ],
        }
        with self.assertRaisesRegex(ReadingPackError, "outside candidate"):
            create_semantic_review(
                ledger=reconciled,
                candidate_run=self.run,
                findings_input=raw,
            )


if __name__ == "__main__":
    unittest.main()
