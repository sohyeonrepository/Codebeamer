"""Regression contracts for Jira core fields in the existing Wizard."""
import unittest

from migration.wizard import proposals, semantic_type


def cb_field(fid, name, kind, multi=False):
    return dict(id=fid, name=name, label=name, typeName=kind, typeId=fid+10,
                multipleSelection=multi, mandatory=False, hidden=False,
                options=None, reference=None)


def jira_field(fid, name, kind):
    return {"id": fid, "name": name, "schema": {"type": kind}}


class CoreFieldMappingTests(unittest.TestCase):
    def setUp(self):
        self.targets = [cb_field(1, "Name", "Text"),
                        cb_field(2, "Description", "Wikitext"),
                        cb_field(3, "Assigned to", "User")]
        self.analysis = {
            "fields": {
                "summary": jira_field("summary", "Summary", "string"),
                "description": jira_field("description", "Description", "string"),
                "assignee": jira_field("assignee", "Assignee", "user"),
            },
            "values": {"summary": ["Sample"], "description": ["Details"],
                       "assignee": [None]},
        }
        self.config = {"codebeamer": {"field_bindings": {}},
                       "migration": {"user_mapping": {}}}

    def test_summary_and_description_map_as_text_without_choice_options(self):
        result = proposals(self.analysis, self.targets, self.config)
        for key, target_id in (("summary", 1), ("description", 2)):
            with self.subTest(key=key):
                self.assertEqual(result[key]["status"], "mapped", result[key])
                self.assertEqual(result[key]["spec"]["field_id"], target_id)
                self.assertEqual(result[key]["spec"]["type"], "text")
                self.assertEqual(result[key]["spec"]["value_mapping"], {})
        self.assertEqual(semantic_type(self.analysis["fields"]["summary"], True), ("text", False))

    def test_assignee_is_user_not_choice_or_reference(self):
        self.assertEqual(semantic_type(self.analysis["fields"]["assignee"], True),
                         ("user", False))
        self.config["migration"]["user_mapping"] = {"legacy": {"id": 10}}
        self.analysis["values"]["assignee"] = [{"name": "legacy"}]
        result = proposals(self.analysis, self.targets, self.config)
        self.assertEqual(result["assignee"]["status"], "mapped", result["assignee"])
        self.assertEqual(result["assignee"]["spec"]["type"], "user")
        self.assertEqual(result["assignee"]["spec"]["field_id"], 3)

    def test_missing_user_identity_mapping_is_not_a_choice_request(self):
        self.analysis["values"]["assignee"] = [{"name": "legacy"}]
        result = proposals(self.analysis, self.targets, self.config)
        self.assertEqual(result["assignee"]["status"], "manual")
        self.assertIn("매핑", result["assignee"]["reason"])

    def test_missing_target_user_metadata_does_not_auto_map(self):
        targets = self.targets[:-1] + [dict(self.targets[-1], typeName="Unknown")]
        result = proposals(self.analysis, targets, self.config)
        self.assertNotEqual(result["assignee"]["status"], "mapped")


if __name__ == "__main__":
    unittest.main()

class ExtendedPolicyTests(unittest.TestCase):
    def plan(self, source, definition, target, values, migration=None):
        return proposals({'fields':{source:definition},'values':{source:values}}, target,
                         {'codebeamer':{'field_bindings':{}},'migration':migration or {}})[source]

    def test_due_date_existing_and_creation_plan(self):
        definition=jira_field('duedate','Due Date','date')
        row=self.plan('duedate',definition,[cb_field(991,'End Date','Date')],['2026-11-01'])
        self.assertEqual(row['spec']['field_id'],991)
        self.assertEqual(row['spec']['type'],'date')
        from migration.fields import payload
        self.assertEqual(payload({'duedate':'2026-11-01'},{'duedate':row['spec']},{})[row['spec']['target']], '2026-11-01')
        plan=self.plan('duedate',definition,[],['2026-11-01'])
        self.assertEqual(plan['creation']['semantic_type'],'date')
        self.assertEqual(plan['creation']['choices'],{})
        # This is a read-only proposal, not proof of a Date creation API.

    def test_priority_options_keep_original_identity(self):
        target=cb_field(902,'Priority','Choice');target['options']=[{'id':81,'name':'High'}]
        row=self.plan('priority',jira_field('priority','Priority','priority'),[target],[{'id':'4','name':'High'}])
        from migration.fields import payload
        self.assertEqual(payload({'priority':{'id':'4','name':'High'}},{'priority':row['spec']},{})[row['spec']['target']],{'id':81})

    def test_components_reference_requires_explicit_id_mapping_and_readback(self):
        from migration.system_fields import verify_reference_values
        from migration.fields import payload
        from clients.http import ApiError
        from types import SimpleNamespace
        definition={'id':'components','name':'Components','schema':{'type':'array','items':'component'}}
        target=cb_field(733,'Components','Reference',True);target['reference']={'trackerId':600}
        policy={'catalog_reference_mapping':{'components':{'field_id':733,'tracker_id':600,
                'target':'componentsRef','value_mapping':{'9':{'id':901}}}}}
        row=self.plan('components',definition,[target],[[{'id':'9','name':'Engine'}]],policy)
        self.assertEqual(row['status'],'mapped',row)
        self.assertEqual(payload({'components':[{'id':'9','name':'Engine'}]},{'components':row['spec']},{}),{'componentsRef':[{'id':901}]})
        verify_reference_values(SimpleNamespace(call=lambda *a:{'id':901,'tracker':{'id':600}}),row['spec'])
        with self.assertRaisesRegex(ApiError,'mismatch'):
            verify_reference_values(SimpleNamespace(call=lambda *a:{'id':901,'tracker':{'id':601}}),row['spec'])
        row=self.plan('components',definition,[target],[[{'id':'10','name':'Engine'}]],policy)
        self.assertNotEqual(row['status'],'mapped')

    def test_standard_fields_do_not_create_custom_lookalikes(self):
        for source,typ in [('summary','string'),('description','string'),('assignee','user')]:
            row=self.plan(source,jira_field(source,source,typ),[],[])
            self.assertNotIn('creation',row)

    def test_named_binding_not_connector_numeric_id(self):
        target=cb_field(12345,'Localized title','Text')
        row=proposals({'fields':{'summary':jira_field('summary','Summary','string')},'values':{'summary':['hello']}},
                      [target],{'codebeamer':{'field_bindings':{'name':{'fieldId':12345}}},'migration':{}})['summary']
        self.assertEqual(row['spec']['field_id'],12345)
        self.assertEqual(row['spec']['source_field_id'],'summary')

    def test_dedicated_and_xray_relationships_not_generic_fields(self):
        definition={'id':'customfield_2','name':'Plans','schema':{'type':'array','items':'string','custom':'raven:testplans'}}
        fields={'comment':jira_field('comment','Comment','string'),'customfield_2':definition}
        self.assertEqual(proposals({'fields':fields,'values':{}},[],{'migration':{},'codebeamer':{}}),{})

    def test_choice_controls_only_for_choice_semantics(self):
        from migration.system_fields import choice_input
        for kind in ('text','user','date','datetime','reference'):
            self.assertFalse(choice_input(kind))
        self.assertTrue(choice_input('option'))
        self.assertTrue(choice_input('status'))
