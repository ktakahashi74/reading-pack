from __future__ import annotations

import json
import hashlib
import copy
import tempfile
import unittest
from pathlib import Path

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import canonical_data_hash
from reading_pack.profiles import (
    PROFILES,
    content_metrics,
    create_default_quality_plan,
    load_quality_plan,
    validate_quality_plan,
    quality_contract_hash,
)


def language_data(*, claims=True, glossary=True, references=True, chapter_fields=None):
    chapter = {
        "id": "CH-01",
        "kind": "chapter",
        "title": "Opening",
        "sections": ["First section"],
        "summary": "A short approved description.",
        "terms": ["term"],
    }
    chapter.update(chapter_fields or {})
    return {
        "chapters": [
            chapter
        ],
        "certainty": [{"id": "CERT-I"}],
        "claims": [{"id": "CL-01"}] if claims else [],
        "misreadings": [],
        "names": [],
        "glossary": [{"id": "TERM-01"}] if glossary else [],
        "references": [{"id": "REF-01"}] if references else [],
    }


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_plan(self, plan):
        (self.project / "quality-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )

    def approved_plan(self, profile="academic-argument"):
        plan = create_default_quality_plan(profile, "complete published edition", "author")
        plan["authority"]["reviewers"] = ["A. Author"]
        plan["authority"]["status"] = "approved"
        plan["critical_policies"] = {key: "approved" for key in plan["critical_policies"]}
        return plan

    def approve_evaluation(self, plan, data):
        data_hash = canonical_data_hash(data)
        contract_hash = quality_contract_hash(plan)
        structure_count = sum(
            len(item["chapters"])
            + sum(len(chapter.get("sections", [])) for chapter in item["chapters"])
            for item in data.values()
        )
        evaluation_record = {
            "format_version": 1,
            "kind": "reading-pack-quality-evaluation",
            "status": "approved",
            "profile": plan["profile"],
            "canonical_data_sha256": data_hash,
            "quality_contract_sha256": contract_hash,
            "method": "Direct comparison of canonical structure and authorized source",
            "reviewer": "A. Evaluator",
            "reviewed_at": "2026-08-13",
            "counts": {
                "expected_structure_records": structure_count,
                "observed_structure_records": structure_count,
                "matched_structure_records": structure_count,
                "source_attribution_errors": [],
                "invented_record_ids": [],
            },
        }
        evidence = self.project / "evaluation" / "result.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text(
            json.dumps(evaluation_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        plan["authority"]["canonical_data_sha256"] = data_hash
        plan["authority"]["quality_contract_sha256"] = contract_hash
        result = plan["acceptance"]["result"]
        result.update(
            {
                "status": "approved",
                "structure_precision": 1.0,
                "structure_recall": 1.0,
                "source_attribution_errors": 0,
                "invented_records": 0,
                "canonical_data_sha256": data_hash,
                "evidence_record": "evaluation/result.json",
                "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "reviewer": "A. Evaluator",
            }
        )
        return plan

    def codes(self, data, release=False, project_level=None):
        return [
            issue.code
            for issue in validate_quality_plan(
                self.project, data, release, project_level=project_level
            )
        ]

    def test_all_builtin_profiles_are_present_and_not_scores(self):
        self.assertEqual(
            set(PROFILES),
            {
                "general-navigation",
                "academic-argument",
                "nonfiction-reading",
                "textbook-learning",
                "fiction-spoiler-free",
                "anthology-attribution",
                "reference-routing",
            },
        )
        for profile in PROFILES.values():
            self.assertFalse(hasattr(profile, "score"))

    def test_default_plan_records_scope_authority_and_pending_human_gates(self):
        plan = create_default_quality_plan("fiction-spoiler-free", "spoiler-free reader aid", "author")
        self.assertEqual(plan["scope"], "spoiler-free reader aid")
        self.assertEqual(plan["authority"]["type"], "author")
        self.assertEqual(plan["spoiler_policy"], "spoiler_free")
        self.assertTrue(plan["conformance_required"])
        self.assertEqual(plan["authority"]["canonical_data_sha256"], "")
        self.assertTrue(all(value == "pending" for value in plan["critical_policies"].values()))

    def test_default_plan_rejects_unknown_profile_or_authority(self):
        with self.assertRaises(ValueError):
            create_default_quality_plan("made-up", "whole book", "author")
        with self.assertRaises(ValueError):
            create_default_quality_plan("general-navigation", "whole book", "robot")

    def test_missing_plan_is_draft_warning_but_release_error(self):
        draft = validate_quality_plan(self.project, {"en": language_data()}, False)
        release = validate_quality_plan(self.project, {"en": language_data()}, True)
        self.assertEqual([(x.severity, x.code) for x in draft], [("warning", "QP001")])
        self.assertEqual([(x.severity, x.code) for x in release], [("error", "QP001")])

    def test_load_returns_none_when_absent_and_reads_object(self):
        self.assertIsNone(load_quality_plan(self.project))
        plan = create_default_quality_plan("general-navigation", "whole book", "author")
        self.write_plan(plan)
        self.assertEqual(load_quality_plan(self.project)["profile"], "general-navigation")

    def test_load_rejects_invalid_json_and_non_object(self):
        (self.project / "quality-plan.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ReadingPackError):
            load_quality_plan(self.project)
        (self.project / "quality-plan.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(ReadingPackError):
            load_quality_plan(self.project)

    def test_complete_academic_plan_passes_release(self):
        data = {"ja": language_data(), "en": language_data()}
        self.write_plan(self.approve_evaluation(self.approved_plan(), data))
        self.assertEqual(
            validate_quality_plan(self.project, data, True),
            [],
        )

    def test_required_module_must_be_nonempty_in_every_language(self):
        data = {"ja": language_data(), "en": language_data(claims=False)}
        self.write_plan(self.approve_evaluation(self.approved_plan(), data))
        issues = validate_quality_plan(self.project, data, True)
        self.assertIn(("error", "QP019"), {(x.severity, x.code) for x in issues})

    def test_required_chapter_field_is_checked(self):
        data = {"en": language_data()}
        data["en"]["chapters"][0]["summary"] = ""
        self.write_plan(self.approve_evaluation(self.approved_plan(), data))
        self.assertIn("QP020", self.codes(data, release=True))

    def test_not_applicable_requires_reason(self):
        plan = self.approved_plan("general-navigation")
        plan["module_overrides"]["names"] = {"status": "not_applicable"}
        self.write_plan(plan)
        self.assertIn("QP016", self.codes({"en": language_data()}, release=True))

    def test_mandatory_module_cannot_be_overridden(self):
        plan = self.approved_plan("academic-argument")
        plan["module_overrides"]["claims"] = {
            "status": "not_applicable",
            "reason": "not useful for this project",
        }
        self.write_plan(plan)
        self.assertIn("QP017", self.codes({"en": language_data()}, release=True))

    def test_nonfiction_references_require_content_or_explicit_not_applicable(self):
        data = {"en": language_data(references=False)}
        plan = self.approved_plan("nonfiction-reading")
        self.write_plan(plan)
        self.assertIn("QP019", self.codes(data, release=True))

        plan["module_overrides"]["references"] = {
            "status": "not_applicable",
            "reason": "The inspected edition declares no Pack-usable reference targets.",
        }
        self.write_plan(plan)
        codes = self.codes(data, release=True)
        self.assertNotIn("QP017", codes)
        self.assertNotIn("QP019", codes)

    def test_academic_references_cannot_be_declared_not_applicable(self):
        plan = self.approved_plan("academic-argument")
        plan["module_overrides"]["references"] = {
            "status": "not_applicable",
            "reason": "No references were supplied.",
        }
        self.write_plan(plan)
        self.assertIn(
            "QP017",
            self.codes({"en": language_data(references=False)}, release=True),
        )

    def test_optional_module_can_be_made_required(self):
        plan = self.approved_plan("general-navigation")
        plan["module_overrides"]["glossary"] = {"status": "required"}
        self.write_plan(plan)
        self.assertIn("QP019", self.codes({"en": language_data(glossary=False)}, release=True))

    def test_release_requires_named_approved_authority_and_policies(self):
        plan = create_default_quality_plan("general-navigation", "whole book", "author")
        self.write_plan(plan)
        codes = self.codes({"en": language_data()}, release=True)
        self.assertIn("QP008", codes)
        self.assertIn("QP010", codes)
        self.assertIn("QP023", codes)

    def test_draft_validation_reports_human_work_as_warnings(self):
        plan = create_default_quality_plan("general-navigation", "whole book", "author")
        self.write_plan(plan)
        issues = validate_quality_plan(self.project, {"en": language_data()}, False)
        self.assertTrue(issues)
        self.assertEqual({issue.severity for issue in issues}, {"warning"})

    def test_fiction_profile_enforces_spoiler_free_policy(self):
        plan = self.approved_plan("fiction-spoiler-free")
        plan["spoiler_policy"] = "full"
        self.write_plan(plan)
        self.assertIn("QP012", self.codes({"en": language_data()}, release=True))

    def test_acceptance_thresholds_are_gates(self):
        plan = self.approved_plan("general-navigation")
        plan["acceptance"]["thresholds"]["structure_recall"] = 0.95
        plan["acceptance"]["thresholds"]["invented_records"] = 1
        self.write_plan(plan)
        codes = self.codes({"en": language_data()}, release=True)
        self.assertIn("QP025", codes)
        self.assertIn("QP026", codes)

    def test_content_metrics_count_grounded_information_fields(self):
        data = language_data()
        data["claims"][0].update(
            {
                "statement": "A bounded claim.",
                "certainty_id": "CERT-I",
                "falsifiability": "A stated countercondition.",
                "revision_conditions": "New source evidence.",
            }
        )
        data["misreadings"] = [
            {
                "misreading": "A mistaken reading.",
                "response": "A concise correction.",
            }
        ]
        data["names"] = [
            {"name": "A. Person", "book_context": "Introduced for one view."}
        ]
        data["glossary"] = [
            {"term": "bounded term", "book_meaning": "Its meaning in this book."}
        ]
        metrics = content_metrics({"en": data})["en"]
        self.assertEqual(metrics["chapter_summaries"], 1)
        self.assertEqual(metrics["summary_characters"], 29)
        self.assertEqual(metrics["claims"], 1)
        self.assertEqual(metrics["claims_with_certainty"], 1)
        self.assertEqual(metrics["claims_with_falsifiability"], 1)
        self.assertEqual(metrics["claims_with_revision_conditions"], 1)
        self.assertEqual(metrics["misreadings"], 1)
        self.assertEqual(metrics["names_with_context"], 1)
        self.assertEqual(metrics["glossary_terms_with_meaning"], 1)
        self.assertGreater(metrics["content_characters"], 100)

    def test_declared_content_floor_blocks_regression(self):
        data = {"en": language_data()}
        plan = self.approved_plan("general-navigation")
        plan["acceptance"]["thresholds"]["content_floor"] = {
            "source_label": "previous reviewed pack",
            "source_sha256": "a" * 64,
            "languages": {
                "en": {
                    "chapter_summaries": 1,
                    "claims": 2,
                    "names_with_context": 1,
                }
            },
        }
        self.write_plan(plan)
        self.assertIn("QP052", self.codes(data, release=True))
        data["en"]["claims"].append({"id": "CL-02"})
        data["en"]["names"].append(
            {"name": "A. Person", "book_context": "Introduced for one view."}
        )
        self.assertNotIn("QP052", self.codes(data, release=True))

    def test_content_floor_requires_a_bound_reviewed_artifact(self):
        plan = self.approved_plan("general-navigation")
        plan["acceptance"]["thresholds"]["content_floor"] = {
            "source_label": "previous reviewed pack",
            "source_sha256": "not-a-hash",
            "languages": {"en": {"claims": 1}},
        }
        self.write_plan(plan)
        issues = validate_quality_plan(
            self.project, {"en": language_data()}, release=False
        )
        self.assertIn(("error", "QP051"), {(item.severity, item.code) for item in issues})

    def test_measured_evaluation_is_required_and_bound_to_current_data(self):
        data = {"en": language_data()}
        plan = self.approved_plan("general-navigation")
        self.write_plan(plan)
        self.assertIn("QP036", self.codes(data, release=True))
        self.approve_evaluation(plan, data)
        self.write_plan(plan)
        self.assertNotIn("QP039", self.codes(data, release=True))
        data["en"]["chapters"][0]["title"] = "Changed after evaluation"
        self.assertIn("QP039", self.codes(data, release=True))

    def test_conformance_false_keeps_release_findings_nonblocking(self):
        plan = create_default_quality_plan("academic-argument", "whole book", "author")
        plan["conformance_required"] = False
        self.write_plan(plan)
        issues = validate_quality_plan(self.project, {"en": language_data(claims=False)}, True)
        self.assertTrue(issues)
        self.assertEqual({issue.severity for issue in issues}, {"warning"})

    def test_unknown_quality_plan_fields_are_structural_errors(self):
        plan = self.approved_plan("general-navigation")
        plan["quality_score"] = 100
        self.write_plan(plan)
        issues = validate_quality_plan(self.project, {"en": language_data()}, False)
        self.assertIn(("error", "QP027"), {(item.severity, item.code) for item in issues})

    def test_profile_minimum_levels_are_enforced(self):
        cases = {
            "fiction-spoiler-free": (2, 1),
            "anthology-attribution": (2, 1),
            "reference-routing": (2, 1),
            "academic-argument": (3, 2),
            "nonfiction-reading": (3, 2),
            "textbook-learning": (3, 2),
        }
        for profile, (minimum, too_low) in cases.items():
            with self.subTest(profile=profile):
                self.write_plan(self.approved_plan(profile))
                self.assertIn(
                    "QP041",
                    self.codes(
                        {"en": language_data()},
                        release=True,
                        project_level=too_low,
                    ),
                )
                self.assertNotIn(
                    "QP041",
                    self.codes(
                        {"en": language_data()},
                        release=True,
                        project_level=minimum,
                    ),
                )
        self.write_plan(self.approved_plan("general-navigation"))
        self.assertNotIn(
            "QP041",
            self.codes(
                {"en": language_data()}, release=True, project_level=1
            ),
        )

    def test_genre_profiles_require_their_distinguishing_fields(self):
        cases = {
            "textbook-learning": ("learning_objectives", ["Explain the mechanism"]),
            "fiction-spoiler-free": ("spoiler_scope", "none"),
            "anthology-attribution": ("contributors", ["M. Contributor"]),
            "reference-routing": ("aliases", ["clockwork horticulture"]),
        }
        for profile, (field, valid_value) in cases.items():
            with self.subTest(profile=profile):
                self.write_plan(self.approved_plan(profile))
                missing = {"en": language_data()}
                self.assertIn("QP020", self.codes(missing, release=True))
                present = {
                    "en": language_data(chapter_fields={field: valid_value})
                }
                self.assertNotIn("QP020", self.codes(present, release=True))

    def test_spoiler_free_profile_rejects_a_nonzero_spoiler_scope(self):
        self.write_plan(self.approved_plan("fiction-spoiler-free"))
        data = {
            "en": language_data(
                chapter_fields={"spoiler_scope": "chapter_only"}
            )
        }
        self.assertIn("QP020", self.codes(data, release=True))

    def test_structural_back_matter_is_not_forced_to_invent_genre_content(self):
        self.write_plan(self.approved_plan("anthology-attribution"))
        data = {
            "en": language_data(
                chapter_fields={
                    "kind": "bibliography",
                    "summary": "",
                    "sections": [],
                }
            )
        }
        self.assertNotIn("QP020", self.codes(data, release=True))

    def test_malformed_nested_types_report_issues_instead_of_crashing(self):
        base = create_default_quality_plan(
            "general-navigation", "whole book", "author"
        )
        mutations = [
            ("authority role", lambda p: p["authority"].update(type=[])),
            ("spoiler policy", lambda p: p.update(spoiler_policy=[])),
            (
                "override status",
                lambda p: p["module_overrides"].update(
                    names={"status": []}
                ),
            ),
            (
                "policy state",
                lambda p: p["critical_policies"].update(source_fidelity=[]),
            ),
            (
                "precision threshold",
                lambda p: p["acceptance"]["thresholds"].update(
                    structure_precision="one"
                ),
            ),
            (
                "error threshold",
                lambda p: p["acceptance"]["thresholds"].update(
                    invented_records=[]
                ),
            ),
            (
                "precision result",
                lambda p: p["acceptance"]["result"].update(
                    structure_precision={}
                ),
            ),
            (
                "authority hash",
                lambda p: p["authority"].update(canonical_data_sha256=[]),
            ),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                plan = copy.deepcopy(base)
                mutate(plan)
                self.write_plan(plan)
                issues = validate_quality_plan(
                    self.project, {"en": language_data()}, release=True
                )
                self.assertTrue(issues)

        self.write_plan(base)
        issues = validate_quality_plan(
            self.project, {"en": {"chapters": None}}, release=True
        )
        self.assertIn("QP048", {issue.code for issue in issues})

    def test_authority_and_evaluation_are_bound_to_current_data(self):
        data = {"en": language_data()}
        plan = self.approve_evaluation(
            self.approved_plan("general-navigation"), data
        )
        self.write_plan(plan)
        self.assertEqual(validate_quality_plan(self.project, data, True), [])
        data["en"]["chapters"][0]["title"] = "Changed"
        codes = set(self.codes(data, release=True))
        self.assertTrue({"QP039", "QP042", "QP050"} <= codes)

    def test_contract_change_invalidates_authority_and_evaluation(self):
        data = {"en": language_data()}
        plan = self.approve_evaluation(
            self.approved_plan("general-navigation"), data
        )
        plan["scope"] = "one chapter only"
        self.write_plan(plan)
        codes = set(self.codes(data, release=True))
        self.assertTrue({"QP043", "QP050"} <= codes)

    def test_evidence_hash_and_derived_metrics_are_checked(self):
        data = {"en": language_data()}
        plan = self.approve_evaluation(
            self.approved_plan("general-navigation"), data
        )
        plan["acceptance"]["result"]["structure_precision"] = 0.5
        self.write_plan(plan)
        self.assertTrue(
            {"QP037", "QP050"} <= set(self.codes(data, release=True))
        )

        plan = self.approve_evaluation(
            self.approved_plan("general-navigation"), data
        )
        evidence = self.project / plan["acceptance"]["result"]["evidence_record"]
        evidence.write_text(evidence.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.write_plan(plan)
        self.assertIn("QP046", self.codes(data, release=True))

    def test_arbitrary_json_is_not_accepted_as_evaluation_evidence(self):
        data = {"en": language_data()}
        plan = self.approve_evaluation(
            self.approved_plan("general-navigation"), data
        )
        evidence = self.project / plan["acceptance"]["result"]["evidence_record"]
        evidence.write_text('{"reviewed":true}\n', encoding="utf-8")
        plan["acceptance"]["result"]["evidence_sha256"] = hashlib.sha256(
            evidence.read_bytes()
        ).hexdigest()
        self.write_plan(plan)
        self.assertIn("QP049", self.codes(data, release=True))

    def test_duplicate_json_keys_are_rejected(self):
        (self.project / "quality-plan.json").write_text(
            '{"format_version":1,"format_version":1}', encoding="utf-8"
        )
        with self.assertRaises(ReadingPackError):
            load_quality_plan(self.project)


if __name__ == "__main__":
    unittest.main()
