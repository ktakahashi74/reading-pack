from __future__ import annotations

import contextlib
import hashlib
import io
import json
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import reading_pack_producer.candidates as candidate_module
from reading_pack_producer.candidates import (
    AI_REVIEW_CHECKS,
    LeakPolicy,
    accept_candidates,
    apply_candidate_run,
    author_review_suggestions,
    create_candidate_run,
    load_ai_review_decisions,
    load_candidate_run,
    reject_candidates,
    run_local_adapter,
)
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json


class CandidateRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.source = self.root / "authorized-source.txt"
        self.source.write_text(
            "The gate opens at dawn. Cobalt is the named mechanism. "
            "A careful reader checks the source before accepting a claim.\n",
            encoding="utf-8",
        )
        create_project(
            self.project,
            title="Evidence Garden",
            author="Ada Example",
            languages=["en"],
            primary_language="en",
        )
        data = load_language_data(self.project, "en")
        data["chapters"] = [
            {
                "id": "CH-01",
                "title": "The Gate",
                "pages": "1-12",
                "sections": ["First mechanism"],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        ]
        data["source"] = {
            "format": "text",
            "name": self.source.name,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }
        data["glossary"] = [
            {
                "id": "TERM-EXISTING",
                "term": "Garden",
                "chapter_id": "CH-01",
                "status": "reviewed",
            }
        ]
        write_json(self.project / "data" / "pack.en.json", data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _term(self, term: str, evidence: str, *, identifier: str = "TERM-COBALT") -> dict:
        return {
            "collection": "glossary",
            "record": {
                "id": identifier,
                "term": term,
                "chapter_id": "CH-01",
                "status": "approved",
            },
            "evidence": [{"snippet": evidence}],
        }

    def _run(self, name: str, responses, **kwargs) -> Path:
        return create_candidate_run(
            self.root / name,
            source_path=self.source,
            responses=responses,
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            project_data_by_lang={"en": load_language_data(self.project, "en")},
            known_chapter_ids={"CH-01"},
            run_id=name,
            created_at="2026-08-13T00:00:00+00:00",
            **kwargs,
        )

    def test_hallucinated_term_is_quarantined_even_with_real_evidence(self) -> None:
        path = self._run("hallucination", self._term("Unobtainium", "Cobalt is the named mechanism"))
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("term_not_in_source", candidate["qa"]["reason_codes"])
        with self.assertRaisesRegex(ReadingPackError, "not accepted"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate["candidate_id"]],
            )

    def test_unsupported_evidence_is_quarantined(self) -> None:
        path = self._run("unsupported", self._term("Cobalt", "This sentence is invented"))
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("unsupported_evidence", candidate["qa"]["reason_codes"])

    def test_transient_evidence_is_not_written_or_printed_and_apply_is_draft_only(self) -> None:
        evidence = "Cobalt is the named mechanism"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            path = self._run("ready", self._term("Cobalt", evidence))
            manifest = load_candidate_run(path)
            candidate = manifest["candidates"][0]
            self.assertEqual(candidate["record"]["status"], "draft")
            accept_candidates(
                path,
                [candidate["candidate_id"]],
                reviewer="Test Reviewer",
                reviewed_at="2026-08-13T00:01:00+00:00",
            )
            applied = apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate["candidate_id"]],
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(applied, [candidate["candidate_id"]])

        serialized = path.read_text(encoding="utf-8")
        self.assertNotIn(evidence, serialized)
        self.assertNotIn(str(self.source.parent), serialized)
        self.assertNotIn('"snippet"', serialized)
        self.assertIn('"excerpt_stored": false', serialized)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        canonical = load_language_data(self.project, "en")
        by_id = {record["id"]: record for record in canonical["glossary"]}
        self.assertEqual(by_id["TERM-COBALT"]["status"], "draft")
        self.assertEqual(
            by_id["TERM-COBALT"]["source_locations"],
            [f"{self.source.name}#normalized-text:24-53"],
        )
        self.assertEqual(by_id["TERM-EXISTING"]["status"], "reviewed")
        completed = load_candidate_run(path)
        self.assertEqual(completed["candidates"][0]["candidate_state"], "applied")
        self.assertEqual(
            completed["application"]["candidate_ids"], [candidate["candidate_id"]]
        )
        self.assertEqual(
            completed["application"]["before_sha256"],
            completed["canonical"]["data_sha256"],
        )
        self.assertRegex(completed["application"]["application_id"], r"^APP-[A-F0-9]{20}$")

    def test_current_replacement_can_be_exported_as_an_author_review_suggestion(self) -> None:
        path = self._run(
            "focused-review",
            {
                "collection": "glossary",
                "record": {
                    "id": "TERM-EXISTING",
                    "term": "Cobalt",
                    "chapter_id": "CH-01",
                    "book_meaning": "Cobalt names the mechanism used at the gate.",
                    "status": "approved",
                },
                "evidence": [{"snippet": "Cobalt is the named mechanism"}],
            },
        )
        suggestions = author_review_suggestions(
            self.project, [path], record_ids=["TERM-EXISTING"]
        )
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["record_id"], "TERM-EXISTING")
        self.assertEqual(suggestions[0]["record"]["status"], "draft")

        data = load_language_data(self.project, "en")
        data["glossary"][0]["term"] = "Changed after generation"
        write_json(self.project / "data" / "pack.en.json", data)
        with self.assertRaisesRegex(ReadingPackError, "stale"):
            author_review_suggestions(self.project, [path])

    def test_ai_review_artifact_can_replace_candidate_human_review(self) -> None:
        path = self._run("ai-reviewed", self._term("Cobalt", "Cobalt is the named mechanism"))
        manifest = load_candidate_run(path)
        candidate = manifest["candidates"][0]
        reviewed_at = "2026-08-13T00:01:00+00:00"
        review_path = self.root / "ai-review.json"
        review_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": manifest["run_id"],
                    "run_integrity_sha256": manifest["integrity_sha256"],
                    "reviewer": {
                        "type": "ai",
                        "name": "test-model",
                        "method": "independent-source-grounded-review-v1",
                        "reviewed_at": reviewed_at,
                    },
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "record_sha256": candidate["record_sha256"],
                            "candidate_artifact_sha256": candidate["review"]["candidate_artifact_sha256"],
                            "decision": "accept",
                            "checks": sorted(AI_REVIEW_CHECKS),
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        provenance = load_ai_review_decisions(
            review_path,
            run=path,
            candidate_ids=[candidate["candidate_id"]],
            reviewer="test-model",
            decision="accept",
        )
        accepted = accept_candidates(
            path,
            [candidate["candidate_id"]],
            reviewer="test-model",
            reviewer_type="ai",
            review_method=provenance["method"],
            review_artifact_sha256=provenance["artifact_sha256"],
            reviewed_at=provenance["reviewed_at"],
        )
        self.assertEqual(accepted, [candidate["candidate_id"]])
        review = load_candidate_run(path)["candidates"][0]["review"]
        self.assertEqual(review["reviewer_type"], "ai")
        self.assertEqual(review["reviewer"], "test-model")
        self.assertEqual(review["review_method"], "independent-source-grounded-review-v1")
        self.assertEqual(review["review_artifact_sha256"], provenance["artifact_sha256"])

    def test_ai_review_artifact_rejects_stale_run_binding(self) -> None:
        path = self._run("ai-stale", self._term("Cobalt", "Cobalt is the named mechanism"))
        manifest = load_candidate_run(path)
        candidate = manifest["candidates"][0]
        review_path = self.root / "ai-review-stale.json"
        review_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": manifest["run_id"],
                    "run_integrity_sha256": "0" * 64,
                    "reviewer": {
                        "type": "ai",
                        "name": "test-model",
                        "method": "independent-source-grounded-review-v1",
                        "reviewed_at": "2026-08-13T00:01:00+00:00",
                    },
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "record_sha256": candidate["record_sha256"],
                            "candidate_artifact_sha256": candidate["review"]["candidate_artifact_sha256"],
                            "decision": "accept",
                            "checks": sorted(AI_REVIEW_CHECKS),
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReadingPackError, "stale"):
            load_ai_review_decisions(
                review_path,
                run=path,
                candidate_ids=[candidate["candidate_id"]],
                reviewer="test-model",
                decision="accept",
            )

    def test_ai_review_artifact_can_record_rejection(self) -> None:
        path = self._run("ai-rejected", self._term("Cobalt", "Cobalt is the named mechanism"))
        manifest = load_candidate_run(path)
        candidate = manifest["candidates"][0]
        review_path = self.root / "ai-review-reject.json"
        review_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": manifest["run_id"],
                    "run_integrity_sha256": manifest["integrity_sha256"],
                    "reviewer": {
                        "type": "ai",
                        "name": "test-model",
                        "method": "independent-source-grounded-review-v1",
                        "reviewed_at": "2026-08-13T00:01:00+00:00",
                    },
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "record_sha256": candidate["record_sha256"],
                            "candidate_artifact_sha256": candidate["review"]["candidate_artifact_sha256"],
                            "decision": "reject",
                            "checks": sorted(AI_REVIEW_CHECKS),
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        provenance = load_ai_review_decisions(
            review_path,
            run=path,
            candidate_ids=[candidate["candidate_id"]],
            reviewer="test-model",
            decision="reject",
        )
        rejected = reject_candidates(
            path,
            [candidate["candidate_id"]],
            reviewer="test-model",
            reviewer_type="ai",
            review_method=provenance["method"],
            review_artifact_sha256=provenance["artifact_sha256"],
            reviewed_at=provenance["reviewed_at"],
        )
        self.assertEqual(rejected, [candidate["candidate_id"]])
        reviewed = load_candidate_run(path)["candidates"][0]
        self.assertEqual(reviewed["candidate_state"], "rejected")
        self.assertEqual(reviewed["review"]["status"], "rejected")
        self.assertEqual(reviewed["review"]["reviewer_type"], "ai")

    def test_evidence_tampering_fails_before_canonical_write(self) -> None:
        path = self._run("tamper", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["candidates"][0]["evidence_refs"][0]["locator"]["char_start"] += 1
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        before = (self.project / "data" / "pack.en.json").read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "integrity"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        self.assertEqual((self.project / "data" / "pack.en.json").read_bytes(), before)

    def test_acceptance_binds_evidence_and_base_even_if_checksum_is_recomputed(self) -> None:
        path = self._run("artifact-bind", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        candidate = manifest["candidates"][0]
        candidate["base_record_sha256"] = "a" * 64
        candidate["evidence_refs"][0]["support"] = "distributed"
        manifest["integrity_sha256"] = candidate_module._manifest_integrity(manifest)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "artifact binding"):
            load_candidate_run(path)

    def test_non_object_manifest_candidate_is_rejected_cleanly(self) -> None:
        path = self._run("manifest-shape", self._term("Cobalt", "Cobalt is the named mechanism"))
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["candidates"] = [0]
        manifest["summary"] = candidate_module._summary(manifest["candidates"])
        manifest["integrity_sha256"] = candidate_module._manifest_integrity(manifest)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "non-object"):
            load_candidate_run(path)

    def test_stale_source_fails_before_canonical_write(self) -> None:
        path = self._run("stale", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        before = (self.project / "data" / "pack.en.json").read_bytes()
        self.source.write_text("The source has changed.\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "stale|does not match"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        self.assertEqual((self.project / "data" / "pack.en.json").read_bytes(), before)

    def test_candidate_source_must_match_existing_canonical_source(self) -> None:
        path = self._run("wrong-project", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        canonical_path = self.project / "data" / "pack.en.json"
        canonical = load_language_data(self.project, "en")
        canonical["source"] = {
            "format": "markdown",
            "name": "different.md",
            "sha256": "0" * 64,
        }
        write_json(canonical_path, canonical)
        before = canonical_path.read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "canonical source"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        self.assertEqual(canonical_path.read_bytes(), before)

    def test_long_source_copy_is_quarantined_and_omitted_from_manifest(self) -> None:
        copied = (
            "A careful reader checks the source before accepting a claim. "
            "A careful reader checks the source before accepting a claim."
        )
        self.source.write_text(self.source.read_text() + copied + "\n", encoding="utf-8")
        canonical = load_language_data(self.project, "en")
        canonical["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project / "data" / "pack.en.json", canonical)
        response = {
            "collection": "chapters",
            "record": {
                "id": "CH-01",
                "title": "The Gate",
                "pages": "1-12",
                "sections": ["First mechanism"],
                "summary": copied,
                "terms": [],
                "status": "draft",
            },
            "evidence": [{"snippet": "careful reader checks the source"}],
        }
        path = self._run(
            "leak",
            response,
            leak_policy=LeakPolicy(max_contiguous_characters=64),
        )
        manifest = load_candidate_run(path)
        candidate = manifest["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("source_copy_risk", candidate["qa"]["reason_codes"])
        self.assertNotIn("record", candidate)
        self.assertNotIn(copied, path.read_text(encoding="utf-8"))

    def test_duplicate_record_ids_are_quarantined(self) -> None:
        response = self._term("Cobalt", "Cobalt is the named mechanism")
        path = self._run("duplicate", [response, response])
        manifest = load_candidate_run(path)
        self.assertEqual(manifest["summary"]["quarantined"], 2)
        for candidate in manifest["candidates"]:
            self.assertIn("duplicate_record_id", candidate["qa"]["reason_codes"])

    def test_human_rejection_changes_only_private_state(self) -> None:
        path = self._run("reject", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        before = (self.project / "data" / "pack.en.json").read_bytes()
        self.assertEqual(reject_candidates(path, [candidate_id]), [candidate_id])
        self.assertEqual(load_candidate_run(path)["candidates"][0]["candidate_state"], "rejected")
        self.assertEqual((self.project / "data" / "pack.en.json").read_bytes(), before)

    def test_ready_candidate_cannot_apply_without_explicit_acceptance(self) -> None:
        path = self._run("needs-acceptance", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        before = (self.project / "data" / "pack.en.json").read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "not accepted"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        self.assertEqual((self.project / "data" / "pack.en.json").read_bytes(), before)

    def test_short_ascii_term_requires_word_boundaries(self) -> None:
        path = self._run("word-boundary", self._term("AI", "careful reader checks"))
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("term_not_in_source", candidate["qa"]["reason_codes"])

    def test_pdf_glyph_spacing_keeps_compact_ascii_word_boundaries(self) -> None:
        self.assertTrue(
            candidate_module._exact_source_term(
                "QXR", candidate_module.normalize_text("Q X R is discussed")
            )
        )
        self.assertFalse(
            candidate_module._exact_source_term(
                "QXR", candidate_module.normalize_text("a q x r z")
            )
        )

    def test_invalid_record_type_is_quarantined_and_not_retained(self) -> None:
        response = {
            "collection": "chapters",
            "record": {
                "id": "CH-01",
                "title": "The Gate",
                "pages": "1-12",
                "sections": ["First mechanism"],
                "summary": 42,
                "terms": [],
                "status": "draft",
            },
            "evidence": [{"snippet": "The gate opens at dawn"}],
        }
        path = self._run("invalid-type", response)
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertNotIn("record", candidate)
        self.assertIn("invalid_string_field", candidate["qa"]["reason_codes"])

    def test_unhashable_enum_types_are_quarantined_without_crashing(self) -> None:
        responses = [
            {
                "collection": "chapters",
                "record": {
                    "id": "CH-01",
                    "kind": [],
                    "title": "The Gate",
                    "pages": "1-12",
                    "sections": ["First mechanism"],
                    "summary": "A short summary.",
                    "terms": [],
                    "status": "draft",
                    "spoiler_scope": [],
                },
                "evidence": [{"snippet": "The gate opens at dawn"}],
            },
            {
                "collection": "claims",
                "record": {
                    "id": "CL-BAD",
                    "layer": [],
                    "kind": "observation",
                    "statement": "The gate opens.",
                    "chapter_ids": ["CH-01"],
                    "status": "draft",
                },
                "evidence": [{"snippet": "The gate opens at dawn"}],
            },
        ]
        path = self._run("unhashable-enums", responses)
        candidates = load_candidate_run(path)["candidates"]
        self.assertTrue(all(item["candidate_state"] == "quarantined" for item in candidates))
        self.assertTrue(all("record" not in item for item in candidates))

    def test_evidence_occurrence_is_bounded(self) -> None:
        response = self._term("Cobalt", "Cobalt is the named mechanism")
        response["evidence"][0]["occurrence"] = 10_001
        path = self._run("occurrence-bound", response)
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("invalid_evidence", candidate["qa"]["reason_codes"])

    def test_many_short_source_chunks_exceed_extractive_budget(self) -> None:
        chunk = "abcdefghij"
        source_text = " ".join(f"{chunk}{index:04d}" for index in range(120))
        self.source.write_text(source_text, encoding="utf-8")
        canonical = load_language_data(self.project, "en")
        canonical["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project / "data" / "pack.en.json", canonical)
        response = {
            "collection": "chapters",
            "record": {
                "id": "CH-01",
                "title": "The Gate",
                "pages": "1-12",
                "sections": ["First mechanism"],
                "summary": "A short overview.",
                "terms": [f"{chunk}{index:04d}" for index in range(100)],
                "status": "draft",
            },
            "evidence": [{"snippet": f"{chunk}0000"}],
        }
        path = self._run("extractive-budget", response)
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["candidate_state"], "quarantined")
        self.assertIn("source_copy_risk", candidate["qa"]["reason_codes"])
        self.assertNotIn("record", candidate)

    def test_canonical_change_after_acceptance_causes_cas_failure(self) -> None:
        path = self._run("canonical-stale", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        canonical = load_language_data(self.project, "en")
        canonical["chapters"][0]["summary"] = "An intervening human edit."
        write_json(self.project / "data" / "pack.en.json", canonical)
        before = (self.project / "data" / "pack.en.json").read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "changed after candidate generation"):
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        self.assertEqual((self.project / "data" / "pack.en.json").read_bytes(), before)

    def test_primary_candidate_cannot_break_bilingual_parity(self) -> None:
        bilingual = self.root / "bilingual"
        create_project(
            bilingual,
            title="Evidence Garden",
            author="Ada Example",
            languages=["en", "ja"],
            primary_language="en",
        )
        data_by_lang = {}
        for language in ("en", "ja"):
            data = load_language_data(bilingual, language)
            data["source"] = {
                "format": "text",
                "name": self.source.name,
                "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            }
            chapter = {
                "id": "CH-01",
                "title": "The Gate" if language == "en" else "門",
                "pages": "1-12",
                "sections": ["First mechanism" if language == "en" else "第一の機構"],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
            if language == "ja":
                primary_chapter = data_by_lang["en"]["chapters"][0]
                chapter.update(
                    {
                        "source_id": "CH-01",
                        "source_hash": candidate_module.semantic_hash(primary_chapter),
                        "translation_status": "draft",
                    }
                )
            data["chapters"] = [chapter]
            data_by_lang[language] = data
            write_json(bilingual / "data" / f"pack.{language}.json", data)
        response = self._term("Cobalt", "Cobalt is the named mechanism")
        path = create_candidate_run(
            self.root / "bilingual-run",
            source_path=self.source,
            responses=response,
            language="en",
            canonical_data=data_by_lang["en"],
            project_data_by_lang=data_by_lang,
            known_chapter_ids={"CH-01"},
        )
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        before = {
            language: (bilingual / "data" / f"pack.{language}.json").read_bytes()
            for language in ("en", "ja")
        }
        with self.assertRaisesRegex(ReadingPackError, "bilingual projects"):
            apply_candidate_run(
                bilingual,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            )
        for language in ("en", "ja"):
            self.assertEqual(
                (bilingual / "data" / f"pack.{language}.json").read_bytes(),
                before[language],
            )

    def test_chapter_application_preserves_imported_structure(self) -> None:
        response = {
            "collection": "chapters",
            "record": {
                "id": "CH-01",
                "title": "The Gate",
                "pages": "1-12",
                "sections": ["First mechanism"],
                "summary": "The chapter introduces a gate and its named mechanism.",
                "terms": ["Cobalt"],
                "status": "approved",
            },
            "evidence": [{"snippet": "The gate opens at dawn"}],
        }
        path = self._run("chapter-merge", response)
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        apply_candidate_run(
            self.project,
            language="en",
            run=path,
            source_path=self.source,
            candidate_ids=[candidate_id],
        )
        chapter = load_language_data(self.project, "en")["chapters"][0]
        self.assertEqual(chapter["title"], "The Gate")
        self.assertEqual(chapter["pages"], "1-12")
        self.assertEqual(chapter["sections"], ["First mechanism"])
        self.assertEqual(chapter["terms"], ["Cobalt"])
        self.assertEqual(chapter["status"], "draft")

    def test_prepared_transaction_recovers_after_manifest_finalize_failure(self) -> None:
        path = self._run("recover", self._term("Cobalt", "Cobalt is the named mechanism"))
        candidate_id = load_candidate_run(path)["candidates"][0]["candidate_id"]
        accept_candidates(path, [candidate_id], reviewer="Test Reviewer")
        original = candidate_module._write_manifest
        calls = 0

        def fail_second(manifest_path, manifest):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ReadingPackError("simulated final manifest failure")
            return original(manifest_path, manifest)

        with mock.patch.object(candidate_module, "_write_manifest", side_effect=fail_second):
            with self.assertRaisesRegex(ReadingPackError, "simulated"):
                apply_candidate_run(
                    self.project,
                    language="en",
                    run=path,
                    source_path=self.source,
                    candidate_ids=[candidate_id],
                )
        self.assertEqual(load_candidate_run(path)["transaction"]["state"], "prepared")
        self.assertEqual(
            apply_candidate_run(
                self.project,
                language="en",
                run=path,
                source_path=self.source,
                candidate_ids=[candidate_id],
            ),
            [candidate_id],
        )
        self.assertIsNone(load_candidate_run(path)["transaction"])
        recovered = load_candidate_run(path)
        self.assertEqual(recovered["candidates"][0]["candidate_state"], "applied")
        self.assertEqual(recovered["application"]["candidate_ids"], [candidate_id])

    def test_invalid_candidate_id_is_not_echoed(self) -> None:
        path = self._run("safe-error", self._term("Cobalt", "Cobalt is the named mechanism"))
        secret = "bad\nSECRET-TEXT"
        with self.assertRaises(ReadingPackError) as raised:
            accept_candidates(path, [secret], reviewer="Test Reviewer")
        self.assertNotIn("SECRET-TEXT", str(raised.exception))

    def test_invalid_record_id_is_not_retained_for_reports(self) -> None:
        response = self._term(
            "Cobalt",
            "Cobalt is the named mechanism",
            identifier="bad\nSECRET-TEXT",
        )
        path = self._run("safe-record-id", response)
        candidate = load_candidate_run(path)["candidates"][0]
        self.assertEqual(candidate["record_id"], "")
        self.assertNotIn("SECRET-TEXT", path.read_text(encoding="utf-8"))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        response = (
            '{"collection":"glossary","collection":"claims",'
            '"record":{},"evidence":[]}'
        )
        with self.assertRaisesRegex(ReadingPackError, "duplicate JSON key"):
            self._run("duplicate-key", response)

    def test_candidate_generation_requires_imported_canonical_source(self) -> None:
        canonical = load_language_data(self.project, "en")
        canonical["source"] = {"format": "none", "name": "", "sha256": ""}
        with self.assertRaisesRegex(ReadingPackError, "not imported"):
            create_candidate_run(
                self.root / "missing-source",
                source_path=self.source,
                responses=self._term("Cobalt", "Cobalt is the named mechanism"),
                language="en",
                canonical_data=canonical,
                project_data_by_lang={"en": canonical},
            )

    def test_pdf_evidence_text_is_derived_by_internal_reader(self) -> None:
        pdf = self.root / "book.pdf"
        pdf.write_bytes(b"%PDF-test")
        canonical = load_language_data(self.project, "en")
        canonical["source"] = {
            "format": "pdf",
            "name": pdf.name,
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        }
        with mock.patch(
            "reading_pack_producer.candidates.extract_pdf_authorized_text",
            return_value="Cobalt is the named mechanism.",
        ) as extractor:
            path = create_candidate_run(
                self.root / "pdf-run",
                source_path=pdf,
                responses=self._term("Cobalt", "Cobalt is the named mechanism"),
                language="en",
                canonical_data=canonical,
                project_data_by_lang={"en": canonical},
                known_chapter_ids={"CH-01"},
            )
        extractor.assert_called_once()
        extracted_path = extractor.call_args.args[0]
        self.assertEqual(extracted_path.suffix, ".pdf")
        self.assertNotEqual(extracted_path, pdf.resolve())
        self.assertFalse(extracted_path.exists())
        self.assertEqual(load_candidate_run(path)["summary"]["ready_for_review"], 1)

    def test_vertical_pdf_selects_reconstructed_authorized_text(self) -> None:
        pdf = self.root / "vertical.pdf"
        pdf.write_bytes(b"%PDF-test")
        canonical = load_language_data(self.project, "en")
        canonical["source"] = {
            "format": "pdf-vertical",
            "name": pdf.name,
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        }
        with mock.patch(
            "reading_pack_producer.candidates.extract_pdf_authorized_text",
            return_value="Cobalt is the named mechanism.",
        ) as extractor:
            path = create_candidate_run(
                self.root / "vertical-pdf-run",
                source_path=pdf,
                responses=self._term("Cobalt", "Cobalt is the named mechanism"),
                language="en",
                canonical_data=canonical,
                project_data_by_lang={"en": canonical},
                known_chapter_ids={"CH-01"},
            )
        self.assertTrue(extractor.call_args.kwargs["vertical"])
        self.assertEqual(load_candidate_run(path)["summary"]["ready_for_review"], 1)

    def test_local_adapter_uses_bounded_json_protocol(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import json,sys; value=json.load(sys.stdin); json.dump({'seen': value['value']}, sys.stdout)",
        ]
        self.assertEqual(
            run_local_adapter(command, {"value": "Cobalt"}, timeout=5, max_output=1024),
            {"seen": "Cobalt"},
        )
        with self.assertRaisesRegex(ReadingPackError, "argument array"):
            run_local_adapter("unsafe shell string", {})

    def test_local_adapter_output_is_hard_limited(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096)"]
        with self.assertRaisesRegex(ReadingPackError, "stdout exceeds"):
            run_local_adapter(command, {}, timeout=5, max_output=64)


if __name__ == "__main__":
    unittest.main()
