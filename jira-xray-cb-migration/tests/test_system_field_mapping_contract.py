"""Regression contracts for Jira core fields in the existing Wizard."""
import unittest

from migration.wizard import proposals, semantic_type


def cb_field(fid, name, kind, multi=False):
    return dict(id=fid, name=name, label=name, typeName=kind, typeId=fid+10,
                multipleSelection=multi, mandatory=False, hidden=False,
                options=None, reference=None)


def jira_field(fid, name, kind):
    return {"id": fid, "name": name, "schema": {"type": kind}}


class CoreFieldMappingTests(unittest.TestCase):
    def setUp(self):
        self.targets = [cb_field(1, "Name", "Text"),
                        cb_field(2, "Description", "Wikitext"),
                        cb_field(3, "Assigned to", "User")]
        self.analysis = {
            "fields": {
                "summary": jira_field("summary", "Summary", "string"),
                "description": jira_field("description", "Description", "string"),
                "assignee": jira_field("assignee", "Assignee", "user"),
            },
            "values": {"summary": ["Sample"], "description": ["Details"],
                       "assignee": [None]},
        }
        self.config = {"codebeamer": {"field_bindings": {}},
                       "migration": {"user_mapping": {}}}

    def test_summary_and_description_map_as_text_without_choice_options(self):
        result = proposals(self.analysis, self.targets, self.config)
        for key, target_id in (("summary", 1), ("description", 2)):
            with self.subTest(key=key):
                self.assertEqual(result[key]["status"], "mapped", result[key])
                self.assertEqual(result[key]["spec"]["field_id"], target_id)
                self.assertEqual(result[key]["spec"]["type"], "text")
                self.assertEqual(result[key]["spec"]["value_mapping"], {})
        self.assertEqual(semantic_type(self.analysis["fields"]["summary"], True), ("text", False))

    def test_assignee_is_user_not_choice_or_reference(self):
        self.assertEqual(semantic_type(self.analysis["fields"]["assignee"], True),
                         ("user", False))
        self.config["migration"]["user_mapping"] = {"legacy": {"id": 10}}
        self.analysis["values"]["assignee"] = [{"name": "legacy"}]
        result = proposals(self.analysis, self.targets, self.config)
        self.assertEqual(result["assignee"]["status"], "mapped", result["assignee"])
        self.assertEqual(result["assignee"]["spec"]["type"], "user")
        self.assertEqual(result["assignee"]["spec"]["field_id"], 3)

    def test_missing_user_identity_mapping_is_not_a_choice_request(self):
        self.analysis["values"]["assignee"] = [{"name": "legacy"}]
        result = proposals(self.analysis, self.targets, self.config)
        self.assertEqual(result["assignee"]["status"], "manual")
        self.assertIn("매핑", result["assignee"]["reason"])

    def test_missing_target_user_metadata_does_not_auto_map(self):
        targets = self.targets[:-1] + [dict(self.targets[-1], typeName="Unknown")]
        result = proposals(self.analysis, targets, self.config)
        self.assertNotEqual(result["assignee"]["status"], "mapped")


if __name__ == "__main__":
    unittest.main()
