import unittest
from unittest.mock import Mock

from clients.http import ApiError
from migration.option_batch import choice_rows, prepare_options, add_options, map_choice

MAPPING={"phase_1":[
    {"jira":"priority.name","cb":{"label":"Jira Priority","type":"Choice","registration":"choiceList[17]"},"mode":"mapped"},
    {"jira":"customfield_17552","cb":{"label":"Squad","type":"Choice / option","registration":"choiceList[13]"},"mode":"mapped"},
    {"jira":"components","cb":{"label":"Components","type":"Choice / work/config","registration":"choiceList[14]"},"mode":"mapped"},
    {"jira":"assignee","cb":{"label":"Assigned to","type":"Members","registration":"assignedTo"},"mode":"mapped"}
]}
DETAIL={"id":222,"type":"OptionChoiceField","valueModel":"ChoiceFieldValue<ChoiceOptionReference>",
        "referenceType":"ChoiceOptionReference","multipleValues":False,
        "options":[{"id":3,"name":"High","type":"ChoiceOptionReference"},
                   {"id":4,"name":"Low","type":"ChoiceOptionReference"}]}

class ChoiceTests(unittest.TestCase):
    def test_only_verified_option_kinds(self):
        self.assertEqual([r["jira"] for r in choice_rows(MAPPING)],["priority.name","customfield_17552"])

    def test_user_does_not_need_option_id(self):
        row=MAPPING["phase_1"][0]
        self.assertEqual(map_choice(row,DETAIL,{"priority":{"name":"High"}}),
                         [{"id":3,"name":"High","type":"ChoiceOptionReference"}])

    def test_alias_from_config(self):
        row=MAPPING["phase_1"][0]
        self.assertEqual(map_choice(row,DETAIL,{"priority":{"name":"Blocker"}},{"Blocker":"High"})[0]["id"],3)

    def test_missing_value_blocks(self):
        with self.assertRaises(ApiError):
            map_choice(MAPPING["phase_1"][0],DETAIL,{"priority":{"name":"Unknown"}})

    def test_null_omitted(self):
        self.assertIsNone(map_choice(MAPPING["phase_1"][0],DETAIL,{"priority":None}))

    def test_field_detail_contract(self):
        cb=Mock()
        cb.request.return_value=DETAIL
        rows={"phase_1":[MAPPING["phase_1"][0]]}
        resolved=prepare_options(cb,4879,[{"id":222,"label":"Jira Priority"}],rows)
        payload=add_options({"name":"Test"},{"priority":{"name":"High"}},rows,resolved)
        self.assertEqual(payload["customFields"],[{
            "fieldId":222,"type":"ChoiceFieldValue",
            "values":[{"id":3,"name":"High","type":"ChoiceOptionReference"}]}])

    def test_wrong_reference_type_blocks(self):
        cb=Mock()
        cb.request.return_value=dict(DETAIL,referenceType="UserReference")
        with self.assertRaises(ApiError):
            prepare_options(cb,4879,[{"id":222,"label":"Jira Priority"}],
                            {"phase_1":[MAPPING["phase_1"][0]]})


if __name__=="__main__":
    unittest.main()
