from __future__ import annotations

import hashlib
import re
import stat
import tempfile
import unittest
from pathlib import Path

from reading_pack_producer.candidates import (
    _manifest_integrity,
    create_candidate_run,
    load_candidate_run,
    normalize_text,
)
from reading_pack_producer.catalog_extraction import (
    _inventory_id,
    _inventory_integrity,
    create_catalog_candidate_run,
    extract_catalog,
    load_catalog_inventory,
    write_catalog_inventory,
)
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.review_bundle import ReviewBundleArtifact, render_private_review_bundle
from reading_pack_producer.work_ledger import (
    create_semantic_review,
    create_work_ledger,
    reconcile_work_results,
    write_semantic_review,
    write_work_ledger,
)
from tests.support import cli


class PrivateReviewBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.primary_source = self.root / "book.txt"
        self.primary_source.write_text(
            "Chapter One maps the Cobalt mechanism. Ada Example identifies the Dawn Gate. "
            "The central claim is that verified evidence improves review. "
            "Reference documentation is available at https://example.test/evidence.\n",
            encoding="utf-8",
        )
        self.qa_source = self.root / "author-qa.txt"
        self.qa_source.write_text(
            "Criticism: The method only samples claims. "
            "Impact: Coverage may be incomplete. "
            "Response: The workflow records omissions. "
            "Remaining uncertainty: Author review is pending.\n",
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
        data["source"] = {
            "format": "text",
            "name": self.primary_source.name,
            "sha256": hashlib.sha256(self.primary_source.read_bytes()).hexdigest(),
        }
        data["chapters"] = [
            {
                "id": "CH-01",
                "title": "Chapter One",
                "pages": "1-10",
                "sections": ["Cobalt mechanism"],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        ]
        write_json(self.project / "data" / "pack.en.json", data)

        self.main_run = self.project / ".reading-pack" / "runs" / "main-run"
        create_candidate_run(
            self.main_run,
            source_path=self.primary_source,
            responses=[
                {
                    "collection": "chapters",
                    "record": {
                        "id": "CH-01",
                        "title": "Chapter One",
                        "pages": "1-10",
                        "sections": ["Cobalt mechanism"],
                        "summary": "Cobalt organizes the review.",
                        "terms": ["Cobalt"],
                        "status": "draft",
                    },
                    "evidence": [
                        {
                            "snippet": "Cobalt mechanism",
                            "supports_field": "summary",
                        }
                    ],
                },
                {
                    "collection": "claims",
                    "record": {
                        "id": "CL-01",
                        "layer": "book",
                        "kind": "central",
                        "statement": "Verified evidence improves review.",
                        "chapter_ids": ["CH-01"],
                        "status": "draft",
                    },
                    "evidence": [
                        {
                            "snippet": "verified evidence improves review",
                            "supports_field": "statement",
                        }
                    ],
                },
                {
                    "collection": "names",
                    "record": {
                        "id": "NAME-ADA",
                        "name": "Ada Example",
                        "chapter_id": "CH-01",
                        "status": "draft",
                    },
                    "evidence": [{"snippet": "Ada Example identifies"}],
                },
                {
                    "collection": "glossary",
                    "record": {
                        "id": "TERM-COBALT",
                        "term": "Cobalt",
                        "chapter_id": "CH-01",
                        "status": "draft",
                    },
                    "evidence": [{"snippet": "Cobalt mechanism"}],
                },
                {
                    "collection": "references",
                    "record": {
                        "id": "REF-EVIDENCE",
                        "url": "https://example.test/evidence",
                        "label": "Evidence documentation",
                        "status": "draft",
                    },
                    "evidence": [
                        {"snippet": "https://example.test/evidence"}
                    ],
                },
            ],
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            project_data_by_lang={"en": load_language_data(self.project, "en")},
            known_chapter_ids={"CH-01"},
            run_id="main-run",
            created_at="2026-08-13T00:00:00+00:00",
        )
        qa_hash = hashlib.sha256(self.qa_source.read_bytes()).hexdigest()
        self.qa_run = self.project / ".reading-pack" / "runs" / "qa-run"
        create_candidate_run(
            self.qa_run,
            source_path=self.qa_source,
            responses=[
                {
                    "collection": "misreadings",
                    "record": {
                        "id": "MIS-SAMPLING",
                        "kind": "open_objection",
                        "misreading": "The method only samples claims.",
                        "impact": "Coverage may be incomplete.",
                        "response": "The workflow records omissions.",
                        "remaining_uncertainty": "Author review is pending.",
                        "chapter_ids": ["CH-01"],
                        "claim_ids": ["CL-01"],
                        "status": "draft",
                    },
                    "evidence": [
                        {
                            "snippet": "The method only samples claims",
                            "supports_field": "misreading",
                        },
                        {
                            "snippet": "Coverage may be incomplete",
                            "supports_field": "impact",
                        },
                        {
                            "snippet": "The workflow records omissions",
                            "supports_field": "response",
                        },
                        {
                            "snippet": "Author review is pending",
                            "supports_field": "remaining_uncertainty",
                        },
                    ],
                }
            ],
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            project_data_by_lang={"en": load_language_data(self.project, "en")},
            known_chapter_ids={"CH-01"},
            run_id="qa-run",
            created_at="2026-08-13T00:01:00+00:00",
            support_source={
                "id": "SRC-QA",
                "role": "author-qa",
                "language": "en",
                "format": "text",
                "name": self.qa_source.name,
                "sha256": qa_hash,
                "size_bytes": self.qa_source.stat().st_size,
            },
        )

        manifest = load_candidate_run(self.main_run)
        candidates_by_collection = {
            candidate["collection"]: candidate["candidate_id"]
            for candidate in manifest["candidates"]
            if candidate["candidate_state"] == "ready_for_review"
        }
        ledger = create_work_ledger(
            language="en",
            canonical_data=load_language_data(self.project, "en"),
            modules=["chapters", "claims", "names", "glossary", "references"],
        )
        results = []
        for item in ledger["items"]:
            candidate_id = candidates_by_collection.get(item["module"])
            if item["scope"].get("kind") == "book" and item["module"] == "claims":
                candidate_id = None
            results.append(
                {
                    "work_id": item["work_id"],
                    "status": "complete" if candidate_id else "no_supported_candidate",
                    "reason_code": "" if candidate_id else "no_book_level_claim",
                    "candidate_ids": [candidate_id] if candidate_id else [],
                }
            )
        ledger = reconcile_work_results(
            ledger,
            {
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "results": results,
            },
            manifest,
        )
        self.ledger_path = self.project / ".reading-pack" / "main-ledger.json"
        write_work_ledger(self.ledger_path, ledger)
        assessed = [
            candidate_id
            for item in ledger["items"]
            for candidate_id in item["candidate_ids"]
        ]
        semantic = create_semantic_review(
            ledger=ledger,
            candidate_run=manifest,
            findings_input={
                "schema_version": 1,
                "plan_id": ledger["plan_id"],
                "run_id": manifest["run_id"],
                "assessment": {
                    "status": "complete",
                    "assessor": "Independent reviewer",
                    "assessed_candidate_ids": assessed,
                },
                "findings": [],
            },
        )
        self.semantic_path = self.project / ".reading-pack" / "main-semantic.json"
        write_semantic_review(self.semantic_path, semantic)

        normalized_source = normalize_text(
            self.primary_source.read_text(encoding="utf-8")
        )
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.primary_source,
            language="en",
            chapter_spans=[
                {
                    "chapter_id": "CH-01",
                    "char_start": 0,
                    "char_end": len(normalized_source),
                    "span_sha256": hashlib.sha256(
                        normalized_source.encode("utf-8")
                    ).hexdigest(),
                }
            ],
        )
        self.catalog_inventory_path = (
            self.project / ".reading-pack" / "catalog-inventory.json"
        )
        write_catalog_inventory(self.catalog_inventory_path, inventory)
        self.catalog_run = self.project / ".reading-pack" / "runs" / "catalog-run"
        create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.primary_source,
            run_directory=self.catalog_run,
            run_id="catalog-run",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def artifacts(self) -> list[ReviewBundleArtifact]:
        return [
            ReviewBundleArtifact(
                run=self.main_run,
                source_path=self.primary_source,
                work_ledger_path=self.ledger_path,
                semantic_review_path=self.semantic_path,
            ),
            ReviewBundleArtifact(run=self.qa_run, source_path=self.qa_source),
        ]

    def catalog_artifact(self) -> ReviewBundleArtifact:
        return ReviewBundleArtifact(
            run=self.catalog_run,
            source_path=self.primary_source,
            catalog_inventory_path=self.catalog_inventory_path,
        )

    def rebind_catalog_after_mutation(self, inventory: dict) -> None:
        inventory["inventory_id"] = _inventory_id(inventory)
        inventory["integrity_sha256"] = _inventory_integrity(inventory)
        write_json(self.catalog_inventory_path, inventory)
        manifest = load_candidate_run(self.catalog_run)
        manifest["generator"]["revision"] = f"1:{inventory['inventory_id']}"
        manifest["generator"]["settings_hash"] = inventory["integrity_sha256"]
        manifest["integrity_sha256"] = _manifest_integrity(manifest)
        write_json(self.catalog_run / "manifest.json", manifest)

    def test_combines_all_human_review_sections_without_bulk_approval(self) -> None:
        manifests_before = [
            (run / "manifest.json").read_bytes()
            for run in (self.main_run, self.qa_run)
        ]
        output = render_private_review_bundle(
            self.project,
            artifacts=self.artifacts(),
            output_path=Path("all-artifacts.html"),
            context_characters=40,
        )

        self.assertEqual(
            output,
            self.project / ".reading-pack" / "reviews" / "all-artifacts.html",
        )
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        rendered = output.read_text(encoding="utf-8")
        for label in (
            "Chapters and structure",
            "Chapter summaries",
            "Claims",
            "People index",
            "Term index",
            "References",
            "Author Q&amp;A and reading issues",
        ):
            self.assertIn(label, rendered)
        self.assertIn("Chapter scope: CH-01", rendered)
        self.assertIn("Ada Example", rendered)
        self.assertIn("Cobalt organizes the review", rendered)
        self.assertIn("open_objection", rendered)
        self.assertIn("SRC-QA", rendered)
        self.assertIn("author-qa.txt", rendered)
        self.assertIn("supports", rendered)
        self.assertIn("span sha256", rendered)
        self.assertIn("<mark>cobalt mechanism</mark>", rendered)
        self.assertIn("semantic review", rendered.lower())
        self.assertIn("review_required", rendered)
        self.assertIn("not_available", rendered)
        self.assertIn(
            "does not accept, apply, approve, or publish any candidate", rendered
        )
        self.assertIn("every decision remains candidate-specific", rendered)
        self.assertRegex(rendered, r"RB-[A-F0-9]{20}")
        self.assertRegex(rendered, r"sha256:[a-f0-9]{64}")
        self.assertIn("Content-Security-Policy", rendered)
        self.assertNotIn("accept-all", rendered.lower())
        self.assertNotIn("--all", rendered)
        self.assertNotIn("candidates accept", rendered)
        self.assertNotIn("<script", rendered.lower())
        self.assertEqual(
            manifests_before,
            [
                (run / "manifest.json").read_bytes()
                for run in (self.main_run, self.qa_run)
            ],
        )

    def test_default_output_name_is_bound_to_artifact_identities(self) -> None:
        output = render_private_review_bundle(
            self.project,
            artifacts=reversed(self.artifacts()),
        )
        self.assertRegex(output.name, r"review-bundle-[a-f0-9]{20}\.html")
        rendered = output.read_text(encoding="utf-8")
        bundle_id = re.search(r"RB-[A-F0-9]{20}", rendered)
        self.assertIsNotNone(bundle_id)
        self.assertEqual(output.stem.removeprefix("review-bundle-").upper(), bundle_id.group()[3:])

    def test_catalog_summary_is_body_free_and_bound_into_bundle(self) -> None:
        inventory = load_catalog_inventory(self.catalog_inventory_path)
        output = render_private_review_bundle(
            self.project,
            artifacts=[self.catalog_artifact()],
            output_path=Path("catalog-summary.html"),
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("Catalog extraction report", rendered)
        self.assertIn("Heuristic seed report", rendered)
        self.assertIn("Chapter-map method", rendered)
        self.assertIn("explicit", rendered)
        self.assertIn("Chapter-map review required", rendered)
        self.assertIn("false", rendered)
        self.assertIn("People candidates", rendered)
        self.assertIn("Term candidates", rendered)
        self.assertIn("Reference candidates", rendered)
        self.assertIn("Unresolved people signals", rendered)
        self.assertIn("Unresolved term signals", rendered)
        self.assertIn("Heuristic confidence signals", rendered)
        self.assertIn("heuristic seed", rendered)
        self.assertIn("heuristic confidence", rendered)
        self.assertIn("explicit_http_url", rendered)
        self.assertIn(inventory["inventory_id"], rendered)
        self.assertIn(inventory["integrity_sha256"], rendered)

        report = rendered.split('id="catalog-extraction-report"', 1)[1]
        report = report.split('id="section-chapters"', 1)[0]
        for item in inventory["items"]:
            self.assertNotIn(item["label"], report)

    def test_catalog_marks_verified_recall_additions_separately(self) -> None:
        inventory = load_catalog_inventory(self.catalog_inventory_path)
        generated_run = self.project / ".reading-pack" / "runs" / "catalog-generated"
        create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.primary_source,
            run_directory=generated_run,
            run_id="catalog-generated",
            generated_responses=[
                {
                    "collection": "glossary",
                    "record": {
                        "id": "TERM-DAWN-GATE",
                        "term": "Dawn Gate",
                        "chapter_id": "CH-01",
                        "status": "draft",
                    },
                    "evidence": [
                        {
                            "snippet": "Ada Example identifies the Dawn Gate",
                            "supports_field": "term",
                        }
                    ],
                }
            ],
        )
        output = render_private_review_bundle(
            self.project,
            artifacts=[
                ReviewBundleArtifact(
                    run=generated_run,
                    source_path=self.primary_source,
                    catalog_inventory_path=self.catalog_inventory_path,
                )
            ],
            output_path=Path("catalog-generated.html"),
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("model/NER recall addition", rendered)
        self.assertIn("Dawn Gate", rendered)

    def test_refuses_catalog_inventory_from_a_different_run(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "catalog inventory.*run"):
            render_private_review_bundle(
                self.project,
                artifacts=[
                    ReviewBundleArtifact(
                        run=self.main_run,
                        source_path=self.primary_source,
                        catalog_inventory_path=self.catalog_inventory_path,
                    )
                ],
                output_path=Path("wrong-catalog-run.html"),
            )

    def test_refuses_stale_catalog_normalized_text_hash(self) -> None:
        inventory = load_catalog_inventory(self.catalog_inventory_path)
        inventory["text_sha256"] = "0" * 64
        self.rebind_catalog_after_mutation(inventory)
        with self.assertRaisesRegex(ReadingPackError, "normalized source hash is stale"):
            render_private_review_bundle(
                self.project,
                artifacts=[self.catalog_artifact()],
                output_path=Path("stale-catalog-text.html"),
            )

    def test_refuses_stale_catalog_chapter_span_hash(self) -> None:
        inventory = load_catalog_inventory(self.catalog_inventory_path)
        inventory["chapter_spans"][0]["span_sha256"] = "0" * 64
        self.rebind_catalog_after_mutation(inventory)
        with self.assertRaisesRegex(ReadingPackError, "chapter span.*source"):
            render_private_review_bundle(
                self.project,
                artifacts=[self.catalog_artifact()],
                output_path=Path("stale-catalog-span.html"),
            )

    def test_refuses_output_outside_private_review_directory(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "direct child"):
            render_private_review_bundle(
                self.project,
                artifacts=self.artifacts(),
                output_path=self.root / "public.html",
            )

    def test_refuses_overwrite(self) -> None:
        output = render_private_review_bundle(
            self.project,
            artifacts=self.artifacts(),
            output_path=Path("once.html"),
        )
        original = output.read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "refusing to overwrite"):
            render_private_review_bundle(
                self.project,
                artifacts=self.artifacts(),
                output_path=Path("once.html"),
            )
        self.assertEqual(output.read_bytes(), original)

    def test_refuses_stale_source_before_writing(self) -> None:
        self.qa_source.write_text("Changed Q&A source.\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "stale|does not match"):
            render_private_review_bundle(
                self.project,
                artifacts=self.artifacts(),
                output_path=Path("stale.html"),
            )
        self.assertFalse(
            (self.project / ".reading-pack" / "reviews" / "stale.html").exists()
        )

    def test_refuses_stale_canonical_snapshot_before_writing(self) -> None:
        data = load_language_data(self.project, "en")
        data["book"]["title"] = "Intervening edit"
        write_json(self.project / "data" / "pack.en.json", data)
        with self.assertRaisesRegex(ReadingPackError, "canonical data changed"):
            render_private_review_bundle(
                self.project,
                artifacts=self.artifacts(),
                output_path=Path("stale-canonical.html"),
            )

    def test_semantic_review_requires_its_reconciled_ledger(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "requires.*work ledger"):
            render_private_review_bundle(
                self.project,
                artifacts=[
                    ReviewBundleArtifact(
                        run=self.main_run,
                        source_path=self.primary_source,
                        semantic_review_path=self.semantic_path,
                    )
                ],
                output_path=Path("semantic-without-ledger.html"),
            )

    def test_refuses_duplicate_run(self) -> None:
        artifact = ReviewBundleArtifact(
            run=self.main_run,
            source_path=self.primary_source,
        )
        with self.assertRaisesRegex(ReadingPackError, "duplicate candidate run"):
            render_private_review_bundle(
                self.project,
                artifacts=[artifact, artifact],
                output_path=Path("duplicate.html"),
            )

    def test_cli_combines_multiple_runs_and_bound_review_metadata(self) -> None:
        result = cli(
            "review",
            "bundle",
            "--project",
            str(self.project),
            "--artifact",
            str(self.main_run),
            str(self.primary_source),
            "--artifact",
            str(self.qa_run),
            str(self.qa_source),
            "--ledger",
            "main-run",
            str(self.ledger_path),
            "--semantic-review",
            "main-run",
            str(self.semantic_path),
            "--output",
            "cli-bundle.html",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no candidates were accepted or applied", result.stdout)
        self.assertNotIn("Cobalt mechanism", result.stdout)
        output = self.project / ".reading-pack" / "reviews" / "cli-bundle.html"
        self.assertTrue(output.is_file())
        self.assertIn("Ada Example", output.read_text(encoding="utf-8"))

    def test_cli_attaches_catalog_inventory_by_run_id(self) -> None:
        result = cli(
            "review",
            "bundle",
            "--project",
            str(self.project),
            "--artifact",
            str(self.catalog_run),
            str(self.primary_source),
            "--catalog",
            "catalog-run",
            str(self.catalog_inventory_path),
            "--output",
            "cli-catalog-bundle.html",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Evidence documentation", result.stdout)
        output = (
            self.project
            / ".reading-pack"
            / "reviews"
            / "cli-catalog-bundle.html"
        )
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("Catalog extraction report", rendered)
        self.assertIn("CAT-", rendered)


if __name__ == "__main__":
    unittest.main()
