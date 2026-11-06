import unittest
from clients.http import ApiError
from migration.phase2_manifest import relation_values, extract_relations

class RelationManifestTests(unittest.TestCase):
    def test_set_relation_capture(self):
        issue={"key":"PRJ-1","fields":{"customfield_17775":[{"key":"PRJ-2"},{"key":"PRJ-3"}]}}
        self.assertEqual(extract_relations("set",issue),[
            {"source_jira_key":"PRJ-1","target_jira_key":"PRJ-2",
             "jira_field":"customfield_17775","relation":"set_contains_test"},
            {"source_jira_key":"PRJ-1","target_jira_key":"PRJ-3",
             "jira_field":"customfield_17775","relation":"set_contains_test"}])

    def test_case_relations(self):
        issue={"key":"PRJ-2","fields":{"customfield_17770":["PRJ-1"],
                                      "customfield_17772":None}}
        self.assertEqual(extract_relations("case",issue)[0]["relation"],"test_in_set")

    def test_unknown_xray_shape_fails_closed(self):
        with self.assertRaises(ApiError):
            relation_values([{"id":73}], "customfield_17775")

    def test_missing_relation_field_fails(self):
        with self.assertRaises(ApiError):
            extract_relations("set", {"key":"PRJ-1","fields":{}})

if __name__=="__main__":
    unittest.main()
