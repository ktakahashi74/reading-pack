from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from reading_pack_review.author_input import (
    MODULES,
    apply_author_input_plan,
    create_author_input_plan,
)
from reading_pack_review.assisted_review import (
    STATIC_HASH_RE,
    _static_hash,
    apply_assisted_author_review_plan,
    assisted_author_review_status,
    create_assisted_author_review_plan,
    export_assisted_author_review,
)
from reading_pack.errors import ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack_review.review_session import build_author_review_session
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack.validation import errors, validate_project
from tests.support import cli, copy_sample, read_json


def _author_project(root: Path) -> Path:
    project = root / "pack"
    create_project(
        project,
        title="One-time Review Book",
        author="Author",
        languages=["en"],
        primary_language="en",
    )
    data = load_language_data(project, "en")
    data["chapters"] = [{
        "id": "CH-01",
        "kind": "chapter",
        "title": "One",
        "pages": "1-10",
        "sections": ["Opening"],
        "summary": "A short chapter summary.",
        "terms": ["garden"],
        "status": "draft",
    }]
    write_json(project / "data" / "pack.en.json", data)

    package = root / "author-package"
    package.mkdir()
    write_json(package / "names.json", {
        "schema_version": 1,
        "module": "names",
        "records": [{
            "id": "NAME-ADA",
            "name": "Ada Lovelace",
            "aliases": ["Lovelace"],
            "chapter_id": "CH-01",
            "book_context": (
                "The book treats Ada as a specific example of a computing pioneer."
            ),
        }],
    })
    modules = {module: {"mode": "generate"} for module in MODULES}
    modules["names"] = {
        "mode": "provided",
        "file": "names.json",
        "format": "json",
        "source_id": "SRC-AUTHOR-NAMES",
    }
    write_json(package / "author-input.json", {
        "schema_version": 1,
        "package_id": "AIP-REVIEW-1",
        "language": "en",
        "authority": {
            "type": "author",
            "name": "Author",
            "supplied_at": "2026-08-14",
        },
        "modules": modules,
        "attachments": [],
    })
    plan = create_author_input_plan(project, package)
    apply_author_input_plan(project, plan, package)
    return project


def _edit_response(
    text: str, kind: str, identifier: str, transform
) -> str:
    pattern = re.compile(
        rf"(<!-- RP_RESPONSE_START {kind} {re.escape(identifier)} -->\n)"
        rf"(.*?)"
        rf"(\n<!-- RP_RESPONSE_END {kind} {re.escape(identifier)} -->)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"missing response {kind} {identifier}")
    body = transform(match.group(2))
    return text[:match.start()] + match.group(1) + body + match.group(3) + text[match.end():]


def _select(text: str, kind: str, identifier: str, token: str) -> str:
    def transform(body: str) -> str:
        pattern = re.compile(
            rf"^- \[ \] (.*<!-- RP_CHOICE {re.escape(token)} -->)$",
            re.MULTILINE,
        )
        changed, count = pattern.subn(r"- [x] \1", body)
        if count != 1:
            raise AssertionError(f"choice {token} count={count}")
        return changed

    return _edit_response(text, kind, identifier, transform)


def _sign(
    text: str, *, submitted: bool, final_signoff: bool
) -> str:
    def transform(body: str) -> str:
        body = body.replace("- Reviewer: ", "- Reviewer: Author", 1)
        body = body.replace("- 確認者: ", "- 確認者: Author", 1)
        body = body.replace(
            "- Review date (YYYY-MM-DD): ",
            "- Review date (YYYY-MM-DD): 2026-08-15",
            1,
        )
        body = body.replace(
            "- 確認日（YYYY-MM-DD）: ",
            "- 確認日（YYYY-MM-DD）: 2026-08-15",
            1,
        )
        if submitted:
            body = re.sub(
                r"^- \[ \] (.*<!-- RP_CHOICE submitted -->)$", r"- [x] \1", body,
                count=1, flags=re.MULTILINE,
            )
        if final_signoff:
            body = re.sub(
                r"^- \[ \] (.*<!-- RP_CHOICE final_signoff -->)$", r"- [x] \1", body,
                count=1, flags=re.MULTILINE,
            )
        return body

    return _edit_response(text, "SIGNOFF", "final", transform)


def _set_overrides(text: str, value: str) -> str:
    candidates = (
        "<!-- RP_OVERRIDES_START -->\nnone\n<!-- RP_OVERRIDES_END -->",
        "<!-- RP_OVERRIDES_START -->\nなし\n<!-- RP_OVERRIDES_END -->",
    )
    after = f"<!-- RP_OVERRIDES_START -->\n{value}\n<!-- RP_OVERRIDES_END -->"
    matches = [before for before in candidates if text.count(before) == 1]
    if len(matches) != 1:
        raise AssertionError("review overrides region is missing or changed")
    return text.replace(matches[0], after, 1)


def _revision(unit_id: str, field: str, value: str, comment: str = "Correction") -> str:
    return f"""### {unit_id}
- `decision`: `revise`
- `comment`: {comment}
#### `{field}`
- `operation`: `set`
<!-- RP_VALUE_START -->
{value}
<!-- RP_VALUE_END -->"""


class AssistedAuthorReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = _author_project(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _export(self, name: str = "assisted") -> tuple[Path, Path, dict]:
        review_file, evidence = export_assisted_author_review(
            self.project, Path(name), created_at="2026-08-15"
        )
        session = build_author_review_session(self.project, evidence)
        return review_file, evidence, session

    def _complete_text(self, review_file: Path, session: dict) -> str:
        text = review_file.read_text(encoding="utf-8")
        eligible_ids = {
            group["group_id"]
            for group in session["groups"]
            if group["bulk_eligible"]
        }
        for group_id in eligible_ids:
            text = _select(text, "GROUP", group_id, "approve")
        for record in session["records"]:
            if record["group_id"] not in eligible_ids:
                text = _select(text, "RECORD", record["unit_id"], "approve")
        for question in session["questions"]:
            if question["required_for_signoff"]:
                text = _select(text, "QUESTION", question["question_id"], "accept")
        return _sign(text, submitted=True, final_signoff=True)

    def test_export_is_human_facing_editable_markdown_with_embedded_agent_help(self) -> None:
        review_file, evidence, _ = self._export()
        text = review_file.read_text(encoding="utf-8")
        self.assertEqual(review_file.name, "assisted.review.md")
        self.assertIn("# Review of One-time Review Book", text)
        self.assertIn("Use this form to check the content", text)
        self.assertIn("## Points to consider first", text)
        self.assertIn("## Content that can be reviewed together", text)
        self.assertIn("### Access to unavailable book text", text)
        self.assertIn("Optional comment:", text)
        self.assertNotIn("## 対象", text)
        self.assertNotIn("(`approve`)", text)
        self.assertIn("RP_AGENT_INSTRUCTIONS_START", text)
        self.assertGreater(
            text.index("RP_AGENT_INSTRUCTIONS_START"),
            text.index("## Editing note"),
        )
        self.assertIn("RP_RESPONSE_START GROUP", text)
        self.assertIn("RP_OVERRIDES_START", text)
        self.assertIn("<!-- RP_ASSISTED_SESSION review_id=", text)
        self.assertNotIn("BASE64", text)
        self.assertEqual(review_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue((evidence / "manifest.json").is_file())
        self.assertEqual(
            {path.name for path in evidence.iterdir()}, {"manifest.json"}
        )
        manifest = read_json(evidence / "manifest.json")
        self.assertEqual(manifest["schema_version"], 2)
        self.assertNotIn("files", manifest)
        self.assertTrue(all("review_file" not in item for item in manifest["records"]))
        self.assertNotIn(
            "The book treats Ada as a specific example",
            json.dumps(manifest, ensure_ascii=False),
        )

        status = assisted_author_review_status(self.project, evidence, review_file)
        self.assertFalse(status["submitted"])
        self.assertEqual(status["summary"]["pending"], 2)

    def test_policy_only_review_is_short_and_activates_only_approved_policy(self) -> None:
        data = load_language_data(self.project, "en")
        data["policies"] = [{
            "id": "POLICY-AUTHORITY",
            "kind": "authority_order",
            "statement": "Use the author-maintained source before derivatives.",
            "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)
        review_file, evidence = export_assisted_author_review(
            self.project,
            Path("policy-only"),
            created_at="2026-08-15",
            modules=("policy",),
        )
        manifest = read_json(evidence / "manifest.json")
        self.assertEqual(manifest["modules"], ["policy"])
        self.assertEqual(
            {(item["module"], item["record_id"]) for item in manifest["records"]},
            {("policy", "POLICY-AUTHORITY")},
        )
        session = build_author_review_session(self.project, evidence)
        self.assertEqual(session["modules"], ["policy"])
        self.assertEqual(session["questions"], [])
        self.assertEqual(session["previews"], {})
        text = review_file.read_text(encoding="utf-8")
        self.assertIn("limited to the `policy` module", text)
        self.assertNotIn("Ada Lovelace", text)
        record = session["records"][0]
        text = _select(text, "RECORD", record["unit_id"], "approve")
        text = _sign(text, submitted=True, final_signoff=False)
        review_file.write_text(text, encoding="utf-8")
        plan = create_assisted_author_review_plan(
            self.project, evidence, review_file
        )
        self.assertEqual(plan["summary"]["approve"], 1)
        apply_assisted_author_review_plan(
            self.project, plan, evidence, review_file
        )
        approved = load_language_data(self.project, "en")["policies"][0]
        self.assertEqual(approved["status"], "approved")
        self.assertIn(
            'author_review = "pending"',
            (self.project / "reading-pack.toml").read_text(encoding="utf-8"),
        )

    def test_module_scoped_review_rejects_whole_pack_signoff(self) -> None:
        data = load_language_data(self.project, "en")
        data["policies"] = [{
            "id": "POLICY-AUTHORITY",
            "kind": "authority_order",
            "statement": "Use the author-maintained source before derivatives.",
            "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)
        review_file, evidence = export_assisted_author_review(
            self.project,
            Path("policy-signoff"),
            created_at="2026-08-15",
            modules=("policy",),
        )
        session = build_author_review_session(self.project, evidence)
        text = _select(
            review_file.read_text(encoding="utf-8"),
            "RECORD",
            session["records"][0]["unit_id"],
            "approve",
        )
        review_file.write_text(
            _sign(text, submitted=True, final_signoff=True), encoding="utf-8"
        )
        with self.assertRaisesRegex(ReadingPackError, "module-scoped"):
            create_assisted_author_review_plan(
                self.project, evidence, review_file
            )

    def test_edited_markdown_is_the_evidence_for_plan_and_apply(self) -> None:
        review_file, evidence, session = self._export()
        review_file.write_text(self._complete_text(review_file, session), encoding="utf-8")
        status = assisted_author_review_status(self.project, evidence, review_file)
        self.assertTrue(status["submitted"])
        self.assertTrue(status["final_signoff"])
        self.assertEqual(status["summary"]["approve"], 2)

        plan = create_assisted_author_review_plan(self.project, evidence, review_file)
        self.assertEqual(plan["summary"]["approve"], 2)
        self.assertTrue(plan["final_signoff"])
        applied = apply_assisted_author_review_plan(
            self.project, plan, evidence, review_file
        )
        self.assertEqual(applied["summary"]["approve"], 2)
        self.assertIn(
            'author_review = "approved"',
            (self.project / "reading-pack.toml").read_text(encoding="utf-8"),
        )

    def test_protected_human_facing_content_cannot_change(self) -> None:
        review_file, evidence, _ = self._export()
        text = review_file.read_text(encoding="utf-8").replace(
            "Use this form to check the content", "This protected text changed", 1
        )
        review_file.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "protected content changed"):
            assisted_author_review_status(self.project, evidence, review_file)

    def test_short_session_reference_is_checked_against_private_evidence(self) -> None:
        review_file, evidence, _ = self._export()
        text = review_file.read_text(encoding="utf-8")
        text = re.sub(
            r"(<!-- RP_ASSISTED_SESSION review_id=AR-[A-F0-9]{20} sha256=)"
            r"[a-f0-9]{64}( -->)",
            rf"\g<1>{'0' * 64}\g<2>",
            text,
            count=1,
        )
        digest = _static_hash(text)
        text = STATIC_HASH_RE.sub(
            f"<!-- RP_ASSISTED_STATIC_SHA256: {digest} -->", text, count=1
        )
        review_file.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "stale or belongs"):
            assisted_author_review_status(self.project, evidence, review_file)

    def test_override_is_a_human_readable_correction_instruction(self) -> None:
        review_file, evidence, session = self._export()
        name_record = next(
            record for record in session["records"]
            if record["collection"] == "names"
        )
        text = review_file.read_text(encoding="utf-8")
        group_id = name_record["group_id"]
        text = _select(text, "GROUP", group_id, "approve")
        override = f"""### {name_record['unit_id']}
- `decision`: `revise`
- `comment`: The human requested clearer book context.
#### `book_context`
- `operation`: `set`
<!-- RP_VALUE_START -->
The revised book-specific context.
<!-- RP_VALUE_END -->"""
        text = _set_overrides(text, override)
        text = _sign(text, submitted=True, final_signoff=False)
        review_file.write_text(text, encoding="utf-8")
        plan = create_assisted_author_review_plan(self.project, evidence, review_file)
        self.assertEqual(plan["summary"]["revise"], 1)
        apply_assisted_author_review_plan(self.project, plan, evidence, review_file)
        record = load_language_data(self.project, "en")["names"][0]
        self.assertEqual(record["book_context"], "The revised book-specific context.")
        self.assertEqual(record["status"], "draft")
        issues = validate_project(self.project)[2]
        self.assertNotIn("RP502", [issue.code for issue in issues])
        self.assertEqual(errors(issues), [])

    def test_candidate_suggestion_is_record_scoped_and_approved_in_one_submission(self) -> None:
        review_file, evidence = export_assisted_author_review(
            self.project,
            Path("focused-suggestion"),
            created_at="2026-08-15",
            record_ids=("NAME-ADA",),
            suggestions=[{
                "language": "en",
                "collection": "names",
                "record_id": "NAME-ADA",
                "candidate_id": "CAND-0123456789ABCDEF0123",
                "run_id": "generation-focused-test",
                "record": {
                    "id": "NAME-ADA",
                    "name": "Ada Lovelace",
                    "aliases": ["Lovelace"],
                    "chapter_id": "CH-01",
                    "book_context": "A shorter, source-grounded book context.",
                    "status": "draft",
                },
            }],
        )
        manifest = read_json(evidence / "manifest.json")
        self.assertEqual(manifest["record_ids"], ["NAME-ADA"])
        self.assertEqual(len(manifest["records"]), 1)
        text = review_file.read_text(encoding="utf-8")
        self.assertIn("`decision`: `revise_approve`", text)
        self.assertIn("A shorter, source-grounded book context.", text)
        self.assertNotIn("CH-01` One", text)

        review_file.write_text(
            _sign(text, submitted=True, final_signoff=False), encoding="utf-8"
        )
        status = assisted_author_review_status(self.project, evidence, review_file)
        self.assertEqual(status["summary"]["total"], 1)
        self.assertEqual(status["summary"]["approve"], 1)
        self.assertEqual(status["summary"]["corrections"], 1)
        plan = create_assisted_author_review_plan(
            self.project, evidence, review_file
        )
        self.assertEqual(plan["summary"]["approve"], 1)
        self.assertEqual(plan["summary"]["revise"], 0)
        self.assertEqual(plan["actions"][0]["changed_fields"], ["book_context"])
        apply_assisted_author_review_plan(
            self.project, plan, evidence, review_file
        )
        record = load_language_data(self.project, "en")["names"][0]
        self.assertEqual(record["book_context"], "A shorter, source-grounded book context.")
        self.assertEqual(record["status"], "approved")

    def test_bilingual_candidate_suggestions_revise_approve_atomically(self) -> None:
        project = copy_sample(self.root / "bilingual-focused")
        review_file, evidence = export_assisted_author_review(
            project,
            Path("focused-bilingual"),
            created_at="2026-08-15",
            record_ids=("CH-01",),
            suggestions=[
                {
                    "language": "ja",
                    "collection": "chapters",
                    "record_id": "CH-01",
                    "candidate_id": "CAND-11111111111111111111",
                    "run_id": "generation-ja-focused",
                    "record": {
                        "id": "CH-01",
                        "summary": "署名された日本語の修正案。",
                        "status": "draft",
                    },
                },
                {
                    "language": "en",
                    "collection": "chapters",
                    "record_id": "CH-01",
                    "candidate_id": "CAND-22222222222222222222",
                    "run_id": "generation-en-focused",
                    "record": {
                        "id": "CH-01",
                        "summary": "The signed English revision.",
                        "status": "draft",
                    },
                },
            ],
        )
        text = _sign(
            review_file.read_text(encoding="utf-8"),
            submitted=True,
            final_signoff=False,
        )
        review_file.write_text(text, encoding="utf-8")
        plan = create_assisted_author_review_plan(project, evidence, review_file)
        self.assertEqual(plan["summary"]["approve"], 2)
        self.assertEqual(plan["summary"]["revise"], 0)
        apply_assisted_author_review_plan(project, plan, evidence, review_file)

        ja = load_language_data(project, "ja")["chapters"][0]
        en = load_language_data(project, "en")["chapters"][0]
        self.assertEqual(ja["summary"], "署名された日本語の修正案。")
        self.assertEqual(ja["status"], "approved")
        self.assertEqual(en["summary"], "The signed English revision.")
        self.assertEqual(en["status"], "approved")
        self.assertEqual(en["translation_status"], "approved")
        self.assertEqual(en["source_hash"], semantic_hash(ja))

    def test_bilingual_revision_is_body_free_and_refreshes_translation_binding(self) -> None:
        project = copy_sample(self.root / "bilingual")
        review_file, evidence = export_assisted_author_review(
            project, Path("review"), created_at="2026-08-15"
        )
        session = build_author_review_session(project, evidence)
        primary = next(
            item for item in session["records"]
            if item["language"] == "ja"
            and item["collection"] == "chapters"
            and item["record_id"] == "CH-01"
        )
        translated = next(
            item for item in session["records"]
            if item["language"] == "en"
            and item["collection"] == "chapters"
            and item["record_id"] == "CH-01"
        )
        summary = "改訂された日本語要約。"
        comment = "著者だけが見る修正理由"
        text = review_file.read_text(encoding="utf-8")
        text = _select(text, "RECORD", translated["unit_id"], "approve")
        text = _set_overrides(
            text, _revision(primary["unit_id"], "summary", summary, comment)
        )
        text = _sign(text, submitted=True, final_signoff=False)
        review_file.write_text(text, encoding="utf-8")

        plan = create_assisted_author_review_plan(project, evidence, review_file)
        serialized = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(summary, serialized)
        self.assertNotIn(comment, serialized)
        self.assertEqual(plan["summary"]["revise"], 1)
        self.assertEqual(plan["summary"]["approve"], 1)
        apply_assisted_author_review_plan(project, plan, evidence, review_file)

        ja = load_language_data(project, "ja")
        en = load_language_data(project, "en")
        self.assertEqual(ja["chapters"][0]["summary"], summary)
        self.assertEqual(ja["chapters"][0]["status"], "draft")
        self.assertEqual(en["chapters"][0]["translation_status"], "approved")
        self.assertEqual(en["chapters"][0]["source_hash"], semantic_hash(ja["chapters"][0]))
        self.assertEqual(errors(validate_project(project)[2]), [])

    def test_primary_revision_requires_an_explicit_translation_decision(self) -> None:
        project = copy_sample(self.root / "translation-required")
        review_file, evidence = export_assisted_author_review(
            project, Path("review"), created_at="2026-08-15"
        )
        session = build_author_review_session(project, evidence)
        primary = next(
            item for item in session["records"]
            if item["language"] == "ja"
            and item["collection"] == "chapters"
            and item["record_id"] == "CH-01"
        )
        text = _set_overrides(
            review_file.read_text(encoding="utf-8"),
            _revision(primary["unit_id"], "summary", "改訂された要約。"),
        )
        review_file.write_text(
            _sign(text, submitted=True, final_signoff=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(ReadingPackError, "requires approve or revise in en"):
            create_assisted_author_review_plan(project, evidence, review_file)

    def test_exclusion_requires_every_configured_language(self) -> None:
        project = copy_sample(self.root / "exclude")
        review_file, evidence = export_assisted_author_review(
            project, Path("review"), created_at="2026-08-15"
        )
        session = build_author_review_session(project, evidence)
        units = {
            item["language"]: item
            for item in session["records"]
            if item["collection"] == "references" and item["record_id"] == "REF-01"
        }
        text = review_file.read_text(encoding="utf-8")
        text = _select(text, "RECORD", units["ja"]["unit_id"], "exclude")
        text = _sign(text, submitted=True, final_signoff=False)
        review_file.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "in every configured language"):
            create_assisted_author_review_plan(project, evidence, review_file)

        text = _select(text, "RECORD", units["en"]["unit_id"], "exclude")
        review_file.write_text(text, encoding="utf-8")
        plan = create_assisted_author_review_plan(project, evidence, review_file)
        apply_assisted_author_review_plan(project, plan, evidence, review_file)
        self.assertEqual(load_language_data(project, "ja")["references"], [])
        self.assertEqual(load_language_data(project, "en")["references"], [])
        self.assertEqual(errors(validate_project(project)[2]), [])

    def test_apply_rejects_a_plan_after_canonical_data_changes(self) -> None:
        review_file, evidence, session = self._export("stale")
        group = next(item for item in session["groups"] if item["bulk_eligible"])
        text = _select(
            review_file.read_text(encoding="utf-8"),
            "GROUP", group["group_id"], "approve",
        )
        review_file.write_text(
            _sign(text, submitted=True, final_signoff=False), encoding="utf-8"
        )
        plan = create_assisted_author_review_plan(self.project, evidence, review_file)
        data = load_language_data(self.project, "en")
        data["chapters"][0]["title"] = "Changed after planning"
        write_json(self.project / "data" / "pack.en.json", data)
        with self.assertRaisesRegex(
            ReadingPackError, "stale or its manifest changed|invalid project"
        ):
            apply_assisted_author_review_plan(
                self.project, plan, evidence, review_file
            )

    def test_private_manifest_must_not_be_a_symlink(self) -> None:
        review_file, evidence, _ = self._export("symlink")
        manifest = evidence / "manifest.json"
        saved = self.root / "saved-manifest.json"
        saved.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(saved)
        with self.assertRaisesRegex(ReadingPackError, "must not be a symlink"):
            assisted_author_review_status(self.project, evidence, review_file)

    def test_plan_requires_human_submission_attestation(self) -> None:
        review_file, evidence, session = self._export()
        group = next(group for group in session["groups"] if group["bulk_eligible"])
        text = _select(
            review_file.read_text(encoding="utf-8"),
            "GROUP", group["group_id"], "approve",
        )
        text = _sign(text, submitted=False, final_signoff=False)
        review_file.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "must check submitted"):
            create_assisted_author_review_plan(self.project, evidence, review_file)

    def test_submission_requires_human_identity_date_and_consistent_signoff(self) -> None:
        review_file, evidence, _ = self._export()
        original = review_file.read_text(encoding="utf-8")

        submitted_without_identity = _select(
            original, "SIGNOFF", "final", "submitted"
        )
        review_file.write_text(submitted_without_identity, encoding="utf-8")
        with self.assertRaisesRegex(
            ReadingPackError, "submission requires reviewer and reviewed_at"
        ):
            assisted_author_review_status(self.project, evidence, review_file)

        final_without_submission = _sign(
            original, submitted=False, final_signoff=True
        )
        review_file.write_text(final_without_submission, encoding="utf-8")
        with self.assertRaisesRegex(
            ReadingPackError, "final_signoff requires submitted"
        ):
            assisted_author_review_status(self.project, evidence, review_file)

    def test_cli_export_status_plan_and_apply(self) -> None:
        exported = cli(
            "review", "export", "--project", str(self.project),
            "--output", "cli-review", "--created-at", "2026-08-15",
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("edited Markdown is the decision evidence", exported.stdout)
        review_file = (
            self.project / ".reading-pack" / "reviews" / "cli-review.review.md"
        )
        evidence = self.project / ".reading-pack" / "reviews" / "cli-review"
        session = build_author_review_session(self.project, evidence)
        review_file.write_text(self._complete_text(review_file, session), encoding="utf-8")
        status = cli(
            "review", "status", str(review_file),
            "--evidence", str(evidence), "--project", str(self.project), "--json",
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["submitted"])
        plan_path = self.root / "plan.json"
        planned = cli(
            "review", "plan", str(review_file),
            "--evidence", str(evidence), "--project", str(self.project),
            "--output", str(plan_path),
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        applied = cli(
            "review", "apply", str(plan_path),
            "--review", str(review_file), "--evidence", str(evidence),
            "--project", str(self.project),
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("approve=2", applied.stdout)


if __name__ == "__main__":
    unittest.main()
