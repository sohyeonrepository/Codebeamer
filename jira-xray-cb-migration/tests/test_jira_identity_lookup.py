"""Offline behavior checks for the Java-inspired Jira Key lookup.

Run: python -m unittest discover -s tests -p 'test_jira_identity_lookup.py'
"""
import unittest
from unittest.mock import Mock
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


if __name__ == "__main__":
    unittest.main()
