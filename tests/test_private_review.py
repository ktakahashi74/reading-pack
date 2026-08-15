from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from reading_pack_producer.candidates import accept_candidates, create_candidate_run, load_candidate_run
from reading_pack.errors import ReadingPackError
from reading_pack_producer.private_review import render_private_candidate_review
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.work_ledger import (
    create_semantic_review,
    create_work_ledger,
    reconcile_work_results,
    write_semantic_review,
)


class PrivateCandidateReviewTests(unittest.TestCase):
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
        self.run_directory = self.project / ".reading-pack" / "runs" / "review-run"
        self.manifest_path = create_candidate_run(
            self.run_directory,
            source_path=self.source,
            responses=[
                {
                    "collection": "glossary",
                    "record": {
                        "id": "TERM-EXISTING",
                        "term": "Cobalt",
                        "chapter_id": "CH-01",
                        "status": "approved",
                    },
                    "evidence": [{"snippet": "Cobalt is the named mechanism"}],
                }
            ],
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            project_data_by_lang={"en": load_language_data(self.project, "en")},
            known_chapter_ids={"CH-01"},
            run_id="review-run",
            created_at="2026-08-13T00:00:00+00:00",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_renders_private_side_by_side_review_without_mutating_manifest(self) -> None:
        before = self.manifest_path.read_bytes()
        candidate_id = load_candidate_run(self.manifest_path)["candidates"][0]["candidate_id"]

        output = render_private_candidate_review(
            self.project,
            run=self.run_directory,
            source_path=self.source,
            output_path=Path("selected.html"),
            candidate_ids=[candidate_id],
            context_characters=40,
        )

        self.assertEqual(output, self.project / ".reading-pack" / "reviews" / "selected.html")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("Current canonical record", rendered)
        self.assertIn("Proposed record", rendered)
        self.assertIn("Garden", rendered)
        self.assertIn("Cobalt", rendered)
        self.assertIn("<mark>cobalt is the named mechanism</mark>", rendered)
        self.assertIn("source excerpts", rendered)
        self.assertIn("Content-Security-Policy", rendered)
        self.assertNotIn("<script", rendered.lower())
        self.assertIn(f"--id {candidate_id}", rendered)
        self.assertNotIn("--all", rendered)
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertNotIn(
            "Cobalt is the named mechanism",
            self.manifest_path.read_text(encoding="utf-8"),
        )

    def test_renders_legacy_manifest_without_source_identity_fields(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        del manifest["source"]["id"]
        del manifest["source"]["role"]
        from reading_pack_producer.candidates import _manifest_integrity

        manifest["integrity_sha256"] = _manifest_integrity(manifest)
        write_json(self.manifest_path, manifest)
        output = render_private_candidate_review(
            self.project,
            run=self.run_directory,
            source_path=self.source,
            output_path=Path("legacy.html"),
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("SRC-1", rendered)
        self.assertIn("primary-book", rendered)

    def test_refuses_output_outside_private_review_directory(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "direct child"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=self.root / "public.html",
            )
        self.assertFalse((self.root / "public.html").exists())

    def test_refuses_to_overwrite_a_review(self) -> None:
        output = render_private_candidate_review(
            self.project,
            run=self.run_directory,
            source_path=self.source,
            output_path=Path("once.html"),
        )
        original = output.read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "refusing to overwrite"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("once.html"),
            )
        self.assertEqual(output.read_bytes(), original)

    def test_refuses_stale_source_before_writing_review(self) -> None:
        self.source.write_text("The source changed.\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "stale|does not match"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("stale-source.html"),
            )
        self.assertFalse(
            (self.project / ".reading-pack" / "reviews" / "stale-source.html").exists()
        )

    def test_refuses_stale_canonical_snapshot_before_writing_review(self) -> None:
        data_path = self.project / "data" / "pack.en.json"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        data["book"]["title"] = "Intervening edit"
        write_json(data_path, data)
        with self.assertRaisesRegex(ReadingPackError, "canonical data changed"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("stale-canonical.html"),
            )
        self.assertFalse(
            (self.project / ".reading-pack" / "reviews" / "stale-canonical.html").exists()
        )

    def test_escapes_candidate_and_source_markup(self) -> None:
        source = self.root / "markup-source.txt"
        source.write_text(
            "The literal term <script>alert(1)</script> appears in a security example.\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"] = {
            "format": "text",
            "name": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        write_json(self.project / "data" / "pack.en.json", data)
        markup_run = self.project / ".reading-pack" / "runs" / "markup-run"
        create_candidate_run(
            markup_run,
            source_path=source,
            responses=[
                {
                    "collection": "glossary",
                    "record": {
                        "id": "TERM-MARKUP",
                        "term": "<script>alert(1)</script>",
                        "chapter_id": "CH-01",
                        "status": "draft",
                    },
                    "evidence": [{"snippet": "<script>alert(1)</script> appears"}],
                }
            ],
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            project_data_by_lang={"en": load_language_data(self.project, "en")},
            known_chapter_ids={"CH-01"},
            run_id="markup-run",
            created_at="2026-08-13T00:00:00+00:00",
        )

        output = render_private_candidate_review(
            self.project,
            run=markup_run,
            source_path=source,
            output_path=Path("escaped.html"),
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

    def test_unknown_candidate_selection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "not found"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("unknown.html"),
                candidate_ids=["CAND-00000000000000000000"],
            )

    def test_evidence_context_is_bounded(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "between 40 and 240"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("too-much-context.html"),
                context_characters=241,
            )

    def test_accepted_candidate_shows_only_id_scoped_apply_and_reject(self) -> None:
        candidate_id = load_candidate_run(self.manifest_path)["candidates"][0]["candidate_id"]
        accept_candidates(
            self.run_directory,
            [candidate_id],
            reviewer="Test Reviewer",
            reviewed_at="2026-08-13T00:01:00+00:00",
        )
        output = render_private_candidate_review(
            self.project,
            run=self.run_directory,
            source_path=self.source,
            output_path=Path("accepted.html"),
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("candidates apply", rendered)
        self.assertIn("candidates reject", rendered)
        self.assertNotIn("candidates accept", rendered)
        self.assertIn(f"--id {candidate_id}", rendered)
        self.assertNotIn("--all", rendered)

    def test_renders_integrity_bound_excerpt_free_semantic_findings(self) -> None:
        manifest = load_candidate_run(self.manifest_path)
        candidate = manifest["candidates"][0]
        candidate_id = candidate["candidate_id"]
        evidence_id = candidate["evidence_refs"][0]["id"]
        ledger = create_work_ledger(
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            modules=["glossary"],
        )
        work_id = ledger["items"][0]["work_id"]
        ledger = reconcile_work_results(
            ledger,
            {
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "assessment": {
                    "status": "complete",
                    "assessor": "Independent semantic reviewer",
                    "assessed_candidate_ids": [candidate_id],
                },
                "results": [
                    {
                        "work_id": work_id,
                        "status": "complete",
                        "reason_code": "",
                        "candidate_ids": [candidate_id],
                    }
                ],
            },
            manifest,
        )
        semantic = create_semantic_review(
            ledger=ledger,
            candidate_run=manifest,
            findings_input={
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "assessment": {
                    "status": "complete",
                    "assessor": "Independent semantic reviewer",
                    "assessed_candidate_ids": [candidate_id],
                },
                "findings": [
                    {
                        "work_id": work_id,
                        "candidate_id": candidate_id,
                        "category": "missing_qualifier",
                        "severity": "error",
                        "reason_code": "scope_not_preserved",
                        "evidence_ref_ids": [evidence_id],
                    }
                ],
            },
        )
        semantic_path = self.project / ".reading-pack" / "semantic-review.json"
        write_semantic_review(semantic_path, semantic)

        output = render_private_candidate_review(
            self.project,
            run=self.run_directory,
            source_path=self.source,
            output_path=Path("semantic.html"),
            semantic_review_path=semantic_path,
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("Semantic findings", rendered)
        self.assertIn("missing_qualifier", rendered)
        self.assertIn("scope_not_preserved", rendered)
        self.assertIn(evidence_id, rendered)
        self.assertNotIn("No bound semantic finding", rendered)

    def test_refuses_semantic_review_bound_to_stale_manifest(self) -> None:
        manifest = load_candidate_run(self.manifest_path)
        candidate_id = manifest["candidates"][0]["candidate_id"]
        ledger = create_work_ledger(
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            modules=["glossary"],
        )
        work_id = ledger["items"][0]["work_id"]
        ledger = reconcile_work_results(
            ledger,
            {
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "results": [
                    {
                        "work_id": work_id,
                        "status": "complete",
                        "reason_code": "",
                        "candidate_ids": [candidate_id],
                    }
                ],
            },
            manifest,
        )
        semantic = create_semantic_review(
            ledger=ledger,
            candidate_run=manifest,
            findings_input={
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "assessment": {
                    "status": "complete",
                    "assessor": "Independent semantic reviewer",
                    "assessed_candidate_ids": [candidate_id],
                },
                "findings": [],
            },
        )
        semantic_path = self.project / ".reading-pack" / "stale-semantic.json"
        write_semantic_review(semantic_path, semantic)
        accept_candidates(self.run_directory, [candidate_id], reviewer="Test Reviewer")

        with self.assertRaisesRegex(ReadingPackError, "semantic review is stale"):
            render_private_candidate_review(
                self.project,
                run=self.run_directory,
                source_path=self.source,
                output_path=Path("stale-semantic.html"),
                semantic_review_path=semantic_path,
            )


if __name__ == "__main__":
    unittest.main()
