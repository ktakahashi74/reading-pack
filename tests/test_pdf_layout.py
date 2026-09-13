from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from reading_pack.errors import ReadingPackError
from reading_pack.importers import ExtractedBook, extract_pdf_vertical
from reading_pack.pdf_layout import recover_vertical_layout, supplement_heading_candidates
from reading_pack.staging import create_import_plan


def layout_fixture(*, blank_page=False):
    root = ET.Element('pdf2xml')
    def page():
        return ET.SubElement(root, 'page', number=str(len(root)+1), width='800', height='600')
    def span(p, text, size, left, top):
        t=ET.SubElement(p,'text',font=str(size),left=str(left),top=str(top));t.text=text
    p=page()
    for size in (10,14,18):ET.SubElement(p,'fontspec',id=str(size),size=str(size))
    span(p,'第1章入口',14,700,80);span(p,'朝の条件',10,670,80)
    span(p,'第2章古い題',14,500,80);span(p,'終了の条件',10,470,80)
    raw=['第1章入口朝の条件第2章古い題終了の条件']
    if blank_page:page();raw.append('')
    for number,title,section in [(1,'入口','朝の条件'),(2,'新しい題','終了の条件')]:
        p=page();span(p,f'第{number}章{title}',18,760,80)
        span(p,section,10,700,80)
        span(p,'本文は安全条件を詳しく説明する。条件が満たされなければ入口を閉じる。'*3,10,670,90)
        span(p,'この文章は本文の続きであり見出しではない。'*3,10,655,80)
        raw.append(f'第{number}章{title}{section}本文は安全条件を詳しく説明する。')
    return root,'\f'.join(raw)+'\f'


class PdfLayoutTests(unittest.TestCase):
    def test_heading_supplement_requires_unique_body_page_evidence(self):
        chapter = {'id': 'CH-01', 'candidates': [], 'body_pages': [
            {'pdf_page': 7, 'start': 100, 'text': '序文。朝の条件本文は安全条件を説明する。'}]}
        proposal = {'pdf_page': 7, 'title': '朝の条件', 'evidence_quote': '序文。朝の条件本文'}
        added = supplement_heading_candidates(chapter, [proposal], 'a' * 64)
        self.assertEqual(added[0]['source_offset'], 103)
        self.assertEqual(added[0]['title'], '朝の条件')
        self.assertEqual(added, supplement_heading_candidates(chapter, [proposal], 'a' * 64))
        for change in [{'pdf_page': 8}, {'title': '架空の見出し'}, {'evidence_quote': '架空の引用'}]:
            with self.subTest(change=change), self.assertRaises(ReadingPackError):
                supplement_heading_candidates(chapter, [{**proposal, **change}], 'a' * 64)
        chapter['body_pages'][0]['text'] *= 2
        with self.assertRaisesRegex(ReadingPackError, 'ambiguous'):
            supplement_heading_candidates(chapter, [proposal], 'a' * 64)

    def test_body_opener_wins_and_toc_difference_remains_evidence(self):
        root,raw=layout_fixture()
        result=recover_vertical_layout(ET.tostring(root),raw)
        self.assertEqual([c['title'] for c in result['chapters']],['第1章入口','第2章新しい題'])
        self.assertEqual(result['chapters'][1]['toc_title'],'第2章古い題')
        self.assertEqual([c['candidates'][0]['title'] for c in result['chapters']],['朝の条件','終了の条件'])
        self.assertTrue(result['requires_independent_review'])
        self.assertEqual(result,recover_vertical_layout(ET.tostring(root),raw))

    def test_blank_physical_page_does_not_shift_body_evidence(self):
        root,raw=layout_fixture(blank_page=True)
        result=recover_vertical_layout(ET.tostring(root),raw)
        self.assertEqual(result['chapters'][0]['pdf_page'],3)
        self.assertIn('朝の条件',result['chapters'][0]['source_text'])
        self.assertNotIn('終了の条件',result['chapters'][0]['source_text'])

    def test_duplicate_or_missing_openers_and_toc_mismatch_are_rejected(self):
        root,raw=layout_fixture()
        variants=[]
        r=copy.deepcopy(root);r[2].find('text').text='第1章重複';variants.append(r)
        r=copy.deepcopy(root);r[2].find('text').text='第3章欠落';variants.append(r)
        r=copy.deepcopy(root)
        for t in r[0].findall('text'):
            if t.text.startswith('第2章'):t.text='第3章別章'
        variants.append(r)
        for r in variants:
            with self.assertRaises(ReadingPackError):recover_vertical_layout(ET.tostring(r),raw)

    def test_malformed_coordinates_unknown_fonts_and_entities_are_rejected(self):
        root,raw=layout_fixture()
        for attr,value in [('left','NaN'),('font','missing'),('top','9000000')]:
            r=copy.deepcopy(root);r[1].find('text').set(attr,value)
            with self.assertRaises(ReadingPackError):recover_vertical_layout(ET.tostring(r),raw)
        with self.assertRaises(ReadingPackError):recover_vertical_layout(b'<!DOCTYPE pdf2xml [<!ENTITY x "x">]><pdf2xml/>','')

    def test_poppler_dtd_is_not_resolved(self):
        root,raw=layout_fixture()
        xml=b'<!DOCTYPE pdf2xml SYSTEM "pdf2xml.dtd">'+ET.tostring(root)
        self.assertEqual(len(recover_vertical_layout(xml,raw)['chapters']),2)

    def test_vertical_import_automatically_recovers_when_plain_structure_is_empty(self):
        root,raw=layout_fixture()
        with patch('reading_pack.importers._read_pdf_text',return_value=('Book',raw)), \
             patch('reading_pack.importers.extract_pdf_text',return_value=ExtractedBook('Book',[],'pdf')), \
             patch('reading_pack.importers._pdf_tool',return_value='pdftohtml'), \
             patch('reading_pack.importers._run_pdf_tool',return_value=ET.tostring(root)):
            book=extract_pdf_vertical(Path('/tmp/example.pdf'))
        self.assertEqual(len(book.chapters),2)
        self.assertEqual(book.chapters[0]['sections'],[])
        self.assertIsNotNone(book.layout)
        self.assertEqual(book.chapters[0]['pages'],'')

    def test_body_free_plan_and_private_layout_evidence_are_separate(self):
        root,raw=layout_fixture()
        layout=recover_vertical_layout(ET.tostring(root),raw)
        from reading_pack.pdf_layout import extracted_layout_book
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'book.pdf';source.write_bytes(b'%PDF synthetic')
            evidence=Path(tmp)/'layout.json'
            with patch('reading_pack.staging.extract',return_value=extracted_layout_book(layout,'Book')):
                plan=create_import_plan(source,'pdf-vertical',layout_output=evidence)
            self.assertEqual(plan['outcome'],'review_required')
            self.assertNotIn('安全条件',json.dumps(plan,ensure_ascii=False))
            self.assertIn('安全条件',evidence.read_text())
            self.assertIn('RPIP111',[d['code'] for d in plan['diagnostics']])
