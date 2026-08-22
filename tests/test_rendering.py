from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reading_pack.rendering import build_packs, output_path, render_pack
from reading_pack.validation import validate_project

from tests.support import copy_sample


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = copy_sample(Path(self.tmp.name))
        self.config, self.data, issues = validate_project(self.project)
        self.assertEqual(issues, [])

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_is_deterministic(self):
        first = render_pack(self.project, "ja", self.config, self.data["ja"])
        second = render_pack(self.project, "ja", self.config, self.data["ja"])
        self.assertEqual(first.encode(), second.encode())

    def test_build_matches_renderer_byte_for_byte(self):
        build_packs(self.project, ["ja", "en"], self.config, self.data)
        for lang in ("ja", "en"):
            expected = render_pack(self.project, lang, self.config, self.data[lang])
            self.assertEqual(output_path(self.project, self.config, lang).read_text(), expected)

    def test_reference_pack_bytes_are_frozen(self):
        expected = {
            "en": "b8d8722c878d72c58a7212a23549c492afc54848f8ec6ed0578e49d13cdeb33e",
            "ja": "efbe6eecabd8d294fa9bc3217525f3fb3b5cd0c146af1a3624720c8e18de9bd2",
        }
        for language, digest in expected.items():
            rendered = render_pack(
                self.project, language, self.config, self.data[language]
            ).encode("utf-8")
            self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)

    def test_pack_contains_required_markers(self):
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertTrue(pack.startswith("PACK |"))
        self.assertIn("## SYS |", pack)
        self.assertIn("## BIB |", pack)
        self.assertIn("## MAP |", pack)
        self.assertTrue(pack.rstrip().endswith("ref=1"))

    def test_format_production_and_generator_metadata_are_separate(self):
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn(
            "format conformance: Reading Pack Format 1.0-draft conformant", pack
        )
        self.assertIn(
            "production target: Reading Pack Production 1.0-draft Level 3 beta",
            pack,
        )
        self.assertIn("generator: reading-pack toolkit 0.6.0", pack)
        self.assertNotIn("specification: Reading Pack Specification", pack)

    def test_manuscript_prose_does_not_leak(self):
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertNotIn("THIS_SYNTHETIC_PROSE_SENTINEL", pack)
        self.assertNotIn("Inventor Rio designed", pack)

    def test_japanese_names_and_english_quotes_survive(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("リオ", ja)
        self.assertIn("role: Serve as a reading companion dedicated to *Clockwork Garden*", en)
        self.assertIn("story's blueprint", en.lower())

    def test_people_and_terms_include_book_specific_explanations(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("context: リオは、本書で月相機構を設計した人物として紹介される。", ja)
        self.assertIn("meaning: 本書で、月の位相に応じて庭園を動かす仕組みを指す。", ja)
        self.assertIn("context: Rio is introduced as the designer of the lunar mechanism.", en)
        self.assertIn("meaning: In the book, this is the mechanism that moves the garden with the lunar phase.", en)

    def test_opening_explains_how_to_use_the_pack(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("**使い方**", ja)
        self.assertIn("質問例：", ja)
        self.assertIn("**How to use it:**", en)
        self.assertIn("Examples:", en)

    def test_welcome_names_only_material_present_in_the_pack(self):
        data = self.data["ja"]
        data["certainty"] = []
        data["misreadings"] = []
        data["names"] = []
        data["glossary"] = []
        data["references"] = []
        welcome = next(
            line for line in render_pack(self.project, "ja", self.config, data).splitlines()
            if line.startswith("R10:")
        )
        self.assertIn("章節地図、章要約、主張", welcome)
        self.assertNotIn("確実性区分", welcome)
        self.assertNotIn("人名索引", welcome)
        self.assertNotIn("用語索引", welcome)

    def test_sys_does_not_name_an_unconfigured_official_page(self):
        self.config["book"]["official_url"] = ""
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertNotIn("not configured", ja)
        self.assertNotIn("not configured", en)
        self.assertIn("収録版より新しい情報や正誤表", ja)
        self.assertIn("Do not invent updates or errata", en)

    def test_pack_forbids_promising_access_to_unprovided_book_text(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("利用者が検索を許可しただけでは利用可能にならない", ja)
        self.assertIn("未提供の本文を検索したり、そこから正確に抜き出せると提案・約束しない", ja)
        self.assertIn("人物が本書で何者として紹介され", ja)
        self.assertIn("Permission to search does not itself provide access", en)
        self.assertIn("Never offer or promise to search unprovided book text", en)
        self.assertIn("which view, work, quotation, or evaluation", en)

    def test_navigation_preserves_companion_use_without_reconstructing_the_book(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("本パックは原著の代替ではない", ja)
        self.assertIn("紙版ページ、章、節の見出しで所在を案内", ja)
        self.assertIn("原著へアクセスできない場合も本文を再現せず", ja)
        self.assertNotIn("原著を所持するとは仮定しない", ja)
        self.assertIn("This pack is not a substitute for the original", en)
        self.assertIn("If the reader cannot access the original, do not reconstruct it", en)

    def test_draft_policy_is_visible_but_not_advertised_or_activated(self):
        policy = {
            "id": "POLICY-AUTHORITY",
            "kind": "authority_order",
            "statement": "Use the author-maintained source before derivatives.",
            "status": "draft",
        }
        self.data["en"]["policies"] = [policy]
        draft = render_pack(self.project, "en", self.config, self.data["en"])
        welcome = next(line for line in draft.splitlines() if line.startswith("R10:"))
        self.assertIn("P2: Treat draft or reviewed POLICY records", draft)
        self.assertNotIn("book-specific policies", welcome)
        policy["status"] = "approved"
        approved = render_pack(self.project, "en", self.config, self.data["en"])
        welcome = next(line for line in approved.splitlines() if line.startswith("R10:"))
        self.assertIn("P2.1[authority_order]", approved)
        self.assertIn("book-specific policies", welcome)

    def test_page_ranges_are_rendered(self):
        pack = render_pack(self.project, "ja", self.config, self.data["ja"])
        self.assertIn("| pp=1-8 |", pack)

    def test_claim_heading_does_not_imply_every_record_is_approved(self):
        ja = render_pack(self.project, "ja", self.config, self.data["ja"])
        en = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("## PROPS | 主張", ja)
        self.assertIn("## PROPS | Claims", en)
        self.assertNotIn("## PROPS | 承認済み主張", ja)
        self.assertNotIn("## PROPS | Approved claims", en)

    def test_open_objection_is_not_rendered_as_a_misreading(self):
        item = self.data["ja"]["misreadings"][0]
        item["kind"] = "open_objection"
        claim_id = self.data["ja"]["claims"][0]["id"]
        item["claim_ids"] = [claim_id]
        item["impact"] = "この主張の適用範囲を狭める。"
        item["remaining_uncertainty"] = "境界条件は未確定である。"
        pack = render_pack(self.project, "ja", self.config, self.data["ja"])
        self.assertIn("kind=open_objection", pack)
        self.assertIn(f"claims={claim_id}", pack)
        self.assertIn("未解決の批判:", pack)
        self.assertIn("本書への影響: この主張の適用範囲を狭める。", pack)
        self.assertIn("残る不確実性: 境界条件は未確定である。", pack)
        self.assertNotIn("誤読: " + item["misreading"], pack)

    def test_clarification_labels_require_support_source_provenance(self):
        item = self.data["ja"]["misreadings"][0]
        item["kind"] = "clarification"
        item["issue"] = item.pop("misreading")
        source_derived = render_pack(self.project, "ja", self.config, self.data["ja"])
        self.assertIn("本書の応答: " + item["response"], source_derived)
        self.assertNotIn("著者による補足: " + item["response"], source_derived)

        item["provenance_source_id"] = "SRC-QA"
        item["provenance_source_hash"] = "1" * 64
        author_supplied = render_pack(self.project, "ja", self.config, self.data["ja"])
        self.assertIn("著者による補足: " + item["response"], author_supplied)

    def test_sys_does_not_claim_absent_challenge_material(self):
        self.data["en"]["misreadings"] = []
        for claim in self.data["en"]["claims"]:
            claim.pop("falsifiability", None)
            claim.pop("revision_conditions", None)
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("Do not invent unrecorded counterconditions", pack)
        self.assertNotIn("Use falsification conditions", pack)

    def test_active_profile_rule_and_conformance_state_are_rendered(self):
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("profile=nonfiction-reading:required", pack)
        self.assertIn("quality profile: nonfiction-reading (required)", pack)
        self.assertIn("P1: Preserve conditions, scope, exceptions", pack)

    def test_disabled_profile_does_not_claim_its_behavioral_rule(self):
        path = self.project / "quality-plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["conformance_required"] = False
        path.write_text(json.dumps(plan), encoding="utf-8")
        pack = render_pack(self.project, "en", self.config, self.data["en"])
        self.assertIn("profile=nonfiction-reading:disabled", pack)
        self.assertIn("quality profile: nonfiction-reading (disabled)", pack)
        self.assertNotIn("P1: Preserve conditions, scope, exceptions", pack)

    def test_no_unresolved_template_placeholder(self):
        for lang in ("ja", "en"):
            self.assertNotIn("{{", render_pack(self.project, lang, self.config, self.data[lang]))


if __name__ == "__main__":
    unittest.main()
