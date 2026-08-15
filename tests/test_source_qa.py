from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from reading_pack_producer.author_qa import (
    create_qa_candidate_run,
    create_qa_plan,
    load_qa_plan,
    qa_plan_to_candidate_responses,
    validate_qa_plan,
    write_qa_plan,
)
from reading_pack_producer.candidates import (
    accept_candidates,
    apply_candidate_run,
    create_candidate_run,
    load_candidate_run,
)
from reading_pack.errors import ReadingPackError
from reading_pack.importers import import_manuscript
from reading_pack.project import create_project, load_language_data
from reading_pack.source_registry import (
    apply_source_plan,
    create_source_plan,
    list_sources,
    load_source_plan,
    verify_registered_source,
    write_source_plan,
)
from tests.support import cli


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _qa_item(source_key: str = "critique-limit") -> dict[str, object]:
    return {
        "source_key": source_key,
        "kind": "misreading",
        "chapter_ids": ["CH-01"],
        "claim_ids": [],
        "criticism": "The book says every system has no limit.",
        "impact": "This would overturn the first chapter's scope.",
        "response": "The chapter says limits can move, not that limits disappear.",
        "remaining_uncertainty": "The rate at which each limit moves remains uncertain.",
    }


class SourceRegistryTests(unittest.TestCase):
    def test_src_1_is_reserved_for_the_primary_book_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "qa.json"
            _write_json(source, {"format_version": 1, "items": [_qa_item()]})
            with self.assertRaisesRegex(ReadingPackError, "SRC-1 is reserved"):
                create_source_plan(
                    source,
                    source_id="SRC-1",
                    role="author-qa",
                    language="en",
                )
            with self.assertRaisesRegex(ReadingPackError, "primary-book is reserved"):
                create_source_plan(
                    source,
                    source_id="SRC-BOOK-2",
                    role="primary-book",
                    language="en",
                )

    def test_plan_and_registry_are_body_free_and_recheck_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            source = root / "author-qa.json"
            sentinel = "CONFIDENTIAL_QA_BODY_SENTINEL"
            _write_json(
                source,
                {"format_version": 1, "items": [{**_qa_item(), "response": sentinel}]},
            )
            plan = create_source_plan(
                source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )
            serialized = json.dumps(plan)
            self.assertNotIn(sentinel, serialized)
            self.assertNotIn(str(root), serialized)
            path = root / "source-plan.json"
            write_source_plan(path, plan)
            self.assertEqual(load_source_plan(path), plan)
            applied = apply_source_plan(project, plan, source)
            self.assertEqual(applied["id"], "SRC-QA-1")
            self.assertEqual(list_sources(project), [applied])
            self.assertEqual(
                verify_registered_source(
                    project, "SRC-QA-1", source, expected_role="author-qa"
                ),
                applied,
            )
            source.write_text(source.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReadingPackError, "stale or mismatched"):
                verify_registered_source(project, "SRC-QA-1", source)

    def test_primary_registration_must_match_canonical_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            canonical = root / "book-a.md"
            other = root / "book-b.md"
            canonical.write_text("# Book A\n", encoding="utf-8")
            other.write_text("# Book B\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, canonical, lang="en")
            plan = create_source_plan(
                other,
                source_id="SRC-1",
                role="primary-book",
                language="en",
            )
            with self.assertRaisesRegex(ReadingPackError, "does not match canonical"):
                apply_source_plan(project, plan, other)

    def test_plan_checksum_prevents_unreviewed_role_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "qa.json"
            _write_json(source, {"format_version": 1, "items": [_qa_item()]})
            plan = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )
        plan["source"]["role"] = "author-canon"
        with self.assertRaisesRegex(ReadingPackError, "checksum"):
            apply_source_plan(Path(tmp), plan, source)

    def test_source_plan_writer_refuses_existing_and_canonical_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.json"
            _write_json(source, {"format_version": 1, "items": [_qa_item()]})
            plan = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )
            existing = root / "plan.json"
            existing.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ReadingPackError, "refusing to overwrite"):
                write_source_plan(existing, plan)
            self.assertEqual(existing.read_text(), "keep")
            data = root / "data"
            data.mkdir()
            with self.assertRaisesRegex(ReadingPackError, "canonical or generated"):
                write_source_plan(data / "plan.json", plan)


class AuthorQaPlanTests(unittest.TestCase):
    def test_structured_plan_has_four_facet_hashes_but_no_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.json"
            value = {"format_version": 1, "items": [_qa_item()]}
            _write_json(source, value)
            source_plan = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )
            first = create_qa_plan(source, source_plan["source"])
            second = create_qa_plan(source, source_plan["source"])
            path = root / "qa-plan.json"
            write_qa_plan(path, first)
            loaded = load_qa_plan(path)
        self.assertEqual(first, second)
        self.assertEqual(first, loaded)
        self.assertEqual(first["outcome"], "ready")
        serialized = json.dumps(first, ensure_ascii=False)
        for facet in ("criticism", "impact", "response", "remaining_uncertainty"):
            self.assertNotIn(str(value["items"][0][facet]), serialized)
            self.assertIn(facet, first["items"][0]["facets"])
        self.assertNotIn(str(root), serialized)

    def test_stable_ids_survive_unrelated_item_insertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.json"
            original = {"format_version": 1, "items": [_qa_item("critique-limit")]}
            _write_json(source, original)
            first_source = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            first = create_qa_plan(source, first_source)
            inserted = {
                "format_version": 1,
                "items": [_qa_item("critique-new"), _qa_item("critique-limit")],
            }
            _write_json(source, inserted)
            second_source = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            second = create_qa_plan(source, second_source)
        old = first["items"][0]
        retained = next(item for item in second["items"] if item["source_key"] == "critique-limit")
        self.assertEqual(old["qa_id"], retained["qa_id"])
        self.assertEqual(old["candidate_record_id"], retained["candidate_record_id"])

    def test_org_appendix_style_requires_explicit_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "appendix.org"
            source.write_text(
                """#+TITLE: Q&A
* Group
** A common objection (Chapter 1)
:PROPERTIES:
:CUSTOM_ID: critique-limit
:END:
- Criticism :: Every system has no limit.
- Impact :: This would overturn chapter one.
- *Response* :: Limits can move; they do not disappear.
- Remaining uncertainty :: Rates remain uncertain.
""",
                encoding="utf-8",
            )
            source_record = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            unresolved = create_qa_plan(source, source_record)
            self.assertEqual(unresolved["outcome"], "review_required")
            self.assertEqual(unresolved["items"][0]["kind"], "unresolved")
            with self.assertRaisesRegex(ReadingPackError, "unresolved item kinds"):
                qa_plan_to_candidate_responses(unresolved, source)

            source.write_text(
                source.read_text().replace(
                    ":CUSTOM_ID: critique-limit",
                    ":CUSTOM_ID: critique-limit\n:QA_TYPE: misreading",
                ),
                encoding="utf-8",
            )
            source_record = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            ready = create_qa_plan(source, source_record)
            self.assertEqual(ready["outcome"], "ready")
            response = qa_plan_to_candidate_responses(ready, source)[0]
        self.assertEqual(response["record"]["kind"], "misreading")
        self.assertEqual(response["record"]["impact"], "This would overturn chapter one.")
        self.assertEqual(response["record"]["remaining_uncertainty"], "Rates remain uncertain.")

    def test_final_org_facet_stops_before_footnote_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "appendix.org"
            source.write_text(
                """** Objection (Chapter 1)
:PROPERTIES:
:CUSTOM_ID: critique-footnote-boundary
:QA_TYPE: clarification
:END:
- Criticism :: The claim may be read too broadly.
- Impact :: The scope would be overstated.
- Response :: The claim is explicitly conditional.
- Remaining uncertainty :: The threshold remains unknown.

[fn:source] This long footnote is bibliography, not Q&A uncertainty.
""",
                encoding="utf-8",
            )
            record = create_source_plan(
                source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )["source"]
            plan = create_qa_plan(source, record)
            response = qa_plan_to_candidate_responses(plan, source)[0]
        self.assertEqual(
            response["record"]["remaining_uncertainty"],
            "The threshold remains unknown.",
        )
        self.assertNotIn("footnote", json.dumps(response))

    def test_plan_rejects_tampering_and_stale_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.json"
            _write_json(source, {"format_version": 1, "items": [_qa_item()]})
            source_record = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            plan = create_qa_plan(source, source_record)
            tampered = deepcopy(plan)
            tampered["items"][0]["kind"] = "clarification"
            with self.assertRaisesRegex(ReadingPackError, "checksum"):
                validate_qa_plan(tampered)
            source.write_text(source.read_text().replace("limits", "boundaries"), encoding="utf-8")
            with self.assertRaisesRegex(ReadingPackError, "stale"):
                qa_plan_to_candidate_responses(plan, source)

    def test_qa_plan_writer_refuses_existing_and_canonical_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.json"
            _write_json(source, {"format_version": 1, "items": [_qa_item()]})
            source_record = create_source_plan(
                source, source_id="SRC-QA-1", role="author-qa", language="en"
            )["source"]
            plan = create_qa_plan(source, source_record)
            existing = root / "plan.json"
            existing.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ReadingPackError, "refusing to overwrite"):
                write_qa_plan(existing, plan)
            self.assertEqual(existing.read_text(), "keep")
            templates = root / "templates"
            templates.mkdir()
            with self.assertRaisesRegex(ReadingPackError, "canonical or generated"):
                write_qa_plan(templates / "plan.json", plan)


class AuthorQaCandidateWorkflowTests(unittest.TestCase):
    def test_generated_qa_responses_cover_plan_and_bind_each_facet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            qa_source = root / "qa.json"
            _write_json(qa_source, {"format_version": 1, "items": [_qa_item()]})
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )
            apply_source_plan(project, source_plan, qa_source)
            plan = create_qa_plan(qa_source, source_plan["source"])
            planned = plan["items"][0]
            original = _qa_item()
            response = {
                "collection": "misreadings",
                "record": {
                    "id": planned["candidate_record_id"],
                    "kind": planned["kind"],
                    "misreading": "The scope may be read as unlimited.",
                    "impact": "That reading would change chapter one.",
                    "response": "The author limits the claim to moving boundaries.",
                    "remaining_uncertainty": "The speed of change remains unknown.",
                    "chapter_ids": planned["chapter_ids"],
                    "status": "draft",
                },
                "evidence": [
                    {"snippet": original["criticism"], "supports_field": "misreading"},
                    {"snippet": original["impact"], "supports_field": "impact"},
                    {"snippet": original["response"], "supports_field": "response"},
                    {
                        "snippet": original["remaining_uncertainty"],
                        "supports_field": "remaining_uncertainty",
                    },
                ],
            }
            manifest_path = create_qa_candidate_run(
                project,
                language="en",
                plan=plan,
                source_path=qa_source,
                run_directory=project / ".reading-pack" / "runs" / "generated-qa",
                generated_responses={"candidates": [response]},
            )
            manifest = load_candidate_run(manifest_path)
            self.assertEqual(manifest["summary"]["ready_for_review"], 1)
            self.assertEqual(
                {ref["supports_field"] for ref in manifest["candidates"][0]["evidence_refs"]},
                {"issue", "impact", "response", "remaining_uncertainty"},
            )
            response["evidence"][0]["snippet"] = original["response"]
            with self.assertRaisesRegex(ReadingPackError, "outside its planned facet"):
                create_qa_candidate_run(
                    project,
                    language="en",
                    plan=plan,
                    source_path=qa_source,
                    run_directory=project / ".reading-pack" / "runs" / "cross-wired",
                    generated_responses={"candidates": [response]},
                )

    def test_equal_json_facets_bind_to_distinct_direct_evidence_locators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            qa_source = root / "qa.json"
            _write_json(
                qa_source,
                {
                    "format_version": 1,
                    "items": [
                        _qa_item("critique-first"),
                        _qa_item("critique-second"),
                    ],
                },
            )
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )
            apply_source_plan(project, source_plan, qa_source)
            qa_plan = create_qa_plan(qa_source, source_plan["source"])
            run = project / ".reading-pack" / "runs" / "duplicate-facets"
            manifest_path = create_qa_candidate_run(
                project,
                language="en",
                plan=qa_plan,
                source_path=qa_source,
                run_directory=run,
                run_id="duplicate-facets",
            )
            manifest = load_candidate_run(manifest_path)

        self.assertEqual(manifest["summary"]["ready_for_review"], 2)
        criticism_refs = [
            next(
                ref
                for ref in candidate["evidence_refs"]
                if ref["supports_field"] == "issue"
            )
            for candidate in manifest["candidates"]
        ]
        self.assertTrue(all(ref["support"] == "direct" for ref in criticism_refs))
        self.assertNotEqual(
            criticism_refs[0]["locator"],
            criticism_refs[1]["locator"],
        )

    def test_explicit_json_text_source_decodes_unicode_for_candidate_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            qa_source = root / "author-qa.txt"
            item = _qa_item()
            item.update(
                {
                    "criticism": "主張の範囲が無制限だと読める。",
                    "impact": "その読みでは第一章の射程が変わる。",
                    "response": "著者は限界が消えるのでなく動くと限定する。",
                    "remaining_uncertainty": "限界が動く速度は未確定である。",
                }
            )
            qa_source.write_text(
                json.dumps(
                    {"format_version": 1, "items": [item]},
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
                explicit_format="json",
            )
            apply_source_plan(project, source_plan, qa_source)
            qa_plan = create_qa_plan(qa_source, source_plan["source"])
            run = project / ".reading-pack" / "runs" / "escaped-unicode"
            manifest_path = create_qa_candidate_run(
                project,
                language="en",
                plan=qa_plan,
                source_path=qa_source,
                run_directory=run,
                run_id="escaped-unicode",
            )
            manifest = load_candidate_run(manifest_path)

        self.assertEqual(source_plan["source"]["format"], "json")
        self.assertEqual(manifest["summary"]["ready_for_review"], 1)
        self.assertEqual(
            manifest["normalization"],
            "json-decoded-strings-nfkc-casefold-whitespace-v1",
        )
        self.assertTrue(
            all(
                ref["representation"]
                == "json-decoded-strings-nfkc-casefold-whitespace-v1"
                for ref in manifest["candidates"][0]["evidence_refs"]
            )
        )

    def test_unpaired_surrogate_is_reported_as_reading_pack_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa_source = root / "qa.json"
            item = _qa_item()
            item["criticism"] = "\ud800"
            qa_source.write_text(
                json.dumps(
                    {"format_version": 1, "items": [item]},
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            source_record = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )["source"]
            with self.assertRaises(ReadingPackError):
                create_qa_plan(qa_source, source_record)

    def test_qa_source_language_must_match_target_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            book = root / "book.md"
            book.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, book, lang="en")
            qa_source = root / "qa.json"
            _write_json(qa_source, {"format_version": 1, "items": [_qa_item()]})
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-JA",
                role="author-qa",
                language="ja",
            )
            apply_source_plan(project, source_plan, qa_source)
            plan = create_qa_plan(qa_source, source_plan["source"])
            with self.assertRaisesRegex(ReadingPackError, "language does not match"):
                create_qa_candidate_run(
                    project,
                    language="en",
                    plan=plan,
                    source_path=qa_source,
                    run_directory=project / ".reading-pack" / "runs" / "wrong-language",
                )

    def test_long_author_qa_copy_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text("# Book\n\n## Chapter One\nBody text.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            # Escaped JSON must be decoded before both evidence matching and
            # copy-risk checks; otherwise ``\\uXXXX`` can hide a verbatim copy.
            copied = "著者QAにだけ含まれる長い応答です。" * 24
            qa_source = root / "qa.json"
            item = _qa_item()
            item["response"] = copied
            qa_source.write_text(
                json.dumps(
                    {"format_version": 1, "items": [item]},
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )
            apply_source_plan(project, source_plan, qa_source)
            qa_plan = create_qa_plan(qa_source, source_plan["source"])
            manifest_path = create_qa_candidate_run(
                project,
                language="en",
                plan=qa_plan,
                source_path=qa_source,
                run_directory=project / ".reading-pack" / "runs" / "qa-copy",
                run_id="qa-copy",
            )
            manifest = load_candidate_run(manifest_path)
        self.assertEqual(manifest["summary"]["quarantined"], 1)
        self.assertIn(
            "source_copy_risk",
            manifest["candidates"][0]["qa"]["reason_codes"],
        )
        self.assertNotIn("record", manifest["candidates"][0])
        self.assertEqual(
            manifest["normalization"],
            "json-decoded-strings-nfkc-casefold-whitespace-v1",
        )

    def test_escaped_json_author_canon_copy_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text(
                "# Book\n\n## Chapter One\nBody text.\n", encoding="utf-8"
            )
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            copied = "著者カノンにだけ含まれる長い応答です。" * 24
            canon_source = root / "author-canon.json"
            canon_source.write_text(
                json.dumps(
                    {"format_version": 1, "response": copied},
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            source_plan = create_source_plan(
                canon_source,
                source_id="SRC-CANON-1",
                role="author-canon",
                language="en",
            )
            support_source = apply_source_plan(project, source_plan, canon_source)
            canonical = load_language_data(project, "en")
            responses = {
                "collection": "misreadings",
                "record": {
                    "id": "MIS-AUTHOR-CANON-COPY",
                    "kind": "clarification",
                    "misreading": "The scope may be read too broadly.",
                    "response": copied,
                    "chapter_ids": ["CH-01"],
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "著者カノンにだけ含まれる長い応答です。",
                        "supports_field": "response",
                    }
                ],
            }
            manifest_path = create_candidate_run(
                project / ".reading-pack" / "runs" / "author-canon-copy",
                source_path=canon_source,
                responses=responses,
                language="en",
                canonical_data=canonical,
                project_data_by_lang={"en": canonical},
                known_chapter_ids={"CH-01"},
                run_id="author-canon-copy",
                support_source=support_source,
            )
            manifest = load_candidate_run(manifest_path)
        self.assertEqual(manifest["source"]["role"], "author-canon")
        self.assertEqual(
            manifest["normalization"],
            "json-decoded-strings-nfkc-casefold-whitespace-v1",
        )
        self.assertEqual(manifest["summary"]["quarantined"], 1)
        self.assertIn(
            "source_copy_risk",
            manifest["candidates"][0]["qa"]["reason_codes"],
        )
        self.assertNotIn("record", manifest["candidates"][0])

    def test_registered_qa_becomes_reviewed_draft_with_support_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            manuscript = root / "book.md"
            manuscript.write_text("# Book\n\n## Chapter One\nBody text.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, manuscript, lang="en")
            qa_source = root / "qa.json"
            _write_json(qa_source, {"format_version": 1, "items": [_qa_item()]})
            source_plan = create_source_plan(
                qa_source,
                source_id="SRC-QA-1",
                role="author-qa",
                language="en",
            )
            apply_source_plan(project, source_plan, qa_source)
            qa_plan = create_qa_plan(qa_source, source_plan["source"])
            run = project / ".reading-pack" / "runs" / "qa-run"
            manifest_path = create_qa_candidate_run(
                project,
                language="en",
                plan=qa_plan,
                source_path=qa_source,
                run_directory=run,
                run_id="qa-run",
            )
            manifest = load_candidate_run(manifest_path)
            self.assertEqual(manifest["source"]["id"], "SRC-QA-1")
            self.assertEqual(manifest["source"]["role"], "author-qa")
            candidate = manifest["candidates"][0]
            self.assertEqual(candidate["candidate_state"], "ready_for_review")
            accept_candidates(run, [candidate["candidate_id"]], reviewer="Editor")
            apply_candidate_run(
                project,
                language="en",
                run=run,
                source_path=qa_source,
                candidate_ids=[candidate["candidate_id"]],
            )
            record = load_language_data(project, "en")["misreadings"][0]
        self.assertEqual(record["kind"], "misreading")
        self.assertEqual(record["impact"], _qa_item()["impact"])
        self.assertEqual(record["remaining_uncertainty"], _qa_item()["remaining_uncertainty"])
        self.assertEqual(record["provenance_source_id"], "SRC-QA-1")
        self.assertEqual(record["provenance_source_hash"], source_plan["source"]["sha256"])
        self.assertEqual(record["status"], "draft")


class SourceQaCliTests(unittest.TestCase):
    def test_cli_candidate_create_binds_registered_support_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            book = root / "book.md"
            book.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            qa_source = root / "qa.json"
            _write_json(qa_source, {"format_version": 1, "items": [_qa_item()]})
            responses = root / "responses.json"
            _write_json(
                responses,
                {
                    "candidates": [
                        {
                            "collection": "misreadings",
                            "record": {
                                "id": "MIS-SUPPORT-1",
                                "kind": "open_objection",
                                "misreading": "The scope may appear unlimited.",
                                "response": "The response confines the scope to moving limits.",
                                "chapter_ids": ["CH-01"],
                                "status": "draft",
                            },
                            "evidence": [
                                {"snippet": str(_qa_item()["criticism"])},
                                {"snippet": str(_qa_item()["response"])},
                            ],
                        }
                    ]
                },
            )
            self.assertEqual(
                cli(
                    "init", str(project), "--title", "Book", "--author", "Author",
                    "--lang", "en",
                ).returncode,
                0,
            )
            self.assertEqual(
                cli("import", str(book), "--project", str(project), "--lang", "en").returncode,
                0,
            )
            source_plan = root / "source-plan.json"
            self.assertEqual(
                cli(
                    "sources", "plan", str(qa_source), "--id", "SRC-QA-1",
                    "--role", "author-qa", "--lang", "en", "--output", str(source_plan),
                ).returncode,
                0,
            )
            self.assertEqual(
                cli(
                    "sources", "apply", str(source_plan), "--source", str(qa_source),
                    "--project", str(project),
                ).returncode,
                0,
            )
            run = project / ".reading-pack" / "runs" / "support-json"
            created = cli(
                "candidates", "create", str(responses), "--source", str(qa_source),
                "--source-id", "SRC-QA-1", "--project", str(project), "--lang", "en",
                "--run-directory", str(run), "--run-id", "support-json",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            manifest = load_candidate_run(run)
        self.assertEqual(manifest["source"]["id"], "SRC-QA-1")
        self.assertEqual(manifest["summary"]["ready_for_review"], 1)
    def test_cli_registers_plans_and_creates_private_qa_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pack"
            book = root / "book.md"
            book.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            qa_source = root / "qa.json"
            _write_json(qa_source, {"format_version": 1, "items": [_qa_item()]})
            source_plan = root / "source-plan.json"
            qa_plan = root / "qa-plan.json"
            run = project / ".reading-pack" / "runs" / "qa-cli"

            self.assertEqual(
                cli(
                    "init", str(project), "--title", "Book", "--author", "Author",
                    "--lang", "en",
                ).returncode,
                0,
            )
            self.assertEqual(
                cli("import", str(book), "--project", str(project), "--lang", "en").returncode,
                0,
            )
            planned = cli(
                "sources", "plan", str(qa_source), "--id", "SRC-QA-1",
                "--role", "author-qa", "--lang", "en", "--output", str(source_plan),
            )
            self.assertEqual(planned.returncode, 0, planned.stderr)
            self.assertNotIn(str(_qa_item()["criticism"]), source_plan.read_text())
            applied = cli(
                "sources", "apply", str(source_plan), "--source", str(qa_source),
                "--project", str(project),
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            listed = cli("sources", "list", "--project", str(project), "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout)[0]["role"], "author-qa")
            qa_planned = cli(
                "qa", "plan", str(qa_source), "--source-id", "SRC-QA-1",
                "--project", str(project), "--output", str(qa_plan),
            )
            self.assertEqual(qa_planned.returncode, 0, qa_planned.stderr)
            self.assertNotIn(str(_qa_item()["response"]), qa_plan.read_text())
            created = cli(
                "qa", "candidates", str(qa_plan), "--source", str(qa_source),
                "--project", str(project), "--lang", "en", "--run-directory", str(run),
                "--run-id", "qa-cli",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"]["id"], "SRC-QA-1")
            self.assertEqual(manifest["summary"]["ready_for_review"], 1)


if __name__ == "__main__":
    unittest.main()
