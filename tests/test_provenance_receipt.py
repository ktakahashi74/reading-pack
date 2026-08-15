from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import reading_pack_producer.candidates as candidate_module
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.candidates import (
    accept_candidates,
    apply_candidate_run,
    create_candidate_run,
    load_candidate_run,
)
from reading_pack_producer.provenance_receipt import (
    AppliedRunArtifact,
    create_provenance_receipt,
    load_provenance_receipt,
    write_provenance_receipt,
)
from tests.support import cli


class ProvenanceReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.source = self.root / "book.txt"
        self.source.write_text(
            "Cobalt is the first mechanism. Amber is the second mechanism.\n",
            encoding="utf-8",
        )
        create_project(
            self.project,
            title="Receipt Book",
            author="Author",
            languages=["en"],
            primary_language="en",
        )
        data = load_language_data(self.project, "en")
        data["source"] = {
            "format": "text",
            "name": self.source.name,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }
        data["chapters"] = [{
            "id": "CH-01",
            "kind": "chapter",
            "title": "Mechanisms",
            "pages": "1-2",
            "sections": ["Two mechanisms"],
            "summary": "",
            "terms": [],
            "status": "draft",
        }]
        write_json(self.project / "data" / "pack.en.json", data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _applied_run(self, name: str, term: str, snippet: str) -> Path:
        data = load_language_data(self.project, "en")
        run = self.root / name
        create_candidate_run(
            run,
            source_path=self.source,
            responses={
                "collection": "glossary",
                "record": {
                    "id": f"TERM-{term.upper()}",
                    "term": term,
                    "chapter_id": "CH-01",
                    "status": "draft",
                },
                "evidence": [{"snippet": snippet}],
            },
            language="en",
            canonical_data=data,
            project_data_by_lang={"en": data},
            known_chapter_ids={"CH-01"},
            run_id=name,
            created_at="2026-08-15T00:00:00+00:00",
        )
        candidate_id = load_candidate_run(run)["candidates"][0]["candidate_id"]
        accept_candidates(
            run,
            [candidate_id],
            reviewer="Synthetic reviewer",
            reviewed_at="2026-08-15T00:01:00+00:00",
        )
        apply_candidate_run(
            self.project,
            language="en",
            run=run,
            source_path=self.source,
            candidate_ids=[candidate_id],
        )
        return run

    def test_sequential_application_receipt_is_verified_deterministic_and_cli_writable(self) -> None:
        first = self._applied_run("run-one", "Cobalt", "Cobalt is the first mechanism")
        second = self._applied_run("run-two", "Amber", "Amber is the second mechanism")
        artifacts = [
            AppliedRunArtifact(first, self.source),
            AppliedRunArtifact(second, self.source),
        ]
        one = create_provenance_receipt(
            self.project, language="en", artifacts=artifacts
        )
        two = create_provenance_receipt(
            self.project, language="en", artifacts=artifacts
        )
        self.assertEqual(one, two)
        self.assertEqual(one["continuity"], {
            "status": "verified",
            "verified_links": 2,
            "total_links": 2,
        })
        self.assertEqual(
            one["runs"][0]["application"]["after_sha256"],
            one["runs"][1]["canonical_before"]["data_sha256"],
        )
        destination = write_provenance_receipt(self.root / "receipt.json", one)
        self.assertEqual(load_provenance_receipt(destination), one)

        result = cli(
            "candidates", "receipt",
            "--project", str(self.project),
            "--lang", "en",
            "--artifact", str(first), str(self.source),
            "--artifact", str(second), str(self.source),
            "--output", "cli-receipt.json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["continuity"]["status"], "verified")
        self.assertTrue(
            (self.project / ".reading-pack/receipts/cli-receipt.json").is_file()
        )

    def test_receipt_rejects_wrong_run_order(self) -> None:
        first = self._applied_run("run-one", "Cobalt", "Cobalt is the first mechanism")
        second = self._applied_run("run-two", "Amber", "Amber is the second mechanism")
        with self.assertRaisesRegex(ReadingPackError, "does not connect"):
            create_provenance_receipt(
                self.project,
                language="en",
                artifacts=[
                    AppliedRunArtifact(second, self.source),
                    AppliedRunArtifact(first, self.source),
                ],
            )

    def test_legacy_run_requires_explicit_partial_continuity(self) -> None:
        first = self._applied_run("run-one", "Cobalt", "Cobalt is the first mechanism")
        second = self._applied_run("run-two", "Amber", "Amber is the second mechanism")
        manifest = load_candidate_run(first)
        manifest.pop("application")
        candidate_module._write_manifest(first / "manifest.json", manifest)
        artifacts = [
            AppliedRunArtifact(first, self.source),
            AppliedRunArtifact(second, self.source),
        ]
        with self.assertRaisesRegex(ReadingPackError, "predates durable application"):
            create_provenance_receipt(
                self.project, language="en", artifacts=artifacts
            )
        receipt = create_provenance_receipt(
            self.project,
            language="en",
            artifacts=artifacts,
            allow_legacy=True,
        )
        self.assertEqual(receipt["continuity"]["status"], "legacy_unverified")
        self.assertEqual(receipt["continuity"]["verified_links"], 1)


if __name__ == "__main__":
    unittest.main()
