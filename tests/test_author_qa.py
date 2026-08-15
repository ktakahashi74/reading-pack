from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reading_pack_producer.author_qa import (
    classify_qa_plan,
    create_qa_plan,
    qa_plan_to_candidate_responses,
)
from reading_pack_producer.candidates import (
    accept_candidates,
    apply_candidate_run,
    create_candidate_run,
    load_candidate_run,
)
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack.source_registry import apply_source_plan, create_source_plan


class AuthorQaWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.book = self.root / "book.txt"
        self.book.write_text("The book has one chapter.\n", encoding="utf-8")
        self.qa = self.root / "appendix.org"
        self.qa.write_text(
            """#+TITLE: Author Q&A

** Is the claim unconditional? (Chapter 1)
:PROPERTIES:
:CUSTOM_ID: critique-unconditional
:END:
- Criticism :: The claim may be read as unconditional.
- Impact :: This would overstate chapter one.
- The book's response :: The claim applies only while the gate is open.
- Remaining uncertainty :: The closing time remains unknown.
""",
            encoding="utf-8",
        )
        create_project(
            self.project,
            title="Book",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        data = load_language_data(self.project, "en")
        data["source"] = {
            "format": "text",
            "name": self.book.name,
            "sha256": hashlib.sha256(self.book.read_bytes()).hexdigest(),
        }
        data["chapters"] = [{
            "id": "CH-01", "title": "One", "pages": "1", "sections": [],
            "summary": "", "terms": [], "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)
        source_plan = create_source_plan(
            self.qa,
            source_id="SRC-AUTHOR-QA",
            role="author-qa",
            language="en",
        )
        self.source_record = apply_source_plan(self.project, source_plan, self.qa)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_org_requires_explicit_classification_then_creates_and_applies_candidate(self) -> None:
        plan = create_qa_plan(self.qa, self.source_record)
        self.assertEqual(plan["outcome"], "review_required")
        self.assertNotIn("The claim may be read", json.dumps(plan))
        plan = classify_qa_plan(plan, {"critique-unconditional": "open_objection"})
        self.assertEqual(plan["outcome"], "ready")
        responses = qa_plan_to_candidate_responses(plan, self.qa)
        record = responses[0]["record"]
        self.assertEqual(record["kind"], "open_objection")
        self.assertEqual(record["impact"], "This would overstate chapter one.")
        data = load_language_data(self.project, "en")
        run = self.project / ".reading-pack" / "runs" / "qa"
        create_candidate_run(
            run,
            source_path=self.qa,
            responses=responses,
            language="en",
            canonical_data=data,
            project_data_by_lang={"en": data},
            known_chapter_ids={"CH-01"},
            support_source=self.source_record,
            run_id="qa-test",
            created_at="2026-08-13T00:00:00+00:00",
            generator={"adapter": "author-qa-deterministic", "model": ""},
        )
        manifest = load_candidate_run(run)
        self.assertEqual(manifest["source"]["id"], "SRC-AUTHOR-QA")
        self.assertEqual(manifest["summary"]["ready_for_review"], 1)
        candidate_id = manifest["candidates"][0]["candidate_id"]
        accept_candidates(run, [candidate_id], reviewer="Editor")
        apply_candidate_run(
            self.project,
            language="en",
            run=run,
            source_path=self.qa,
            candidate_ids=[candidate_id],
        )
        item = load_language_data(self.project, "en")["misreadings"][0]
        self.assertEqual(item["kind"], "open_objection")
        self.assertEqual(item["status"], "draft")


if __name__ == "__main__":
    unittest.main()
