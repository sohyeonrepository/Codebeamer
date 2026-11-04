import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clients.jira_client import JiraClient
from clients.http import ApiError
from mapping_helper import collect_catalog


class FakeHttp:
    def __init__(self):
        self.calls = []

    def request(self, method, path):
        self.calls.append((method, path))
        data = {
            "/rest/api/2/issuetype": [{"id": "10", "name": "Test Plan"},
                                       {"id": "11", "name": "Test Set"},
                                       {"id": "12", "name": "Test Execution"},
                                       {"id": "13", "name": "Issue_시험"},
                                       {"id": "14", "name": "기능안전_SSR", "subtask": True}],
            "/rest/api/2/priority": [{"id": "1", "name": "P0 - ASAP"}],
            "/rest/api/2/status": [{"id": "1", "name": "접수완료"}],
            "/rest/api/2/resolution": [{"id": "1", "name": "Fixed"}],
            "/rest/api/2/project/P": {"key": "P", "issueTypes": [
                {"id": "10", "name": "Test Plan"},
                {"id": "11", "name": "Test Set"},
                {"id": "12", "name": "Test Execution"},
                {"id": "13", "name": "Issue_시험"},
                {"id": "14", "name": "기능안전_SSR", "subtask": True},
            ]},
        }
        if path not in data:
            raise AssertionError("Unexpected API: " + path)
        return data[path]


class JiraCatalogTests(unittest.TestCase):
    def test_global_catalog_reads_jira_dc_definition_apis(self):
        http = FakeHttp()
        client = JiraClient(http, {})
        result = client.global_catalog()
        self.assertIn("Issue_시험", [x["name"] for x in result["issue_types"]])
        self.assertEqual(result["priorities"][0]["name"], "P0 - ASAP")
        self.assertEqual(result["statuses"][0]["name"], "접수완료")
        self.assertEqual(result["resolutions"][0]["name"], "Fixed")
        self.assertEqual(len(http.calls), 4)
        self.assertTrue(all(method == "GET" for method, _ in http.calls))

    def test_project_catalog_includes_types_without_issues(self):
        http = FakeHttp()
        result = JiraClient(http, {}).project_issue_type_catalog("P")
        self.assertEqual(len(result), 5)
        self.assertEqual(result[-1]["name"], "기능안전_SSR")
        self.assertTrue(result[-1]["subtask"])

    def test_global_and_project_catalog_are_distinct(self):
        http = FakeHttp()
        client = JiraClient(http, {})
        self.assertEqual(len(client.global_catalog()["issue_types"]), 5)
        self.assertEqual(len(client.project_issue_type_catalog("P")), 5)

    def test_mapping_catalog_does_not_discard_unseen_issue_types(self):
        """The prior mapper only enumerated Issue Types present in sampled Issues."""
        definition = [{"id": "summary", "name": "Summary", "schema": {"type": "string"}}]
        project_types = [
            {"id": "10", "name": "Test Plan"},
            {"id": "11", "name": "Test Set"},
            {"id": "12", "name": "Test Execution"},
            {"id": "14", "name": "기능안전_SSR", "subtask": True},
        ]
        sample = {"key": "P-1", "fields": {
            "issuetype": {"id": "10", "name": "Test Plan"},
            "summary": "sample",
        }}
        jira = SimpleNamespace(
            config={}, http=None, fields=lambda: definition,
            projects=lambda: [{"key": "P"}],
            project_issue_type_catalog=lambda key: project_types,
        )
        cb = SimpleNamespace(fields=lambda tracker: [],
                             configuration=lambda tracker: {"fields": []})
        class Reader:
            def __init__(self, http, config):
                pass
            def issues(self, key):
                return iter([sample])
        with patch("mapping_helper.JiraClient", Reader):
            result = collect_catalog(jira, cb, [], 1)
        found = {x["name"]: x for x in result["projects"][0]["types"]}
        self.assertEqual(len(found), 4)
        self.assertEqual(found["Test Plan"]["sample_count"], 1)
        self.assertTrue(found["Test Set"]["metadata_only"])
        self.assertTrue(found["기능안전_SSR"]["subtask"])
        self.assertEqual(found["Test Execution"]["fields"], {})

    def test_invalid_duplicate_definitions_fail_closed(self):
        client = JiraClient(FakeHttp(), {})
        original = client.issue_types
        client.issue_types = lambda project: [{"id": "1", "name": "A"}, {"id": "1", "name": "B"}]
        with self.assertRaisesRegex(ApiError, "Duplicate"):
            client.project_issue_type_catalog("P")


if __name__ == "__main__":
    unittest.main()
