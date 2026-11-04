import unittest

from clients.http import ApiError
from migration.field_semantics import jira_field_semantics
from migration.wizard import semantic_type, compatible, proposals, text_conversion
from migration.fields import convert


def source(schema, allowed=None):
    value = {"id": "customfield_10000", "name": "Demo", "schema": schema}
    if allowed is not None:
        value["allowedValues"] = allowed
    return value


class JiraFieldSemanticTests(unittest.TestCase):
    def test_multiselect_option_objects(self):
        spec = source({"type": "array", "items": "option"})
        self.assertEqual(semantic_type(spec, True), ("option", True))

    def test_multiselect_string_with_allowed_values_is_choice(self):
        spec = source({"type": "array", "items": "string"}, ["Low", "High"])
        self.assertEqual(semantic_type(spec, True), ("option", True))

    def test_custom_multiselect_without_allowed_values_is_choice(self):
        spec = source({"type": "array", "items": "string",
                       "custom": "com.atlassian.jira.plugin.system.customfieldtypes:multiselect"})
        self.assertEqual(semantic_type(spec, True), ("option", True))

    def test_checkbox_and_single_select(self):
        checkbox = source({"type": "array", "items": "string",
                           "custom": "com.atlassian.jira.plugin.system.customfieldtypes:checkboxes"})
        select = source({"type": "string",
                         "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select"})
        self.assertEqual(semantic_type(checkbox, True), ("option", True))
        self.assertEqual(semantic_type(select, True), ("option", False))

    def test_freeform_string_array_is_not_choice(self):
        spec = source({"type": "array", "items": "string"})
        self.assertEqual(semantic_type(spec, True), ("text", True))

    def test_jira_textarea_is_single_text(self):
        spec = source({"type": "string",
                       "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textarea"})
        self.assertEqual(semantic_type(spec, True), ("text", False))
        self.assertEqual(convert("one\ntwo", "text", {}), "one\ntwo")

    def test_choice_option_user_reference_preserve_cardinality(self):
        cases = [
            ({"type": "array", "items": "user"}, ("user", True)),
            ({"type": "array", "items": "component"}, ("option", True)),
            ({"type": "array", "items": "issue"}, ("reference", True)),
            ({"type": "array", "items": "version"}, ("option", True)),
            ({"type": "array", "items": "status"}, ("status", True)),
        ]
        for schema, expected in cases:
            with self.subTest(schema=schema):
                self.assertEqual(semantic_type(source(schema), True), expected)

    def test_unknown_type_is_not_assigned_arbitrarily(self):
        kind, multi, explanation = jira_field_semantics(source({"type": "array", "items": "unknown"}))
        self.assertIsNone(kind)
        self.assertTrue(multi)
        self.assertIn("unrecognized", explanation)

    def test_known_choice_does_not_map_to_text(self):
        target_text = {"typeName": "Text", "multipleSelection": False}
        target_choice = {"typeName": "Choice", "multipleSelection": True}
        self.assertFalse(compatible("option", True, target_text))
        self.assertTrue(compatible("option", True, target_choice))

    def test_choice_values_requires_mapped_ids(self):
        with self.assertRaises(ApiError):
            convert(["Low", "High"], "option", {"Low": {"id": 1}})
        self.assertEqual(convert(["Low", "High"], "option",
                                 {"Low": {"id": 1}, "High": {"id": 2}}),
                         [{"id": 1}, {"id": 2}])

    def test_choice_option_value_only_identity(self):
        from mapping_helper import source_choices, suggest_values
        source_options = [{"value": "Low"}, {"value": "High"}]
        self.assertEqual(source_choices(source_options), {"Low": "Low", "High": "High"})
        matches, missing = suggest_values(
            [["Low", "High"]], [{"id": 10, "name": "Low"}, {"id": 20, "name": "High"}]
        )
        self.assertEqual(missing, [])
        self.assertEqual(matches, {"Low": {"id": 10}, "High": {"id": 20}})
        self.assertEqual(
            convert([{"value": "Low"}, {"value": "High"}], "option", matches),
            [{"id": 10}, {"id": 20}],
        )

    def test_missing_or_conflicting_choice_metadata(self):
        unknown = source({"type": "array", "items": "custom_object"})
        self.assertEqual(semantic_type(unknown, True), (None, True))
        conflicting = source({"type": "array", "items": "user",
                              "custom": "com.atlassian.jira.plugin.system.customfieldtypes:multiselect"})
        self.assertEqual(semantic_type(conflicting, True), (None, True))

    def test_freeform_multitext_conversion_is_distinct(self):
        text_target = {"typeName": "Text", "multipleSelection": False}
        choice_target = {"typeName": "Choice", "multipleSelection": True}
        self.assertEqual(text_conversion("text", True, text_target), "text_multi")
        self.assertFalse(compatible("text", True, choice_target))
        self.assertEqual(convert(["alpha", "beta"], "text_multi", {}), "alpha\nbeta")

    def test_choice_creation_proposal_has_multiselect_options(self):
        spec = source({"type": "array", "items": "string"}, ["Low", "High"])
        analysis = {"fields": {"customfield_10000": spec},
                    "values": {"customfield_10000": [["Low", "High"]]}}
        config = {"migration": {}, "codebeamer": {"field_bindings": {}}}
        rows = proposals(analysis, [], config)
        row = rows["customfield_10000"]
        self.assertEqual(row["status"], "create")
        self.assertEqual(row["creation"]["semantic_type"], "option")
        self.assertTrue(row["creation"]["multipleSelection"])
        self.assertEqual(row["creation"]["choices"], {"Low": "Low", "High": "High"})


if __name__ == "__main__":
    unittest.main()
