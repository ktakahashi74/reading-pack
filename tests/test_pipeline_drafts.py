from __future__ import annotations

import copy
import unittest

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data, write_json
from reading_pack.validation import errors, validate_project
from reading_pack_review.assisted_review import (
    apply_assisted_author_review_plan, create_assisted_author_review_plan,
)
from reading_pack_review.author_review import load_author_review_state
from reading_pack_producer.pipeline_drafts import register_draft_revisions
from tests import test_assisted_review as fixtures


class DraftRevisionTests(unittest.TestCase):
    setUp = fixtures.AssistedAuthorReviewTests.setUp
    tearDown = fixtures.AssistedAuthorReviewTests.tearDown
    _export = fixtures.AssistedAuthorReviewTests._export
    _complete_text = fixtures.AssistedAuthorReviewTests._complete_text

    def approve(self, name):
        review, evidence, session = self._export(name)
        review.write_text(self._complete_text(review, session), encoding='utf-8')
        plan = create_assisted_author_review_plan(self.project, evidence, review)
        apply_assisted_author_review_plan(self.project, plan, evidence, review)

    def edit(self, text):
        before = load_language_data(self.project, 'en')
        after = copy.deepcopy(before)
        after['chapters'][0]['summary'] = text
        write_json(self.project / 'data/pack.en.json', after)
        return before

    def test_draft_retains_human_history_and_requires_new_approval(self):
        self.approve('first')
        history = load_author_review_state(self.project)['reviews']
        before = self.edit('The source adds a necessary entry condition.')
        self.assertIn('RP505', [i.code for i in errors(validate_project(self.project)[2])])
        self.assertEqual(register_draft_revisions(self.project, 'en', before, origin='test:repair'), 1)
        self.assertEqual(load_author_review_state(self.project)['reviews'], history)
        self.assertEqual(errors(validate_project(self.project)[2]), [])
        self.assertEqual(load_language_data(self.project, 'en')['chapters'][0]['status'], 'draft')
        self.assertTrue(errors(validate_project(self.project, release=True)[2]))
        # A producer receipt must not let a status edit manufacture approval.
        data = load_language_data(self.project, 'en')
        data['chapters'][0]['status'] = 'approved'
        write_json(self.project / 'data/pack.en.json', data)
        self.assertIn('RP505', [i.code for i in errors(validate_project(self.project)[2])])

    def test_second_real_author_decision_connects_through_the_draft(self):
        self.approve('first')
        before = self.edit('The next draft preserves the source condition.')
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        self.approve('second')
        state = load_author_review_state(self.project)
        self.assertEqual(len(state['reviews']), 2)
        self.assertEqual(len(state['draft_revisions']), 1)
        self.assertEqual(load_language_data(self.project, 'en')['chapters'][0]['status'], 'approved')
        self.assertEqual(errors(validate_project(self.project)[2]), [])
        # A third draft starts at the second approval, not the superseded first.
        before = self.edit('The third draft explains the same condition more clearly.')
        register_draft_revisions(self.project, 'en', before, origin='test:repair-2')
        self.assertEqual(errors(validate_project(self.project)[2]), [])

    def test_unregistered_edits_and_stale_origins_remain_errors(self):
        self.approve('first')
        before = self.edit('An accurately registered pending draft.')
        register_draft_revisions(self.project, 'en', before, origin='test:repair')
        self.edit('An unregistered change after the receipt.')
        self.assertIn('RP505', [i.code for i in errors(validate_project(self.project)[2])])
        stale = self.edit('A subsequent edit cannot bless the unregistered change.')
        with self.assertRaisesRegex(ReadingPackError, 'no longer follows'):
            register_draft_revisions(self.project, 'en', stale, origin='test:stale')

    def test_protected_author_records_cannot_gain_draft_successors(self):
        self.approve('first')
        before = load_language_data(self.project, 'en')
        after = copy.deepcopy(before)
        after['names'][0]['name'] = 'Unprovided Replacement'
        write_json(self.project / 'data/pack.en.json', after)
        with self.assertRaisesRegex(ReadingPackError, 'protected'):
            register_draft_revisions(self.project, 'en', before, origin='test:forbidden')
        self.assertNotIn('draft_revisions', load_author_review_state(self.project))
