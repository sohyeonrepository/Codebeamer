import unittest
from copy import deepcopy
from clients.jira_client import JiraClient
from migration.wizard import field_analysis, proposals, ImportWizard
from tests import test_import_wizard as base
from tests.test_system_field_mapping_contract import cb_field, jira_field

class FieldMappingRepairTests(unittest.TestCase):
    setUp=base.WizardTests.setUp

    def test_field_configuration_preserves_screen_schema_options_and_required(self):
        self.source.definitions.append({'id':'squad','name':'Squad'})
        self.source.configured.append({'id':'squad','hidden':False})
        original=self.source.request
        def request(method,path,*args,**kwargs):
            if '/createmeta?' in path:
                return {'projects':[{'key':'P','issuetypes':[{'id':'1','fields':{'squad':{
                    'name':'Actual Squad','schema':{'type':'option'},'required':True,
                    'allowedValues':[{'id':'7','value':'A'}]}}}]}]}
            return original(method,path,*args,**kwargs)
        self.source.request=request
        field=field_analysis(self.wizard.jira,'P',self.source.types[0])['fields']['squad']
        self.assertEqual(field['schema'],{'type':'option'})
        self.assertEqual(field['name'],'Actual Squad')
        self.assertTrue(field['required'])
        self.assertEqual(field['allowedValues'][0]['id'],'7')

    def test_unproven_empty_fields_not_selected_but_reviewable(self):
        self.wizard.jira.config.pop('field_configuration')
        self.wizard.discover();self.wizard.select_project('P',10)
        self.wizard.map_tracker('P','1',100)
        entry=self.wizard.analyze('P:1')
        self.assertIn('empty',entry['rows'])
        self.assertFalse(entry['rows']['empty']['selected'])
        self.assertFalse(entry['rows']['summary']['selected']) # UI requires explicit selection.
        self.assertFalse(entry['analysis']['fields']['empty']['configured'])
        self.wizard.select_field('P:1','empty',True)
        self.assertTrue(self.wizard.analyze('P:1')['rows']['empty']['selected'])

    def test_configured_null_field_is_kept(self):
        self.wizard.discover();self.wizard.select_project('P',10);self.wizard.map_tracker('P','1',100)
        entry=self.wizard.analyze('P:1')
        self.assertIn('empty',entry['rows'])
        self.assertFalse(entry['rows']['empty']['selected'])
        self.assertTrue(entry['analysis']['fields']['empty']['configured'])

    def test_text_existing_field_needs_no_choice_or_reference_definition(self):
        target=cb_field(501,'Name','Text');target.pop('options');target.pop('reference')
        row=proposals({'fields':{'summary':jira_field('summary','Summary','string')},'values':{'summary':['Title']}},
                      [target],{'migration':{},'codebeamer':{}})['summary']
        self.assertEqual(row['status'],'mapped')
        self.assertNotIn('missing_definition',row['spec'])
        self.assertEqual(row['spec']['value_mapping'],{})

    def test_unobserved_choice_and_explicit_status_mapping_do_not_block(self):
        target=cb_field(502,'Status','Choice');target['options']=[{'id':99,'name':'CB accepted'}]
        definition=jira_field('status','Status','status')
        definition['allowedValues']=[{'id':'2','name':'Unused'}]
        row=proposals({'fields':{'status':definition},'values':{'status':[{'id':'1','name':'Jira open'}]}},
                      [target],{'migration':{'status_mapping':{'1':{'id':99},'2':{'id':88}}},'codebeamer':{}})['status']
        self.assertEqual(row['status'],'mapped',row)
        invalid=deepcopy(row['spec']);invalid['value_mapping']['1']={'id':88}
        changed=proposals({'fields':{'status':definition},'values':{'status':[{'id':'1','name':'Jira open'}]}},
                          [target],{'migration':{},'codebeamer':{}},{'status':invalid})['status']
        self.assertIn('선택지 ID',changed['spec']['selection_unverified'])

    def test_users_match_observed_usernames_not_display_name_only(self):
        target=cb_field(503,'Assigned to','User');target['options']=[{'id':42,'name':'legacy'}]
        analysis={'fields':{'assignee':jira_field('assignee','Assignee','user')},
                  'values':{'assignee':[{'accountId':'original','name':'legacy','displayName':'Different display'}]}}
        result=proposals(analysis,[target],{'migration':{},'codebeamer':{}})['assignee']
        self.assertEqual(result['spec']['value_mapping'],{'original':{'id':42}})
        analysis['values']['assignee'][0].update(name='other',displayName='legacy')
        self.assertEqual(proposals(analysis,[target],{'migration':{},'codebeamer':{}})['assignee']['status'],'manual')

    def test_date_plan_uses_verified_template_and_imports_without_popup(self):
        class DateTarget(base.Target):
            def discover(self):
                super().discover()
                self.spec['components']={'schemas':{'DateFieldValue':{'type':'object','properties':{
                    'type':{'enum':['DateFieldValue']},'value':{'type':'string','format':'date'}}}}}
                return []
            def call(self,name,params=None,data=None,**kwargs):
                result=super().call(name,params,data,**kwargs)
                if name=='helper_field_create' and data['typeId']==71:
                    self.definitions[-1].update(typeName='Date',valueModel='DateFieldValue')
                return result
        self.cb=DateTarget()
        self.cb.definitions.append(dict(base.field(77,'Server date template'),typeName='Date',typeId=71,
                                        permission={'type':'UNRESTRICTED'},valueModel='DateFieldValue'))
        new=base.definition('customfield_date','Release Date','date')
        self.source.definitions.append(new);self.source.configured.append(dict(new,hidden=False))
        for row in self.source.rows:row['fields']['customfield_date']='2026-11-04'
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.config)
        self.wizard.discover();self.wizard.select_project('P',10)
        for typ in self.source.types:
            self.wizard.map_tracker('P',typ['id'],100)
            entry=self.wizard.analyze('P:'+typ['id'])
            suggested=proposals(entry['analysis'],entry['target_fields'],self.wizard.config)
            for source,row in suggested.items():
                if row.get('spec'):
                    spec=row['spec']
                    self.wizard.manual_mapping('P:'+typ['id'],source,spec['field_id'],spec['target'],spec.get('value_mapping',{}))
                    self.wizard.select_field('P:'+typ['id'],source,True)
            self.wizard.configure_creation('P:'+typ['id'],'customfield_date','Release Date','date',False,{})
            self.wizard.select_field('P:'+typ['id'],'customfield_date',True)
            profile=self.wizard._creation_profile_for_tracker('P:'+typ['id'],entry['rows']['customfield_date'],100)
            self.assertEqual(profile['source_tracker_id'],100)
            self.assertEqual(profile['type_name'],'Date')
        self.assertFalse(self.cb.mutations)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertEqual(len([f for f in self.cb.definitions if f['label']=='Release Date']),1)
        self.assertTrue(all(item['jira_customfield_date']=='2026-11-04' for item in self.cb.items.values()))
