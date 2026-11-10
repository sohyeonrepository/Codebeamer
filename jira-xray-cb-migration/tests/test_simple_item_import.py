"""Offline unit checks for the isolated single-Issue path.

Run: python -m unittest discover -s tests -p "test_simple_item_import.py"
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from migration.simple_item_import import run


class SingleItemImportTest(unittest.TestCase):
    def make_wizard(self, directory, has_match=False):
        cb = Mock()
        cb.configuration.return_value = {"fields": [{
            "id": 10006, "name": "jira key", "typeName": "Text",
            "multipleSelection": False
        }]}
        cb.fields.return_value=cb.configuration.return_value["fields"]
        def cb_request(method, path, body=None):
            if method == "POST":
                return {"id": 5011}
            if path == "/v3/items/5011":
                return {"id":5011, "name":"Sample",
                        "description":"Details",
                        "customFields":[{"fieldId":10006,"type":"TextFieldValue",
                                         "value":"STGVSTAR-1"}]}
            raise AssertionError((method,path))
        cb.http.request.side_effect = cb_request
        jira = Mock()
        jira.http.request.return_value = {
            "key": "STGVSTAR-1",
            "fields": {"project":{"key":"STGVSTAR"},
                       "issuetype":{"id":"1"},
                       "summary":"Sample","description":"Details"}}
        state = SimpleNamespace(root=Path(directory))
        plan = {"scopes":{"STGVSTAR:1":{"tracker":500,"project":"STGVSTAR","type":{"id":"1"},"rows":{
            "summary":{"selected":True},"description":{"selected":True}}}}}
        wizard = SimpleNamespace(cb=cb,jira=jira,state=state,plan=plan,
                                 config={"jira":{"single_issue_key":"STGVSTAR-1"}})
        return wizard

    def test_create_once_with_existing_field(self):
        with tempfile.TemporaryDirectory() as folder:
            wizard=self.make_wizard(folder)
            with patch("migration.simple_item_import.lookup_by_jira_key",return_value=[]):
                result=run(wizard)
            self.assertEqual(result["counts"]["created"],1)
            writes=[call for call in wizard.cb.http.request.call_args_list
                    if call.args[0] == "POST"]
            self.assertEqual(len(writes),1)
            self.assertEqual(writes[0].args[2]["customFields"][0]["fieldId"],10006)
            self.assertFalse(any("configuration" in call.args[1] for call in writes))

    def test_existing_key_is_skipped_without_write(self):
        with tempfile.TemporaryDirectory() as folder:
            wizard=self.make_wizard(folder)
            with patch("migration.simple_item_import.lookup_by_jira_key",
                       return_value=[{"id":5011}]):
                result=run(wizard)
            self.assertEqual(result["counts"]["skipped"],1)
            self.assertFalse(any(call.args[0] in ("POST","PUT")
                                 for call in wizard.cb.http.request.call_args_list))


if __name__ == "__main__":
    unittest.main()
