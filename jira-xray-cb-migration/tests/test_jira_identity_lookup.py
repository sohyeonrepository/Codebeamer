"""Offline behavior checks for the Java-inspired Jira Key lookup.

Run: python -m unittest discover -s tests -p 'test_jira_identity_lookup.py'
"""
import unittest
from unittest.mock import Mock, patch
from clients.http import ApiError
from migration.jira_identity_lookup import lookup_by_jira_key, item_identity


def item(id_, key=None):
    fields = [] if key is None else [{"fieldId":17,"type":"TextFieldValue","value":key}]
    return {"id":id_, "customFields":fields}


class IdentityLookupTest(unittest.TestCase):
    def fake_cb(self, pages, details):
        cb = Mock()
        cb.http.request.side_effect = lambda method,path: pages[path.split("?")[1].split("&")[0]]
        cb.call.side_effect = lambda name,params: details[params["itemId"]]
        cb.last_api_call = None
        return cb

    def test_exact_match_with_full_pagination(self):
        cb=self.fake_cb({"page=1":{"itemRefs":[{"id":11}],"total":2},
                         "page=2":{"itemRefs":[{"id":12}],"total":2}},
                        {11:item(11,"OTHER-2"),12:item(12,"STGVSTAR-3")})
        self.assertEqual(lookup_by_jira_key(cb,500,"STGVSTAR-3",17,page_size=1),[{"id":12}])

    def test_missing_custom_fields_is_not_an_empty_match(self):
        with self.assertRaises(ApiError):
            item_identity({"id":14},17)

    def test_duplicate_jira_keys_are_rejected(self):
        cb=self.fake_cb({"page=1":{"itemRefs":[{"id":11},{"id":12}],"total":2}},
                        {11:item(11,"TEST-1"),12:item(12,"TEST-1")})
        with self.assertRaisesRegex(ApiError,"JIRA_LOOKUP_DUPLICATE"):
            lookup_by_jira_key(cb,500,"TEST-1",17,page_size=2)

    def test_incomplete_pages_cannot_claim_no_match(self):
        cb=self.fake_cb({"page=1":{"items":[],"total":4}}, {})
        with self.assertRaisesRegex(ApiError,"JIRA_LOOKUP_INCOMPLETE"):
            lookup_by_jira_key(cb,500,"TEST-1",17)

    def test_missing_item_response_shape_cannot_claim_no_match(self):
        cb=self.fake_cb({"page=1":{"total":0}}, {})
        with self.assertRaisesRegex(ApiError,"JIRA_LOOKUP_PAGE_UNVERIFIED"):
            lookup_by_jira_key(cb,500,"TEST-1",17)

    def test_empty_tracker_exactly_zero_total(self):
        cb=self.fake_cb({"page=1":{"itemRefs":[],"total":0}}, {})
        self.assertEqual(lookup_by_jira_key(cb,500,"TEST-1",17),[])

    def test_text_creation_profile_without_discriminator_enum(self):
        from migration.jira_identity_lookup import text_creation_profile
        cb=Mock()
        cb.spec={"components":{"schemas":{"TextFieldValue":{"type":"object",
                    "properties":{"type":{"type":"string"},
                                  "value":{"type":"string"}}}}}}
        cb.configuration.return_value={"basicInformation":{"trackerId":500},"fields":[
            {"id":17,"label":"Legacy ID","typeId":12,"permission":{},
             "mandatory":False,"multipleSelection":False,"options":None,"reference":None}]}
        fields=[{"id":17,"name":"Legacy ID","typeId":12,"typeName":"Text",
                 "valueModel":"TextFieldValue","multipleSelection":False}]
        template=[{"name":"Text","definition":{"typeId":12,"permission":{},
                   "mandatory":False,"multipleSelection":False,"options":None,"reference":None}}]
        with patch("migration.wizard.semantic_type",return_value=("text",False)), \
             patch("mapping_helper.creation_templates",return_value=template), \
             patch("clients.tracker_fields.binding",return_value=None), \
             patch("clients.codebeamer_3007.configuration_field_contract",return_value={}):
            profile=text_creation_profile(cb,500,fields)
        self.assertEqual(profile["binding"]["type"],"TextFieldValue")
        self.assertEqual(profile["binding"]["value_property"],"value")

    def test_conflicting_discriminator_rejected(self):
        from migration.jira_identity_lookup import text_creation_profile
        cb=Mock()
        cb.spec={"components":{"schemas":{"TextFieldValue":{"type":"object",
                    "properties":{"type":{"type":"string","enum":["NumberFieldValue"]},
                                  "value":{"type":"string"}}}}}}
        cb.configuration.return_value={"basicInformation":{"trackerId":500},"fields":[
            {"id":17,"label":"Legacy ID","typeId":12,"permission":{}}]}
        fields=[{"id":17,"name":"Legacy ID","typeId":12,"typeName":"Text",
                 "valueModel":"TextFieldValue","multipleSelection":False}]
        with patch("migration.wizard.semantic_type",return_value=("text",False)), \
             patch("clients.tracker_fields.binding",return_value=None):
            with self.assertRaisesRegex(ApiError,"JIRA_IDENTITY_CREATE_UNVERIFIED"):
                text_creation_profile(cb,500,fields)

    def test_java_style_name_based_fieldvalue_readback(self):
        actual={"id":14,"customFields":[{"name":"Jira Key","type":"TextFieldValue","value":"TEST-14"}]}
        self.assertEqual(item_identity(actual,17),"TEST-14")

    def test_identity_field_accepts_verified_text_binding_when_server_omits_details(self):
        from migration.jira_identity_lookup import identity_field
        field={"id":42,"name":"Jira Key","typeName":"Text","multipleSelection":False}
        cb=Mock(spec=["spec"])
        cb.spec={"components":{"schemas":{}}}
        actual=identity_field(cb,[field],{"type":"TextFieldValue","value_property":"value"})
        self.assertEqual(actual["field_id"],42)
        self.assertEqual(actual["binding"]["fieldId"],42)



if __name__ == "__main__":
    unittest.main()
