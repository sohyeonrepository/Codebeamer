import unittest
from clients.http import ApiError
from test_case_set_post import source_field_names, resolve_direct_fields, mapped_payload

CASE = {"phase_1": [
    {"jira":"summary","cb":{"label":"Name","type":"Text"},"mode":"direct"},
    {"jira":"description","cb":{"label":"Description","type":"WikiText"},"mode":"direct"},
    {"jira":"customfield_19804","cb":{"label":"시험 전 조건","type":"WikiText"},"mode":"direct"},
    {"jira":"customfield_19102","cb":{"label":"[TC] ID","type":"Text"},"mode":"direct"},
    {"jira":"priority.name","cb":{"label":"Jira Priority","type":"Choice"},"mode":"mapped"}
]}


class TestCaseSetMappingTest(unittest.TestCase):
    def test_only_direct_fields_queried(self):
        self.assertEqual(source_field_names(CASE), ["customfield_19804","customfield_19102"])

    def test_field_registration_not_used_as_numeric_id(self):
        fields = [
            {"id": 95111, "label": "시험 전 조건", "typeName": "WikiText"},
            {"id": 95112, "label": "[TC] ID", "typeName": "Text"}
        ]
        resolved = resolve_direct_fields(fields, CASE)
        self.assertEqual(resolved["customfield_19804"], (95111, "WikiTextFieldValue"))
        payload = mapped_payload(CASE, {"summary":"Example", "description":None,
                                         "customfield_19804":"준비됨",
                                         "customfield_19102":"T-123"}, resolved,
                                 jira_url="https://jira.example", jira_key="ABC-1")
        self.assertEqual(payload["name"], "Example")
        self.assertNotIn("description", payload)
        self.assertEqual(payload["customFields"], [
            {"fieldId":95111,"type":"WikiTextFieldValue","value":"준비됨"},
            {"fieldId":95112,"type":"TextFieldValue","value":"T-123"}
        ])

    def test_null_skipped(self):
        fields = [{"id":1,"label":"시험 전 조건","typeName":"WikiText"},
                  {"id":2,"label":"[TC] ID","typeName":"Text"}]
        resolved = resolve_direct_fields(fields,CASE)
        payload=mapped_payload(CASE,{"summary":"Ok","description":"", "customfield_19804":None,
                                     "customfield_19102":None},resolved,
                              jira_url="https://jira.example",jira_key="ABC-1")
        self.assertNotIn("customFields",payload)

    def test_wrong_type_fails_before_post(self):
        with self.assertRaises(ApiError):
            resolve_direct_fields([{"id":1,"label":"시험 전 조건","typeName":"Text"},
                                   {"id":2,"label":"[TC] ID","typeName":"Text"}],CASE)

    def test_nonstring_fails_before_post(self):
        fields=[{"id":1,"label":"시험 전 조건","typeName":"WikiText"},
                {"id":2,"label":"[TC] ID","typeName":"Text"}]
        resolved=resolve_direct_fields(fields,CASE)
        with self.assertRaises(ApiError):
            mapped_payload(CASE,{"summary":"Ok","customfield_19804":["unexpected"]},resolved,
                           jira_url="https://jira.example",jira_key="ABC-1")

if __name__=="__main__":
    unittest.main()
