from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SAMPLE, cli, copy_sample, read_json, write_json


class CliEndToEndTests(unittest.TestCase):
    def test_japanese_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "ja-pack"
            result = cli("init", str(project), "--title", "歯車仕掛けの庭", "--author", "著者", "--lang", "ja")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = cli("import", str(SAMPLE / "manuscripts" / "book.ja.md"), "--project", str(project), "--lang", "ja")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("manuscript prose was not copied", result.stdout)
            self.assertEqual(cli("build", "--project", str(project), "--lang", "ja").returncode, 0)
            self.assertEqual(cli("check", "--project", str(project), "--lang", "ja").returncode, 0)

    def test_init_records_selected_quality_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "profiled-pack"
            result = cli(
                "init",
                str(project),
                "--title",
                "Argument Book",
                "--author",
                "Author",
                "--lang",
                "en",
                "--profile",
                "academic-argument",
                "--scope",
                "published first edition",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = read_json(project / "quality-plan.json")
            self.assertEqual(plan["profile"], "academic-argument")
            self.assertEqual(plan["scope"], "published first edition")
            self.assertTrue(plan["conformance_required"])
            self.assertEqual(plan["authority"]["status"], "pending")
            self.assertIn(".reading-pack/", (project / ".gitignore").read_text())

    def test_profiles_are_machine_listable(self):
        result = cli("profiles", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        profiles = {item["name"] for item in json.loads(result.stdout)}
        self.assertIn("academic-argument", profiles)
        self.assertIn("fiction-spoiler-free", profiles)

    def test_measure_reports_machine_readable_content_counts(self):
        result = cli("measure", "--project", str(SAMPLE), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(result.stdout)
        self.assertEqual(metrics["ja"]["chapter_summaries"], 2)
        self.assertEqual(metrics["ja"]["names_with_context"], 1)
        self.assertEqual(metrics["en"]["glossary_terms_with_meaning"], 1)

    def test_import_plan_requires_explicit_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "planned-pack"
            source = SAMPLE / "manuscripts" / "book.en.md"
            plan = root / "import-plan.json"
            self.assertEqual(
                cli("init", str(project), "--title", "Book", "--author", "Author", "--lang", "en").returncode,
                0,
            )
            planned = cli("import-plan", str(source), "--output", str(plan))
            self.assertEqual(planned.returncode, 0, planned.stderr)
            self.assertEqual(read_json(project / "data" / "pack.en.json")["chapters"], [])
            applied = cli(
                "import-apply",
                str(plan),
                "--source",
                str(source),
                "--project",
                str(project),
                "--lang",
                "en",
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            data = read_json(project / "data" / "pack.en.json")
            self.assertEqual([item["id"] for item in data["chapters"]], ["CH-01", "CH-02"])
            self.assertTrue(all(item["status"] == "draft" for item in data["chapters"]))

    def test_import_plan_never_overwrites_source_or_existing_output_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.md"
            source.write_text("# Book\n\n## One\nBody\n", encoding="utf-8")
            before = source.read_bytes()
            same = cli("import-plan", str(source), "--output", str(source))
            self.assertEqual(same.returncode, 4)
            self.assertEqual(source.read_bytes(), before)
            output = root / "plan.json"
            output.write_text("keep", encoding="utf-8")
            existing = cli("import-plan", str(source), "--output", str(output))
            self.assertEqual(existing.returncode, 4)
            self.assertEqual(output.read_text(), "keep")

    def test_candidate_cli_verifies_and_applies_draft_without_printing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "candidate-pack"
            source = SAMPLE / "manuscripts" / "book.en.md"
            responses = root / "responses.json"
            run = project / ".reading-pack" / "runs" / "test-run"
            cli("init", str(project), "--title", "Book", "--author", "Author", "--lang", "en")
            cli("import", str(source), "--project", str(project), "--lang", "en")
            evidence = "garden whose beds moved with the phases of the moon"
            write_json(
                responses,
                [
                    {
                        "collection": "glossary",
                        "record": {
                            "id": "TERM-GARDEN-NEW",
                            "term": "garden",
                            "chapter_id": "CH-01",
                            "status": "approved",
                        },
                        "evidence": [{"snippet": evidence}],
                    }
                ],
            )
            created = cli(
                "candidates",
                "create",
                str(responses),
                "--run-directory",
                str(run),
                "--source",
                str(source),
                "--project",
                str(project),
                "--lang",
                "en",
                "--run-id",
                "test-run",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            manifest = run / "manifest.json"
            self.assertNotIn(evidence, manifest.read_text())
            report = cli("candidates", "report", str(run))
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertNotIn(evidence, report.stdout)
            reviewed = cli(
                "candidates",
                "review",
                str(run),
                "--source",
                str(source),
                "--project",
                str(project),
                "--output",
                "test-review.html",
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            review_path = project / ".reading-pack" / "reviews" / "test-review.html"
            self.assertTrue(review_path.is_file())
            self.assertIn(evidence, review_path.read_text(encoding="utf-8"))
            self.assertNotIn(evidence, reviewed.stdout)
            verified = cli("candidates", "verify", str(run), "--source", str(source))
            self.assertEqual(verified.returncode, 0, verified.stderr)
            candidate_id = read_json(manifest)["candidates"][0]["candidate_id"]
            accepted = cli(
                "candidates",
                "accept",
                str(run),
                "--id",
                candidate_id,
                "--reviewer",
                "Test Reviewer",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            applied = cli(
                "candidates",
                "apply",
                str(run),
                "--source",
                str(source),
                "--project",
                str(project),
                "--lang",
                "en",
                "--id",
                candidate_id,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            term = read_json(project / "data" / "pack.en.json")["glossary"][0]
            self.assertEqual(term["id"], "TERM-GARDEN-NEW")
            self.assertEqual(term["status"], "draft")

    def test_candidate_cli_accepts_run_bound_ai_review_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ai-reviewed-pack"
            source = SAMPLE / "manuscripts" / "book.en.md"
            responses = root / "responses.json"
            review_artifact = root / "ai-review.json"
            run = project / ".reading-pack" / "runs" / "ai-run"
            cli("init", str(project), "--title", "Book", "--author", "Author", "--lang", "en")
            cli("import", str(source), "--project", str(project), "--lang", "en")
            write_json(
                responses,
                [
                    {
                        "collection": "glossary",
                        "record": {
                            "id": "TERM-AI-REVIEWED",
                            "term": "garden",
                            "chapter_id": "CH-01",
                            "status": "draft",
                        },
                        "evidence": [
                            {"snippet": "garden whose beds moved with the phases of the moon"}
                        ],
                    }
                ],
            )
            created = cli(
                "candidates", "create", str(responses),
                "--run-directory", str(run), "--source", str(source),
                "--project", str(project), "--lang", "en", "--run-id", "ai-run",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            manifest = read_json(run / "manifest.json")
            candidate = manifest["candidates"][0]
            write_json(
                review_artifact,
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
                            "decision": "accept",
                            "checks": [
                                "scope_and_qualification",
                                "semantic_fidelity",
                                "source_support",
                            ],
                        }
                    ],
                },
            )
            accepted = cli(
                "candidates", "accept", str(run),
                "--id", candidate["candidate_id"],
                "--reviewer", "test-model", "--reviewer-type", "ai",
                "--review-artifact", str(review_artifact),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn("by ai review", accepted.stdout)
            recorded = read_json(run / "manifest.json")["candidates"][0]["review"]
            self.assertEqual(recorded["reviewer_type"], "ai")
            self.assertEqual(len(recorded["review_artifact_sha256"]), 64)

    def test_english_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "en-pack"
            self.assertEqual(cli("init", str(project), "--title", "Clockwork Garden", "--author", "Author", "--lang", "en").returncode, 0)
            self.assertEqual(cli("import", str(SAMPLE / "manuscripts" / "book.en.md"), "--project", str(project), "--lang", "en").returncode, 0)
            self.assertEqual(cli("build", "--project", str(project)).returncode, 0)
            self.assertEqual(cli("check", "--project", str(project)).returncode, 0)

    def test_bilingual_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "both-pack"
            result = cli("init", str(project), "--title", "歯車仕掛けの庭", "--author", "Author", "--lang", "ja", "--lang", "en", "--primary-language", "ja")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cli("import", str(SAMPLE / "manuscripts" / "book.ja.md"), "--project", str(project), "--lang", "ja").returncode, 0)
            self.assertEqual(cli("import", str(SAMPLE / "manuscripts" / "book.en.md"), "--project", str(project), "--lang", "en").returncode, 0)
            self.assertEqual(cli("validate", "--project", str(project)).returncode, 0)
            self.assertEqual(cli("build", "--project", str(project), "--lang", "all").returncode, 0)
            self.assertEqual(cli("check", "--project", str(project), "--lang", "all").returncode, 0)

    def test_import_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "pack"
            cli("init", str(project), "--title", "Book", "--author", "Author", "--lang", "en")
            source = str(SAMPLE / "manuscripts" / "book.en.md")
            self.assertEqual(cli("import", source, "--project", str(project), "--lang", "en").returncode, 0)
            second = cli("import", source, "--project", str(project), "--lang", "en")
            self.assertEqual(second.returncode, 3)
            self.assertIn("refusing", second.stderr)

    def test_check_detects_generated_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = copy_sample(Path(tmp))
            path = project / "dist" / "clockwork-garden-reading-pack.en.md"
            path.write_text(path.read_text() + "manual edit\n")
            result = cli("check", "--project", str(project), "--lang", "en")
            self.assertEqual(result.returncode, 5)
            self.assertIn("differs from canonical", result.stderr)

    def test_release_check_passes_for_complete_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = copy_sample(Path(tmp))
            result = cli("check", "--project", str(project), "--release")
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctor_reports_offline_runtime(self):
        result = cli("doctor", "--project", str(SAMPLE))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime dependency: jsonschema", result.stdout)
        self.assertNotIn("standard library only", result.stdout)
        self.assertIn("network/API keys: not required", result.stdout)
        self.assertIn("optional PDF import", result.stdout)

    def test_link_translations_refreshes_hash_and_revokes_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = copy_sample(Path(tmp))
            primary_path = project / "data" / "pack.ja.json"
            primary = read_json(primary_path)
            primary["claims"][0]["statement"] += " 改訂"
            write_json(primary_path, primary)
            stale = cli("validate", "--project", str(project))
            self.assertEqual(stale.returncode, 3)
            self.assertIn("RP202", stale.stderr)
            linked = cli("link-translations", "--project", str(project), "--lang", "en")
            self.assertEqual(linked.returncode, 0, linked.stderr)
            translated = read_json(project / "data" / "pack.en.json")
            self.assertEqual(translated["claims"][0]["status"], "draft")
            self.assertEqual(translated["claims"][0]["translation_status"], "draft")
            self.assertEqual(cli("validate", "--project", str(project)).returncode, 0)
            self.assertEqual(cli("validate", "--project", str(project), "--release").returncode, 3)

    def test_nonempty_init_target_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "keep.txt").write_text("keep")
            result = cli("init", str(target), "--title", "Book", "--author", "Author", "--lang", "en")
            self.assertEqual(result.returncode, 4)
            self.assertEqual((target / "keep.txt").read_text(), "keep")

    def test_init_refuses_file_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "occupied"
            target.write_text("keep")
            result = cli("init", str(target), "--title", "Book", "--author", "Author", "--lang", "en")
            self.assertEqual(result.returncode, 4)
            self.assertEqual(target.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
