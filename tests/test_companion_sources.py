from __future__ import annotations

import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from reading_pack.companion import companion_findings
from reading_pack.hashing import semantic_hash
from reading_pack.project import load_config, load_language_data
from reading_pack.rendering import render_pack
from reading_pack.validation import errors, release_issues, validate_project
from tests.support import copy_sample, write_json


DECLARATION = {
    "relation": "official_companion",
    "url_scope": "prefix",
    "retrieval_policy": "proactive_when_relevant",
}


class CompanionSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = copy_sample(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _declare_bilingually(self) -> tuple[dict, dict]:
        ja = load_language_data(self.project, "ja")
        en = load_language_data(self.project, "en")
        ja["references"][0].update(DECLARATION)
        en["references"][0].update(DECLARATION)
        en["references"][0]["source_hash"] = semantic_hash(ja["references"][0])
        write_json(self.project / "data" / "pack.ja.json", ja)
        write_json(self.project / "data" / "pack.en.json", en)
        return ja, en

    def test_undeclared_project_retains_exact_pre_feature_output(self) -> None:
        config = load_config(self.project)
        expected = {
            "ja": "5e2e093a56f0cc3dddd9ea7d14cb2e34fff14e16e979323ae5aca8f43a5f6755",
            "en": "7c01d8ebf74e962a8ba4a3d5f0ec8b59262f9537c18fa8765b9d6840f5c90e0a",
        }
        for language, digest in expected.items():
            rendered = render_pack(
                self.project,
                language,
                config,
                load_language_data(self.project, language),
            )
            self.assertEqual(hashlib.sha256(rendered.encode()).hexdigest(), digest)
            self.assertNotIn("C1:", rendered)
            self.assertNotIn("relation=official_companion", rendered)

    def test_declaration_deterministically_generates_ref_and_model_neutral_sys(self) -> None:
        data = self._declare_bilingually()
        config, loaded, issues = validate_project(self.project)
        self.assertEqual(errors(issues), [])
        for language in ("ja", "en"):
            first = render_pack(self.project, language, config, loaded[language])
            second = render_pack(self.project, language, config, loaded[language])
            self.assertEqual(first.encode(), second.encode())
            self.assertIn("relation=official_companion", first)
            self.assertIn("scope=prefix", first)
            self.assertIn("retrieval=proactive_when_relevant", first)
            self.assertIn("C1:", first)
            self.assertIn("C2:", first)
            self.assertIn("C3:", first)
            self.assertNotIn("Anthropic", first)
            self.assertNotIn("computer_", first)
        ja = render_pack(self.project, "ja", config, data[0])
        for trigger in ("付録", "補足論考", "刊行後の更新", "著者見解", "根拠", "詳細"):
            self.assertIn(trigger, ja)
        self.assertIn("公式サイト以外のWeb資料の利用を禁じない", ja)
        self.assertIn("更新時点を区別", ja)
        self.assertIn("ページのURLを可能な限り", ja)
        self.assertEqual(release_issues(config, {"ja": data[0], "en": data[1]}), [])

    def test_invalid_companion_declarations_are_rejected(self) -> None:
        base = {
            "id": "REF-X",
            "url": "https://example.com/book/",
            "label": "Official companion",
            **DECLARATION,
        }
        invalid = {
            "partial": {key: value for key, value in base.items() if key != "url_scope"},
            "http": {**base, "url": "http://example.com/book/"},
            "credentials": {**base, "url": "https://reader:secret@example.com/book/"},
            "overlong": {**base, "url": "https://example.com/" + "a" * 2_100 + "/"},
            "bad_prefix": {**base, "url": "https://example.com/book?part=1"},
        }
        for name, record in invalid.items():
            with self.subTest(name=name):
                self.assertTrue(companion_findings([record]))

        duplicate = deepcopy(base)
        duplicate["id"] = "REF-Y"
        duplicate["url"] = "https://EXAMPLE.com:443/book/"
        findings = companion_findings([base, duplicate])
        self.assertTrue(any(item.kind == "collection" for item in findings))

        excessive = [
            {**base, "id": f"REF-{index}", "url": f"https://example.com/{index}/"}
            for index in range(33)
        ]
        findings = companion_findings(excessive)
        self.assertTrue(any("at most 32" in item.message for item in findings))

    def test_project_validation_reports_companion_specific_codes(self) -> None:
        data = load_language_data(self.project, "ja")
        data["references"][0].update(DECLARATION)
        data["references"][0]["url"] = "https://user@example.com/book/"
        write_json(self.project / "data" / "pack.ja.json", data)
        codes = [issue.code for issue in validate_project(self.project)[2]]
        self.assertIn("RP130", codes)


if __name__ == "__main__":
    unittest.main()
