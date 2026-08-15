import unittest

from reading_pack.hashing import semantic_hash, semantic_value


class HashingTests(unittest.TestCase):
    def test_key_order_does_not_change_hash(self):
        self.assertEqual(semantic_hash({"id": "X", "text": "a"}), semantic_hash({"text": "a", "id": "X"}))

    def test_review_metadata_does_not_change_hash(self):
        draft = {"id": "X", "text": "a", "status": "draft"}
        approved = {"id": "X", "text": "a", "status": "approved", "review_notes": "ok"}
        self.assertEqual(semantic_hash(draft), semantic_hash(approved))

    def test_content_change_changes_hash(self):
        self.assertNotEqual(semantic_hash({"id": "X", "text": "a"}), semantic_hash({"id": "X", "text": "b"}))

    def test_translation_link_fields_are_excluded(self):
        value = semantic_value({"id": "X", "source_id": "X", "source_hash": "0", "translation_status": "draft"})
        self.assertEqual(value, {"id": "X"})


if __name__ == "__main__":
    unittest.main()
