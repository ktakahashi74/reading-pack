from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from reading_pack_producer.agent_skill import (
    MAX_REFERENCE_BYTES,
    archive_bytes,
    build_agent_skill,
    check_agent_skill,
    distribution_paths,
    expected_agent_skill,
)
from reading_pack.errors import EXIT_CHECK, EXIT_IO, EXIT_VALIDATION, ReadingPackError
from reading_pack.validation import validate_project

from tests.support import SAMPLE, cli, copy_sample


class AgentSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = copy_sample(Path(self.tmp.name))
        self.config, self.data, issues = validate_project(self.project)
        self.assertEqual(issues, [])
        self.directory, self.archive = distribution_paths(
            self.project, self.config["slug"]
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _artifact_snapshot(self) -> tuple[dict[str, bytes], bytes]:
        files = {
            path.relative_to(self.directory).as_posix(): path.read_bytes()
            for path in sorted(self.directory.rglob("*"))
            if path.is_file()
        }
        return files, self.archive.read_bytes()

    def _build(self) -> tuple[dict[str, bytes], bytes]:
        build_agent_skill(self.project, self.config, self.data)
        return self._artifact_snapshot()

    def test_bilingual_build_is_deterministic_and_references_are_pack_bytes(self):
        ordinary = {
            language: (
                self.project
                / "dist"
                / f"{self.config['output_basename']}.{language}.md"
            ).read_bytes()
            for language in ("ja", "en")
        }
        first = self._build()
        second = self._build()
        self.assertEqual(first, second)
        for language, value in ordinary.items():
            reference = (
                self.directory
                / "references"
                / f"{self.config['output_basename']}.{language}.md"
            )
            self.assertEqual(reference.read_bytes(), value)
        check_agent_skill(self.project, self.config, self.data)

    def test_frontmatter_manifest_and_archive_follow_the_contract(self):
        self._build()
        skill = (self.directory / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith('---\nname: "clockwork-garden"\n'))
        self.assertIn('\ndescription: "', skill)
        self.assertIn('\nlicense: "CC0 1.0 Universal"\n', skill)
        self.assertIn('\ncompatibility: "', skill)
        self.assertIn('\nmetadata:\n', skill)
        self.assertIn('reading-pack-languages: "ja,en"', skill)
        self.assertIn("Follow the selected Reading Pack's `SYS` section", skill)
        self.assertNotIn("allowed-tools:", skill)
        self.assertNotIn("<script", skill.lower())

        manifest = json.loads((self.directory / "manifest.json").read_text())
        self.assertEqual(manifest["format_version"], "1")
        self.assertEqual(manifest["kind"], "reading-pack-agent-skill")
        self.assertEqual(manifest["skill"], {"name": "clockwork-garden"})
        self.assertEqual(manifest["reading_pack"]["languages"], ["ja", "en"])
        self.assertEqual(
            [record["path"] for record in manifest["files"]],
            [
                "SKILL.md",
                "references/clockwork-garden-reading-pack.en.md",
                "references/clockwork-garden-reading-pack.ja.md",
            ],
        )
        for record in manifest["files"]:
            value = (self.directory / record["path"]).read_bytes()
            self.assertEqual(record["bytes"], len(value))
            self.assertEqual(record["sha256"], hashlib.sha256(value).hexdigest())
        with zipfile.ZipFile(self.archive) as zipped:
            infos = zipped.infolist()
            self.assertEqual(
                [info.filename for info in infos],
                sorted(info.filename for info in infos),
            )
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos))
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in infos))
            self.assertTrue(all((info.external_attr >> 16) == 0o100644 for info in infos))

    def test_monolingual_cli_build_contains_only_configured_language(self):
        root = Path(self.tmp.name) / "mono"
        initialized = cli(
            "init",
            str(root),
            "--title",
            "One Language",
            "--author",
            "Author",
            "--lang",
            "en",
            "--slug",
            "one-language",
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        imported = cli(
            "import",
            str(SAMPLE / "manuscripts" / "book.en.md"),
            "--project",
            str(root),
            "--lang",
            "en",
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(
            cli("build", "--project", str(root), "--lang", "en").returncode, 0
        )
        built = cli("agent-skill", "build", "--project", str(root))
        self.assertEqual(built.returncode, 0, built.stderr)
        manifest = json.loads(
            (root / "dist" / "agent-skill" / "one-language" / "manifest.json").read_text()
        )
        self.assertEqual(manifest["reading_pack"]["languages"], ["en"])
        references = root / "dist" / "agent-skill" / "one-language" / "references"
        self.assertEqual([path.name for path in references.iterdir()], ["reading-pack.en.md"])

    def test_invalid_and_too_long_names_are_rejected_without_shortening(self):
        for name in ("Bad_Name", "a" * 65):
            with self.subTest(name=name):
                config = copy.deepcopy(self.config)
                config["slug"] = name
                with self.assertRaises(ReadingPackError) as caught:
                    expected_agent_skill(self.project, config, self.data)
                self.assertEqual(caught.exception.exit_code, EXIT_VALIDATION)

    def test_dangerous_reference_basename_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["output_basename"] = "../escape"
        with self.assertRaises(ReadingPackError) as caught:
            expected_agent_skill(self.project, config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_VALIDATION)
        self.assertFalse((self.project / "escape.ja.md").exists())

    def test_missing_or_stale_ordinary_pack_preserves_existing_distribution(self):
        before = self._build()
        ordinary = self.project / "dist" / "clockwork-garden-reading-pack.en.md"
        original = ordinary.read_bytes()
        for state in ("stale", "missing"):
            with self.subTest(state=state):
                ordinary.write_bytes(original)
                if state == "stale":
                    ordinary.write_bytes(original + b"tampered\n")
                else:
                    ordinary.unlink()
                with self.assertRaises(ReadingPackError) as caught:
                    build_agent_skill(self.project, self.config, self.data)
                self.assertEqual(caught.exception.exit_code, EXIT_CHECK)
                self.assertEqual(self._artifact_snapshot(), before)
        ordinary.write_bytes(original)

    def test_staging_failure_preserves_existing_distribution(self):
        before = self._build()
        with mock.patch(
            "reading_pack_producer.agent_skill._validate_archive",
            side_effect=ReadingPackError("staged failure", EXIT_IO),
        ):
            with self.assertRaises(ReadingPackError):
                build_agent_skill(self.project, self.config, self.data)
        self.assertEqual(self._artifact_snapshot(), before)

    def test_install_failure_rolls_back_both_existing_outputs(self):
        before = self._build()
        real_replace = os.replace

        def fail_final_archive(source, destination):
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                destination_path == self.archive
                and source_path.name == self.archive.name
                and source_path.parent.name.startswith(".agent-skill-stage-")
            ):
                raise OSError("simulated archive install failure")
            return real_replace(source, destination)

        with mock.patch(
            "reading_pack_producer.agent_skill.os.replace", side_effect=fail_final_archive
        ):
            with self.assertRaises(ReadingPackError) as caught:
                build_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_IO)
        self.assertEqual(self._artifact_snapshot(), before)

    def test_check_detects_file_tamper_and_is_read_only(self):
        self._build()
        skill = self.directory / "SKILL.md"
        skill.write_bytes(skill.read_bytes() + b"tampered\n")
        before = self._artifact_snapshot()
        with self.assertRaises(ReadingPackError) as caught:
            check_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_CHECK)
        self.assertEqual(self._artifact_snapshot(), before)

    def test_check_rejects_extra_file_and_scripts_injection(self):
        for relative in ("extra.txt", "scripts/run.sh", "scripts/"):
            with self.subTest(relative=relative):
                self._build()
                path = self.directory / relative
                if relative.endswith("/"):
                    path.mkdir()
                else:
                    path.parent.mkdir(exist_ok=True)
                    path.write_text("untrusted", encoding="utf-8")
                with self.assertRaises(ReadingPackError) as caught:
                    check_agent_skill(self.project, self.config, self.data)
                self.assertEqual(caught.exception.exit_code, EXIT_CHECK)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_check_and_build_reject_symlink_outputs(self):
        self._build()
        reference = self.directory / "references" / "clockwork-garden-reading-pack.en.md"
        reference.unlink()
        reference.symlink_to(self.project / "dist" / "clockwork-garden-reading-pack.en.md")
        with self.assertRaises(ReadingPackError) as caught:
            check_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_CHECK)

        self._build()
        self.archive.unlink()
        self.archive.symlink_to(self.project / "dist" / "clockwork-garden-reading-pack.en.md")
        with self.assertRaises(ReadingPackError) as caught:
            build_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_IO)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are unavailable")
    def test_check_rejects_nonregular_file(self):
        self._build()
        os.mkfifo(self.directory / "unexpected-fifo")
        with self.assertRaises(ReadingPackError) as caught:
            check_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_CHECK)

    def test_check_rejects_zip_traversal_and_zip_tamper(self):
        for member in ("../escape", "clockwork-garden/SKILL.md"):
            with self.subTest(member=member):
                self._build()
                with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_STORED) as archive:
                    archive.writestr(member, b"tampered")
                with self.assertRaises(ReadingPackError) as caught:
                    check_agent_skill(self.project, self.config, self.data)
                self.assertEqual(caught.exception.exit_code, EXIT_CHECK)

    def test_check_rejects_zip_symlink_entry(self):
        self._build()
        info = zipfile.ZipInfo("clockwork-garden/SKILL.md")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr(info, b"manifest.json")
        with self.assertRaises(ReadingPackError) as caught:
            check_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_CHECK)

    def test_oversize_reference_is_rejected(self):
        with mock.patch(
            "reading_pack_producer.agent_skill.MAX_REFERENCE_BYTES",
            len((self.project / "dist" / "clockwork-garden-reading-pack.en.md").read_bytes()) - 1,
        ):
            with self.assertRaises(ReadingPackError) as caught:
                expected_agent_skill(self.project, self.config, self.data)
        self.assertEqual(caught.exception.exit_code, EXIT_VALIDATION)

    def test_generated_control_files_exclude_private_and_environment_data(self):
        self._build()
        control = (
            (self.directory / "SKILL.md").read_text()
            + (self.directory / "manifest.json").read_text()
        )
        forbidden = (
            str(self.project),
            str(Path.home()),
            ".reading-pack/",
            "quality-plan.json",
            "data/pack.",
            "templates/",
            "candidate",
            "private review",
            "signature",
            "authentication",
        )
        for value in forbidden:
            self.assertNotIn(value, control)

    def test_agent_skill_build_does_not_modify_ordinary_packs(self):
        ordinary = {
            path.name: path.read_bytes()
            for path in (self.project / "dist").glob("*.md")
        }
        self._build()
        self.assertEqual(
            ordinary,
            {
                path.name: path.read_bytes()
                for path in (self.project / "dist").glob("*.md")
            },
        )

    def test_cli_exit_codes_and_release_gate(self):
        built = cli(
            "agent-skill", "build", "--project", str(self.project), "--release"
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        (self.directory / "manifest.json").write_text("{}\n", encoding="utf-8")
        checked = cli(
            "agent-skill", "check", "--project", str(self.project), "--release"
        )
        self.assertEqual(checked.returncode, EXIT_CHECK, checked.stderr)

        config_path = self.project / "reading-pack.toml"
        config_text = config_path.read_text(encoding="utf-8")
        self.assertIn('publication_decision = "approved"', config_text)
        config_path.write_text(
            config_text.replace(
                'publication_decision = "approved"',
                'publication_decision = "pending"',
            ),
            encoding="utf-8",
        )
        release = cli(
            "agent-skill", "build", "--project", str(self.project), "--release"
        )
        self.assertEqual(release.returncode, EXIT_VALIDATION, release.stderr)

    def test_expected_archive_function_is_byte_deterministic(self):
        expected = expected_agent_skill(self.project, self.config, self.data)
        first = archive_bytes(self.config["slug"], expected)
        second = archive_bytes(self.config["slug"], expected)
        self.assertEqual(first, second)
        self.assertLess(len(first), MAX_REFERENCE_BYTES)

    def test_public_docs_keep_the_three_layers_and_valid_local_links(self):
        root = Path(__file__).resolve().parents[1]
        documents = (
            root / "README.md",
            root / "README.ja.md",
            root / "docs" / "agent-skills.en.md",
            root / "docs" / "agent-skills.ja.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in documents)
        self.assertIn("Conversational Edition", combined)
        self.assertIn("対話版", combined)
        self.assertIn("Reading Pack", combined)
        self.assertIn("読解パック", combined)
        self.assertIn("optional compatibility container", combined)
        self.assertIn("任意の互換コンテナ", combined)
        self.assertNotIn("AI Reading Pack", combined)
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                local = (document.parent / target.split("#", 1)[0]).resolve()
                self.assertTrue(local.exists(), f"broken link in {document}: {target}")


if __name__ == "__main__":
    unittest.main()
