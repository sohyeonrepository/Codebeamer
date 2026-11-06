import unittest
from unittest.mock import Mock
from clients.http import ApiError
from migration.simple_item_import import _supported_custom_field_values


class FirstImportFieldTests(unittest.TestCase):
    def test_selected_text_and_wikitext(self):
        entry={"rows":{
            "summary":{"selected":True},
            "customfield_19807":{"selected":True,"spec":{"field_id":75}},
            "customfield_19806":{"selected":True,"spec":{"field_id":15}},
        }}
        target=[
            {"id":75,"valueModel":"TextFieldValue","typeName":"Text"},
            {"id":15,"valueModel":"WikiTextFieldValue","typeName":"Wikitext"},
        ]
        source={"summary":"A","customfield_19807":"Lab",
                "customfield_19806":"Acceptance"}
        actual=_supported_custom_field_values(entry,target,source,999)
        self.assertEqual([(x["fieldId"],x["type"]) for x in actual],
                         [(75,"TextFieldValue"),(15,"WikiTextFieldValue")])
        self.assertEqual(actual[0]["value"],"Lab")

    def test_null_is_not_sent(self):
        entry={"rows":{"customfield_19807":{"selected":True,
                "spec":{"field_id":75}}}}
        actual=_supported_custom_field_values(entry,[{"id":75,"valueModel":"TextFieldValue"}],
                                              {"customfield_19807":None},999)
        self.assertEqual(actual,[])

    def test_selected_choice_is_not_silently_discarded(self):
        entry={"rows":{"priority":{"selected":True,"spec":{"field_id":17}}}}
        with self.assertRaisesRegex(ApiError,"priority"):
            _supported_custom_field_values(entry,
                [{"id":17,"valueModel":"ChoiceFieldValue","typeName":"Choice"}],
                {"priority":{"name":"High"}},999)

    def test_choice_option_reference_mapped(self):
        entry={"rows":{"labels":{"selected":True,
                "spec":{"field_id":18,"value_mapping":{"alpha":"ALPHA","beta":"BETA"}}}}}
        target=[{"id":18,"valueModel":"ChoiceFieldValue",
                 "referenceType":"ChoiceOptionReference","multipleValues":True,
                 "options":[{"id":101,"name":"ALPHA","type":"ChoiceOptionReference"},
                            {"id":102,"name":"BETA","type":"ChoiceOptionReference"}]}]
        actual=_supported_custom_field_values(entry,target,
                                               {"labels":["alpha","beta"]},999)
        self.assertEqual(actual,[{"fieldId":18,"type":"ChoiceFieldValue",
                                  "values":[{"id":101,"name":"ALPHA","type":"ChoiceOptionReference"},
                                            {"id":102,"name":"BETA","type":"ChoiceOptionReference"}]}])

    def test_unmapped_choice_blocks_write(self):
        entry={"rows":{"priority":{"selected":True,
                "spec":{"field_id":17,"value_mapping":{}}}}}
        target=[{"id":17,"valueModel":"ChoiceFieldValue",
                 "referenceType":"ChoiceOptionReference","multipleValues":False,
                 "options":[{"id":1,"name":"High","type":"ChoiceOptionReference"}]}]
        with self.assertRaisesRegex(ApiError,"매핑이 없습니다"):
            _supported_custom_field_values(entry,target,
                                           {"priority":{"name":"High"}},999)

    def test_mapping_missing_fails(self):
        entry={"rows":{"customfield_19806":{"selected":True,"spec":None}}}
        with self.assertRaisesRegex(ApiError,"customfield_19806"):
            _supported_custom_field_values(entry,[],
                {"customfield_19806":"Acceptance"},999)

    def test_identity_conflict_fails(self):
        entry={"rows":{"customfield_19807":{"selected":True,
                                             "spec":{"field_id":99}}}}
        with self.assertRaisesRegex(ApiError,"Jira Key"):
            _supported_custom_field_values(entry,
                [{"id":99,"valueModel":"TextFieldValue"}],
                {"customfield_19807":"Lab"},99)

if __name__=="__main__":
    unittest.main()
