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
            result = collect_catalog(jira, cb, [100], 1)
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


class CatalogRegressionTests(unittest.TestCase):
    def catalog(self, definitions, samples=(), search_error=False, definition_error=False):
        def project_types(key):
            if definition_error:
                raise ApiError('HTTP 403 project metadata')
            return definitions
        jira = SimpleNamespace(config={}, http=None, fields=lambda: [],
                               projects=lambda: [{'key':'P'}], project_issue_type_catalog=project_types)
        cb = SimpleNamespace(fields=lambda _: [], configuration=lambda _: {'fields':[]})
        class Reader:
            def __init__(self, *args): pass
            def issues(self, key):
                if search_error: raise ApiError('HTTP 403 search')
                return iter(samples)
        with patch('mapping_helper.JiraClient', Reader):
            return collect_catalog(jira, cb, [100], 1)

    def test_empty_project_and_subtask_unknown(self):
        result = self.catalog([{'id':'1','name':'Test Plan','subtask':False},
                               {'id':'2','name':'SW-CR (Sub-task)','subtask':True},
                               {'id':'3','name':'기능안전_SSR'}])
        project = result['projects'][0]
        self.assertTrue(project['issue_types_complete'])
        self.assertEqual([t['subtask'] for t in project['types']], [False,True,None])
        self.assertTrue(all(t['sample_count']==0 for t in project['types']))
        self.assertFalse(project['security_levels_verified'])

    def test_search_permission_does_not_hide_defined_types(self):
        result = self.catalog([{'id':'1','name':'Test Set'}], search_error=True)
        self.assertEqual(result['projects'][0]['types'][0]['name'], 'Test Set')
        self.assertIn('403', result['warnings'][0])

    def test_sample_type_not_declared_project_available(self):
        issue = {'fields':{'issuetype':{'id':'2','name':'Historical type','subtask':True}}}
        result = self.catalog([{'id':'1','name':'Test Plan'}], [issue])
        types = result['projects'][0]['types']
        self.assertEqual(types[0]['project_availability'], 'configured')
        self.assertEqual(types[1]['project_availability'], 'observed_only')
        self.assertTrue(types[1]['subtask'])

    def test_definition_failure_and_duplicate_not_complete(self):
        for definitions, error in (([], True), ([{'id':'1','name':'A'},{'id':'1','name':'B'}],False)):
            with self.subTest(error=error):
                result = self.catalog(definitions, definition_error=error)
                self.assertFalse(result['projects'][0]['issue_types_complete'])
                self.assertEqual(result['projects'][0]['types'], [])
                self.assertTrue(result['warnings'])

    def test_project_list_not_global_union_and_raw_archived(self):
        saved=[]
        def request(method,path):
            if path.endswith('/project/P'):
                return {'issueTypes':[{'id':'1','name':'Local','subtask':False}]}
            if path.endswith('/issuetype'):
                return [{'id':'1','name':'Local'},{'id':'2','name':'Other project'}]
            return [{'id':'9','name':'Original'}]
        client=JiraClient(SimpleNamespace(request=request), {}, lambda *args:saved.append(args))
        self.assertEqual(len(client.global_catalog()['issue_types']),2)
        self.assertEqual([t['id'] for t in client.issue_types('P')], ['1'])
        self.assertEqual(len(saved),5)

    def test_wizard_project_api_rejects_malformed_subtask(self):
        for value in ([], {'issueTypes':[{'id':'1','name':'A','subtask':'false'}]},
                      {'issueTypes':[{'id':'1','name':'A'},{'id':'1','name':'B'}]}):
            with self.subTest(value=value), self.assertRaises(ApiError):
                JiraClient(SimpleNamespace(request=lambda *args:value), {}).issue_types('P')


if __name__ == "__main__":
    unittest.main()
