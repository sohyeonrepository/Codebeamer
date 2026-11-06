import unittest
from unittest.mock import Mock
from clients.http import ApiError
from migration.connected_case_set import selected_scope, required_source_columns, preview, load_mapping

class ConnectedCaseSetTests(unittest.TestCase):
    def fixture(self, name="Test Set"):
        wizard=Mock()
        wizard.plan={"projects":{"STGVSTAR":{"trackers":[{"id":4879,"name":"Tests"}]}},
                     "scopes":{"STGVSTAR:100":{"project":"STGVSTAR",
                        "tracker":4879,"type":{"id":"100","name":name}}}}
        wizard.jira.http.base="https://jira.example"
        return wizard

    def test_reuses_wizard_project_tracker(self):
        wizard=self.fixture()
        self.assertEqual(selected_scope(wizard,"STGVSTAR:100"),
                         ("set","STGVSTAR",4879))

    def test_rejects_other_issue_type(self):
        wizard=self.fixture("Test Execution")
        with self.assertRaises(ApiError):
            selected_scope(wizard,"STGVSTAR:100")

    def test_source_columns_include_full_mapping_fields(self):
        mapping=load_mapping("set")
        columns=required_source_columns(mapping)
        self.assertIn("customfield_17775",columns)
        self.assertIn("customfield_17552",columns)
        self.assertIn("assignee",columns)
        self.assertNotIn("key",columns)

    def test_preview_reuses_connected_http_and_no_post(self):
        wizard=self.fixture()
        wizard.jira.http.request.return_value={
            "key":"STGVSTAR-1",
            "fields":{"summary":"Set","description":"Text",
                      "issuetype":{"name":"Test Set"},
                      "project":{"key":"STGVSTAR"},
                      "customfield_17775":None}
        }
        wizard.cb.http.request.return_value={"fields":[]}
        result=preview(wizard,"STGVSTAR:100","STGVSTAR-1")
        self.assertEqual(result["mode"],"READ_ONLY")
        self.assertEqual(result["tracker"],4879)
        self.assertTrue(any(r["source"]=="customfield_17775" for r in result["mapped_fields"]))
        self.assertEqual(wizard.jira.http.request.call_args.args[0],"GET")
        self.assertEqual(wizard.cb.http.request.call_args.args[0],"GET")
        self.assertTrue(result["complete"] is False)

    def test_rejects_unselected_project(self):
        wizard=self.fixture()
        with self.assertRaises(ApiError):
            preview(wizard,"STGVSTAR:100","OTHER-1")

if __name__=="__main__":
    unittest.main()
