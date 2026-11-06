import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from clients.http import ApiError
from batch_case_set_import import load_events, search_page, verify_item


class BatchCaseSetTest(unittest.TestCase):
    def test_jira_search_pagination(self):
        jira = Mock()
        jira.request.return_value = {"startAt": 0, "total": 2, "issues": [
            {"key": "ABC-1", "fields": {}}]}
        page, total = search_page(jira, "ABC", "case", ["summary","description"], 0, 1)
        self.assertEqual(len(page), 1)
        self.assertEqual(total, 2)
        request_path = jira.request.call_args.args[1]
        self.assertIn("/rest/api/2/search?", request_path)
        self.assertIn("startAt=0", request_path)
        self.assertIn("maxResults=1", request_path)

    def test_unknown_search_response_is_not_complete(self):
        jira = Mock()
        jira.request.return_value = {"issues": []}
        with self.assertRaises(ApiError):
            search_page(jira,"ABC","set",["summary"],0,50)

    def test_replay_last_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with path.open("w",encoding="utf-8") as handle:
                for record in [
                    {"jira":"ABC-1","status":"POST_ATTEMPT"},
                    {"jira":"ABC-1","status":"VERIFIED","cb":123},
                    {"jira":"ABC-2","status":"POST_UNKNOWN_OR_FAILED"}]:
                    handle.write(json.dumps(record)+"\n")
            records=load_events(path)
            self.assertEqual(records["ABC-1"]["cb"],123)
            self.assertEqual(records["ABC-2"]["status"],"POST_UNKNOWN_OR_FAILED")

    def test_verify_name_and_custom_fields(self):
        cb=Mock()
        cb.request.return_value={"name":"Test", "description":"Details",
                                  "customFields":[{"fieldId":123,"value":"abc"}]}
        verify_item(cb,900,{"name":"Test","description":"Details","customFields":[
            {"fieldId":123,"type":"TextFieldValue","value":"abc"}]})

    def test_get_mismatch_refuses_verified(self):
        cb=Mock()
        cb.request.return_value={"name":"Test","customFields":[{"fieldId":123,"value":"wrong"}]}
        with self.assertRaises(ApiError):
            verify_item(cb,900,{"name":"Test","customFields":[
                {"fieldId":123,"type":"TextFieldValue","value":"abc"}]})


if __name__=="__main__":
    unittest.main()
