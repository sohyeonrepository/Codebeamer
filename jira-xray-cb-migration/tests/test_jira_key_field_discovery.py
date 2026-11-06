import unittest
from unittest.mock import Mock
from clients.http import ApiError
from migration.simple_item_import import _field


class JiraKeyFieldDiscoveryTests(unittest.TestCase):
    def cb(self, configured, listed):
        cb=Mock()
        cb.configuration.return_value={"fields":configured}
        cb.fields.return_value=listed
        return cb

    def test_name_missing_configuration_present_field_list(self):
        cb=self.cb([{"id":74,"typeName":"Text"}],
                   [{"id":74,"name":"Jira Key","typeName":"Text",
                     "valueModel":"TextFieldValue","multipleValues":False}])
        self.assertEqual(_field(cb,500),74)

    def test_configuration_and_list_same_id_not_duplicate(self):
        cb=self.cb([{"id":74,"label":"Jira Key","typeName":"Text"}],
                   [{"id":74,"name":"Jira Key","valueModel":"TextFieldValue"}])
        self.assertEqual(_field(cb,500),74)

    def test_wikitext_is_supported(self):
        cb=self.cb([], [{"id":74,"name":"Jira Key",
                         "typeName":"Wikitext","valueModel":"WikiTextFieldValue"}])
        self.assertEqual(_field(cb,500),74)

    def test_wikitext_link_duplicate_uses_original_key(self):
        from migration.jira_identity_lookup import canonical_jira_identity, item_identity
        value="[STGVSTAR-12|https://jira.example/browse/STGVSTAR-12]"
        self.assertEqual(canonical_jira_identity(value),"STGVSTAR-12")
        item={"id":1001,"customFields":[{"fieldId":74,"type":"WikiTextFieldValue",
                                         "value":value}]}
        self.assertEqual(item_identity(item,74),"STGVSTAR-12")

    def test_unknown_wikitext_blocks_duplicate_scan(self):
        from migration.jira_identity_lookup import canonical_jira_identity
        with self.assertRaisesRegex(ApiError,"JIRA_LOOKUP_IDENTITY_FORMAT"):
            canonical_jira_identity("[unrelated link|https://jira.example/wiki]")

    def test_missing_field_message_includes_observed_counts(self):
        cb=self.cb([{"id":71,"label":"Name"}],[{"id":71,"name":"Name"}])
        with self.assertRaisesRegex(ApiError,"Configuration 필드=1"):
            _field(cb,500)

if __name__=="__main__":
    unittest.main()
