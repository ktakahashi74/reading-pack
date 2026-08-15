from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.candidates import (
    accept_candidates,
    apply_candidate_run,
    load_candidate_run,
    normalize_text,
)
from reading_pack_producer.generation_session import (
    AUTO_MODULES,
    COVERAGE_MODULES,
    COVERAGE_RUBRIC,
    MAX_RESPONSE_BYTES,
    MAX_GENERATED_GLOSSARY_MEANING_CHARACTERS,
    close_generation_work,
    create_generation_session,
    finalize_generation_session,
    generation_session_status,
    ingest_generation_response,
    next_generation_request,
    retry_generation_work,
    run_generation_adapter,
)
from reading_pack_producer.work_ledger import load_work_ledger
from tests.support import SAMPLE, cli


class GenerationSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.source = self.root / "book.txt"
        self.source.write_text(
            "First Gate introduces Ada and the brass engine at dawn. "
            "Its central rule says that every opening needs a key.\n"
            "Second Gate introduces Turing and a clockwork orchard at noon.\n",
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
                "kind": "chapter",
                "title": "First Gate",
                "pages": "1-5",
                "sections": ["Opening"],
                "summary": "",
                "terms": [],
                "status": "draft",
            },
            {
                "id": "CH-02",
                "kind": "chapter",
                "title": "Second Gate",
                "pages": "6-9",
                "sections": ["Orchard"],
                "summary": "",
                "terms": [],
                "status": "draft",
            },
        ]
        write_json(self.project / "data" / "pack.en.json", data)
        self.data = data

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _session(self, name: str = "session", modules: list[str] | None = None) -> Path:
        path = self.root / name
        create_generation_session(
            path,
            project=self.project,
            language="en",
            source_path=self.source,
            modules=modules,
        )
        return path

    def _request(self, session: Path) -> dict:
        return next_generation_request(
            session, project=self.project, source_path=self.source
        )

    def _chapter_map(self, name: str = "chapter-map.json") -> Path:
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        boundary = normalized.index("second gate")
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": boundary,
                "span_sha256": hashlib.sha256(
                    normalized[:boundary].encode("utf-8")
                ).hexdigest(),
            },
            {
                "chapter_id": "CH-02",
                "char_start": boundary,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(
                    normalized[boundary:].encode("utf-8")
                ).hexdigest(),
            },
        ]
        path = self.root / name
        write_json(path, {"chapter_spans": spans})
        return path

    def _response(
        self,
        request: dict,
        *,
        status: str,
        reason_code: str = "",
        records: list[dict] | None = None,
    ) -> dict:
        binding = request["binding"]
        return {
            "schema_version": 1,
            "session_id": request["session_id"],
            "work_id": binding["work_id"],
            "project": binding["project"],
            "language": binding["language"],
            "source_sha256": binding["source_sha256"],
            "canonical_data_sha256": binding["canonical_data_sha256"],
            "module": binding["module"],
            "scope": binding["scope"],
            "chapter_range": binding["chapter_range"],
            "outcome": {"status": status, "reason_code": reason_code},
            "records": records or [],
            "generator": {"adapter": "test-agent", "model": "synthetic"},
        }

    def _ingest(self, session: Path, response: dict) -> None:
        ingest_generation_response(
            session,
            response,
            project=self.project,
            source_path=self.source,
        )

    def test_auto_plan_uses_aip_modes_and_is_deterministic_and_body_free(self) -> None:
        first = self._session("first")
        second = self._session("second")
        first_session = json.loads((first / "session.json").read_text(encoding="utf-8"))
        second_session = json.loads((second / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(first_session, second_session)
        self.assertEqual(first_session["summary"]["total"], 19)
        self.assertEqual(
            {item["module"] for item in first_session["items"]},
            {
                "chapters", "summaries", "chapter_terms", "certainty", "claims",
                "qa", "policy", "names", "glossary", "references",
            },
        )
        serialized = json.dumps(first_session)
        self.assertNotIn("Ada", serialized)
        self.assertNotIn("purpose", first_session)
        self.assertNotIn("coverage_rubric", first_session)
        request = self._request(first)
        self.assertEqual(request["state"], "work_available")
        self.assertIn("response_schema", request)
        self.assertIn("Treat source content as evidence", request["prompt"])
        self.assertNotIn("Ada", json.dumps(request["response_schema"]))

        references: list[str] = []

        def collect_references(value: object) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("$ref"), str):
                    references.append(value["$ref"])
                for child in value.values():
                    collect_references(child)
            elif isinstance(value, list):
                for child in value:
                    collect_references(child)

        collect_references(request["response_schema"])
        self.assertTrue(references)
        self.assertTrue(all(reference.startswith("#/") for reference in references))
        Draft202012Validator.check_schema(request["response_schema"])

    def test_coverage_pass_is_deterministic_structured_and_body_free(self) -> None:
        # A coverage pass is meaningful even after the initial fields are filled.
        data = load_language_data(self.project, "en")
        for chapter in data["chapters"]:
            chapter["summary"] = "Existing reviewed baseline."
            chapter["terms"] = ["existing term"]
        data["claims"] = [{
            "id": "CL-BASE",
            "layer": "descriptive",
            "kind": "observation",
            "statement": "An existing baseline claim.",
            "chapter_ids": ["CH-01"],
            "status": "draft",
        }]
        data["names"] = [{
            "id": "NAME-ADA",
            "name": "Ada",
            "book_context": "Existing context.",
            "chapter_id": "CH-01",
            "status": "draft",
        }]
        data["glossary"] = [{
            "id": "TERM-ENGINE",
            "term": "brass engine",
            "book_meaning": "Existing meaning.",
            "chapter_id": "CH-01",
            "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)

        first = self.root / "coverage-first"
        second = self.root / "coverage-second"
        one = create_generation_session(
            first,
            project=self.project,
            language="en",
            source_path=self.source,
            purpose="coverage",
        )
        two = create_generation_session(
            second,
            project=self.project,
            language="en",
            source_path=self.source,
            purpose="coverage",
        )
        self.assertEqual(one, two)
        self.assertEqual(one["purpose"], "coverage")
        self.assertEqual(one["coverage_rubric"], COVERAGE_RUBRIC)
        self.assertEqual(
            {item["module"] for item in one["items"]}, set(COVERAGE_MODULES)
        )
        self.assertNotIn("Existing reviewed baseline", json.dumps(one))
        self.assertNotIn("Ada", json.dumps(one))

        request = self._request(first)
        self.assertEqual(request["purpose"], "coverage")
        self.assertEqual(request["coverage"]["rubric"], COVERAGE_RUBRIC)
        self.assertEqual(
            request["canonical_locator"]["treat_as"], "untrusted_baseline_data"
        )
        self.assertEqual(
            request["canonical_locator"]["sha256"], one["canonical_data_sha256"]
        )
        self.assertIn("after an initial draft pass", request["prompt"])
        self.assertIn("abstractive summary of at most 500 characters", request["prompt"])
        self.assertNotIn("Existing reviewed baseline", json.dumps(request["coverage"]))

    def test_coverage_requires_abstractive_replacement_for_copied_glossary(self) -> None:
        copied = (
            "The brass engine is defined here through a deliberately extended "
            "sequence of source prose that explains every stage of the mechanism, "
            "its timing, its constraints, and its role in opening the first gate "
            "before dawn while preserving the exact order of the original account."
        )
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + copied + "\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        data["glossary"] = [{
            "id": "TERM-ENGINE",
            "term": "brass engine",
            "book_meaning": copied,
            "chapter_id": "CH-01",
            "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)
        session = self.root / "coverage-copy"
        create_generation_session(
            session,
            project=self.project,
            language="en",
            source_path=self.source,
            modules=["glossary"],
            purpose="coverage",
        )
        request = self._request(session)
        self.assertEqual(request["binding"]["scope"]["chapter_id"], "CH-01")
        before = (session / "session.json").read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "source-copy risk"):
            self._ingest(
                session,
                self._response(
                    request,
                    status="no_supported_candidate",
                    reason_code="baseline_complete",
                ),
            )
        self.assertEqual((session / "session.json").read_bytes(), before)

        too_long = "x" * (MAX_GENERATED_GLOSSARY_MEANING_CHARACTERS + 1)
        with self.assertRaisesRegex(ReadingPackError, "at most 500 characters"):
            self._ingest(
                session,
                self._response(
                    request,
                    status="complete",
                    records=[{
                        "record": {
                            "id": "TERM-ENGINE",
                            "term": "brass engine",
                            "chapter_id": "CH-01",
                            "book_meaning": too_long,
                        },
                        "evidence": [{"snippet": "brass engine"}],
                    }],
                ),
            )
        replacement = (
            "The book uses this mechanism as the gate's timed opening device."
        )
        self._ingest(
            session,
            self._response(
                request,
                status="complete",
                records=[{
                    "record": {
                        "id": "TERM-ENGINE",
                        "term": "brass engine",
                        "aliases": ["brass gate engine"],
                        "chapter_id": "CH-01",
                        "book_meaning": replacement,
                    },
                    "evidence": [{"snippet": "brass engine"}],
                }],
            ),
        )
        second = self._request(session)
        self.assertEqual(second["binding"]["scope"]["chapter_id"], "CH-02")
        self._ingest(
            session,
            self._response(
                second,
                status="no_supported_candidate",
                reason_code="no_glossary_gap",
            ),
        )
        run_directory = self.root / "coverage-copy-candidates"
        finalized = finalize_generation_session(
            session,
            project=self.project,
            source_path=self.source,
            run_directory=run_directory,
        )
        self.assertEqual(finalized["candidate_summary"]["ready_for_review"], 1)
        run = load_candidate_run(run_directory)
        candidate = run["candidates"][0]
        self.assertTrue(candidate["base_record_sha256"])
        accept_candidates(
            run_directory,
            [candidate["candidate_id"]],
            reviewer="Synthetic human reviewer",
            reviewed_at="2026-08-15T00:00:00+00:00",
        )
        apply_candidate_run(
            self.project,
            language="en",
            run=run_directory,
            source_path=self.source,
            candidate_ids=[candidate["candidate_id"]],
        )
        glossary = load_language_data(self.project, "en")["glossary"]
        self.assertEqual(glossary[0]["book_meaning"], replacement)
        self.assertEqual(glossary[0]["aliases"], ["brass gate engine"])

    def test_coverage_defaults_to_fixed_modules_after_fully_provided_aip(self) -> None:
        provided_state = {
            "languages": {
                "en": {
                    "modules": {
                        module: {"mode": "provided"} for module in AUTO_MODULES
                    }
                }
            }
        }
        with patch(
            "reading_pack_producer.generation_session.load_author_input_state",
            return_value=provided_state,
        ):
            session = create_generation_session(
                self.root / "coverage-provided",
                project=self.project,
                language="en",
                source_path=self.source,
                purpose="coverage",
            )
        self.assertEqual(
            {item["module"] for item in session["items"]}, set(COVERAGE_MODULES)
        )

    def test_coverage_pass_rejects_modules_outside_fixed_rubric(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "coverage sessions support only"):
            create_generation_session(
                self.root / "invalid-coverage",
                project=self.project,
                language="en",
                source_path=self.source,
                modules=["references"],
                purpose="coverage",
            )

    def test_bindings_scope_duplicates_and_qa_policy_are_rejected(self) -> None:
        session = self._session(modules=["summaries"])
        request = self._request(session)
        record = {
            "record": {"chapter_id": "CH-01", "summary": "Ada opens the first gate."},
            "evidence": [{"snippet": "First Gate introduces Ada", "supports_field": "summary"}],
        }
        stale = self._response(request, status="complete", records=[record])
        stale["canonical_data_sha256"] = "0" * 64
        with self.assertRaisesRegex(ReadingPackError, "canonical_data_sha256 binding"):
            self._ingest(session, stale)
        foreign = self._response(request, status="complete", records=[record])
        foreign["project"] = {**foreign["project"], "slug": "other-project"}
        with self.assertRaisesRegex(ReadingPackError, "project binding"):
            self._ingest(session, foreign)
        out_of_scope = self._response(request, status="complete", records=[copy.deepcopy(record)])
        out_of_scope["records"][0]["record"]["chapter_id"] = "CH-02"
        with self.assertRaisesRegex(ReadingPackError, "outside its chapter"):
            self._ingest(session, out_of_scope)
        valid = self._response(request, status="complete", records=[record])
        self._ingest(session, valid)
        with self.assertRaisesRegex(ReadingPackError, "duplicate generation response"):
            self._ingest(session, valid)

        qa_session = self._session("qa-session", modules=["qa"])
        qa_request = self._request(qa_session)
        qa = self._response(
            qa_request,
            status="complete",
            records=[{
                "record": {
                    "id": "MIS-QA",
                    "misreading": "A question",
                    "response": "An answer",
                    "chapter_ids": ["CH-01"],
                },
                "evidence": [{"snippet": "First Gate introduces Ada"}],
            }],
        )
        with self.assertRaisesRegex(ReadingPackError, "independent author-Q&A"):
            self._ingest(qa_session, qa)

        misreading_session = self._session(
            "misreading-session", modules=["misreadings"]
        )
        misreading_request = self._request(misreading_session)
        misreading = self._response(
            misreading_request,
            status="complete",
            records=[{
                "record": {
                    "id": "MIS-CLARIFY",
                    "kind": "clarification",
                    "issue": "Does the first gate introduce Ada?",
                    "response": "The source explicitly introduces Ada at the first gate.",
                    "chapter_ids": ["CH-01"],
                    "status": "draft",
                },
                "evidence": [{
                    "snippet": "First Gate introduces Ada",
                    "supports_field": "response",
                }],
            }],
        )
        self._ingest(misreading_session, misreading)
        state = json.loads(
            (misreading_session / "session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["summary"]["ingested"], 1)

    def test_reviewed_chapter_map_rejects_evidence_outside_work_scope_at_ingest(self) -> None:
        session = self.root / "span-bound-session"
        created = create_generation_session(
            session,
            project=self.project,
            language="en",
            source_path=self.source,
            modules=["summaries"],
            chapter_map=self._chapter_map(),
        )
        self.assertIn("chapter_spans", created)
        request = self._request(session)
        self.assertEqual(
            request["source_locator"]["chapter_span"]["chapter_id"], "CH-01"
        )
        before = (session / "session.json").read_bytes()
        wrong = self._response(
            request,
            status="complete",
            records=[{
                "record": {
                    "chapter_id": "CH-01",
                    "summary": "A summary with evidence from the wrong chapter.",
                },
                "evidence": [{
                    "snippet": "Second Gate introduces Turing",
                    "supports_field": "summary",
                }],
            }],
        )
        with self.assertRaisesRegex(ReadingPackError, "outside its chapter span"):
            self._ingest(session, wrong)
        self.assertEqual((session / "session.json").read_bytes(), before)
        correct = self._response(
            request,
            status="complete",
            records=[{
                "record": {
                    "chapter_id": "CH-01",
                    "summary": "The first gate introduces Ada and a key rule.",
                },
                "evidence": [{
                    "snippet": "First Gate introduces Ada",
                    "supports_field": "summary",
                }],
            }],
        )
        self._ingest(session, correct)

    def test_generation_chapter_map_must_cover_every_chapter_work_item(self) -> None:
        complete = json.loads(self._chapter_map().read_text(encoding="utf-8"))
        incomplete = self.root / "incomplete-map.json"
        write_json(incomplete, {"chapter_spans": complete["chapter_spans"][:1]})
        with self.assertRaisesRegex(ReadingPackError, "missing work scope.*CH-02"):
            create_generation_session(
                self.root / "incomplete-span-session",
                project=self.project,
                language="en",
                source_path=self.source,
                modules=["summaries"],
                chapter_map=incomplete,
            )

    def test_oversized_response_and_adapter_timeout_leave_session_unchanged(self) -> None:
        session = self._session(modules=["summaries"])
        before = (session / "session.json").read_bytes()
        request = self._request(session)
        huge = self._response(
            request,
            status="complete",
            records=[{
                "record": {"chapter_id": "CH-01", "summary": "x" * (MAX_RESPONSE_BYTES + 1)},
                "evidence": [{"snippet": "First Gate introduces Ada"}],
            }],
        )
        with self.assertRaisesRegex(ReadingPackError, "exceeds"):
            self._ingest(session, huge)
        self.assertEqual((session / "session.json").read_bytes(), before)
        with self.assertRaisesRegex(ReadingPackError, "timed out"):
            run_generation_adapter(
                session,
                project=self.project,
                source_path=self.source,
                command=[sys.executable, "-c", "import time; time.sleep(1)"],
                timeout=0.02,
            )
        self.assertEqual((session / "session.json").read_bytes(), before)

    def test_finalize_reuses_candidate_review_and_apply_path(self) -> None:
        session = self._session(modules=["summaries", "chapter_terms", "claims"])
        while True:
            request = self._request(session)
            if request["state"] == "ready_to_finalize":
                break
            binding = request["binding"]
            module = binding["module"]
            chapter_id = binding["scope"].get("chapter_id")
            if module == "summaries":
                summary = (
                    "The chapter links Ada, a brass engine, and a key rule."
                    if chapter_id == "CH-01"
                    else "The chapter places Turing in a clockwork orchard."
                )
                snippet = (
                    "First Gate introduces Ada and the brass engine"
                    if chapter_id == "CH-01"
                    else "Second Gate introduces Turing and a clockwork orchard"
                )
                response = self._response(
                    request,
                    status="complete",
                    records=[{
                        "record": {"chapter_id": chapter_id, "summary": summary},
                        "evidence": [{"snippet": snippet, "supports_field": "summary"}],
                    }],
                )
            elif module == "chapter_terms":
                term = "brass engine" if chapter_id == "CH-01" else "clockwork orchard"
                response = self._response(
                    request,
                    status="complete",
                    records=[{
                        "record": {"chapter_id": chapter_id, "terms": [term]},
                        "evidence": [{"snippet": term, "supports_field": "terms"}],
                    }],
                )
            elif chapter_id == "CH-01":
                response = self._response(
                    request,
                    status="complete",
                    records=[{
                        "record": {
                            "id": "CL-KEY",
                            "layer": "descriptive",
                            "kind": "rule",
                            "statement": "The first gate's rule requires a key for each opening.",
                            "chapter_ids": ["CH-01"],
                        },
                        "evidence": [{
                            "snippet": "every opening needs a key",
                            "supports_field": "statement",
                        }],
                    }],
                )
            else:
                response = self._response(
                    request,
                    status="no_supported_candidate",
                    reason_code=("no_explicit_claim" if chapter_id else "no_book_wide_claim"),
                )
            self._ingest(session, response)

        run_directory = self.root / "candidate-run"
        result = finalize_generation_session(
            session,
            project=self.project,
            source_path=self.source,
            run_directory=run_directory,
        )
        self.assertEqual(result["candidate_summary"]["ready_for_review"], 3)
        self.assertEqual(result["coverage"]["complete"], 5)
        self.assertEqual(result["coverage"]["no_supported_candidate"], 2)
        self.assertFalse(any((session / "responses").iterdir()))
        ledger = load_work_ledger(session / "ledger.json")
        self.assertEqual(ledger["summary"], result["coverage"])
        status = generation_session_status(session)
        self.assertEqual(status["state"], "finalized")
        self.assertFalse(status["approval_granted"])

        run = load_candidate_run(run_directory)
        ids = [item["candidate_id"] for item in run["candidates"]]
        accept_candidates(
            run_directory,
            ids,
            reviewer="Synthetic human reviewer",
            reviewed_at="2026-08-15T00:00:00+00:00",
        )
        applied = apply_candidate_run(
            self.project,
            language="en",
            run=run_directory,
            source_path=self.source,
            candidate_ids=ids,
        )
        self.assertEqual(set(applied), set(ids))
        data = load_language_data(self.project, "en")
        self.assertEqual(data["chapters"][0]["terms"], ["brass engine"])
        self.assertTrue(data["chapters"][0]["summary"])
        self.assertEqual(data["claims"][0]["id"], "CL-KEY")
        self.assertTrue(all(item["status"] == "draft" for item in data["chapters"] + data["claims"]))

    def test_finalize_failure_does_not_publish_candidate_run(self) -> None:
        session = self._session(modules=["summaries"])
        failed_work_id = ""
        while True:
            request = self._request(session)
            if request["state"] == "ready_to_finalize":
                break
            chapter_id = request["binding"]["scope"]["chapter_id"]
            if chapter_id == "CH-01":
                failed_work_id = request["binding"]["work_id"]
                response = self._response(
                    request,
                    status="complete",
                    records=[{
                        "record": {
                            "chapter_id": chapter_id,
                            "summary": "A deliberately unsupported summary.",
                        },
                        "evidence": [{
                            "snippet": "This evidence does not occur in the source.",
                            "supports_field": "summary",
                        }],
                    }],
                )
            else:
                response = self._response(
                    request,
                    status="no_supported_candidate",
                    reason_code="no_supported_summary",
                )
            self._ingest(session, response)

        run_directory = self.root / "failed-candidate-run"
        with self.assertRaisesRegex(
            ReadingPackError, "completed work item produced no reviewable candidate"
        ):
            finalize_generation_session(
                session,
                project=self.project,
                source_path=self.source,
                run_directory=run_directory,
            )
        self.assertFalse(run_directory.exists())
        self.assertEqual(generation_session_status(session)["state"], "open")
        self.assertEqual(len(list((session / "responses").iterdir())), 2)

        retried = retry_generation_work(
            session,
            failed_work_id,
            project=self.project,
            source_path=self.source,
        )
        self.assertEqual(retried["state"], "awaiting_response")
        request = self._request(session)
        self.assertEqual(request["binding"]["work_id"], failed_work_id)
        self._ingest(
            session,
            self._response(
                request,
                status="complete",
                records=[{
                    "record": {
                        "chapter_id": "CH-01",
                        "summary": "The chapter links Ada, a brass engine, and a key rule.",
                    },
                    "evidence": [{
                        "snippet": "First Gate introduces Ada and the brass engine",
                        "supports_field": "summary",
                    }],
                }],
            ),
        )
        result = finalize_generation_session(
            session,
            project=self.project,
            source_path=self.source,
            run_directory=run_directory,
        )
        self.assertEqual(result["state"], "finalized")
        self.assertTrue(run_directory.is_dir())

    def test_cli_plan_next_status_and_wrong_project(self) -> None:
        session = self.root / "cli-session"
        planned = cli(
            "work", "plan",
            "--project", str(self.project),
            "--lang", "en",
            "--module", "summaries",
            "--session-directory", str(session),
            "--source", str(self.source),
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        requested = cli(
            "work", "next", str(session),
            "--project", str(self.project),
            "--source", str(self.source),
        )
        self.assertEqual(requested.returncode, 0, requested.stderr)
        request = json.loads(requested.stdout)
        self.assertEqual(request["state"], "work_available")
        response_path = self.root / "cli-response.json"
        write_json(
            response_path,
            self._response(
                request,
                status="no_supported_candidate",
                reason_code="no_supported_summary",
            ),
        )
        ingested = cli(
            "work", "ingest", str(session), str(response_path),
            "--project", str(self.project),
            "--source", str(self.source),
        )
        self.assertEqual(ingested.returncode, 0, ingested.stderr)
        retried = cli(
            "work", "retry", str(session),
            "--id", request["binding"]["work_id"],
            "--project", str(self.project),
            "--source", str(self.source),
        )
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(json.loads(retried.stdout)["state"], "awaiting_response")
        reported = cli("work", "status", str(session), "--json")
        self.assertEqual(reported.returncode, 0, reported.stderr)
        self.assertEqual(json.loads(reported.stdout)["summary"]["awaiting_response"], 2)

        other = self.root / "other"
        create_project(
            other,
            title="Other",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        wrong = cli(
            "work", "next", str(session),
            "--project", str(other),
            "--source", str(self.source),
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("another project", wrong.stderr)

        coverage = cli(
            "work", "plan",
            "--project", str(self.project),
            "--lang", "en",
            "--module", "summaries",
            "--purpose", "coverage",
            "--session-directory", str(self.root / "cli-coverage"),
            "--source", str(self.source),
        )
        self.assertEqual(coverage.returncode, 0, coverage.stderr)
        self.assertEqual(json.loads(coverage.stdout)["purpose"], "coverage")

    def test_work_close_uses_bound_response_contract_and_advances_one_item(self) -> None:
        session = self._session("close-session", modules=["summaries"])
        first = self._request(session)
        result = close_generation_work(
            session,
            project=self.project,
            source_path=self.source,
            outcome="no_supported_candidate",
            reason_code="no_supported_summary",
        )
        self.assertEqual(result["work_id"], first["binding"]["work_id"])
        self.assertEqual(
            result["outcome"],
            {"status": "no_supported_candidate", "reason_code": "no_supported_summary"},
        )
        stored = json.loads(
            (session / "responses" / f"{result['work_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored["records"], [])
        self.assertEqual(stored["generator"]["adapter"], "reading-pack-work-close")

        twin = self._session("close-session-twin", modules=["summaries"])
        twin_result = close_generation_work(
            twin,
            project=self.project,
            source_path=self.source,
            outcome="no_supported_candidate",
            reason_code="no_supported_summary",
        )
        self.assertEqual(twin_result["work_id"], result["work_id"])
        self.assertEqual(
            (twin / "responses" / f"{result['work_id']}.json").read_bytes(),
            (session / "responses" / f"{result['work_id']}.json").read_bytes(),
        )
        self.assertEqual(
            self._request(session)["binding"]["scope"]["chapter_id"], "CH-02"
        )

        current = load_language_data(self.project, "en")
        changed = copy.deepcopy(current)
        changed["chapters"][0]["summary"] = "A newer canonical snapshot."
        write_json(self.project / "data" / "pack.en.json", changed)
        with self.assertRaisesRegex(ReadingPackError, "canonical snapshot is stale"):
            close_generation_work(
                session,
                project=self.project,
                source_path=self.source,
                outcome="skipped",
                reason_code="catalog_path_used",
            )
        write_json(self.project / "data" / "pack.en.json", current)

        before = (session / "session.json").read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "reason_code"):
            close_generation_work(
                session,
                project=self.project,
                source_path=self.source,
                outcome="skipped",
                reason_code="not valid",
            )
        self.assertEqual((session / "session.json").read_bytes(), before)

        closed = cli(
            "work", "close", str(session),
            "--project", str(self.project),
            "--source", str(self.source),
            "--outcome", "skipped",
            "--reason", "catalog_path_used",
        )
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["summary"]["awaiting_response"], 0)
        ready = self._request(session)
        self.assertEqual(ready["state"], "ready_to_finalize")
        unavailable = cli(
            "work", "close", str(session),
            "--project", str(self.project),
            "--source", str(self.source),
            "--outcome", "skipped",
            "--reason", "catalog_path_used",
        )
        self.assertNotEqual(unavailable.returncode, 0)
        self.assertIn("no pending work", unavailable.stderr)

    def test_multilingual_coverage_sessions_feed_language_isolated_build_all(self) -> None:
        project = self.root / "bilingual"
        initialized = cli(
            "init", str(project),
            "--title", "歯車仕掛けの庭",
            "--author", "Author",
            "--lang", "ja",
            "--lang", "en",
            "--primary-language", "ja",
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        sources = {
            "ja": SAMPLE / "manuscripts" / "book.ja.md",
            "en": SAMPLE / "manuscripts" / "book.en.md",
        }
        for language, source in sources.items():
            imported = cli(
                "import", str(source),
                "--project", str(project),
                "--lang", language,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)

        sessions: dict[str, Path] = {}
        runs: dict[str, Path] = {}
        for language, source in sources.items():
            session = self.root / f"coverage-{language}"
            create_generation_session(
                session,
                project=project,
                language=language,
                source_path=source,
                modules=["summaries"],
                purpose="coverage",
            )
            sessions[language] = session
            while True:
                request = next_generation_request(
                    session, project=project, source_path=source
                )
                if request["state"] == "ready_to_finalize":
                    break
                self.assertEqual(request["binding"]["language"], language)
                self.assertTrue(
                    request["canonical_locator"]["local_path"].endswith(
                        f"pack.{language}.json"
                    )
                )
                chapter_id = request["binding"]["scope"]["chapter_id"]
                if language == "en" and chapter_id == "CH-01":
                    response = self._response(
                        request,
                        status="complete",
                        records=[{
                            "record": {
                                "chapter_id": "CH-01",
                                "summary": (
                                    "The chapter presents Rio's moon-driven garden design."
                                ),
                            },
                            "evidence": [{
                                "snippet": (
                                    "Inventor Rio designed a garden whose beds moved "
                                    "with the phases of the moon"
                                ),
                                "supports_field": "summary",
                            }],
                        }],
                    )
                else:
                    response = self._response(
                        request,
                        status="no_supported_candidate",
                        reason_code="no_material_summary_improvement",
                    )
                ingest_generation_response(
                    session,
                    response,
                    project=project,
                    source_path=source,
                )
            run = self.root / f"run-{language}"
            finalize_generation_session(
                session,
                project=project,
                source_path=source,
                run_directory=run,
            )
            runs[language] = run
            self.assertEqual(load_candidate_run(run)["language"], language)

        self.assertEqual(load_candidate_run(runs["ja"])["summary"]["total"], 0)
        english_run = load_candidate_run(runs["en"])
        english_ids = [
            candidate["candidate_id"] for candidate in english_run["candidates"]
        ]
        self.assertEqual(len(english_ids), 1)
        accept_candidates(
            runs["en"],
            english_ids,
            reviewer="Synthetic bilingual reviewer",
            reviewed_at="2026-08-15T00:00:00+00:00",
        )
        apply_candidate_run(
            project,
            language="en",
            run=runs["en"],
            source_path=sources["en"],
            candidate_ids=english_ids,
        )

        built = cli("build", "--project", str(project), "--lang", "all")
        self.assertEqual(built.returncode, 0, built.stderr)
        checked = cli("check", "--project", str(project), "--lang", "all")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        english_pack = (project / "dist/reading-pack.en.md").read_text(encoding="utf-8")
        japanese_pack = (project / "dist/reading-pack.ja.md").read_text(encoding="utf-8")
        generated_summary = "The chapter presents Rio's moon-driven garden design."
        self.assertIn(generated_summary, english_pack)
        self.assertNotIn(generated_summary, japanese_pack)


if __name__ == "__main__":
    unittest.main()
