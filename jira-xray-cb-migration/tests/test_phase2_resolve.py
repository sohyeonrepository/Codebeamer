import unittest
from phase2_resolve_test_sets import resolve_sets

class RelationResolveTest(unittest.TestCase):
    def test_dedupe_inverse_memberships(self):
        sets={"A-1":{"status":"VERIFIED","cb":100}}
        cases={"A-2":{"status":"VERIFIED","cb":200}}
        set_manifest={"A-1":{"relations":[{"relation":"set_contains_test","target_jira_key":"A-2"}]}}
        case_manifest={"A-2":{"relations":[{"relation":"test_in_set","target_jira_key":"A-1"}]}}
        ready,unresolved=resolve_sets(sets,cases,set_manifest,case_manifest)
        self.assertEqual(len(ready),1)
        self.assertEqual(ready[0]["test_set_cb_id"],100)
        self.assertEqual(ready[0]["test_case_cb_id"],200)
        self.assertEqual(unresolved,[])

    def test_incomplete_own_target_is_reported(self):
        ready,unresolved=resolve_sets({"A-1":{"status":"VERIFIED","cb":100}},{},
            {"A-1":{"relations":[{"relation":"set_contains_test","target_jira_key":"A-2"}]}},{})
        self.assertEqual(ready,[])
        self.assertEqual(unresolved[0]["reason"],"Missing verified Item ID")

    def test_test_plan_relation_not_mislabeled(self):
        ready,unresolved=resolve_sets({},{"A-2":{"status":"VERIFIED","cb":200}},
            {},{"A-2":{"relations":[{"relation":"test_in_plan","target_jira_key":"A-3"}]}})
        self.assertEqual(ready,[])
        self.assertEqual(unresolved[0]["reason"],"Not Test Set membership / not in scope")

if __name__=="__main__":
    unittest.main()
