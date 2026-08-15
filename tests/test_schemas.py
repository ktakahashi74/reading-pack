import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from reading_pack_producer.author_qa import create_qa_candidate_run, create_qa_plan
from reading_pack.importers import import_manuscript
from reading_pack.project import create_project, load_config, load_language_data
from reading_pack.schema_validation import (
    SCHEMA_NAMES,
    qp_structural_code,
    rp_structural_code,
    schemas,
    structural_findings,
)
from reading_pack.source_registry import apply_source_plan, create_source_plan


class SchemaTests(unittest.TestCase):
    def test_schemas_are_valid_json_and_draft_2020_12(self):
        root = Path(__file__).resolve().parents[1] / "schema"
        paths = sorted(root.glob("*.schema.json"))
        self.assertEqual({path.name for path in paths}, set(SCHEMA_NAMES))
        self.assertEqual(len(paths), 29)
        self.assertEqual(set(schemas().schemas), set(SCHEMA_NAMES))
        for path in paths:
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema["type"], "object")
            Draft202012Validator.check_schema(schema)

    def test_every_published_schema_has_a_runtime_validation_site(self):
        root = Path(__file__).resolve().parents[1] / "src"
        sources = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        for schema_name in SCHEMA_NAMES:
            with self.subTest(schema=schema_name):
                # One occurrence declares the closed 28-schema catalog; a
                # second occurrence must connect the schema to an artifact path.
                self.assertGreaterEqual(sources.count(f'"{schema_name}"'), 2)

    def test_structural_error_code_and_message_compatibility_fixture(self):
        root = Path(__file__).resolve().parents[1]
        project = root / "examples" / "clockwork-garden"
        artifacts = {
            "project": load_config(project),
            "language": load_language_data(project, "en"),
            "quality": json.loads((project / "quality-plan.json").read_text(encoding="utf-8")),
        }
        fixture = json.loads(
            (root / "tests" / "fixtures" / "schema_error_compatibility.json").read_text(
                encoding="utf-8"
            )
        )
        for case in fixture:
            with self.subTest(case=case["artifact"], path=case["path"]):
                value = deepcopy(artifacts[case["artifact"]])
                target = value
                for component in case["path"][:-1]:
                    target = target[component]
                target[case["path"][-1]] = case["value"]
                finding = next(
                    item
                    for item in structural_findings(case["schema"], value)
                    if list(item.path) == case["path"]
                )
                code = (
                    qp_structural_code(finding)
                    if case["schema"] == "quality-plan.schema.json"
                    else rp_structural_code(finding)
                )
                self.assertEqual(
                    {
                        "code": code,
                        "keyword": finding.keyword,
                        "message": finding.message,
                    },
                    case["expected"],
                )

    def test_language_schema_lists_all_canonical_collections(self):
        path = Path(__file__).resolve().parents[1] / "schema" / "language-pack.schema.json"
        schema = json.loads(path.read_text())
        required = set(schema["required"])
        self.assertTrue({"chapters", "certainty", "claims", "misreadings", "names", "glossary", "references"} <= required)
        self.assertIn("policies", schema["properties"])

    def test_reading_issue_field_is_neutral_with_legacy_compatibility(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schema" / "language-pack.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        data = load_language_data(root / "examples" / "clockwork-garden", "en")
        legacy = deepcopy(data)
        self.assertEqual(list(validator.iter_errors(legacy)), [])
        neutral = deepcopy(data)
        neutral["misreadings"][0]["issue"] = neutral["misreadings"][0].pop(
            "misreading"
        )
        self.assertEqual(list(validator.iter_errors(neutral)), [])
        ambiguous = deepcopy(neutral)
        ambiguous["misreadings"][0]["misreading"] = "legacy duplicate"
        self.assertTrue(list(validator.iter_errors(ambiguous)))

    def test_json_author_qa_manifest_requires_decoded_string_normalization(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schema" / "candidate-run.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = workspace / "pack"
            book = workspace / "book.md"
            book.write_text("# Book\n\n## Chapter One\nBody.\n", encoding="utf-8")
            create_project(
                project,
                title="Book",
                author="Author",
                languages=["en"],
                primary_language="en",
            )
            import_manuscript(project, book, lang="en")
            qa_source = workspace / "author-qa.json"
            qa_source.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "items": [
                            {
                                "source_key": "critique-limit",
                                "kind": "misreading",
                                "chapter_ids": ["CH-01"],
                                "claim_ids": [],
                                "criticism": "The scope may be read as unlimited.",
                                "impact": "That would overstate chapter one.",
                                "response": "The claim concerns moving limits.",
                                "remaining_uncertainty": "The rate remains unknown.",
                            }
                        ],
                    },
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
                run_directory=project / ".reading-pack" / "runs" / "schema-json-qa",
                run_id="schema-json-qa",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["normalization"],
            "json-decoded-strings-nfkc-casefold-whitespace-v1",
        )
        validator.validate(manifest)
        old_normalization = dict(manifest)
        old_normalization["normalization"] = "nfkc-casefold-whitespace-v1"
        self.assertFalse(validator.is_valid(old_normalization))

    def test_bilingual_spec_has_matching_requirement_ids_and_section_numbers(self):
        root = Path(__file__).resolve().parents[1] / "spec"
        ja = (root / "reading-pack-spec.ja.md").read_text(encoding="utf-8")
        en = (root / "reading-pack-spec.en.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\*\*(RP-\d{3})\*\*", ja), re.findall(r"\*\*(RP-\d{3})\*\*", en))
        self.assertEqual(re.findall(r"^## (\d+)\.", ja, re.MULTILINE), re.findall(r"^## (\d+)\.", en, re.MULTILINE))

    def test_cc_by_license_matches_installed_canonical_text_when_available(self):
        root = Path(__file__).resolve().parents[1]
        bundled = (root / "LICENSES" / "CC-BY-4.0.txt").read_text(encoding="utf-8")
        canonical = Path("/usr/share/texlive/texmf-dist/tex/latex/doclicense/license-texts/doclicense-CC-by-4.0-plaintext.tex")
        if canonical.exists():
            self.assertEqual(bundled.rstrip(), canonical.read_text(encoding="utf-8").rstrip())
        else:
            self.assertIn("Creative Commons Attribution 4.0 International Public License", bundled)


if __name__ == "__main__":
    unittest.main()
