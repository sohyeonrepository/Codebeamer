import unittest
from clients.codebeamer_client import CodebeamerClient
from clients.http import ApiError
from clients.tracker_fields import read, binding
from migration.wizard import semantic_type, compatible

class TrackerFieldsTests(unittest.TestCase):
    def client(self):
        class HTTP:
            def __init__(self): self.calls=[]
            def request(self, method, path):
                self.calls.append((method,path))
                if path.endswith('/configuration'):
                    return {'basicInformation':{'trackerId':20}, 'fields':[
                        {'id':1001,'label':'Old label','typeId':8,'mandatory':False,
                         'multipleSelection':False,'reference':None,'options':[],
                         'permission':{'type':'UNRESTRICTED'}}]}
                if path.endswith('/fields'):return [{'id':1001,'name':'User custom choice'}]
                if path.endswith('/fields/1001'):
                    return {'id':1001,'name':'User custom choice','type':'OptionChoiceField',
                            'hidden':False,'valueModel':'ChoiceFieldValue<ChoiceOptionReference>',
                            'multipleValues':False,'options':[{'id':1,'name':'First option'}],
                            'referenceType':'ChoiceOptionReference'}
                raise ApiError('HTTP 404')
        return CodebeamerClient(HTTP(),{},False)

    def test_documented_detail_shape_and_selected_tracker_get_only(self):
        cb=self.client(); field=read(cb,20)[0]
        self.assertEqual(field['label'],'User custom choice')
        self.assertEqual(semantic_type(field),('option',False))
        self.assertTrue(compatible('option',False,field))
        self.assertFalse(compatible('option',True,field))
        self.assertEqual(field['options'][0]['id'],1)
        self.assertEqual(field['typeId'],8)
        self.assertFalse(field['visibility_unknown'])
        self.assertTrue(all(m=='GET' and '/20/' in p for m,p in cb.http.calls))
        self.assertIsNone(cb.spec)

    def test_detail_failure_retains_names_and_reports_unknown(self):
        cb=self.client(); original=cb.http.request
        def fail(method,path):
            if path.endswith('/1001'):raise ApiError('HTTP 403')
            return original(method,path)
        cb.http.request=fail
        field=read(cb,20)[0]
        self.assertEqual(field['label'],'User custom choice')
        self.assertTrue(field['visibility_unknown'])
        self.assertIn('HTTP 403',cb.config['tracker_field_evidence']['20']['warnings'][0])

    def test_value_model_requires_writable_schema_and_discriminator(self):
        field=read(self.client(),20)[0]
        self.assertIsNone(binding(None,field))
        spec={'components':{'schemas':{'ChoiceFieldValue':{'type':'object','properties':{
            'type':{'enum':['ChoiceFieldValue']}, 'values':{'type':'array'}}}}}}
        self.assertEqual(binding(spec,field),{'fieldId':1001,'type':'ChoiceFieldValue','value_property':'values'})
        spec['components']['schemas']['ChoiceFieldValue']['properties']['type']={'type':'string'}
        self.assertIsNone(binding(spec,field))

    def test_creation_types_come_from_server_configuration_and_schema(self):
        import tempfile
        from migration.state import State
        from migration.wizard import ImportWizard
        cb=self.client()
        model = {'type':'object', 'properties':{'type':{'enum':['ChoiceFieldValue']}, 'values':{'type':'array'}}}
        schema = {'type':'object','properties':{'basicInformation':{'type':'object'}, 'fields':{'type':'array','items':{'type':'object'}}}}
        operation = {'requestBody':{'content':{'application/json':{'schema':schema}}}}
        cb.spec = {'components':{'schemas':{'ChoiceFieldValue':model}}, 'paths':{'/v3/tracker/configuration':{'post':operation}}}
        cb.config['operations']={'tracker_configuration_update':{'method':'POST','path':'/v3/tracker/configuration'}}
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            wizard=ImportWizard(None,cb,state,{'jira':{'url':'https://jira.test'},
                'codebeamer':{'url':'https://cb.test'},'migration':{}})
            wizard.plan['scopes']['P:1']={'tracker':20,'rows':{}}
            profiles=wizard.creation_profiles('P:1')
            self.assertEqual(profiles['option']['definition']['typeId'],8)
            self.assertEqual(profiles['option']['binding']['type'],'ChoiceFieldValue')
            self.assertNotIn('fieldId',profiles['option']['binding'])
            cb.spec['components']['schemas'] = {}
            wizard.config['migration']['wizard']['field_types'] = {}
            profiles = wizard.creation_profiles('P:1')
            self.assertEqual(profiles['option']['definition_contract'],'verified')
            self.assertEqual(profiles['option']['value_contract'],'unverified')
            self.assertIsNone(profiles['option']['binding'])
            self.assertFalse(cb.write)
            self.assertTrue(all(method=='GET' for method,_ in cb.http.calls))
            state.db.close()
