from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reading_pack.delivery import (
    CORE_INDEX_MAX_CHARACTERS,
    CORE_INDEX_MAX_UTF8_BYTES,
    CORE_INDEX_PROFILE,
    DEFAULT_DELIVERY_PLAN,
    DIRECT_PROFILE,
    PORTABLE_PROFILE,
    PROFILE,
    build_delivery,
    check_delivery,
    generate_probes,
    normalize_base_url,
    parse_pack,
    record_units,
    sha256_bytes,
    split_section,
    verify_bundle_directory,
)
from reading_pack.errors import EXIT_CHECK, ReadingPackError
from reading_pack.project import copy_project, load_config, load_language_data
from reading_pack.rendering import output_path, render_pack


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        source = Path(__file__).resolve().parents[1] / "examples" / "clockwork-garden"
        self.project = self.workspace / "clockwork-garden"
        copy_project(source, self.project)
        self.config = load_config(self.project)
        self.data = {
            lang: load_language_data(self.project, lang)
            for lang in self.config["languages"]
        }
        self.output = self.workspace / "delivery"
        self.base_url = "https://staging.example/reading-packs/clockwork-garden"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_parse_and_record_units_cover_canonical_bytes_once(self) -> None:
        pack = output_path(self.project, self.config, "en").read_text(encoding="utf-8")
        structure = parse_pack(pack)
        self.assertEqual(
            structure.prologue
            + "".join(section.text for section in structure.sections.values())
            + structure.epilogue,
            pack,
        )
        for module, section in structure.sections.items():
            if module in {"SYS", "BIB"}:
                continue
            units = record_units(section)
            self.assertEqual("".join(unit.text for unit in units), section.text)

    def test_part_split_preserves_record_and_section_bytes(self) -> None:
        pack = output_path(self.project, self.config, "en").read_text(encoding="utf-8")
        section = parse_pack(pack).sections["MAP"]
        parts = split_section(
            section,
            pack_sha256=sha256_bytes(pack.encode("utf-8")),
            language="en",
            maximum_bytes=4096,
        )
        self.assertEqual(b"".join(part.payload for part in parts), section.text.encode("utf-8"))
        self.assertEqual(
            [record_id for part in parts for record_id in part.record_ids],
            ["CH-01", "CH-02"],
        )
        self.assertTrue(all(len(part.content) <= 4096 for part in parts))

    def test_build_and_check_are_deterministic_and_portable_pack_is_exact(self) -> None:
        builds = build_delivery(
            self.project,
            ["ja", "en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )
        self.assertEqual([build.language for build in builds], ["ja", "en"])
        for build in builds:
            canonical = output_path(self.project, self.config, build.language).read_bytes()
            self.assertEqual(build.pack.read_bytes(), canonical)
            self.assertEqual((build.directory / build.language / "pack.txt").read_bytes(), canonical)
            self.assertTrue(
                (build.directory / DIRECT_PROFILE / build.language / "entry-prompt.txt").is_file()
            )
            self.assertTrue(
                (build.directory / PORTABLE_PROFILE / build.language / "entry-prompt.txt").is_file()
            )
            core_index_root = build.directory / CORE_INDEX_PROFILE / build.language
            artifacts = {
                name: (core_index_root / f"{name}.md").read_bytes()
                for name in ("core", "mis", "names", "gloss")
            }
            for name, content in artifacts.items():
                self.assertEqual((core_index_root / f"{name}.txt").read_bytes(), content)
                self.assertLessEqual(len(content), CORE_INDEX_MAX_UTF8_BYTES)
                self.assertLessEqual(
                    len(content.decode("utf-8")), CORE_INDEX_MAX_CHARACTERS
                )
            core_index_manifest = json.loads(
                (core_index_root / "manifest.json").read_text(encoding="utf-8")
            )
            reconstructed = b"".join(
                artifacts[item["artifact"].removesuffix(".md")][
                    item["payload_offset"] : item["payload_offset"]
                    + item["payload_bytes"]
                ]
                for item in core_index_manifest["components"]
            )
            self.assertEqual(reconstructed, canonical)
            self.assertEqual(
                [item["ordinal"] for item in core_index_manifest["components"]],
                list(range(len(core_index_manifest["components"]))),
            )
            direct = (
                build.directory / DIRECT_PROFILE / build.language / "entry-prompt.txt"
            ).read_text(encoding="utf-8")
            lazy = (
                build.directory / PROFILE / build.language / "entry-prompt.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("pack.txt", direct)
            self.assertNotIn("pack.md", direct)
            self.assertIn(
                "Web取得禁止" if build.language == "ja" else "never Web-fetch",
                lazy,
            )
            structure = parse_pack(canonical.decode("utf-8"))
            core = artifacts["core"]
            for module in ("MIS", "NAMES", "GLOSS"):
                self.assertNotIn(structure.sections[module].text.encode("utf-8"), core)
                self.assertIn(
                    structure.sections[module].text.encode("utf-8"),
                    artifacts[module.lower()],
                )
            core_index_prompt = (core_index_root / "entry-prompt.txt").read_text(
                encoding="utf-8"
            )
            for name in ("core", "mis", "names", "gloss"):
                self.assertIn(
                    f"/{CORE_INDEX_PROFILE}/{build.language}/{name}.txt",
                    core_index_prompt,
                )
            self.assertIn("ENDPACKCORE", core_index_prompt)
            self.assertIn("ENDPACKSHARD", core_index_prompt)
            self.assertIn(structure.sections["SYS"].text.encode("utf-8"), lazy.encode("utf-8"))
            bootstrap = (
                build.directory / PROFILE / build.language / "bootstrap.md"
            ).read_bytes()
            self.assertIn(structure.prologue.encode("utf-8"), bootstrap)
            self.assertIn(structure.sections["SYS"].text.encode("utf-8"), bootstrap)
            self.assertIn(structure.sections["BIB"].text.encode("utf-8"), bootstrap)
            manifest = verify_bundle_directory(
                build.directory,
                language=build.language,
                plan=DEFAULT_DELIVERY_PLAN,
            )
            self.assertEqual(manifest["pack"]["sha256"], sha256_bytes(canonical))
            self.assertEqual(manifest["profile"], PROFILE)
        checked = check_delivery(
            self.project,
            ["ja", "en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )
        self.assertEqual(
            [(item.language, item.pack_sha256) for item in checked],
            [(item.language, item.pack_sha256) for item in builds],
        )

    def test_stale_canonical_pack_blocks_bundle_build(self) -> None:
        path = output_path(self.project, self.config, "en")
        path.write_text(path.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
        with self.assertRaises(ReadingPackError) as raised:
            build_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url=self.base_url,
                output_root=self.output,
            )
        self.assertEqual(raised.exception.exit_code, EXIT_CHECK)
        self.assertIn("stale", str(raised.exception))

    def test_tampered_part_fails_hash_and_reconstruction_check(self) -> None:
        build = build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )[0]
        part = next((build.directory / PROFILE / "en" / "modules").rglob("part-*.md"))
        content = part.read_bytes()
        part.write_bytes(content.replace(b"ENDPART", b"ENDPARX", 1))
        with self.assertRaises(ReadingPackError):
            verify_bundle_directory(
                build.directory,
                language="en",
                plan=DEFAULT_DELIVERY_PLAN,
            )

    def test_tampered_core_index_artifact_fails_closed(self) -> None:
        build = build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )[0]
        core_path = build.directory / CORE_INDEX_PROFILE / "en" / "core.md"
        core_path.write_bytes(core_path.read_bytes().replace(b"ENDPACKCORE", b"ENDPACKCORX", 1))
        with self.assertRaises(ReadingPackError):
            verify_bundle_directory(
                build.directory,
                language="en",
                plan=DEFAULT_DELIVERY_PLAN,
            )

    def test_core_index_byte_budget_fails_without_truncating_a_large_shard(self) -> None:
        self.data["en"]["glossary"][0]["book_meaning"] += "x" * CORE_INDEX_MAX_UTF8_BYTES
        rendered = render_pack(
            self.project,
            "en",
            self.config,
            self.data["en"],
        )
        output_path(self.project, self.config, "en").write_text(rendered, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "web-core-index-v2 gloss.*maximum"):
            build_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url=self.base_url,
                output_root=self.output,
            )

    def test_core_index_character_budget_is_enforced_separately(self) -> None:
        self.data["en"]["names"][0]["book_context"] += "x" * CORE_INDEX_MAX_CHARACTERS
        rendered = render_pack(
            self.project,
            "en",
            self.config,
            self.data["en"],
        )
        output_path(self.project, self.config, "en").write_text(rendered, encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "web-core-index-v2 names.*characters"):
            build_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url=self.base_url,
                output_root=self.output,
            )

    def test_forged_entry_block_still_fails_canonical_byte_check(self) -> None:
        build = build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )[0]
        entry_path = build.directory / PROFILE / "en" / "entry-prompt.txt"
        entry = entry_path.read_bytes()
        marker = b"BEGIN_AUTHORITATIVE_SYS | "
        start = entry.index(marker)
        line_end = entry.index(b"\n", start)
        fields = dict(
            item.split("=", 1)
            for item in entry[start:line_end].decode("ascii").split(" | ")[1:]
        )
        size = int(fields["bytes"])
        payload_start = line_end + 1
        altered = bytearray(entry[payload_start : payload_start + size])
        altered[0] = ord("X") if altered[0] != ord("X") else ord("Y")
        altered_bytes = bytes(altered)
        replacement_header = (
            f"BEGIN_AUTHORITATIVE_SYS | bytes={size} | sha256={sha256_bytes(altered_bytes)}\n"
        ).encode("ascii")
        forged = (
            entry[:start]
            + replacement_header
            + altered_bytes
            + entry[payload_start + size :]
        )
        entry_path.write_bytes(forged)
        manifest_path = build.manifest
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entry_prompt"]["sha256"] = sha256_bytes(forged)
        manifest["entry_prompt"]["bytes"] = len(forged)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReadingPackError, "differs from canonical bytes"):
            verify_bundle_directory(
                build.directory,
                language="en",
                plan=DEFAULT_DELIVERY_PLAN,
            )

    def test_part_version_and_record_metadata_are_checked_beyond_outer_hash(self) -> None:
        build = build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )[0]
        base = build.directory
        for case in ("version", "records"):
            with self.subTest(case=case):
                root = self.workspace / "variants" / case / build.pack_sha256
                shutil.copytree(base, root)
                manifest_path = root / PROFILE / "en" / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                module = next(item for item in manifest["modules"] if item["id"] != "META")
                declared = module["parts"][0]
                part_path = (
                    root
                    / PROFILE
                    / "en"
                    / "modules"
                    / module["id"]
                    / f"part-{declared['number']:03d}.md"
                )
                content = part_path.read_bytes()
                if case == "version":
                    content = content.replace(
                        build.pack_sha256.encode("ascii"),
                        ("0" * 64).encode("ascii"),
                    )
                else:
                    old_count = f"records={declared['records']}".encode("ascii")
                    new_count = f"records={declared['records'] + 1}".encode("ascii")
                    content = content.replace(old_count, new_count)
                part_path.write_bytes(content)
                declared["sha256"] = sha256_bytes(content)
                declared["bytes"] = len(content)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ReadingPackError):
                    verify_bundle_directory(
                        root,
                        language="en",
                        plan=DEFAULT_DELIVERY_PLAN,
                    )

    def test_existing_hash_directory_cannot_change_with_another_origin(self) -> None:
        build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )
        with self.assertRaises(ReadingPackError) as raised:
            build_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url="https://another.example/reading-packs/clockwork-garden",
                output_root=self.output,
            )
        self.assertEqual(raised.exception.exit_code, EXIT_CHECK)
        self.assertIn("immutable", str(raised.exception))

    def test_delivery_check_rejects_symlinks_in_the_bundle(self) -> None:
        build = build_delivery(
            self.project,
            ["en"],
            self.config,
            self.data,
            base_url=self.base_url,
            output_root=self.output,
        )[0]
        link = build.directory / "unexpected-link"
        try:
            link.symlink_to(build.pack)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(ReadingPackError, "symlink"):
            check_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url=self.base_url,
                output_root=self.output,
            )

    def test_probe_files_have_exact_declared_sizes_and_markers(self) -> None:
        manifest_path = generate_probes(self.workspace / "probes")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["sizes"]:
            content = (manifest_path.parent / item["path"]).read_bytes()
            self.assertEqual(len(content), item["bytes"])
            self.assertEqual(sha256_bytes(content), item["sha256"])
            for marker in item["markers"]:
                self.assertIn(marker.encode("ascii"), content)
        self.assertEqual(
            [case["count"] for case in manifest["chain_cases"]],
            [1, 2, 4, 8],
        )
        for case in manifest["chain_cases"]:
            self.assertEqual(len(case["paths"]), case["count"])
            self.assertEqual(len(case["markers"]), case["count"])
        corrupt = manifest["corrupt"]
        self.assertEqual(
            {case["id"] for case in corrupt["cases"]},
            {
                "tail-missing",
                "middle-missing",
                "mixed-version",
                "duplicate-part",
                "wrong-record-count",
                "wrong-boundaries",
            },
        )
        for relative, declared in corrupt["files"].items():
            content = (manifest_path.parent / relative).read_bytes()
            self.assertEqual(len(content), declared["bytes"])
            self.assertEqual(sha256_bytes(content), declared["sha256"])

    def test_base_url_requires_https_except_localhost(self) -> None:
        self.assertRegex(PROFILE, r"-v[1-9][0-9]*$")
        self.assertRegex(DIRECT_PROFILE, r"-v[1-9][0-9]*$")
        self.assertRegex(PORTABLE_PROFILE, r"-v[1-9][0-9]*$")
        self.assertRegex(CORE_INDEX_PROFILE, r"-v[1-9][0-9]*$")
        self.assertEqual(
            normalize_base_url("https://staging.example/path/"),
            "https://staging.example/path",
        )
        self.assertEqual(normalize_base_url("http://localhost:8000/path"), "http://localhost:8000/path")
        with self.assertRaises(ReadingPackError):
            normalize_base_url("http://example.com/path")
        with self.assertRaises(ReadingPackError):
            normalize_base_url("https://user:secret@example.com/path")

    def test_build_requires_base_url_to_end_with_pack_slug(self) -> None:
        with self.assertRaisesRegex(ReadingPackError, "Pack slug"):
            build_delivery(
                self.project,
                ["en"],
                self.config,
                self.data,
                base_url="https://staging.example/reading-packs/wrong-book",
                output_root=self.output,
            )


if __name__ == "__main__":
    unittest.main()
