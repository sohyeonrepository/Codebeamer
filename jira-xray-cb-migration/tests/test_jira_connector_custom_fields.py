import json
import unittest

from migration.fields import convert
from migration.jira_connector_custom_fields import (
    ChecklistConversionError, WIKI_HEADER, WIKI_FOOTER,
    to_codebeamer_items, to_codebeamer_wiki,
    checklist_metadata, parse_checklist_changes,
)


class JiraChecklistAdapterTests(unittest.TestCase):
    def test_checklist_import_does_not_modify_source(self):
        source = [{
            "name": "Check this\\n>>description", "isHeader": True,
            "option": True, "rank": 4, "priorityId": 3,
            "dueDate": "15/Oct/2026",
            "status": {"id": "blocked"},
            "checked": True,
        }]
        before = json.dumps(source, sort_keys=True)
        result = to_codebeamer_items(source)
        self.assertEqual(json.dumps(source, sort_keys=True), before)
        self.assertTrue(result[0]["header"])
        self.assertTrue(result[0]["pinned"])
        self.assertEqual(result[0]["priorityId"], 3)
        self.assertEqual(result[0]["status"], {"name": "Blocked", "style": "blocked"})
        self.assertEqual(result[0]["endDate"], "2026-10-15T00:00:00")
        self.assertNotIn("rank", result[0])

    def test_wiki_payload_and_explicit_field_conversion(self):
        source = [{"name": "First", "priorityId": 4}]
        output = convert(source, "jira_checklist", {"4": {"id": 10, "name": "High"}})
        self.assertTrue(output.startswith(WIKI_HEADER))
        self.assertTrue(output.endswith(WIKI_FOOTER))
        value = json.loads(output[len(WIKI_HEADER):-len(WIKI_FOOTER)])
        self.assertEqual(value[0]["priority"], {"id": 10, "name": "High"})

    def test_metadata_is_proposal_only(self):
        value = checklist_metadata({"allowedValues": [{"name": "template"}]})
        self.assertEqual(value["target_field_type"], "WikiText")
        self.assertTrue(value["requires_user_confirmation"])

    def test_history_remains_source_delta(self):
        self.assertEqual(
            parse_checklist_changes("1) [added] First\n2) [removed] Second"),
            {1: "[added] First", 2: "[removed] Second"},
        )

    def test_invalid_input_fails_closed(self):
        with self.assertRaises(ChecklistConversionError):
            to_codebeamer_wiki({"name": "not-an-array"})
        with self.assertRaises(ChecklistConversionError):
            parse_checklist_changes("unrecognized history without ids")
        with self.assertRaises(ChecklistConversionError):
            to_codebeamer_wiki([{"name": "x", "dueDate": "not-a-date"}])


if __name__ == "__main__":
    unittest.main()
