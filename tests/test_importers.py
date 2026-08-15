from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from reading_pack.errors import ReadingPackError
from reading_pack.importers import (
    ExtractedBook,
    extract,
    extract_epub,
    extract_markdown,
    extract_org,
    extract_pdf,
    extract_pdf_text,
    extract_text,
    reconstruct_pdf_vertical_text,
    _run_pdf_tool,
)


class TextImporterTests(unittest.TestCase):
    def test_markdown_extracts_structure_not_prose(self):
        text = """# Book\n\n## One\nsecret prose\n### Detail\n```\n## Not a chapter\n```\n## Two\n"""
        book = extract_markdown(text)
        self.assertEqual(book.title, "Book")
        self.assertEqual([chapter["title"] for chapter in book.chapters], ["One", "Two"])
        self.assertEqual(book.chapters[0]["sections"], ["Detail"])
        self.assertNotIn("secret prose", str(book.chapters))

    def test_markdown_with_multiple_h1s_treats_each_as_a_chapter(self):
        text = """# Preface\n\n## Why this book\n\n# Chapter one\n\n## First topic\n"""
        book = extract_markdown(text)
        self.assertEqual(book.title, "Untitled book")
        self.assertEqual(
            [chapter["title"] for chapter in book.chapters],
            ["Preface", "Chapter one"],
        )
        self.assertEqual(book.chapters[0]["sections"], ["Why this book"])
        self.assertEqual(book.chapters[1]["sections"], ["First topic"])

    def test_single_markdown_h1_remains_the_title_without_children(self):
        book = extract_markdown("# Book\n")
        self.assertEqual(book.title, "Book")
        self.assertEqual([chapter["title"] for chapter in book.chapters], ["Book"])

    def test_org_supports_japanese_headings_and_todo_keyword(self):
        text = "#+TITLE: 架空の本\n* TODO 第一章 :draft:\n** 節一\n本文\n* 第二章\n"
        book = extract_org(text)
        self.assertEqual(book.title, "架空の本")
        self.assertEqual(book.chapters[0]["title"], "第一章")
        self.assertEqual(book.chapters[0]["sections"], ["節一"])

    def test_org_title_does_not_hide_an_identically_named_first_chapter(self):
        text = "#+TITLE: Book\n* Book\n** Opening\n* Next\n"
        book = extract_org(text)
        self.assertEqual(
            [chapter["title"] for chapter in book.chapters], ["Book", "Next"]
        )

    def test_org_requires_includes_to_be_resolved_before_import(self):
        text = '#+TITLE: Book\n#+INCLUDE: "chapter.org"\n'
        with self.assertRaisesRegex(ReadingPackError, "resolve includes first"):
            extract_org(text)

    def test_plain_text_is_supported(self):
        book = extract_text("A Book\nChapter 1: Beginning\nprose\nChapter 2: End\n")
        self.assertEqual(len(book.chapters), 2)

    def test_unknown_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.docx"
            path.write_bytes(b"not a document")
            with self.assertRaisesRegex(ReadingPackError, "unsupported manuscript format"):
                extract(path)

    def test_invalid_utf8_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.md"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(ReadingPackError, "must be UTF-8"):
                extract(path)


def make_epub(
    path: Path,
    *,
    encrypted: bool = False,
    unsafe_manifest: bool = False,
    unsafe_xml: bool = False,
    package_version: str = "3.0",
) -> None:
    container = b'''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
 <rootfiles><rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    if unsafe_xml:
        container = b'<!DOCTYPE x><container/>'
    href = "../../outside.xhtml" if unsafe_manifest else "chapter.xhtml"
    opf = f'''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{package_version}">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB Book</dc:title></metadata>
 <manifest><item id="c1" href="{href}" media-type="application/xhtml+xml"/></manifest>
 <spine><itemref idref="c1"/></spine>
</package>'''.encode()
    xhtml = b'''<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Opening</h1><p>PROSE_SENTINEL</p><h2>A Detail</h2></body></html>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/package.opf", opf)
        archive.writestr("OPS/chapter.xhtml", xhtml)
        if encrypted:
            archive.writestr("META-INF/encryption.xml", "<encryption/>")


class EpubImporterTests(unittest.TestCase):
    def test_epub3_extracts_metadata_spine_and_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path)
            book = extract_epub(path)
            self.assertEqual(book.title, "EPUB Book")
            self.assertEqual(book.chapters[0]["title"], "Opening")
            self.assertEqual(book.chapters[0]["sections"], ["A Detail"])
            self.assertNotIn("PROSE_SENTINEL", str(book.chapters))

    def test_encrypted_epub_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path, encrypted=True)
            with self.assertRaisesRegex(ReadingPackError, "DRM"):
                extract_epub(path)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path, unsafe_manifest=True)
            with self.assertRaisesRegex(ReadingPackError, "unsafe EPUB member path"):
                extract_epub(path)

    def test_doctype_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path, unsafe_xml=True)
            with self.assertRaisesRegex(ReadingPackError, "unsafe XML"):
                extract_epub(path)

    def test_epub2_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path, package_version="2.0")
            with self.assertRaisesRegex(ReadingPackError, "only EPUB3"):
                extract_epub(path)

    def test_duplicate_zip_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            make_epub(path)
            with self.assertWarns(UserWarning):
                with zipfile.ZipFile(path, "a") as archive:
                    archive.writestr("OPS/chapter.xhtml", "<html/>")
            with self.assertRaisesRegex(ReadingPackError, "duplicate member"):
                extract_epub(path)


PDF_RAW_SAMPLE = """ま え が き
3
第 1 章 は じ め に ─ ─ ─ ─ ─
17
1
・
1
最 初 の 問 い
17
PROSE_SENTINEL must never reach canonical data.
第
2
章 二 つ 目 の 章
31
2
・ 1 第 二 の 節
31
第
11
章
長 い 主 題
─ 副 題 へ
─ ─ ─ ─ ─
40
11
・
1
最 後 の 節
40
あ と が き
50
注
52
"""


class PdfImporterTests(unittest.TestCase):
    def test_vertical_pdf_reconstructs_glyph_lines_with_guarded_repairs(self):
        japanese = list("日本語縦書本文確認処理対象文字列") * 3
        corrupted = ["ò", "û", "º", "}", "q"] * 5
        raw = "\n".join(
            japanese
            + corrupted
            + [
                "引",
                "440",
                "D",
                "張",
                "ヴ",
                "n",
                "ン",
                "フ",
                "k",
                "ò",
                "ム",
                "シ",
                "K",
                "神",
                "ツ",
                "}",
                "―",
                "Ÿ",
                "‡",
                "20330年",
                "200532009年",
                "図D)",
                "フ204",
                "}",
                "ò",
                "ズ",
                "Ç",
                "´",
                "\u0336\u0336",
            ]
        )
        reconstructed = reconstruct_pdf_vertical_text(raw)
        self.assertIn("日本語縦書", reconstructed)
        self.assertIn("ーッィェュ", reconstructed)
        self.assertTrue(
            reconstructed.endswith(
                "引440っ張ヴァンフォームシャ神ツェ―ョか"
                "20〜30年2005〜2009年図)フェーズ:≒――"
            )
        )

    def test_vertical_pdf_does_not_repair_ambiguous_latin_without_signature(self):
        self.assertEqual(
            reconstruct_pdf_vertical_text("D\ng\nŸ\n‡\nordinary prose\n"),
            "DgŸ‡ordinary prose",
        )

    def test_vertical_pdf_preserves_horizontal_ascii_word_boundaries(self):
        japanese = list("日本語縦書本文確認処理対象文字列") * 3
        corrupted = ["ò", "û", "º", "}", "q"] * 5
        raw = "\n".join(
            japanese + corrupted + ["The", "Intelligence Age", "Ａ", "Ｉ"]
        )
        self.assertIn(
            "The Intelligence AgeAI", reconstruct_pdf_vertical_text(raw)
        )

    def test_vertical_pdf_preserves_ambiguous_ascii_inside_formula(self):
        japanese = list("日本語縦書本文確認処理対象文字列") * 3
        corrupted = ["ò", "û", "º", "}", "q"] * 5
        raw = "\n".join(
            japanese
            + corrupted
            + ["d", "D", "x", "quality", "N", "H", "K", "出", "版"]
        )
        self.assertTrue(
            reconstruct_pdf_vertical_text(raw).endswith("dDxqualityNHK出版")
        )

    def test_vertical_pdf_does_not_apply_known_ligature_aliases_without_signature(self):
        raw = "20330年\n200532009年\n図D)\nフ204}ーズ\n"
        self.assertEqual(
            reconstruct_pdf_vertical_text(raw),
            "20330年200532009年図D)フ204}ーズ",
        )

    def test_vertical_pdf_repairs_colon_grouped_with_vertical_bracket(self):
        japanese = list("日本語縦書本文確認処理対象文字列") * 3
        corrupted = ["ò", "û", "º", "}", "q"] * 5
        raw = "\n".join(japanese + corrupted + ["Ç ﹁", "定", "義"])
        self.assertTrue(reconstruct_pdf_vertical_text(raw).endswith(': 「定義'))

    def test_vertical_pdf_normalizes_japanese_radical_font_aliases(self):
        japanese = list("日本語縦書本文確認処理対象文字列") * 3
        corrupted = ["ò", "û", "º", "}", "q"] * 5
        raw = "\n".join(japanese + corrupted + list("⺠⻄⻑⻤⻩⻭⻲"))
        self.assertTrue(
            reconstruct_pdf_vertical_text(raw).endswith("民西長鬼黄歯亀")
        )

    def test_vertical_pdf_is_an_explicit_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(b"%PDF-synthetic")
            expected = ExtractedBook("Book", [], "pdf-vertical")
            with patch(
                "reading_pack.importers.extract_pdf_vertical", return_value=expected
            ) as importer:
                book = extract(path, explicit_format="pdf-vertical")
        self.assertEqual(book.source_format, "pdf-vertical")
        importer.assert_called_once_with(path)

    def test_pdf_raw_text_extracts_toc_structure_not_prose(self):
        book = extract_pdf_text(PDF_RAW_SAMPLE, metadata_title="A Test Book")
        self.assertEqual(book.title, "A Test Book")
        self.assertEqual(
            [chapter["id"] for chapter in book.chapters],
            ["CH-PREFACE", "CH-01", "CH-02", "CH-11", "CH-AFTERWORD"],
        )
        self.assertEqual(book.chapters[1]["title"], "はじめに")
        self.assertEqual(book.chapters[1]["pages"], "17-30")
        self.assertEqual(book.chapters[1]["sections"], ["最初の問い"])
        self.assertEqual(book.chapters[3]["title"], "長い主題――副題へ")
        self.assertEqual(book.chapters[4]["pages"], "50-51")
        self.assertNotIn("PROSE_SENTINEL", str(book.chapters))

    def test_pdf_metadata_layout_filename_is_not_used_as_book_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(b"%PDF-synthetic")
            outputs = [
                b"Title: layout.indd\nEncrypted: no\nPages: 12\n",
                PDF_RAW_SAMPLE.encode(),
            ]
            with patch("reading_pack.importers._pdf_tool", side_effect=lambda name: name):
                with patch("reading_pack.importers._run_pdf_tool", side_effect=outputs):
                    book = extract_pdf(path)
        self.assertEqual(book.title, "")
        self.assertEqual(book.source_format, "pdf")

    def test_encrypted_pdf_is_rejected_before_text_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(b"%PDF-synthetic")
            with patch("reading_pack.importers._pdf_tool", return_value="pdfinfo"):
                with patch(
                    "reading_pack.importers._run_pdf_tool",
                    return_value=b"Encrypted: yes (print:yes copy:no)\n",
                ) as runner:
                    with self.assertRaisesRegex(ReadingPackError, "encrypted"):
                        extract_pdf(path)
        self.assertEqual(runner.call_count, 1)

    def test_missing_poppler_tool_has_clear_error(self):
        with patch("reading_pack.importers.shutil.which", return_value=None):
            with self.assertRaisesRegex(ReadingPackError, "requires the local Poppler tool"):
                extract_pdf(Path(__file__))

    def test_pdf_extension_is_auto_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(b"%PDF-synthetic")
            expected = extract_pdf_text(PDF_RAW_SAMPLE, metadata_title="Book")
            with patch("reading_pack.importers.extract_pdf", return_value=expected) as importer:
                book = extract(path)
        self.assertEqual(book.source_format, "pdf")
        importer.assert_called_once_with(path)

    def test_pdf_without_unambiguous_headings_returns_no_chapters(self):
        book = extract_pdf_text("A body sentence.\nPROSE_SENTINEL\n", metadata_title="Book")
        self.assertEqual(book.chapters, [])
        self.assertNotIn("PROSE_SENTINEL", str(book.chapters))

    def test_pdfinfo_must_report_encryption_and_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(b"%PDF-synthetic")
            with patch("reading_pack.importers._pdf_tool", return_value="tool"):
                with patch("reading_pack.importers._run_pdf_tool", return_value=b"Pages: 1\n"):
                    with self.assertRaisesRegex(ReadingPackError, "encryption state"):
                        extract_pdf(path)
                with patch(
                    "reading_pack.importers._run_pdf_tool", return_value=b"Encrypted: no\n"
                ):
                    with self.assertRaisesRegex(ReadingPackError, "page count"):
                        extract_pdf(path)

    def test_pdf_tool_output_is_hard_limited(self):
        with self.assertRaisesRegex(ReadingPackError, "stdout exceeds 10 bytes"):
            _run_pdf_tool(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 11)"],
                "synthetic PDF tool",
                max_stdout=10,
            )

    def test_pdf_tool_timeout_kills_child(self):
        with patch("reading_pack.importers.PDF_TOOL_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(ReadingPackError, "timed out"):
                _run_pdf_tool(
                    [sys.executable, "-c", "import time; time.sleep(2)"],
                    "synthetic PDF tool",
                    max_stdout=10,
                )


if __name__ == "__main__":
    unittest.main()
