from __future__ import annotations

import copy
import unittest

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack.project import load_language_data, write_json
from reading_pack.validation import validate_project, errors
from reading_pack_review.author_review import load_author_review_state
from reading_pack_review.author_input import load_author_input_state
from reading_pack_producer.pipeline_drafts import authorize_draft_revisions, register_draft_revisions
from reading_pack_producer.pipeline_generation import author_contract, protection_reason, generation_context
from tests import test_pipeline_drafts as draft_fixtures


class DraftPermissionTests(unittest.TestCase):
    setUp = draft_fixtures.DraftRevisionTests.setUp
    tearDown = draft_fixtures.DraftRevisionTests.tearDown
    _export = draft_fixtures.DraftRevisionTests._export
    _complete_text = draft_fixtures.DraftRevisionTests._complete_text
    approve = draft_fixtures.DraftRevisionTests.approve

    def permit(self):
        return authorize_draft_revisions(self.project, 'en', ['NAME-ADA'], origin='test:explicit-user-permission')

    def change(self, text='The book distinguishes the pioneer from later implementations.'):
        before = load_language_data(self.project, 'en')
        after = copy.deepcopy(before)
        after['names'][0]['book_context'] = text
        after['names'][0]['status'] = 'draft'
        write_json(self.project / 'data/pack.en.json', after)
        return before

    def test_permission_preserves_originals_and_only_allows_named_replacement(self):
        self.approve('first')
        before = load_language_data(self.project, 'en')
        inputs = load_author_input_state(self.project)
        reviews = load_author_review_state(self.project)['reviews']
        self.assertEqual(self.permit(), 1)
        self.assertEqual(load_author_input_state(self.project), inputs)
        self.assertEqual(load_language_data(self.project, 'en'), before)
        self.assertEqual(load_author_review_state(self.project)['reviews'], reviews)
        contract = author_contract(self.project, 'en')
        self.assertIsNone(protection_reason({'collection': 'names', 'record': before['names'][0]}, before, contract))
        self.assertIsNotNone(protection_reason({'collection': 'names', 'record': {'id': 'NAME-OTHER'}}, before, contract))
        self.assertEqual(generation_context(before, contract)[0]['names'][0]['name'], 'Ada Lovelace')
        self.assertEqual(len(list((self.project / 'draft-permission-origins').glob('*.json'))), 1)

    def test_permitted_revision_requires_receipt_and_remains_unapproved(self):
        self.approve('first'); self.permit()
        before = self.change()
        self.assertIn('RP502', [i.code for i in errors(validate_project(self.project)[2])])
        register_draft_revisions(self.project, 'en', before, origin='test:source-reviewed-repair')
        self.assertEqual(errors(validate_project(self.project)[2]), [])
        self.assertTrue(errors(validate_project(self.project, release=True)[2]))
        after = load_language_data(self.project, 'en')
        after['names'][0]['status'] = 'approved'
        write_json(self.project / 'data/pack.en.json', after)
        self.assertIn('RP502', [i.code for i in errors(validate_project(self.project)[2])])

    def test_real_author_adoption_consumes_permission_for_the_old_content(self):
        self.approve('first'); self.permit()
        before = self.change()
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        self.approve('second')
        self.assertEqual(errors(validate_project(self.project)[2]), [])
        data = load_language_data(self.project, 'en')
        self.assertEqual(data['names'][0]['status'], 'approved')
        self.assertIsNotNone(protection_reason({'collection':'names', 'record':data['names'][0]}, data, author_contract(self.project,'en')))

    def test_unreviewed_provided_input_can_have_an_explicit_draft_permission(self):
        self.permit()
        before = self.change()
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        self.assertEqual(load_author_review_state(self.project)['reviews'], [])
        self.assertEqual(errors(validate_project(self.project)[2]), [])

    def test_stale_or_unregistered_edit_cannot_be_blessed(self):
        self.approve('first'); self.permit()
        before = self.change()
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        self.change('An unregistered change.')
        with self.assertRaisesRegex(ReadingPackError, 'invalid draft project'):
            self.permit()
        before = self.change('Another unregistered change.')
        with self.assertRaisesRegex(ReadingPackError, 'no longer follows'):
            register_draft_revisions(self.project, 'en', before, origin='test:invalid')

    def test_changed_source_metadata_is_restored_as_original_supply_provenance(self):
        self.approve('first'); self.permit()
        before = self.change()
        after = load_language_data(self.project, 'en')
        after['names'][0].pop('provenance_source_id')
        after['names'][0].pop('provenance_source_hash')
        after['names'][0]['source_locations'] = ['book.md#normalized-text:10-40']
        write_json(self.project / 'data/pack.en.json', after)
        register_draft_revisions(self.project, 'en', before, origin='test:source-reviewed-repair')
        after = load_language_data(self.project, 'en')
        self.assertEqual(after['names'][0]['provenance_source_id'], before['names'][0]['provenance_source_id'])
        self.assertEqual(after['names'][0]['source_locations'], ['book.md#normalized-text:10-40'])
        self.assertEqual(errors(validate_project(self.project)[2]), [])

    def test_invalid_permission_does_not_partially_update_review_state(self):
        self.approve('first')
        before = load_author_review_state(self.project)
        for ids in [['NAME-ADA','MISSING'], ['NAME-ADA','NAME-ADA'], ['CH-01']]:
            with self.assertRaises(ReadingPackError):
                authorize_draft_revisions(self.project, 'en', ids, origin='test:user')
            self.assertEqual(load_author_review_state(self.project), before)


    def test_restart_permission_requires_explicit_authorization_and_checkpoint(self):
        from pathlib import Path
        from reading_pack_producer.pipeline_reuse import restart_pipeline
        missing = Path('/nonexistent-test-run')
        for kwargs in [dict(permit_draft_records=['NAME-ADA']), dict(authorization_file=missing),
                       dict(permit_draft_records=['NAME-ADA'], authorization_file=missing)]:
            with self.assertRaises(ReadingPackError):
                restart_pipeline(missing, missing, {}, **kwargs)

    def test_duplicate_or_misbound_permission_is_rejected_by_schema_validation(self):
        from reading_pack_review.author_review import validate_author_review_state
        self.permit(); state = load_author_review_state(self.project)
        duplicate = copy.deepcopy(state)
        duplicate['draft_permissions'].append(copy.deepcopy(duplicate['draft_permissions'][0]))
        with self.assertRaisesRegex(ReadingPackError, 'duplicate'):
            validate_author_review_state(duplicate)
        state['draft_permissions'][0]['module'] = 'claims'
        with self.assertRaisesRegex(ReadingPackError, 'mismatch'):
            validate_author_review_state(state)


    def test_provenance_only_reproposal_restores_origin_without_a_new_revision(self):
        self.approve('first'); self.permit()
        before = self.change()
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        before = load_language_data(self.project, 'en')
        state = load_author_review_state(self.project)
        after = copy.deepcopy(before)
        after['names'][0].pop('provenance_source_id')
        after['names'][0].pop('provenance_source_hash')
        write_json(self.project / 'data/pack.en.json', after)
        self.assertEqual(register_draft_revisions(self.project, 'en', before, origin='test:equivalent'), 0)
        self.assertEqual(load_language_data(self.project, 'en'), before)
        self.assertEqual(load_author_review_state(self.project), state)
        self.assertEqual(errors(validate_project(self.project)[2]), [])
