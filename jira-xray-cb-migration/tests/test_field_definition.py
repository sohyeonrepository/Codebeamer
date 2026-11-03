import unittest
from copy import deepcopy
from types import SimpleNamespace
from clients.field_definition import plan_definition
from clients.http import ApiError

class DefinitionTests(unittest.TestCase):
    def client(self, required=True, **identifier):
        field={'type':'object','required':['referenceId'] if required else [],
               'properties':{'referenceId':dict(type='integer',**identifier)}}
        schema={'type':'object','properties':{'fields':{'type':'array','items':field}}}
        return SimpleNamespace(operation=lambda _: {'method':'POST','path':'/v3/tracker/configuration'},
            spec={'paths':{'/v3/tracker/configuration':{'post':{'requestBody':{'content':{'application/json':{'schema':schema}}}}}}})
    def test_required_schema_bounds_allocate_without_modifying_baseline(self):
        baseline={'fields':[{'referenceId':41,'permission':{'type':'restricted'}}]}
        before=deepcopy(baseline)
        result=plan_definition(self.client(minimum=41,maximum=43),{'label':'Jira Text'},baseline)
        self.assertEqual(result,{'label':'Jira Text','referenceId':42})
        self.assertEqual(before,baseline)
    def test_server_assigned_does_not_invent_id(self):
        self.assertEqual(plan_definition(self.client(False),{'label':'Text'},{'fields':[]}),{'label':'Text'})
    def test_no_range_no_guess(self):
        with self.assertRaisesRegex(ApiError,'unverified'):plan_definition(self.client(),{'label':'Text'},{'fields':[]})
    def test_exhaustion_and_copied_id_blocked(self):
        with self.assertRaisesRegex(ApiError,'No unused'):plan_definition(self.client(enum=[41]),{'label':'Text'},{'fields':[{'referenceId':41}]})
        with self.assertRaisesRegex(ApiError,'copy'):plan_definition(self.client(),{'referenceId':41},{'fields':[]})
    def test_readonly_required_id_blocked(self):
        with self.assertRaisesRegex(ApiError,'writable'):plan_definition(self.client(readOnly=True),{}, {'fields':[]})
    def test_text_definition_post_and_readback_preserve_permissions(self):
        import tempfile
        from pathlib import Path
        from mapping_helper import create_confirmed_field
        cb=self.client(minimum=41,maximum=43)
        baseline={'basicInformation':{'trackerId':10},'fields':[{'referenceId':41,'typeId':0,'label':'Existing','permission':{'type':'restricted'}}]}
        actual=deepcopy(baseline);writes=[]
        cb.write=False
        cb.configuration=lambda _:deepcopy(actual)
        def post(name,params,body):
            writes.append(deepcopy(body));actual.clear();actual.update(deepcopy(body));return {}
        cb.call=post
        definition={'typeId':0,'label':'Jira Text','permission':{'type':'UNRESTRICTED'},'mandatory':False,'multipleSelection':False,'options':None,'reference':None}
        with tempfile.TemporaryDirectory() as directory:
            journal=Path(directory)/'creation.json'
            with self.assertRaises(ApiError):create_confirmed_field(cb,10,definition,baseline,journal)
            self.assertEqual(writes,[])
            created,result=create_confirmed_field(cb,10,definition,baseline,journal,approved=True)
            self.assertEqual(created['referenceId'],42)
            self.assertEqual(result['fields'][0],baseline['fields'][0])
            with self.assertRaises(ApiError):create_confirmed_field(cb,10,definition,result,journal,approved=True)
            self.assertEqual(len(writes),1)
            self.assertFalse(cb.write)
