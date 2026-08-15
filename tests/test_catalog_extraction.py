from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reading_pack_producer.candidates import load_candidate_run, normalize_text
from reading_pack_producer.catalog_extraction import (
    EXTRACTOR_VERSION,
    PDF_VERTICAL_EXTRACTOR_VERSION,
    _extract_catalog_items,
    _inventory_id,
    _inventory_integrity,
    catalog_candidate_responses,
    create_catalog_candidate_run,
    create_catalog_context_candidate_run,
    create_catalog_context_plan,
    extract_catalog,
    load_catalog_inventory,
    validate_catalog_inventory,
    validate_catalog_context_responses,
    validate_generated_catalog_responses,
    write_catalog_inventory,
)
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, load_language_data, write_json
from reading_pack_producer.work_ledger import load_work_ledger
from tests.support import cli, read_json, write_json as write_test_json


class CatalogExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "pack"
        self.source = self.root / "book.txt"
        self.source.write_text(
            "Chapter One\n"
            "Ada Lovelace proposed a review method. "
            "「Cobalt mechanism」とは検証済みの仕組みを指す。 "
            "QXR is discussed with evidence at https://example.test/qxr.\n",
            encoding="utf-8",
        )
        create_project(
            self.project,
            title="Catalog Book",
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
        data["chapters"] = [
            {
                "id": "CH-01",
                "kind": "chapter",
                "title": "Chapter One",
                "pages": "1-2",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        ]
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        self.chapter_spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inventory(self, *, chapter_spans=None):
        return extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=self.chapter_spans if chapter_spans is None else chapter_spans,
        )

    def test_extracts_separate_people_terms_and_reference_workflows(self) -> None:
        inventory = self.inventory()
        self.assertEqual(inventory["summary"]["people"], 1)
        self.assertGreaterEqual(inventory["summary"]["terms"], 2)
        self.assertEqual(inventory["summary"]["references"], 1)
        self.assertEqual(inventory["summary"]["unresolved_people"], 0)
        labels = {(item["kind"], item["label"]) for item in inventory["items"]}
        self.assertIn(("person", "Ada Lovelace"), labels)
        self.assertIn(("term", "Cobalt mechanism"), labels)
        self.assertIn(("term", "QXR"), labels)
        reference = next(item for item in inventory["items"] if item["kind"] == "reference")
        self.assertEqual(reference["url"], "https://example.test/qxr")
        self.assertTrue(all("excerpt" not in item for item in inventory["items"]))
        self.assertNotIn(str(self.root), json.dumps(inventory))
        self.assertEqual(validate_catalog_inventory(inventory), inventory)

    def test_context_plan_requires_complete_source_grounded_record_updates(self) -> None:
        inventory = self.inventory()
        data = load_language_data(self.project, "en")
        data["names"] = [
            {
                "id": "NAME-ADA",
                "name": "Ada Lovelace",
                "chapter_id": "CH-01",
                "status": "draft",
            }
        ]
        data["glossary"] = [
            {
                "id": "TERM-COBALT",
                "term": "Cobalt mechanism",
                "chapter_id": "CH-01",
                "status": "draft",
            }
        ]
        write_json(self.project / "data" / "pack.en.json", data)
        plan = create_catalog_context_plan(
            self.project,
            language="en",
            inventory=inventory,
        )
        self.assertEqual(plan["summary"], {"total": 2, "names": 1, "glossary": 1})
        self.assertNotIn("proposed a review method", json.dumps(plan))
        source_snippet = (
            "Ada Lovelace proposed a review method. "
            "「Cobalt mechanism」とは検証済みの仕組みを指す。"
        )
        responses = {
            "plan_id": plan["plan_id"],
            "candidates": [
                {
                    "record_id": "NAME-ADA",
                    "description": "The book introduces her as proposing a method for review.",
                    "evidence": [
                        {
                            "snippet": source_snippet,
                            "supports_field": "book_context",
                        }
                    ],
                },
                {
                    "record_id": "TERM-COBALT",
                    "description": "The book uses it for a mechanism whose verification has been completed.",
                    "evidence": [
                        {
                            "snippet": source_snippet,
                            "supports_field": "book_meaning",
                        }
                    ],
                },
            ],
        }
        transient = validate_catalog_context_responses(
            plan, responses, self.source, data
        )
        self.assertEqual(transient[0]["record"]["book_context"], responses["candidates"][0]["description"])
        self.assertEqual(transient[1]["record"]["book_meaning"], responses["candidates"][1]["description"])
        manifest_path = create_catalog_context_candidate_run(
            self.project,
            language="en",
            plan=plan,
            source_path=self.source,
            responses=responses,
            run_directory=self.project / ".reading-pack" / "runs" / "context",
            run_id="context-test",
        )
        manifest = load_candidate_run(manifest_path)
        self.assertEqual(manifest["summary"]["ready_for_review"], 2)
        self.assertTrue(
            all(candidate["base_record_sha256"] for candidate in manifest["candidates"])
        )

        incomplete = {
            "plan_id": plan["plan_id"],
            "candidates": responses["candidates"][:1],
        }
        with self.assertRaisesRegex(ReadingPackError, "cover every plan target"):
            validate_catalog_context_responses(plan, incomplete, self.source, data)

        data["names"][0]["book_context"] = "Existing person context."
        data["glossary"][0]["book_meaning"] = "Existing term meaning."
        write_json(self.project / "data" / "pack.en.json", data)
        missing_only = create_catalog_context_plan(
            self.project,
            language="en",
            inventory=inventory,
        )
        self.assertEqual(
            missing_only["summary"],
            {"total": 0, "names": 0, "glossary": 0},
        )
        refreshed = create_catalog_context_plan(
            self.project,
            language="en",
            inventory=inventory,
            refresh_existing=True,
        )
        self.assertEqual(
            refreshed["summary"],
            {"total": 2, "names": 1, "glossary": 1},
        )

    def test_conservative_seed_rejects_generic_subjects_and_heading_tokens(self) -> None:
        self.source.write_text(
            "第1章\n"
            "熱力学の理論は重要である。コストが結果を示した。"
            "シミュレーションは予測を高速化した。"
            "プルラリティは協働を論じた。Active Inference proposed a model. "
            "I. II. LAWS TAKAHASHI\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        data["chapters"][0]["title"] = "第1章"
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=[
                {
                    "chapter_id": "CH-01",
                    "char_start": 0,
                    "char_end": len(normalized),
                    "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                }
            ],
        )
        labels = {item["label"] for item in inventory["items"]}
        for false_positive in (
            "熱力学",
            "コスト",
            "シミュレーション",
            "プルラリティ",
            "Active Inference",
            "I.",
            "II.",
            "LAWS",
            "TAKAHASHI",
        ):
            self.assertNotIn(false_positive, labels)

    def test_casefold_length_change_does_not_shift_labels_or_lowercase_urls(self) -> None:
        self.source.write_text(
            "Chapter One\nStraße prefix. "
            "「Cobalt Mechanism」とは検証概念である。 "
            "See https://Example.test/Case/FileA?Key=X for data.\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        ]
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=spans,
        )
        labels = {(item["kind"], item["label"]) for item in inventory["items"]}
        self.assertIn(("term", "Cobalt Mechanism"), labels)
        reference = next(
            item for item in inventory["items"] if item["kind"] == "reference"
        )
        self.assertEqual(reference["url"], "https://Example.test/Case/FileA?Key=X")

    def test_unmapped_people_and_terms_are_reported_but_not_canonicalized(self) -> None:
        inventory = self.inventory(chapter_spans=[])
        self.assertEqual(inventory["summary"]["unresolved_people"], 1)
        self.assertGreaterEqual(inventory["summary"]["unresolved_terms"], 2)
        responses = catalog_candidate_responses(inventory, self.source)
        self.assertEqual(
            {response["collection"] for response in responses}, {"references"}
        )

    def test_auto_title_sequence_map_is_visible_as_review_required(self) -> None:
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
        )
        self.assertEqual(inventory["chapter_map"]["method"], "title_sequence")
        self.assertTrue(inventory["chapter_map"]["review_required"])
        self.assertEqual(inventory["summary"]["resolved_chapters"], 1)
        with self.assertRaisesRegex(ReadingPackError, "explicit chapter map"):
            create_catalog_candidate_run(
                self.project,
                language="en",
                inventory=inventory,
                source_path=self.source,
                run_directory=self.project / ".reading-pack" / "runs" / "inferred",
                run_id="inferred",
            )

    def test_pdf_style_per_glyph_spacing_remains_grounded(self) -> None:
        self.source.write_text(
            "第 1 章\n"
            "ア オ イ ・ ミ ナ ト は 予 測 を 述 べ た 。 "
            "「 結 晶 配 置 原 理 」 と は 制 度 の 構 想 で あ る 。 "
            "Q X R は 対 象 語 で あ る 。 "
            "青 銅 番 号 7 ± 2 は 架 空 の 表 現 で あ る 。\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        data["chapters"][0]["title"] = "第1章"
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        ]
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=spans,
        )
        labels = {(item["kind"], item["label"]) for item in inventory["items"]}
        self.assertIn(("person", "アオイ・ミナト"), labels)
        self.assertIn(("term", "結晶配置原理"), labels)
        self.assertIn(("term", "QXR"), labels)
        generated = [
            {
                "collection": "glossary",
                "record": {
                    "id": "TERM-COBALT-NUMBER",
                    "term": "青銅番号7 ± 2",
                    "chapter_id": "CH-01",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "青 銅 番 号 7 ± 2 は 架 空 の 表 現",
                        "supports_field": "term",
                    }
                ],
            }
        ]
        run = self.project / ".reading-pack" / "runs" / "spaced"
        manifest_path, _ = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=run,
            run_id="spaced",
            generated_responses=generated,
        )
        manifest = load_candidate_run(manifest_path)
        self.assertGreaterEqual(manifest["summary"]["ready_for_review"], 4)
        self.assertEqual(manifest["summary"]["quarantined"], 0)

    def test_pdf_vertical_uses_source_aware_conservative_seed(self) -> None:
        source_text = (
            "プロジェクトは進行する。著者はこの点を論じた。"
            "セレス・ノヴァは危険を論じた。"
            "藍銅教授は安全策を提唱した。"
            "結晶跳躍とは急速な変化である。"
            "この仕組みが成立する条件とは前提を指す。"
            "「星図配置空間」とは概念である。"
        )
        normalized = normalize_text(source_text)
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        ]

        ordinary = _extract_catalog_items(
            source_text, normalized, spans, source_format="text"
        )
        ordinary_labels = {(item["kind"], item["label"]) for item in ordinary}
        # Keep the pre-existing text recognizer behavior unchanged; the extra
        # precision gate belongs only to the explicitly selected source type.
        self.assertIn(("person", "プロジェクト"), ordinary_labels)
        self.assertIn(("term", "この仕組みが成立する条件"), ordinary_labels)

        vertical = _extract_catalog_items(
            source_text, normalized, spans, source_format="pdf-vertical"
        )
        vertical_labels = {(item["kind"], item["label"]) for item in vertical}
        self.assertNotIn(("person", "プロジェクト"), vertical_labels)
        self.assertNotIn(
            ("term", "この仕組みが成立する条件"), vertical_labels
        )
        self.assertIn(("person", "セレス・ノヴァ"), vertical_labels)
        self.assertIn(("person", "藍銅"), vertical_labels)
        self.assertIn(("term", "結晶跳躍"), vertical_labels)
        self.assertIn(("term", "星図配置空間"), vertical_labels)
        reasons = {
            item["label"]: item["reason_codes"] for item in vertical
        }
        self.assertIn("vertical_kanji_person_marker", reasons["藍銅"])
        self.assertIn("vertical_definition_token_shape", reasons["結晶跳躍"])

        pdf = self.root / "vertical.pdf"
        pdf.write_bytes(b"%PDF-synthetic")
        data = load_language_data(self.project, "en")
        data["source"] = {
            "format": "pdf-vertical",
            "name": pdf.name,
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        }
        write_json(self.project / "data" / "pack.en.json", data)
        with mock.patch(
            "reading_pack_producer.catalog_extraction._source_text_snapshot",
            return_value=(pdf.read_bytes(), source_text),
        ):
            inventory = extract_catalog(
                self.project,
                "SRC-1",
                pdf,
                language="en",
                chapter_spans=spans,
            )
        self.assertEqual(inventory["extractor"], PDF_VERTICAL_EXTRACTOR_VERSION)
        self.assertEqual(
            {(item["kind"], item["label"]) for item in inventory["items"]},
            vertical_labels,
        )

    def test_generated_recall_additions_are_source_and_chapter_bound(self) -> None:
        self.source.write_text(
            self.source.read_text(encoding="utf-8")
            + "Grace Hopper attended the meeting. The compiler transformed code.\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        ]
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=spans,
        )
        generated = [
            {
                "collection": "names",
                "record": {
                    "id": "NAME-GRACE-HOPPER",
                    "name": "Grace Hopper",
                    "chapter_id": "CH-01",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "Grace Hopper attended the meeting.",
                        "supports_field": "name",
                    }
                ],
            },
            {
                "collection": "glossary",
                "record": {
                    "id": "TERM-COMPILER",
                    "term": "compiler",
                    "chapter_id": "CH-01",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "The compiler transformed code.",
                        "supports_field": "term",
                    }
                ],
            },
        ]
        checked = validate_generated_catalog_responses(
            inventory, {"candidates": generated}, self.source
        )
        self.assertEqual(len(checked), 2)
        run = self.project / ".reading-pack" / "runs" / "generated-recall"
        manifest_path, _ = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=run,
            run_id="generated-recall",
            generated_responses=generated,
        )
        manifest = load_candidate_run(manifest_path)
        labels = {
            candidate["record"].get("name", candidate["record"].get("term", ""))
            for candidate in manifest["candidates"]
            if candidate["collection"] in {"names", "glossary"}
        }
        self.assertIn("Grace Hopper", labels)
        self.assertIn("compiler", labels)
        self.assertEqual(manifest["summary"]["quarantined"], 0)

    def test_generated_chapter_assignment_cannot_borrow_other_chapter_evidence(self) -> None:
        inventory = self.inventory()
        tampered = [
            {
                "collection": "names",
                "record": {
                    "id": "NAME-ADA-WRONG-CHAPTER",
                    "name": "Ada Lovelace",
                    "chapter_id": "CH-99",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "Ada Lovelace proposed a review method.",
                        "supports_field": "name",
                    }
                ],
            }
        ]
        with self.assertRaisesRegex(ReadingPackError, "not source-mapped"):
            validate_generated_catalog_responses(inventory, tampered, self.source)

    def test_glossary_prefers_substantive_definition_over_earlier_mention(self) -> None:
        self.source.write_text(
            "Chapter One\nCobalt is mentioned here.\n"
            "Chapter Two\n「Cobalt」とは検証概念である。\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        data["chapters"].append(
            {
                "id": "CH-02",
                "kind": "chapter",
                "title": "Chapter Two",
                "pages": "3-4",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        boundary = normalized.index("chapter two")
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": boundary,
                "span_sha256": hashlib.sha256(normalized[:boundary].encode()).hexdigest(),
            },
            {
                "chapter_id": "CH-02",
                "char_start": boundary,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized[boundary:].encode()).hexdigest(),
            },
        ]
        inventory = extract_catalog(
            self.project, "SRC-1", self.source, language="en", chapter_spans=spans
        )
        cobalt = next(
            item
            for item in inventory["items"]
            if item["kind"] == "term" and normalize_text(item["label"]) == "cobalt"
        )
        self.assertEqual(cobalt["chapter"]["chapter_id"], "CH-02")
        generated = [
            {
                "collection": "glossary",
                "record": {
                    "id": "TERM-COBALT-LATE",
                    "term": "Cobalt",
                    "chapter_id": "CH-02",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "Chapter Two 「Cobalt」とは検証概念である。",
                        "supports_field": "term",
                    }
                ],
            }
        ]
        checked = validate_generated_catalog_responses(
            inventory, generated, self.source
        )
        self.assertEqual(checked[0]["record"]["chapter_id"], "CH-02")
        generated[0]["record"]["chapter_id"] = "CH-01"
        generated[0]["evidence"][0]["snippet"] = "Cobalt is mentioned here."
        with self.assertRaisesRegex(
            ReadingPackError, "preferred substantive term chapter"
        ):
            validate_generated_catalog_responses(inventory, generated, self.source)

    def test_references_are_book_scope_and_deduplicated_by_exact_url(self) -> None:
        url = "https://Example.test/Case/FileA?Key=X"
        self.source.write_text(
            f"Chapter One\nSee {url}.\nChapter Two\nAgain {url}.\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        data["chapters"].append(
            {
                "id": "CH-02",
                "kind": "chapter",
                "title": "Chapter Two",
                "pages": "3-4",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        boundary = normalized.index("chapter two")
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": boundary,
                "span_sha256": hashlib.sha256(normalized[:boundary].encode()).hexdigest(),
            },
            {
                "chapter_id": "CH-02",
                "char_start": boundary,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized[boundary:].encode()).hexdigest(),
            },
        ]
        inventory = extract_catalog(
            self.project, "SRC-1", self.source, language="en", chapter_spans=spans
        )
        references = [item for item in inventory["items"] if item["kind"] == "reference"]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["url"], url)
        self.assertEqual(references[0]["chapter"]["state"], "unresolved")
        self.assertEqual(len(references[0]["occurrences"]), 2)

    def test_every_generated_evidence_item_must_directly_support_the_index_value(self) -> None:
        inventory = self.inventory()
        generated = [
            {
                "collection": "names",
                "record": {
                    "id": "NAME-ADA-EXTRA-EVIDENCE",
                    "name": "Ada Lovelace",
                    "chapter_id": "CH-01",
                    "status": "draft",
                },
                "evidence": [
                    {
                        "snippet": "Ada Lovelace proposed a review method.",
                        "supports_field": "name",
                    },
                    {
                        "snippet": "QXR is discussed with evidence",
                        "supports_field": "name",
                    },
                ],
            }
        ]
        with self.assertRaisesRegex(ReadingPackError, "does not contain"):
            validate_generated_catalog_responses(inventory, generated, self.source)

    def test_catalog_evidence_locator_preserves_the_validated_repeated_occurrence(self) -> None:
        repeated = "Ada Lovelace proposed a review method. "
        self.source.write_text(
            repeated + ("padding " * 20) + repeated + "QXR is defined here.\n",
            encoding="utf-8",
        )
        data = load_language_data(self.project, "en")
        data["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        second = normalized.rfind("ada lovelace")
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": second,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized[second:].encode()).hexdigest(),
            }
        ]
        inventory = extract_catalog(
            self.project,
            "SRC-1",
            self.source,
            language="en",
            chapter_spans=spans,
        )
        response = next(
            item
            for item in catalog_candidate_responses(inventory, self.source)
            if item["collection"] == "names"
        )
        snippet = normalize_text(response["evidence"][0]["snippet"])
        response_start = normalized.find(snippet)
        self.assertGreaterEqual(response_start, second)
        manifest_path, _ = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=self.project / ".reading-pack" / "runs" / "repeated",
            run_id="repeated",
        )
        candidate = next(
            item
            for item in load_candidate_run(manifest_path)["candidates"]
            if item["collection"] == "names"
        )
        self.assertEqual(
            candidate["evidence_refs"][0]["locator"]["char_start"], response_start
        )
        self.assertGreaterEqual(response_start, second)

    def test_inventory_write_is_private_and_refuses_overwrite(self) -> None:
        path = self.root / "inventory.json"
        write_catalog_inventory(path, self.inventory())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(load_catalog_inventory(path), self.inventory())
        original = path.read_bytes()
        with self.assertRaisesRegex(ReadingPackError, "refusing to overwrite"):
            write_catalog_inventory(path, self.inventory())
        self.assertEqual(path.read_bytes(), original)

    def test_rejects_tampered_inventory_and_stale_source(self) -> None:
        inventory = self.inventory()
        inventory["items"][0]["label"] = "Invented Person"
        with self.assertRaisesRegex(ReadingPackError, "integrity|ID is stale"):
            validate_catalog_inventory(inventory)
        fresh = self.inventory()
        self.source.write_text("Changed source.\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadingPackError, "stale|mismatched"):
            catalog_candidate_responses(fresh, self.source)

    def test_inventory_validator_matches_schema_resource_bounds(self) -> None:
        inventory = self.inventory()
        inventory["source"]["size_bytes"] = 100 * 1024 * 1024 + 1
        inventory["inventory_id"] = _inventory_id(inventory)
        inventory["integrity_sha256"] = _inventory_integrity(inventory)
        with self.assertRaisesRegex(ReadingPackError, "source size"):
            validate_catalog_inventory(inventory)

        inventory = self.inventory()
        inventory["items"][0]["reason_codes"].append(
            inventory["items"][0]["reason_codes"][0]
        )
        inventory["inventory_id"] = _inventory_id(inventory)
        inventory["integrity_sha256"] = _inventory_integrity(inventory)
        with self.assertRaisesRegex(ReadingPackError, "reasons"):
            validate_catalog_inventory(inventory)

    def test_legacy_inventory_remains_readable_after_v3_seed_update(self) -> None:
        for extractor in ("catalog-heuristics-v1", "catalog-heuristics-v2"):
            with self.subTest(extractor=extractor):
                inventory = self.inventory()
                inventory["extractor"] = extractor
                inventory["inventory_id"] = _inventory_id(inventory)
                inventory["integrity_sha256"] = _inventory_integrity(inventory)
                self.assertEqual(validate_catalog_inventory(inventory), inventory)

    def test_primary_inventory_source_language_must_match_target(self) -> None:
        inventory = self.inventory()
        inventory["source"]["language"] = "und"
        inventory["inventory_id"] = _inventory_id(inventory)
        inventory["integrity_sha256"] = _inventory_integrity(inventory)
        with self.assertRaisesRegex(ReadingPackError, "source identity"):
            validate_catalog_inventory(inventory)

    def test_rejects_chapter_span_with_wrong_hash(self) -> None:
        spans = [dict(self.chapter_spans[0], span_sha256="0" * 64)]
        with self.assertRaisesRegex(ReadingPackError, "hash does not match"):
            self.inventory(chapter_spans=spans)

    def test_combines_modules_into_one_run_and_reconciled_ledger(self) -> None:
        inventory = self.inventory()
        run = self.project / ".reading-pack" / "runs" / "catalog-v1"
        ledger = self.project / ".reading-pack" / "catalog-v1-ledger.json"
        manifest_path, ledger_path = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=run,
            run_id="catalog-v1",
            ledger_output=ledger,
        )
        self.assertEqual(ledger_path, ledger.resolve())
        manifest = load_candidate_run(manifest_path)
        self.assertEqual(manifest["summary"]["quarantined"], 0)
        self.assertEqual(manifest["generator"]["adapter"], EXTRACTOR_VERSION)
        self.assertEqual(
            {candidate["collection"] for candidate in manifest["candidates"]},
            {"names", "glossary", "references"},
        )
        reconciled = load_work_ledger(ledger)
        self.assertEqual(reconciled["run"]["run_id"], "catalog-v1")
        self.assertEqual(reconciled["summary"]["pending"], 0)
        self.assertEqual(reconciled["summary"]["failed"], 0)
        manifest_text = manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("Ada Lovelace proposed", manifest_text)
        self.assertNotIn(str(self.source), manifest_text)

    def test_zero_match_scope_remains_an_omission_review_failure(self) -> None:
        inventory = self.inventory()
        manifest_path, ledger_path = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=self.project / ".reading-pack" / "runs" / "names-only-empty",
            run_id="names-only-empty",
            collections=["names"],
            ledger_output=self.project / ".reading-pack" / "names-only-empty-ledger.json",
        )
        manifest = load_candidate_run(manifest_path)
        self.assertGreater(manifest["summary"]["ready_for_review"], 0)
        ledger = load_work_ledger(ledger_path)
        # The book-wide names scope has candidates in this fixture. Add a
        # second source-mapped chapter with no candidate to exercise omission.
        self.assertEqual(ledger["summary"]["failed"], 0)

        data = load_language_data(self.project, "en")
        data["chapters"].append(
            {
                "id": "CH-02",
                "kind": "chapter",
                "title": "Chapter Two",
                "pages": "3-4",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )
        write_json(self.project / "data" / "pack.en.json", data)
        normalized = normalize_text(self.source.read_text(encoding="utf-8"))
        split = normalized.index("ada lovelace")
        spans = [
            {
                "chapter_id": "CH-01",
                "char_start": 0,
                "char_end": split,
                "span_sha256": hashlib.sha256(normalized[:split].encode()).hexdigest(),
            },
            {
                "chapter_id": "CH-02",
                "char_start": split,
                "char_end": len(normalized),
                "span_sha256": hashlib.sha256(normalized[split:].encode()).hexdigest(),
            },
        ]
        inventory = extract_catalog(
            self.project, "SRC-1", self.source, language="en", chapter_spans=spans
        )
        _, second_ledger_path = create_catalog_candidate_run(
            self.project,
            language="en",
            inventory=inventory,
            source_path=self.source,
            run_directory=self.project / ".reading-pack" / "runs" / "names-with-empty",
            run_id="names-with-empty",
            collections=["names"],
            ledger_output=self.project / ".reading-pack" / "names-with-empty-ledger.json",
        )
        second_ledger = load_work_ledger(second_ledger_path)
        self.assertGreaterEqual(second_ledger["summary"]["failed"], 1)
        self.assertNotIn(
            "no_supported_candidate",
            {item["status"] for item in second_ledger["items"]},
        )

    def test_cli_extracts_combines_and_builds_one_stop_review(self) -> None:
        private = self.project / ".reading-pack"
        chapter_map = self.root / "chapter-map.json"
        write_test_json(chapter_map, {"chapter_spans": self.chapter_spans})
        inventory = private / "catalog.json"
        extracted = cli(
            "catalog",
            "extract",
            str(self.source),
            "--project",
            str(self.project),
            "--lang",
            "en",
            "--chapter-map",
            str(chapter_map),
            "--output",
            str(inventory),
        )
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        self.assertNotIn("Ada Lovelace", extracted.stdout)
        report = cli("catalog", "report", str(inventory), "--json")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)["summary"]["people"], 1)
        run = private / "runs" / "catalog-cli"
        ledger = private / "catalog-cli-ledger.json"
        generated = self.root / "catalog-generated.json"
        write_test_json(
            generated,
            {
                "candidates": [
                    {
                        "collection": "glossary",
                        "record": {
                            "id": "TERM-REVIEW-METHOD",
                            "term": "review method",
                            "chapter_id": "CH-01",
                            "status": "draft",
                        },
                        "evidence": [
                            {
                                "snippet": "Ada Lovelace proposed a review method.",
                                "supports_field": "term",
                            }
                        ],
                    }
                ]
            },
        )
        created = cli(
            "catalog",
            "candidates",
            str(inventory),
            "--source",
            str(self.source),
            "--project",
            str(self.project),
            "--lang",
            "en",
            "--run-directory",
            str(run),
            "--run-id",
            "catalog-cli",
            "--ledger-output",
            str(ledger),
            "--responses",
            str(generated),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertIn("no candidates were accepted or applied", created.stdout)
        reviewed = cli(
            "review",
            "bundle",
            "--project",
            str(self.project),
            "--artifact",
            str(run),
            str(self.source),
            "--ledger",
            "catalog-cli",
            str(ledger),
            "--output",
            "catalog-one-stop.html",
        )
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        rendered = (
            private / "reviews" / "catalog-one-stop.html"
        ).read_text(encoding="utf-8")
        self.assertIn("People index", rendered)
        self.assertIn("Term index", rendered)
        self.assertIn("References", rendered)
        self.assertIn("Ada Lovelace", rendered)
        self.assertIn("review method", rendered)
        self.assertEqual(read_json(run / "manifest.json")["summary"]["accepted"], 0)


if __name__ == "__main__":
    unittest.main()
