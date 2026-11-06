import unittest
from unittest.mock import Mock
from migration.selected_field_validation import validate_selected


class SelectedMappingValidationTests(unittest.TestCase):
    def setup_wizard(self):
        wizard=Mock()
        fields={
            "reporter":{"name":"Reporter"},
            "priority":{"name":"Priority"},
            "status":{"name":"Status"},
            "security":{"name":"Security Level"},
            "summary":{"name":"Summary"},
            "description":{"name":"Description"},
        }
        rows={
            "reporter":{"selected":True,"spec":{"field_id":16},"target":{"label":"Reporter"}},
            "priority":{"selected":True,"spec":{"field_id":17},"target":{"label":"Jira Priority"}},
            "status":{"selected":True,"spec":{"field_id":7},"target":{"label":"Status"}},
            "security":{"selected":True,"spec":{"field_id":1003},"target":{"label":"Security Level"}},
            "summary":{"selected":True},
            "description":{"selected":True},
        }
        wizard.plan={"scopes":{"STGVSTAR:14013":{
            "project":"STGVSTAR","tracker":500,
            "type":{"id":"14013","name":"Test Set"},
            "analysis":{"fields":fields},"rows":rows,
        }}}
        jira_fields={
            "project":{"key":"STGVSTAR"},"issuetype":{"id":"14013"},
            "reporter":{"name":"user1"},"priority":{"name":"High"},
            "status":{"name":"Open"},"security":{"name":"Level 1"},
            "summary":"Hello","description":"World",
        }
        wizard.jira.http.request.return_value={"key":"STGVSTAR-12","fields":jira_fields}
        def cb_get(method,path):
            if "/configuration" in path:
                return {"fields":[{"id":i} for i in (16,17,7,1003)]}
            if path.startswith("/v3/users/"):
                return {"id":12,"name":"user1"}
            for f,labels in [(16,[]),(17,["High"]),(7,["Open"]),(1003,["Level 1"])]:
                if path.endswith("/"+str(f)):
                    return {"id":f,"referenceType":"UserReference" if f==16 else "ChoiceOptionReference",
                            "valueModel":"ChoiceFieldValue",
                            "options":[{"id":i+1,"name":name} for i,name in enumerate(labels)]}
            raise AssertionError(path)
        wizard.cb.http.request.side_effect=cb_get
        return wizard

    def test_six_selected_fields_get_verified_no_post(self):
        wizard=self.setup_wizard()
        report=validate_selected(wizard,"STGVSTAR:14013","STGVSTAR-12")
        verdict={r["source"]:r["verdict"] for r in report["rows"]}
        self.assertEqual(verdict["summary"],"BASELINE")
        self.assertEqual(verdict["description"],"BASELINE")
        self.assertEqual({verdict[f] for f in ("reporter","priority","status","security")},{"GET_VERIFIED"})
        self.assertEqual(report["blocked"],0)
        self.assertEqual(report["writes"],0)
        self.assertFalse(report["post_ready"])
        for call in wizard.jira.http.request.call_args_list+wizard.cb.http.request.call_args_list:
            self.assertEqual(call.args[0],"GET")

    def test_missing_priority_option_blocks(self):
        wizard=self.setup_wizard()
        original=wizard.cb.http.request.side_effect
        def cb_get(method,path):
            if path.endswith("/17"):
                return {"id":17,"referenceType":"ChoiceOptionReference",
                        "valueModel":"ChoiceFieldValue","options":[]}
            return original(method,path)
        wizard.cb.http.request.side_effect=cb_get
        report=validate_selected(wizard,"STGVSTAR:14013","STGVSTAR-12")
        verdict={r["source"]:r["verdict"] for r in report["rows"]}
        self.assertEqual(verdict["priority"],"BLOCKED")
        self.assertFalse(report["post_ready"])

    def test_null_reporter_does_not_require_target_user(self):
        wizard=self.setup_wizard()
        wizard.jira.http.request.return_value["fields"]["reporter"]=None
        report=validate_selected(wizard,"STGVSTAR:14013","STGVSTAR-12")
        verdict={r["source"]:r["verdict"] for r in report["rows"]}
        self.assertEqual(verdict["reporter"],"SKIP_NULL")


if __name__=="__main__":
    unittest.main()
