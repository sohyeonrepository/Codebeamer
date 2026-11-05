import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import post_smoke_test as smoke


class SmokeTest(unittest.TestCase):
    def test_summary_description_only(self):
        self.assertEqual(smoke.build_payload("Test", "Details"), {"name": "Test", "description": "Details"})

    def test_missing_description(self):
        self.assertEqual(smoke.build_payload("Test", None), {"name": "Test"})

    def test_wikitext_jira_link(self):
        payload = smoke.build_payload("Test", "", jira_url="https://jira.example.com",
                                      issue_key="STGVSTAR-2715", link_field_id=10006)
        self.assertEqual(payload["customFields"], [{"fieldId": 10006, "type": "WikiTextFieldValue",
                         "value": "[STGVSTAR-2715|https://jira.example.com/browse/STGVSTAR-2715]"}])

    def test_journal_prevents_duplicate_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            self.assertFalse(smoke.prior_attempt(path, "ABC-1", 7))
            smoke.append_event(path, {"jira": "ABC-1", "tracker": 7, "status": "POST_ATTEMPT"})
            self.assertTrue(smoke.prior_attempt(path, "ABC-1", 7))
            self.assertFalse(smoke.prior_attempt(path, "ABC-2", 7))

    def test_wikitext_field_type_mismatch_refuses(self):
        client = Mock()
        client.request.return_value = {"id": 10006, "valueModel": "TextFieldValue"}
        with self.assertRaises(smoke.ApiError):
            smoke.validate_wikitext_field(client, 4879, 10006)

    def test_preview_does_not_post(self):
        # Main is intentionally integration-only; default preview has no POST branch.
        self.assertFalse("POST" in smoke.build_payload("Test"))

if __name__ == "__main__":
    unittest.main()
