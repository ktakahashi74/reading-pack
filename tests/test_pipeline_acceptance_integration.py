from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data, write_json
from reading_pack_producer.pipeline import (
    Runner,
    _seal,
    _unseal,
    default_recipe,
    resume_pipeline,
    start_pipeline,
)
from reading_pack_producer.pipeline_acceptance import inspect_artifact
from reading_pack_producer.pipeline_resources import PHASES
from tests.support import cli
from tests.test_pipeline import SOURCE, SUMMARY


class ArtifactWorker:
    """A transport-level synthetic content inspector using only supplied spans."""

    def __init__(self, mode: str = "pass"):
        self.mode = mode
        self.requests: list[dict] = []

    def __call__(self, command, request, **kwargs):
        self.requests.append(copy.deepcopy(request))
        if request["stage"] not in {"artifact_content", "artifact_instructions"}:
            raise AssertionError(request["stage"])
        payload = request["payload"]
        checks = payload["checks"]
        if self.mode == "empty":
            values = []
        elif self.mode == "unknown":
            span = payload["evidence_spans"][0]
            values = [{
                "check_id": "UNKNOWN-CHECK", "status": "complete", "outcome": "pass",
                "reason": "Malformed synthetic response.",
                "evidence": [{"source_id": span["source_id"], "span_id": span["id"]}],
                "reader_impact": "",
            }]
        else:
            values = []
            for index, check in enumerate(checks):
                if self.mode == "defect" and index:
                    continue
                span = self._span_for(check, payload["evidence_spans"])
                defect = self.mode == "defect"
                values.append({
                    "check_id": check["id"], "status": "complete",
                    "outcome": "defect" if defect else "pass",
                    "reason": "Synthetic source-bound defect." if defect else "Synthetic source-bound pass.",
                    "evidence": [{"source_id": span["source_id"], "span_id": span["id"]}],
                    "reader_impact": "A reader could be misled by this concrete defect." if defect else "",
                })
        return {
            "schema_version": 1,
            "request_id": request["request_id"],
            "model": request["model"],
            "result": {"checks": values},
        }

    @staticmethod
    def _span_for(check: dict, spans: list[dict]) -> dict:
        for span in spans:
            for source_range in check["source_ranges"]:
                if (span["source_id"] == source_range["source_id"] and
                        span["source_sha256"] == source_range["source_sha256"] and
                        source_range["start"] <= span["start"] < span["end"] <= source_range["end"]):
                    return span
        raise AssertionError("no supplied span lies inside this check's source range")


class AcceptanceInspectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "book.md"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.seed = self.root / "seed"
        self.legacy = self.root / "legacy"
        self.recipe = default_recipe(
            ["test-generator"], ["test-judge"], ["test-reader"],
            generator_model="generator-v1", judge_model="judge-v1", reader_model="reader-v1",
        )
        self.recipe.update(profile="general-navigation", max_rounds=1, answer_repetitions=1)
        start_pipeline(self.legacy, self.source, self.recipe)
        Runner(self.legacy).bootstrap(self.seed)
        data = load_language_data(self.seed, "en")
        data["chapters"][0].update(summary=SUMMARY, source_locations=["book.md#normalized-text:0-100"])
        write_json(self.seed / "data" / "pack.en.json", data)
        self.run = self.root / "artifact"
        artifact_recipe = copy.deepcopy(self.recipe)
        artifact_recipe["contract_version"] = "artifact-acceptance-1"
        start_pipeline(self.run, self.source, artifact_recipe, project=self.seed)

    def inspect(self, worker: ArtifactWorker, **kwargs) -> dict:
        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=worker):
            return inspect_artifact(self.run, self.seed, experimental=True, **kwargs)

    def artifact_run(self, name: str, **overrides) -> Path:
        run = self.root / name
        recipe = copy.deepcopy(self.recipe)
        recipe.update(contract_version="artifact-acceptance-1", **overrides)
        start_pipeline(run, self.source, recipe, project=self.seed)
        return run

    def test_prepare_only_makes_no_calls_and_leaves_all_checks_pending(self):
        worker = ArtifactWorker()
        report = self.inspect(worker, prepare_only=True)

        self.assertEqual(worker.requests, [])
        self.assertEqual(report["execution"]["status"], "ready")
        self.assertEqual(report["acceptance"]["status"], "not_run")
        self.assertEqual(report["acceptance"]["coverage"], "not_run")
        self.assertEqual(report["acceptance"]["completed_check_ids"], [])
        self.assertEqual(report["ledger"]["attempts"], [])
        frozen = _unseal(self.run / report["inventory"]["path"])
        self.assertEqual(report["acceptance"]["pending_check_ids"], [check["id"] for check in frozen["checks"]])
        self.assertIn("inspection_id", report)
        self.assertIn("path", report["report"])

    def test_cli_prepare_only_uses_the_inspection_handler_without_dispatch(self):
        result = cli("pipeline", "inspect-artifact", "--run", str(self.run), "--project", str(self.seed),
                     "--prepare-only")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["execution"]["status"], "ready")
        self.assertEqual(report["acceptance"]["status"], "not_run")

    def test_all_content_instructions_and_delivery_checks_can_complete(self):
        report = self.inspect(ArtifactWorker())
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["acceptance"]["status"], "pass")
        self.assertEqual(report["acceptance"]["coverage"], "complete")
        self.assertEqual(report["acceptance"]["pending_check_ids"], [])

    def test_concrete_defect_fails_even_when_other_checks_are_omitted(self):
        report = self.inspect(ArtifactWorker("defect"))

        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["acceptance"]["status"], "fail")
        self.assertEqual(report["acceptance"]["coverage"], "incomplete")
        self.assertTrue(report["acceptance"]["confirmed_defects"])
        self.assertTrue(report["acceptance"]["pending_check_ids"])

    def test_empty_worker_responses_leave_content_checks_pending(self):
        report = self.inspect(ArtifactWorker("empty"))

        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["acceptance"]["status"], "inconclusive")
        self.assertEqual(report["acceptance"]["coverage"], "incomplete")
        self.assertTrue(report["acceptance"]["pending_check_ids"])
        self.assertFalse(report["acceptance"]["confirmed_defects"])

    def test_replay_reuses_saved_evidence_without_calls_or_rewriting_old_reports(self):
        worker = ArtifactWorker()
        first = self.inspect(worker)
        reports = self.run / "acceptance" / "inspections" / first["inspection_id"] / "reports"
        before = {path.name: path.read_bytes() for path in reports.glob("*.json")}
        calls = len(worker.requests)

        second = self.inspect(worker)

        self.assertEqual(len(worker.requests), calls)
        self.assertEqual(second["inspection_id"], first["inspection_id"])
        self.assertEqual({path.name: path.read_bytes() for path in reports.glob("*.json") if path.name in before}, before)

    def test_changed_original_candidate_creates_new_immutable_snapshot(self):
        first = self.inspect(ArtifactWorker())
        first_candidate = self.run / "acceptance" / "inspections" / first["inspection_id"] / "candidate" / "data" / "pack.en.json"
        before = first_candidate.read_bytes()
        changed = load_language_data(self.seed, "en")
        changed["chapters"][0]["summary"] += " A changed local candidate."
        write_json(self.seed / "data" / "pack.en.json", changed)

        second = self.inspect(ArtifactWorker())

        self.assertNotEqual(first["inspection_id"], second["inspection_id"])
        self.assertEqual(first_candidate.read_bytes(), before)

    def test_editing_snapshot_or_saved_evidence_is_rejected(self):
        prepared = self.inspect(ArtifactWorker(), prepare_only=True)
        candidate = self.run / "acceptance" / "inspections" / prepared["inspection_id"] / "candidate" / "data" / "pack.en.json"
        candidate.write_text("{}", encoding="utf-8")
        with self.assertRaises(ReadingPackError):
            self.inspect(ArtifactWorker(), prepare_only=True)

        # A separate fresh run reaches saved evidence, then a resealed edit is
        # still rejected because the earlier immutable report binds its hash.
        fresh = self.root / "fresh-artifact"
        recipe = copy.deepcopy(self.recipe)
        recipe["contract_version"] = "artifact-acceptance-1"
        start_pipeline(fresh, self.source, recipe, project=self.seed)
        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=ArtifactWorker()):
            completed = inspect_artifact(fresh, self.seed, experimental=True)
        reference = completed["ledger"]["attempts"][0]["evidence_ref"]
        path = fresh / reference["path"]
        edited = _unseal(path)
        edited["results"] = []
        _seal(path, edited)
        with self.assertRaises(ReadingPackError):
            with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=AssertionError("must replay first")):
                inspect_artifact(fresh, self.seed, experimental=True)

    def test_deleting_reported_evidence_rejects_replay_before_dispatch(self):
        completed = self.inspect(ArtifactWorker())
        reference = completed["ledger"]["attempts"][-1]["evidence_ref"]
        (self.run / reference["path"]).unlink()

        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=AssertionError("must not dispatch")):
            with self.assertRaises(ReadingPackError):
                inspect_artifact(self.run, self.seed, experimental=True)

    def test_budget_stop_preserves_earlier_defect_and_never_spends_again_on_replay(self):
        run = self.artifact_run("one-call", max_calls=1)
        worker = ArtifactWorker("defect")
        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=worker):
            first = inspect_artifact(run, self.seed, experimental=True)

        self.assertEqual(len(worker.requests), 1)
        self.assertEqual(first["execution"]["status"], "stopped")
        self.assertEqual(first["acceptance"]["status"], "fail")
        self.assertEqual(first["acceptance"]["coverage"], "incomplete")
        self.assertTrue(first["acceptance"]["confirmed_defects"])

        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=AssertionError("budget stop must not retry")):
            replay = inspect_artifact(run, self.seed, experimental=True)
        self.assertEqual(replay["execution"]["status"], "stopped")
        self.assertEqual(replay["acceptance"]["status"], "fail")
        self.assertEqual(replay["acceptance"]["confirmed_defects"], first["acceptance"]["confirmed_defects"])

    def test_malformed_response_stops_once_and_retains_the_raw_job(self):
        worker = ArtifactWorker("unknown")
        report = self.inspect(worker)

        self.assertEqual(len(worker.requests), 1)
        self.assertEqual(report["execution"]["status"], "stopped")
        self.assertEqual(report["acceptance"]["status"], "inconclusive")
        self.assertEqual(report["acceptance"]["coverage"], "incomplete")
        jobs = sorted((self.run / "jobs").glob("*.json"))
        self.assertEqual(len(jobs), 1)
        job = _unseal(jobs[0])
        self.assertEqual(len(job["attempts"]), 1)
        self.assertEqual(job["response"]["result"]["checks"][0]["check_id"], "UNKNOWN-CHECK")

    def test_contract_and_operating_envelope_guards_reject_unsupported_dispatch(self):
        with patch("reading_pack_producer.pipeline.run_local_adapter") as adapter:
            self.assertEqual(resume_pipeline(self.run)["acceptance"]["status"], "not_run")
            adapter.assert_not_called()
        with self.assertRaisesRegex(ReadingPackError, "bounded artifact inspection entry"):
            Runner(self.run).call("artifact_content", {}, "direct")
        with self.assertRaisesRegex(ReadingPackError, "direct inspection requires"):
            inspect_artifact(self.legacy, self.seed, prepare_only=True)

        enveloped = self.root / "enveloped"
        recipe = copy.deepcopy(self.recipe)
        recipe.update(contract_version="artifact-acceptance-1", max_calls=len(PHASES), operating_envelope={
            "mode": "qualification", "max_wall_seconds": 600, "local_reserve_seconds": 30,
            "max_cost_usd": 1.0, "call_allowance_usd": 0.1,
            "phase_call_limits": {phase: 1 for phase in PHASES},
        })
        start_pipeline(enveloped, self.source, recipe, project=self.seed)
        with patch("reading_pack_producer.pipeline.run_local_adapter", side_effect=AssertionError("no resource bypass")):
            result = inspect_artifact(enveloped, self.seed, experimental=True)
            self.assertEqual(result["execution"]["status"], "stopped")
            self.assertEqual(result["acceptance"]["status"], "not_run")


if __name__ == "__main__":
    unittest.main()
