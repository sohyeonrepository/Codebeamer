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

    def test_wikitext_is_present_but_not_supported(self):
        cb=self.cb([], [{"id":74,"name":"Jira Key",
                         "typeName":"Wikitext","valueModel":"WikiTextFieldValue"}])
        with self.assertRaisesRegex(ApiError,"존재합니다"):
            _field(cb,500)

    def test_missing_field_message_includes_observed_counts(self):
        cb=self.cb([{"id":71,"label":"Name"}],[{"id":71,"name":"Name"}])
        with self.assertRaisesRegex(ApiError,"Configuration 필드=1"):
            _field(cb,500)

if __name__=="__main__":
    unittest.main()
