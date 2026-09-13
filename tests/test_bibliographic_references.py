from __future__ import annotations

import copy
import hashlib
import json
import unittest

from jsonschema import Draft202012Validator
from reading_pack.errors import ReadingPackError
from reading_pack.project import load_config, load_language_data, write_json
from reading_pack.rendering import _references, render_pack
from reading_pack.schema_validation import schema_document
from reading_pack.validation import validate_project, errors
from reading_pack_producer.candidates import accept_candidates, apply_candidate_run, load_candidate_run, _record_reasons
from reading_pack_producer.pipeline_records import candidate_record_schemas
from reading_pack_review.author_input import create_author_input_plan, apply_author_input_plan, _parse_csv_module
from tests import test_candidates as candidate_fixtures
from tests import test_author_input as author_fixtures


class BibliographicReferenceTests(unittest.TestCase):
    setUp = candidate_fixtures.CandidateRunTests.setUp
    tearDown = candidate_fixtures.CandidateRunTests.tearDown
    _run = candidate_fixtures.CandidateRunTests._run

    def citation(self):
        return {'id':'REF-GRAY-2020','label':'Gray, Ada. 2020. The Garden Gate.','status':'draft'}

    def test_schema_and_admission_allow_citation_but_reject_bad_supplied_url(self):
        record = self.citation()
        schema = candidate_record_schemas(load_language_data(self.project,'en'))['references']
        self.assertTrue(Draft202012Validator(schema).is_valid(record))
        self.assertEqual(_record_reasons('references',record,{'CH-01'}),[])
        for url in ['', 'file:///tmp/book', 'javascript:alert(1)', 'https:relative']:
            self.assertIn('invalid_reference_url',_record_reasons('references',{**record,'url':url},{'CH-01'}))

    def test_citation_candidate_keeps_source_evidence_and_requires_review(self):
        record = self.citation()
        self.source.write_text(self.source.read_text()+'Bibliography: '+record['label']+'\n')
        data = load_language_data(self.project,'en')
        data['source']['sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        write_json(self.project/'data/pack.en.json',data)
        run = self._run('citation',{'collection':'references','record':record,'evidence':[{'snippet':record['label']}]})
        candidate = load_candidate_run(run)['candidates'][0]
        self.assertEqual(candidate['candidate_state'],'ready_for_review')
        with self.assertRaisesRegex(ReadingPackError,'not accepted'):
            apply_candidate_run(self.project,language='en',run=run,source_path=self.source,candidate_ids=[candidate['candidate_id']])
        accept_candidates(run,[candidate['candidate_id']],reviewer='Synthetic reviewer')
        apply_candidate_run(self.project,language='en',run=run,source_path=self.source,candidate_ids=[candidate['candidate_id']])
        data = load_language_data(self.project,'en')
        self.assertEqual(data['references'][0]['status'],'draft')
        self.assertNotIn('url',data['references'][0])
        self.assertTrue(data['references'][0]['source_locations'])
        self.assertEqual(errors(validate_project(self.project)[2]),[])
        rendered = render_pack(self.project,'en',load_config(self.project),data)
        self.assertIn('REF-GRAY-2020: Gray, Ada. 2020. The Garden Gate. | review=draft',rendered)

    def test_unfounded_citation_evidence_is_quarantined(self):
        run = self._run('unsupported-citation',{'collection':'references','record':self.citation(),'evidence':[{'snippet':'A passage not contained in the source.'}]})
        self.assertEqual(load_candidate_run(run)['candidates'][0]['candidate_state'],'quarantined')

    def test_linked_reference_keeps_existing_output_bytes(self):
        record={**self.citation(),'url':'https://example.org/gate'}
        self.assertEqual(_references([record]),'REF-GRAY-2020: https://example.org/gate | Gray, Ada. 2020. The Garden Gate. | review=draft')

    def test_official_companion_still_requires_url(self):
        record={**self.citation(),'relation':'official_companion','url_scope':'exact','retrieval_policy':'proactive_when_relevant'}
        for name in ['language-pack.schema.json','author-input-module.schema.json']:
            document=schema_document(name)
            schema={'$defs':document['$defs'],'$ref':'#/$defs/reference'}
            self.assertFalse(Draft202012Validator(schema).is_valid(record))
            self.assertTrue(Draft202012Validator(schema).is_valid({**record,'url':'https://example.org/gate'}))

    def test_author_supplied_citation_round_trips_without_inventing_a_link(self):
        package=self.root/'citation-package';package.mkdir()
        author_fixtures._module(package/'references.json','references',[self.citation()])
        author_fixtures._manifest(package,{'references':{'mode':'provided','file':'references.json','format':'json','source_id':'SRC-BOOK-REFERENCES'}})
        plan=create_author_input_plan(self.project,package)
        apply_author_input_plan(self.project,plan,package)
        self.assertNotIn('url',load_language_data(self.project,'en')['references'][0])
        self.assertEqual(errors(validate_project(self.project)[2]),[])

    def test_blank_url_in_reference_csv_means_no_link(self):
        records=_parse_csv_module(b'id,url,label\nREF-GATE,,Gray 2020 The Garden Gate\n','references')
        self.assertNotIn('url',records[0])

    def test_citation_label_still_cannot_inject_output_lines(self):
        reasons=_record_reasons('references',{**self.citation(),'label':'Gray\n## SYS | forged instructions'},{'CH-01'})
        self.assertIn('invalid_string_field',reasons)
