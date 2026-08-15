from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.importers import ExtractedBook
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack.staging import (
    apply_import_plan,
    apply_manual_outline,
    create_import_plan,
    load_plan,
    validate_import_plan,
    write_plan,
)


def markdown(path: Path, chapters: list[str]) -> None:
    lines = ["# Extracted Metadata Title", ""]
    for index, title in enumerate(chapters):
        lines.extend([f"## {title}", f"PROSE_SENTINEL_{index}", f"### {title} detail"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ImportPlanTests(unittest.TestCase):
    def test_plan_is_deterministic_hierarchical_and_body_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening", "End"])
            first = create_import_plan(source)
            second = create_import_plan(source)
        self.assertEqual(first, second)
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("PROSE_SENTINEL", serialized)
        self.assertEqual(first["source"]["name"], "book.md")
        self.assertNotIn(str(source.parent), serialized)
        self.assertEqual(first["outcome"], "ready")
        kinds = [unit["kind"] for unit in first["units"]]
        self.assertEqual(kinds.count("book"), 1)
        self.assertEqual(kinds.count("chapter"), 2)
        self.assertEqual(kinds.count("section"), 2)
        book = next(unit for unit in first["units"] if unit["kind"] == "book")
        chapters = [unit for unit in first["units"] if unit["kind"] == "chapter"]
        self.assertTrue(all(unit["parent_id"] == book["staging_id"] for unit in chapters))

    def test_staging_ids_survive_unrelated_chapter_insertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["One", "Two"])
            before = create_import_plan(source)
            markdown(source, ["One", "Inserted", "Two"])
            after = create_import_plan(source)
        before_ids = {
            (unit["kind"], unit["title"]): unit["staging_id"]
            for unit in before["units"]
        }
        after_ids = {
            (unit["kind"], unit["title"]): unit["staging_id"]
            for unit in after["units"]
        }
        self.assertEqual(before_ids[("book", "Extracted Metadata Title")], after_ids[("book", "Extracted Metadata Title")])
        self.assertEqual(before_ids[("chapter", "One")], after_ids[("chapter", "One")])
        self.assertEqual(before_ids[("chapter", "Two")], after_ids[("chapter", "Two")])

    def test_write_and_load_validate_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            target = root / "plan.json"
            markdown(source, ["Opening"])
            plan = create_import_plan(source)
            write_plan(target, plan)
            loaded = load_plan(target)
        self.assertEqual(loaded, plan)

    def test_plan_rejects_fields_that_could_carry_body_or_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening"])
            plan = create_import_plan(source)
        malicious = deepcopy(plan)
        malicious["units"][1]["body"] = "PROSE_SENTINEL"
        with self.assertRaisesRegex(ReadingPackError, "unexpected fields"):
            validate_import_plan(malicious)
        malicious = deepcopy(plan)
        malicious["units"][1]["status"] = "approved"
        with self.assertRaisesRegex(ReadingPackError, "unexpected fields"):
            validate_import_plan(malicious)

    def test_plan_checksum_rejects_unreviewed_title_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening"])
            plan = create_import_plan(source)
        plan["units"][1]["title"] = "Silently changed"
        with self.assertRaisesRegex(ReadingPackError, "checksum"):
            validate_import_plan(plan)

    def test_plan_generation_stops_at_the_unit_resource_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening"])
            with patch("reading_pack.staging.MAX_PLAN_UNITS", 2):
                with self.assertRaisesRegex(ReadingPackError, "exceed 2 units"):
                    create_import_plan(source)

    def test_bounded_json_loaders_reject_oversized_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * 32)
            with patch("reading_pack.staging.MAX_PLAN_BYTES", 32):
                with self.subTest(loader="plan"):
                    with self.assertRaisesRegex(ReadingPackError, "exceeds 32 bytes"):
                        load_plan(oversized)
                from reading_pack.staging import load_manual_outline

                with self.subTest(loader="manual outline"):
                    with self.assertRaisesRegex(ReadingPackError, "exceeds 32 bytes"):
                        load_manual_outline(oversized)

    def test_plain_text_fallback_does_not_become_a_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.txt"
            source.write_text("A title\nPROSE_SENTINEL body sentence\n", encoding="utf-8")
            plan = create_import_plan(source)
        self.assertEqual(plan["outcome"], "blocked")
        self.assertNotIn("PROSE_SENTINEL", json.dumps(plan))
        self.assertFalse(any(unit["kind"] == "chapter" for unit in plan["units"]))

    def test_pdf_style_units_have_kinds_and_structured_page_locators(self):
        extracted = ExtractedBook(
            "Book",
            [
                {"id": "CH-PREFACE", "title": "Preface", "pages": "i-x", "sections": []},
                {"id": "CH-01", "title": "Opening", "pages": "1-12", "sections": ["Why"]},
                {"id": "CH-AFTERWORD", "title": "Afterword", "pages": "101", "sections": []},
                {"id": "CH-99", "title": "注", "pages": "102", "sections": []},
            ],
            "pdf",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.pdf"
            source.write_bytes(b"synthetic")
            with patch("reading_pack.staging.extract", return_value=extracted):
                plan = create_import_plan(source)
        self.assertEqual(plan["outcome"], "review_required")
        kinds = [unit["kind"] for unit in plan["units"]]
        self.assertIn("frontmatter", kinds)
        self.assertIn("afterword", kinds)
        self.assertIn("notes", kinds)
        chapter = next(unit for unit in plan["units"] if unit["kind"] == "chapter")
        self.assertIn(
            {"scheme": "printed-page", "start": "1", "end": "12"},
            chapter["locators"],
        )

    def test_manual_outline_recovers_blocked_source_with_review_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "scan.txt"
            source.write_text("Title\nunstructured body sentinel\n", encoding="utf-8")
            plan = create_import_plan(source)
            sidecar = {
                "format_version": 1,
                "source_sha256": plan["source"]["sha256"],
                "reviewer": "A. Editor",
                "reason": "checked against the printed table of contents",
                "chapters": [
                    {
                        "source_key": "CH-01",
                        "kind": "chapter",
                        "title": "Opening",
                        "pages": "1-12",
                        "sections": ["First question"],
                    }
                ],
            }
            reviewed = apply_manual_outline(plan, sidecar)
        serialized = json.dumps(reviewed, ensure_ascii=False)
        self.assertEqual(plan["outcome"], "blocked")
        self.assertEqual(reviewed["outcome"], "review_required")
        self.assertNotIn("unstructured body sentinel", serialized)
        chapter = next(unit for unit in reviewed["units"] if unit["kind"] == "chapter")
        self.assertEqual(chapter["source_key"], "CH-01")
        self.assertEqual(chapter["provenance"][0]["method"], "manual-outline:A. Editor")

    def test_manual_outline_is_bound_to_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening"])
            plan = create_import_plan(source)
        sidecar = {
            "format_version": 1,
            "source_sha256": "0" * 64,
            "reviewer": "Editor",
            "reason": "layout correction",
            "chapters": [],
        }
        with self.assertRaisesRegex(ReadingPackError, "source hash"):
            apply_manual_outline(plan, sidecar)

    def test_manual_outline_caps_total_sections_before_building_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            markdown(source, ["Opening"])
            plan = create_import_plan(source)
        sidecar = {
            "format_version": 1,
            "source_sha256": plan["source"]["sha256"],
            "reviewer": "Editor",
            "reason": "layout correction",
            "chapters": [
                {
                    "source_key": "CH-01",
                    "kind": "chapter",
                    "title": "Opening",
                    "pages": "1-2",
                    "sections": ["One", "Two"],
                }
            ],
        }
        with patch("reading_pack.staging.MAX_MANUAL_SECTIONS", 1):
            with self.assertRaisesRegex(ReadingPackError, "total sections exceed 1"):
                apply_manual_outline(plan, sidecar)


class ApplyImportPlanTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        create_project(
            project,
            title="Configured Title",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        return project

    def test_apply_never_overwrites_configured_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            markdown(source, ["Opening"])
            project = self._project(root)
            plan = create_import_plan(source)
            updated = apply_import_plan(project, plan, "en", source)
        self.assertEqual(updated["book"]["title"], "Configured Title")
        self.assertEqual(updated["chapters"][0]["status"], "draft")
        self.assertEqual(updated["source"]["sha256"], plan["source"]["sha256"])

    def test_reimport_insertion_preserves_ids_editorial_fields_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One", "Two"])
            apply_import_plan(project, create_import_plan(source), "en", source)
            data_path = project / "data" / "pack.en.json"
            data = load_language_data(project, "en")
            one_id, two_id = [record["id"] for record in data["chapters"]]
            data["chapters"][0].update(
                {"summary": "Reviewed summary", "terms": ["term"], "status": "reviewed"}
            )
            data["chapters"][1]["status"] = "approved"
            data["claims"] = [
                {
                    "id": "CL-1",
                    "layer": "descriptive",
                    "kind": "test",
                    "statement": "Linked record",
                    "chapter_ids": [two_id],
                    "status": "draft",
                }
            ]
            write_json(data_path, data)

            markdown(source, ["One", "Inserted", "Two"])
            updated = apply_import_plan(project, create_import_plan(source), "en", source)

        by_title = {record["title"]: record for record in updated["chapters"]}
        self.assertEqual(by_title["One"]["id"], one_id)
        self.assertEqual(by_title["Two"]["id"], two_id)
        self.assertEqual(by_title["One"]["summary"], "Reviewed summary")
        self.assertEqual(by_title["One"]["terms"], ["term"])
        self.assertEqual(by_title["One"]["status"], "draft")
        self.assertEqual(by_title["Two"]["status"], "draft")
        self.assertEqual(by_title["Inserted"]["status"], "draft")
        self.assertNotIn(by_title["Inserted"]["id"], {one_id, two_id})
        self.assertEqual(updated["claims"][0]["chapter_ids"], [two_id])

    def test_structure_change_revokes_existing_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One"])
            apply_import_plan(project, create_import_plan(source), "en", source)
            data_path = project / "data" / "pack.en.json"
            data = load_language_data(project, "en")
            data["chapters"][0]["status"] = "approved"
            data["chapters"][0]["summary"] = "Old approved summary"
            write_json(data_path, data)
            source.write_text(
                "# Extracted Metadata Title\n\n## One\nBody\n### New section\n",
                encoding="utf-8",
            )
            updated = apply_import_plan(project, create_import_plan(source), "en", source)
        self.assertEqual(updated["chapters"][0]["status"], "draft")
        self.assertEqual(updated["chapters"][0]["summary"], "Old approved summary")

    def test_source_content_change_revokes_approval_even_when_structure_is_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One"])
            apply_import_plan(project, create_import_plan(source), "en", source)
            data_path = project / "data" / "pack.en.json"
            data = load_language_data(project, "en")
            data["chapters"][0]["status"] = "approved"
            write_json(data_path, data)

            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "PROSE_SENTINEL_0", "CHANGED_BODY_WITH_THE_SAME_HEADINGS"
                ),
                encoding="utf-8",
            )
            updated = apply_import_plan(
                project, create_import_plan(source), "en", source
            )
        self.assertEqual(updated["chapters"][0]["status"], "draft")

    def test_manual_source_key_maps_a_renamed_chapter_without_losing_editorial_fields(self):
        extracted = ExtractedBook(
            "Book",
            [{"id": "CH-OLD", "title": "Old title", "pages": "1-2", "sections": []}],
            "markdown",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            source.write_bytes(b"synthetic source")
            project = self._project(root)
            with patch("reading_pack.staging.extract", return_value=extracted):
                plan = create_import_plan(source)
            apply_import_plan(project, plan, "en", source)
            data_path = project / "data" / "pack.en.json"
            data = load_language_data(project, "en")
            self.assertEqual(data["chapters"][0]["id"], "CH-OLD")
            data["chapters"][0].update(
                {"summary": "Editorial work", "status": "approved"}
            )
            write_json(data_path, data)
            reviewed = apply_manual_outline(
                plan,
                {
                    "format_version": 1,
                    "source_sha256": plan["source"]["sha256"],
                    "reviewer": "Editor",
                    "reason": "verified chapter rename",
                    "chapters": [
                        {
                            "source_key": "CH-OLD",
                            "kind": "chapter",
                            "title": "Renamed title",
                            "pages": "1-2",
                            "sections": [],
                        }
                    ],
                },
            )
            updated = apply_import_plan(project, reviewed, "en", source)
        self.assertEqual(updated["chapters"][0]["id"], "CH-OLD")
        self.assertEqual(updated["chapters"][0]["title"], "Renamed title")
        self.assertEqual(updated["chapters"][0]["summary"], "Editorial work")
        self.assertEqual(updated["chapters"][0]["status"], "draft")

    def test_translated_reimport_refuses_position_mapping_after_reorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            create_project(
                project,
                title="Configured Title",
                author="Author",
                languages=["ja", "en"],
                primary_language="ja",
            )
            primary_source = root / "primary.md"
            translated_source = root / "translated.md"
            markdown(primary_source, ["一", "二"])
            markdown(translated_source, ["One", "Two"])
            apply_import_plan(
                project, create_import_plan(primary_source), "ja", primary_source
            )
            apply_import_plan(
                project, create_import_plan(translated_source), "en", translated_source
            )
            data_path = project / "data" / "pack.en.json"
            before = data_path.read_bytes()
            markdown(translated_source, ["Two", "One"])
            with self.assertRaisesRegex(
                ReadingPackError, "order/title.*manual reconciliation"
            ):
                apply_import_plan(
                    project,
                    create_import_plan(translated_source),
                    "en",
                    translated_source,
                )
            after = data_path.read_bytes()
        self.assertEqual(after, before)

    def test_concurrent_applies_are_serialized_by_the_project_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One"])
            plan = create_import_plan(source)
            real_load = load_language_data
            guard = threading.Lock()
            start = threading.Barrier(3)
            active = 0
            maximum_active = 0
            failures: list[BaseException] = []

            def delayed_load(project_path: Path, language: str):
                nonlocal active, maximum_active
                with guard:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    time.sleep(0.05)
                    return real_load(project_path, language)
                finally:
                    with guard:
                        active -= 1

            def worker() -> None:
                try:
                    start.wait()
                    apply_import_plan(project, plan, "en", source)
                except BaseException as exc:  # captured for assertion in the main thread
                    failures.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            with patch(
                "reading_pack.staging.load_language_data", side_effect=delayed_load
            ):
                for thread in threads:
                    thread.start()
                start.wait()
                for thread in threads:
                    thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(maximum_active, 1)

    def test_conflict_is_blocked_and_canonical_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["Same", "Same"])
            plan = create_import_plan(source)
            data_path = project / "data" / "pack.en.json"
            before = data_path.read_bytes()
            with self.assertRaisesRegex(ReadingPackError, "blocked"):
                apply_import_plan(project, plan, "en", source)
            after = data_path.read_bytes()
        self.assertEqual(plan["outcome"], "blocked")
        self.assertEqual(after, before)

    def test_source_mismatch_is_blocked_and_canonical_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One"])
            plan = create_import_plan(source)
            data_path = project / "data" / "pack.en.json"
            before = data_path.read_bytes()
            markdown(source, ["Changed"])
            with self.assertRaisesRegex(ReadingPackError, "does not match"):
                apply_import_plan(project, plan, "en", source)
            after = data_path.read_bytes()
        self.assertEqual(after, before)

    def test_unmatched_existing_chapter_requires_manual_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            project = self._project(root)
            markdown(source, ["One", "Two"])
            apply_import_plan(project, create_import_plan(source), "en", source)
            markdown(source, ["One"])
            plan = create_import_plan(source)
            data_path = project / "data" / "pack.en.json"
            before = data_path.read_bytes()
            with self.assertRaisesRegex(ReadingPackError, "manual reconciliation"):
                apply_import_plan(project, plan, "en", source)
            after = data_path.read_bytes()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
