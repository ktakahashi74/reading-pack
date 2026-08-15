from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from reading_pack_review.author_input import (
    MODULES,
    apply_author_input_plan,
    author_input_consistency_findings,
    create_author_input_plan,
    create_author_input_template,
    load_author_input_state,
    _recover_prepared,
)
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_config, load_language_data, write_json
from reading_pack.hashing import semantic_hash
from reading_pack.source_registry import load_source_registry
from reading_pack.rendering import render_pack
from reading_pack.validation import errors, validate_project
from tests.support import cli, read_json


def _chapter(summary: str = "Generated summary") -> dict[str, object]:
    return {
        "id": "CH-01",
        "kind": "chapter",
        "title": "One",
        "pages": "1-10",
        "sections": ["Opening"],
        "summary": summary,
        "terms": ["old term"],
        "status": "approved",
    }


def _module(path: Path, module: str, records: list[dict[str, object]]) -> None:
    write_json(path, {"schema_version": 1, "module": module, "records": records})


def _manifest(
    package: Path,
    declarations: dict[str, dict[str, str]],
    *,
    package_id: str = "AIP-TEST-1",
    language: str = "en",
) -> None:
    modules: dict[str, dict[str, str]] = {
        module: {"mode": "generate"} for module in MODULES
    }
    modules.update(declarations)
    write_json(
        package / "author-input.json",
        {
            "schema_version": 1,
            "package_id": package_id,
            "language": language,
            "authority": {
                "type": "author",
                "name": "Author",
                "supplied_at": "2026-08-14",
            },
            "modules": modules,
            "attachments": [],
        },
    )


class AuthorInputTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "pack"
        create_project(
            project,
            title="Book",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        data = load_language_data(project, "en")
        data["chapters"] = [_chapter()]
        write_json(project / "data" / "pack.en.json", data)
        return project

    def _bilingual_project(self, root: Path) -> Path:
        project = root / "pack"
        create_project(
            project,
            title="Book",
            author="Author",
            languages=["ja", "en"],
            primary_language="ja",
        )
        primary = load_language_data(project, "ja")
        primary["chapters"] = [_chapter("生成された要約")]
        translated = load_language_data(project, "en")
        translated_chapter = _chapter("Generated summary")
        translated_chapter.update({
            "source_id": "CH-01",
            "source_hash": semantic_hash(primary["chapters"][0]),
            "translation_status": "approved",
        })
        translated["chapters"] = [translated_chapter]
        write_json(project / "data" / "pack.ja.json", primary)
        write_json(project / "data" / "pack.en.json", translated)
        return project

    def _bilingual_name_packages(self, root: Path) -> tuple[Path, Path]:
        packages: list[Path] = []
        for language, package_id, source_id, term_source_id, name, context, term, meaning in (
            ("ja", "AIP-JA-1", "SRC-JA-NAMES", "SRC-JA-GLOSSARY", "エイダ・ラブレス", "本書で計算機の先駆者として扱う。", "解析機関", "本書で説明する機械式計算機。"),
            ("en", "AIP-EN-1", "SRC-EN-NAMES", "SRC-EN-GLOSSARY", "Ada Lovelace", "The book treats her as a computing pioneer.", "Analytical Engine", "The mechanical computer discussed in the book."),
        ):
            package = root / f"package-{language}"
            package.mkdir()
            _module(
                package / "names.json",
                "names",
                [{
                    "id": "NAME-ADA",
                    "name": name,
                    "aliases": ["Ada"],
                    "chapter_id": "CH-01",
                    "book_context": context,
                }],
            )
            _module(
                package / "glossary.json",
                "glossary",
                [{
                    "id": "TERM-ENGINE",
                    "term": term,
                    "aliases": ["Engine"],
                    "chapter_id": "CH-01",
                    "book_meaning": meaning,
                }],
            )
            _manifest(
                package,
                {
                    "names": {
                        "mode": "provided",
                        "file": "names.json",
                        "format": "json",
                        "source_id": source_id,
                    },
                    "glossary": {
                        "mode": "provided",
                        "file": "glossary.json",
                        "format": "json",
                        "source_id": term_source_id,
                    },
                },
                package_id=package_id,
                language=language,
            )
            packages.append(package)
        return packages[0], packages[1]

    def test_template_declares_every_module_and_init_creates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            state = read_json(project / "author-input-state.json")
            self.assertEqual(set(state["languages"]["en"]["modules"]), set(MODULES))
            self.assertTrue(
                all(
                    item["mode"] == "generate"
                    for item in state["languages"]["en"]["modules"].values()
                )
            )
            manifest_path = create_author_input_template(
                root / "author-package",
                language="en",
                authority_type="author",
                authority_name="Author",
                supplied_at="2026-08-14",
                package_id="AIP-TEMPLATE-1",
            )
            manifest = read_json(manifest_path)
            self.assertEqual(set(manifest["modules"]), set(MODULES))
            self.assertTrue((manifest_path.parent / "names.json").is_file())
            self.assertNotIn(str(root), json.dumps(manifest))

    def test_plan_and_apply_mix_provided_augment_and_generate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            sentinel = "Author supplied account of Ada in this book."
            _module(
                package / "summaries.json",
                "summaries",
                [{"chapter_id": "CH-01", "summary": "Author summary"}],
            )
            _module(
                package / "names.json",
                "names",
                [{
                    "id": "NAME-ADA",
                    "name": "Ada Lovelace",
                    "aliases": ["Lovelace"],
                    "chapter_id": "CH-01",
                    "book_context": sentinel,
                    "status": "approved",
                }],
            )
            _manifest(
                package,
                {
                    "summaries": {
                        "mode": "provided",
                        "file": "summaries.json",
                        "format": "json",
                        "source_id": "SRC-AUTHOR-SUMMARIES",
                    },
                    "names": {
                        "mode": "augment",
                        "file": "names.json",
                        "format": "json",
                        "source_id": "SRC-AUTHOR-NAMES",
                    },
                },
            )
            plan = create_author_input_plan(project, package)
            serialized = json.dumps(plan, ensure_ascii=False)
            self.assertNotIn(sentinel, serialized)
            self.assertNotIn(str(root), serialized)
            result = apply_author_input_plan(project, plan, package)
            self.assertEqual(result["language"], "en")

            data = load_language_data(project, "en")
            self.assertEqual(data["chapters"][0]["summary"], "Author summary")
            self.assertEqual(data["chapters"][0]["status"], "draft")
            self.assertEqual(data["names"][0]["status"], "draft")
            self.assertEqual(data["names"][0]["aliases"], ["Lovelace"])
            self.assertEqual(
                data["names"][0]["provenance_source_id"], "SRC-AUTHOR-NAMES"
            )
            sources = read_json(project / "sources.json")["sources"]
            self.assertEqual(
                {item["role"] for item in sources}, {"author-data"}
            )
            state = load_author_input_state(project)
            self.assertEqual(
                state["languages"]["en"]["modules"]["summaries"]["mode"],
                "provided",
            )
            findings = author_input_consistency_findings({"en": data}, state)
            self.assertEqual(findings, [])
            rendered = render_pack(project, "en", load_config(project), data)
            self.assertIn("aliases=Lovelace", rendered)

    def test_provided_replaces_augment_preserves_and_omit_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            data = load_language_data(project, "en")
            data["names"] = [{"id": "NAME-OLD", "name": "Old", "chapter_id": "CH-01", "status": "draft"}]
            data["references"] = [{"id": "REF-OLD", "url": "https://example.org", "label": "Old", "status": "draft"}]
            write_json(project / "data" / "pack.en.json", data)
            package = root / "author-package"
            package.mkdir()
            _module(package / "names.json", "names", [{"id": "NAME-NEW", "name": "New", "chapter_id": "CH-01", "book_context": "Named as the chapter's example."}])
            _module(package / "glossary.csv.json", "glossary", [{"id": "TERM-NEW", "term": "New term", "chapter_id": "CH-01", "book_meaning": "The book's local meaning."}])
            _manifest(
                package,
                {
                    "names": {"mode": "provided", "file": "names.json", "format": "json", "source_id": "SRC-NAMES"},
                    "glossary": {"mode": "augment", "file": "glossary.csv.json", "format": "json", "source_id": "SRC-GLOSSARY"},
                    "references": {"mode": "omit"},
                },
            )
            plan = create_author_input_plan(project, package)
            apply_author_input_plan(project, plan, package)
            data = load_language_data(project, "en")
            self.assertEqual([item["id"] for item in data["names"]], ["NAME-NEW"])
            self.assertEqual([item["id"] for item in data["glossary"]], ["TERM-NEW"])
            self.assertEqual(data["references"], [])

    def test_csv_lists_and_stale_plan_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            csv_path = package / "names.csv"
            csv_path.write_text(
                "id,name,aliases,chapter_id,book_context\n"
                "NAME-ADA,Ada Lovelace,Ada|Lovelace,CH-01,Discussed as a computing pioneer.\n",
                encoding="utf-8",
            )
            _manifest(package, {"names": {"mode": "provided", "file": "names.csv", "format": "csv", "source_id": "SRC-NAMES-CSV"}})
            plan = create_author_input_plan(project, package)
            csv_path.write_text(csv_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReadingPackError, "changed after planning"):
                apply_author_input_plan(project, plan, package)
            self.assertEqual(load_language_data(project, "en")["names"], [])

    def test_invalid_module_shape_and_source_id_reuse_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            _module(package / "names.json", "names", [{"id": "NAME-BROKEN"}])
            _manifest(package, {"names": {"mode": "provided", "file": "names.json", "format": "json", "source_id": "SRC-NAMES"}})
            with self.assertRaisesRegex(ReadingPackError, "missing fields"):
                create_author_input_plan(project, package)

            _module(package / "names.json", "names", [{"id": "NAME-ONE", "name": "One", "chapter_id": "CH-01", "book_context": "First supplied person."}])
            first = create_author_input_plan(project, package)
            apply_author_input_plan(project, first, package)
            _module(package / "names.json", "names", [{"id": "NAME-TWO", "name": "Two", "chapter_id": "CH-01", "book_context": "Second supplied person."}])
            with self.assertRaisesRegex(ReadingPackError, "use a new source ID"):
                create_author_input_plan(project, package)

    def test_validation_detects_author_provided_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            _module(package / "summaries.json", "summaries", [{"chapter_id": "CH-01", "summary": "Author summary"}])
            _manifest(package, {"summaries": {"mode": "provided", "file": "summaries.json", "format": "json", "source_id": "SRC-SUMMARY"}})
            plan = create_author_input_plan(project, package)
            apply_author_input_plan(project, plan, package)
            data = load_language_data(project, "en")
            data["chapters"][0]["summary"] = "Unrecorded edit"
            write_json(project / "data" / "pack.en.json", data)
            _, _, issues = validate_project(project)
            self.assertTrue(any(issue.code == "RP502" for issue in issues))

    def test_claim_locators_reader_notes_and_qa_anchors_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            _module(
                package / "claims.json",
                "claims",
                [{
                    "id": "AX-1",
                    "layer": "descriptive",
                    "kind": "definition",
                    "statement": "The supplied claim.",
                    "chapter_ids": ["CH-01"],
                    "source_locations": ["ch01.org:10-12", "appendix-1.org#ax-1"],
                    "reader_note": "An authority-supplied reader note.",
                }],
            )
            _module(
                package / "qa.json",
                "qa",
                [{
                    "id": "MIS-01",
                    "kind": "misreading",
                    "misreading": "The supplied misreading.",
                    "response": "The supplied response.",
                    "chapter_ids": ["CH-01"],
                    "claim_ids": ["AX-1"],
                    "anchor": "critique-supplied",
                }],
            )
            _manifest(
                package,
                {
                    "claims": {
                        "mode": "provided",
                        "file": "claims.json",
                        "format": "json",
                        "source_id": "SRC-CLAIMS",
                    },
                    "qa": {
                        "mode": "provided",
                        "file": "qa.json",
                        "format": "json",
                        "source_id": "SRC-QA",
                    },
                },
            )
            plan = create_author_input_plan(project, package)
            apply_author_input_plan(project, plan, package)
            data = load_language_data(project, "en")
            self.assertEqual(
                data["claims"][0]["source_locations"],
                ["ch01.org:10-12", "appendix-1.org#ax-1"],
            )
            self.assertEqual(data["misreadings"][0]["anchor"], "critique-supplied")
            self.assertEqual(
                data["misreadings"][0]["issue"], "The supplied misreading."
            )
            self.assertNotIn("misreading", data["misreadings"][0])
            rendered = render_pack(project, "en", load_config(project), data)
            self.assertIn("loc: ch01.org:10-12; appendix-1.org#ax-1", rendered)
            self.assertIn("note: An authority-supplied reader note.", rendered)
            self.assertIn("a=critique-supplied", rendered)

    def test_policy_module_is_provenance_bound_and_activated_only_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "author-package"
            package.mkdir()
            statement = "Do not translate passages beyond the rights holder's recorded permission."
            _module(
                package / "policy.json",
                "policy",
                [{
                    "id": "POLICY-TRANSLATION",
                    "kind": "translation_rights",
                    "statement": statement,
                    "source_locations": ["src/policy.yaml#translation_rights"],
                }],
            )
            _manifest(
                package,
                {
                    "policy": {
                        "mode": "provided",
                        "file": "policy.json",
                        "format": "json",
                        "source_id": "SRC-AUTHOR-POLICY",
                    }
                },
            )
            plan = create_author_input_plan(project, package)
            apply_author_input_plan(project, plan, package)
            data = load_language_data(project, "en")
            policy = data["policies"][0]
            self.assertEqual(policy["status"], "draft")
            self.assertEqual(policy["provenance_source_id"], "SRC-AUTHOR-POLICY")
            self.assertEqual(
                policy["source_locations"],
                ["src/policy.yaml#translation_rights"],
            )
            draft_pack = render_pack(project, "en", load_config(project), data)
            self.assertIn("## POLICY | Book-specific policies", draft_pack)
            self.assertIn("Treat draft or reviewed POLICY records", draft_pack)
            self.assertNotIn("P2.1[translation_rights]", draft_pack)

            policy["status"] = "approved"
            approved_pack = render_pack(project, "en", load_config(project), data)
            self.assertIn(
                f"P2.1[translation_rights]: Book-specific policy: {statement}",
                approved_pack,
            )

    def test_cli_template_plan_apply_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            package = root / "cli-package"
            created = cli("author-input", "template", str(package), "--package-id", "AIP-CLI-1", "--lang", "en", "--authority-name", "Author")
            self.assertEqual(created.returncode, 0, created.stderr)
            manifest = read_json(package / "author-input.json")
            manifest["modules"]["references"] = {"mode": "omit"}
            write_json(package / "author-input.json", manifest)
            plan_path = root / "author-plan.json"
            planned = cli("author-input", "plan", str(package), "--project", str(project), "--output", str(plan_path))
            self.assertEqual(planned.returncode, 0, planned.stderr)
            applied = cli("author-input", "apply", str(plan_path), "--package", str(package), "--project", str(project))
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("draft, not approved", applied.stdout)
            report = cli("author-input", "report", "--project", str(project), "--json")
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertEqual(json.loads(report.stdout)["languages"]["en"]["modules"]["references"]["mode"], "omit")

    def test_bilingual_packages_add_matching_ids_atomically_and_plan_is_body_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            package_ja, package_en = self._bilingual_name_packages(root)
            plan = create_author_input_plan(project, [package_en, package_ja])
            serialized = json.dumps(plan, ensure_ascii=False)
            self.assertEqual(plan["schema_version"], 2)
            self.assertEqual([item["language"] for item in plan["packages"]], ["ja", "en"])
            self.assertEqual(set(plan["languages"]), {"ja", "en"})
            self.assertNotIn("The book treats her as a computing pioneer.", serialized)
            self.assertNotIn(str(root), serialized)

            result = apply_author_input_plan(
                project, plan, [package_ja, package_en]
            )
            self.assertEqual(result["languages"], ["ja", "en"])
            primary = load_language_data(project, "ja")
            translated = load_language_data(project, "en")
            self.assertEqual([item["id"] for item in primary["names"]], ["NAME-ADA"])
            self.assertEqual([item["id"] for item in translated["names"]], ["NAME-ADA"])
            self.assertEqual(
                [item["id"] for item in primary["glossary"]], ["TERM-ENGINE"]
            )
            self.assertEqual(
                [item["id"] for item in translated["glossary"]], ["TERM-ENGINE"]
            )
            self.assertEqual(translated["names"][0]["source_id"], "NAME-ADA")
            self.assertEqual(
                translated["names"][0]["source_hash"],
                semantic_hash(primary["names"][0]),
            )
            self.assertEqual(
                translated["glossary"][0]["source_hash"],
                semantic_hash(primary["glossary"][0]),
            )

    def test_bilingual_companion_references_flow_through_plan_apply_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            packages: list[Path] = []
            for language, package_id, source_id, url, label in (
                (
                    "ja", "AIP-JA-REFS", "SRC-JA-REFS",
                    "https://example.com/book/", "公式補完資料",
                ),
                (
                    "en", "AIP-EN-REFS", "SRC-EN-REFS",
                    "https://example.com/en/book/", "Official companion material",
                ),
            ):
                package = root / f"references-{language}"
                package.mkdir()
                _module(
                    package / "references.json",
                    "references",
                    [{
                        "id": "REF-COMPANION",
                        "url": url,
                        "label": label,
                        "relation": "official_companion",
                        "url_scope": "prefix",
                        "retrieval_policy": "proactive_when_relevant",
                    }],
                )
                _manifest(
                    package,
                    {"references": {
                        "mode": "provided",
                        "file": "references.json",
                        "format": "json",
                        "source_id": source_id,
                    }},
                    package_id=package_id,
                    language=language,
                )
                packages.append(package)

            plan = create_author_input_plan(project, list(reversed(packages)))
            serialized = json.dumps(plan, ensure_ascii=False)
            self.assertNotIn("https://example.com/book/", serialized)
            apply_author_input_plan(project, plan, packages)
            config, data, issues = validate_project(project)
            self.assertEqual(errors(issues), [])
            self.assertEqual(
                data["en"]["references"][0]["source_hash"],
                semantic_hash(data["ja"]["references"][0]),
            )
            for language in ("ja", "en"):
                reference = data[language]["references"][0]
                self.assertEqual(reference["status"], "draft")
                self.assertEqual(reference["relation"], "official_companion")
                rendered = render_pack(project, language, config, data[language])
                self.assertIn("relation=official_companion", rendered)
                self.assertIn("C1:", rendered)

    def test_reference_csv_accepts_legacy_and_companion_headers(self) -> None:
        variants = (
            (
                "id,url,label\nREF-LEGACY,https://example.com/book,Reference\n",
                False,
            ),
            (
                "id,url,label,relation,url_scope,retrieval_policy\n"
                "REF-COMPANION,https://example.com/book/,Official companion,"
                "official_companion,prefix,proactive_when_relevant\n",
                True,
            ),
        )
        for index, (csv_text, companion) in enumerate(variants):
            with self.subTest(companion=companion), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project = self._project(root)
                package = root / "package"
                package.mkdir()
                (package / "references.csv").write_text(csv_text, encoding="utf-8")
                _manifest(
                    package,
                    {"references": {
                        "mode": "provided",
                        "file": "references.csv",
                        "format": "csv",
                        "source_id": f"SRC-REFS-{index}",
                    }},
                    package_id=f"AIP-REFS-{index}",
                )
                plan = create_author_input_plan(project, package)
                apply_author_input_plan(project, plan, package)
                record = load_language_data(project, "en")["references"][0]
                self.assertEqual("relation" in record, companion)

    def test_translation_hash_uses_prospective_primary_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            packages: list[Path] = []
            for language, package_id, source_id, summary in (
                ("ja", "AIP-JA-SUMMARY", "SRC-JA-SUMMARY", "著者が改訂した要約"),
                ("en", "AIP-EN-SUMMARY", "SRC-EN-SUMMARY", "Author revised summary"),
            ):
                package = root / f"summary-{language}"
                package.mkdir()
                _module(
                    package / "summaries.json",
                    "summaries",
                    [{"chapter_id": "CH-01", "summary": summary}],
                )
                _manifest(
                    package,
                    {"summaries": {
                        "mode": "provided",
                        "file": "summaries.json",
                        "format": "json",
                        "source_id": source_id,
                    }},
                    package_id=package_id,
                    language=language,
                )
                packages.append(package)
            plan = create_author_input_plan(project, packages)
            apply_author_input_plan(project, plan, packages)
            primary = load_language_data(project, "ja")["chapters"][0]
            translated = load_language_data(project, "en")["chapters"][0]
            self.assertEqual(primary["summary"], "著者が改訂した要約")
            self.assertEqual(translated["summary"], "Author revised summary")
            self.assertEqual(translated["source_hash"], semantic_hash(primary))
            self.assertEqual(translated["translation_status"], "draft")

    def test_one_bilingual_package_that_breaks_parity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            package_ja, _ = self._bilingual_name_packages(root)
            with self.assertRaisesRegex(ReadingPackError, "RP200"):
                create_author_input_plan(project, package_ja)
            self.assertEqual(load_language_data(project, "ja")["names"], [])

    def test_duplicate_language_package_and_source_declarations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            package_ja, package_en = self._bilingual_name_packages(root)
            duplicate_language = root / "package-en-2"
            duplicate_language.mkdir()
            _manifest(
                duplicate_language,
                {},
                package_id="AIP-EN-2",
                language="en",
            )
            with self.assertRaisesRegex(ReadingPackError, "unique languages"):
                create_author_input_plan(project, [package_en, duplicate_language])

            manifest_en = read_json(package_en / "author-input.json")
            manifest_en["package_id"] = "AIP-JA-1"
            write_json(package_en / "author-input.json", manifest_en)
            with self.assertRaisesRegex(ReadingPackError, "unique package IDs"):
                create_author_input_plan(project, [package_ja, package_en])

            manifest_en["package_id"] = "AIP-EN-1"
            manifest_en["modules"]["names"]["source_id"] = "SRC-JA-NAMES"
            write_json(package_en / "author-input.json", manifest_en)
            with self.assertRaisesRegex(ReadingPackError, "unique source IDs"):
                create_author_input_plan(project, [package_ja, package_en])

    def test_changing_either_package_after_planning_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            package_ja, package_en = self._bilingual_name_packages(root)
            plan = create_author_input_plan(project, [package_ja, package_en])
            names = read_json(package_en / "names.json")
            names["records"][0]["book_context"] += " Changed."
            write_json(package_en / "names.json", names)
            with self.assertRaisesRegex(ReadingPackError, "changed after planning"):
                apply_author_input_plan(project, plan, [package_ja, package_en])
            self.assertEqual(load_language_data(project, "ja")["names"], [])
            self.assertEqual(load_language_data(project, "en")["names"], [])
            self.assertEqual(load_source_registry(project)["sources"], [])
            state = load_author_input_state(project)
            self.assertEqual(state["languages"]["ja"]["history"], [])
            self.assertEqual(state["languages"]["en"]["history"], [])

    def test_prepared_recovery_restores_both_language_files_sources_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._bilingual_project(root)
            package_ja, package_en = self._bilingual_name_packages(root)
            before_data = {
                language: load_language_data(project, language)
                for language in ("ja", "en")
            }
            before_sources = load_source_registry(project)
            before_state = load_author_input_state(project)
            plan = create_author_input_plan(project, [package_ja, package_en])
            apply_author_input_plan(project, plan, [package_ja, package_en])
            after_data = {
                language: load_language_data(project, language)
                for language in ("ja", "en")
            }
            after_sources = load_source_registry(project)
            after_state = load_author_input_state(project)

            def artifact(path: str, before: object, after: object) -> dict[str, object]:
                raw = json.dumps(
                    after,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                return {
                    "path": path,
                    "kind": "json",
                    "before": before,
                    "before_exists": True,
                    "after_sha256": hashlib.sha256(raw).hexdigest(),
                }

            write_json(
                project / ".reading-pack" / "author-input-prepared.json",
                {
                    "schema_version": 1,
                    "artifacts": [
                        *[
                            artifact(
                                f"data/pack.{language}.json",
                                before_data[language],
                                after_data[language],
                            )
                            for language in ("ja", "en")
                        ],
                        artifact("sources.json", before_sources, after_sources),
                        artifact(
                            "author-input-state.json", before_state, after_state
                        ),
                    ],
                },
            )
            _recover_prepared(project)
            self.assertEqual(load_language_data(project, "ja"), before_data["ja"])
            self.assertEqual(load_language_data(project, "en"), before_data["en"])
            self.assertEqual(load_source_registry(project), before_sources)
            self.assertEqual(load_author_input_state(project), before_state)


if __name__ == "__main__":
    unittest.main()
