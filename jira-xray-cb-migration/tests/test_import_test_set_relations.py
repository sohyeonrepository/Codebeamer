import unittest
from unittest.mock import Mock
from clients.http import ApiError
from import_test_set_relations import discover_test_cases_table, build_table_update, table_member_ids

class TestSetTableTests(unittest.TestCase):
    def test_discover_unique_table(self):
        cb=Mock()
        cb.request.side_effect=[
            [{"id":1000,"name":"Test Cases"}],
            {"id":1000,"name":"Test Cases","type":"TableField","valueModel":"TableFieldValue",
             "columns":[{"id":1001,"name":"Test Case","type":"TrackerItemChoiceField",
                         "valueModel":"ChoiceFieldValue","referenceType":"TrackerItemReference"}]}
        ]
        self.assertEqual(discover_test_cases_table(cb,500),(1000,1001,"Test Cases"))

    def test_table_update_readback(self):
        update=build_table_update([201,202],1001)
        item={"customFields":[{"fieldId":1000,"type":"TableFieldValue",
                               "values":update["rows"]}]}
        self.assertEqual(table_member_ids(item,1000,1001),[201,202])

    def test_duplicate_ids_refused(self):
        with self.assertRaises(ApiError):
            build_table_update([201,201],1001)

    def test_invalid_table_blocked(self):
        cb=Mock()
        cb.request.side_effect=[
            [{"id":1000}],
            {"id":1000,"name":"Other Table","type":"TableField",
             "valueModel":"TableFieldValue","columns":[]}
        ]
        with self.assertRaises(ApiError):
            discover_test_cases_table(cb,500)

if __name__=="__main__":
    unittest.main()
