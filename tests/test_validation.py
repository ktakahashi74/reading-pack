from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reading_pack.validation import errors, validate_project

from tests.support import copy_sample, read_json, write_json


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = copy_sample(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, release: bool = False):
        return [issue.code for issue in validate_project(self.project, release=release)[2]]

    def test_complete_bilingual_sample_passes_release_validation(self):
        self.assertEqual(self.codes(release=True), [])

    def test_duplicate_id_is_detected_across_collections(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["names"][0]["id"] = "CH-01"
        write_json(path, data)
        self.assertIn("RP115", self.codes())

    def test_broken_chapter_reference_is_detected(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["claims"][0]["chapter_ids"] = ["CH-99"]
        write_json(path, data)
        self.assertIn("RP118", self.codes())

    def test_broken_certainty_reference_is_detected(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["claims"][0]["certainty_id"] = "CERT-X"
        write_json(path, data)
        self.assertIn("RP120", self.codes())

    def test_misreading_kind_and_claim_links_are_validated(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["misreadings"][0]["kind"] = "question"
        data["misreadings"][0]["claim_ids"] = ["PROP-NOT-THERE"]
        write_json(path, data)
        codes = self.codes()
        self.assertIn("RP126", codes)
        self.assertIn("RP121", codes)

    def test_support_source_provenance_requires_a_valid_pair(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["claims"][0]["provenance_source_id"] = "bad source"
        write_json(path, data)
        self.assertIn("RP127", self.codes())

        data = read_json(path)
        data["claims"][0]["provenance_source_id"] = "SRC-AUTHOR-QA"
        data["claims"][0]["provenance_source_hash"] = "0" * 64
        write_json(path, data)
        self.assertNotIn("RP127", self.codes())

    def test_support_source_provenance_must_match_registry(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["claims"][0]["provenance_source_id"] = "SRC-MISSING"
        data["claims"][0]["provenance_source_hash"] = "0" * 64
        write_json(path, data)
        self.assertIn("RP128", self.codes())

    def test_optional_rendered_misreading_fields_must_be_strings(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["misreadings"][0]["impact"] = 42
        write_json(path, data)
        self.assertIn("RP123", self.codes())

    def test_language_parity_mismatch_is_detected(self):
        path = self.project / "data" / "pack.en.json"
        data = read_json(path)
        data["chapters"].pop()
        write_json(path, data)
        self.assertIn("RP200", self.codes())

    def test_stale_translation_is_detected_after_primary_change(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["chapters"][0]["summary"] += " 改訂。"
        write_json(path, data)
        self.assertIn("RP202", self.codes())

    def test_review_status_change_does_not_stale_translation(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["chapters"][0]["status"] = "reviewed"
        write_json(path, data)
        self.assertNotIn("RP202", self.codes())

    def test_release_gate_rejects_pending_decision(self):
        path = self.project / "reading-pack.toml"
        path.write_text(path.read_text().replace('publication_decision = "approved"', 'publication_decision = "pending"'))
        self.assertIn("RP300", self.codes(release=True))

    def test_release_gate_requires_explicit_pack_license(self):
        path = self.project / "reading-pack.toml"
        path.write_text(path.read_text().replace('pack_license = "CC0 1.0 Universal"', 'pack_license = "rights-holder decision pending"'))
        self.assertIn("RP302", self.codes(release=True))

    def test_release_gate_rejects_draft_record(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["claims"][0]["status"] = "draft"
        write_json(path, data)
        self.assertIn("RP301", self.codes(release=True))

    def test_release_gate_requires_book_specific_index_context(self):
        for language in ("ja", "en"):
            path = self.project / "data" / f"pack.{language}.json"
            data = read_json(path)
            data["names"][0].pop("book_context")
            data["glossary"][0].pop("book_meaning")
            write_json(path, data)
        self.assertIn("RP303", self.codes(release=True))

    def test_release_gate_rejects_formulaic_index_context(self):
        for language in ("ja", "en"):
            path = self.project / "data" / f"pack.{language}.json"
            data = read_json(path)
            data["names"][0]["book_context"] = (
                "本書では第1章で、同章の先行研究・議論の参照人物として言及される。"
            )
            data["glossary"][0]["book_meaning"] = (
                "本書では第1章で、同章の議論を組み立てる概念・枠組みとして用いる。"
            )
            write_json(path, data)
        self.assertIn("RP304", self.codes(release=True))

    def test_overlong_summary_is_detected(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["chapters"][0]["summary"] = "長" * 501
        write_json(path, data)
        self.assertIn("RP117", self.codes())

    def test_invalid_id_is_detected(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["chapters"][0]["id"] = "bad id"
        write_json(path, data)
        self.assertIn("RP102", self.codes())

    def test_invalid_reference_url_is_detected(self):
        path = self.project / "data" / "pack.ja.json"
        data = read_json(path)
        data["references"][0]["url"] = "file:///private/book"
        write_json(path, data)
        self.assertIn("RP109", self.codes())

    def test_missing_translation_status_is_detected(self):
        path = self.project / "data" / "pack.en.json"
        data = read_json(path)
        del data["claims"][0]["translation_status"]
        write_json(path, data)
        self.assertIn("RP203", self.codes())


if __name__ == "__main__":
    unittest.main()
