import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from clients.http import ApiError
from clients.codebeamer_client import CodebeamerClient
from clients.codebeamer_verified_reads import projects,project,trackers,tracker,fields,configuration,load_successful_openapi
from migration.state import State


class HTTP:
    base='https://cb.test/cb/api'
    def __init__(self):self.calls=[]
    def request(self,method,path,*args,**kwargs):
        self.calls.append((method,path))
        responses={
            '/v3/projects':[{'id':10,'name':'Project'}],
            '/v3/projects/10':{'id':10,'name':'Project'},
            '/v3/projects/10/trackers':[{'id':20,'name':'Tracker'}],
            '/v3/trackers/20':{'id':20,'name':'Tracker','project':{'id':10}},
            '/v3/trackers/20/fields':[{'id':1,'label':'Name','typeName':'Text'}],
            '/v3/tracker/20/configuration':{'fields':[{'id':1,'label':'Name','hidden':False}]}}
        if path not in responses:raise ApiError('HTTP 404')
        return responses[path]


class VerifiedReadRegressionTests(unittest.TestCase):
    def test_successful_read_sequence_is_get_only_and_never_discovers_swagger(self):
        http=HTTP();cb=CodebeamerClient(http,{'auth':'basic','operations':{}},False)
        self.assertEqual(projects(cb)[0]['id'],10)
        self.assertEqual(project(cb,10)['id'],10)
        self.assertEqual(trackers(cb,10)[0]['id'],20)
        self.assertEqual(tracker(cb,20,10)['project']['id'],10)
        self.assertEqual(fields(cb,20)[0]['label'],'Name')
        self.assertEqual(configuration(cb,20)['fields'][0]['id'],1)
        self.assertTrue(all(method=='GET' for method,_ in http.calls))
        self.assertFalse(any('swagger' in path or 'api-docs' in path for _,path in http.calls))
        self.assertEqual(cb.config['verified_read_operations'],{
            'projects_get':'/v3/projects','project_get':'/v3/projects/{projectId}',
            'trackers_get':'/v3/projects/{projectId}/trackers','tracker_get':'/v3/trackers/{trackerId}',
            'fields':'/v3/trackers/{trackerId}/fields',
            'tracker_configuration_get':'/v3/tracker/{trackerId}/configuration'})

    def test_invalid_or_partial_response_is_not_accepted(self):
        http=HTTP();http.request=lambda method,path:{'items':[{'id':10}]}
        cb=CodebeamerClient(http,{'operations':{}},False)
        with self.assertRaisesRegex(ApiError,'response contract mismatch'):projects(cb)

    def test_previous_successful_openapi_is_reused_only_for_same_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory)/'single-import')
            state.mark('single-import-binding','done',{'jira':'https://jira.test','codebeamer':'https://cb.test',
                'issue_key':'P-1','project_id':10,'tracker_id':20})
            spec={'openapi':'3.0.0','paths':{'/v3/trackers/{trackerId}/items':{'post':{}}}}
            source=state.save('discovery','single-openapi',spec);state.db.close()
            loaded=load_successful_openapi(Path(directory)/'import-wizard','https://jira.test/','https://cb.test/')
            self.assertEqual(loaded,(spec,str(source)))
            self.assertIsNone(load_successful_openapi(Path(directory)/'import-wizard','https://other.test','https://cb.test'))

    def test_missing_openapi_blocks_write_contract_without_any_write(self):
        from migration.wizard import ImportWizard
        cb=CodebeamerClient(HTTP(),{'operations':{},'verified_reads':True},False)
        jira=type('Jira',(),{})()
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            wizard=ImportWizard(jira,cb,state,{'jira':{'url':'https://jira.test'},
                'codebeamer':{'url':'https://cb.test'},'migration':{}})
            with patch.object(cb,'discover',side_effect=ApiError('server document unavailable')):
                with self.assertRaisesRegex(ApiError,'Item 생성 POST.*Attachment.*server document unavailable'):
                    wizard.ensure_write_contracts()
            self.assertFalse(cb.write);state.db.close()

    def test_item_write_contract_records_actual_post_get_and_schema(self):
        from migration.wizard import ImportWizard
        spec={'openapi':'3.0.0','paths':{
            '/v3/trackers/{trackerId}/items':{'post':{
                'requestBody':{'content':{'application/json':{'schema':{'type':'object','properties':{
                    'name':{'type':'string'},'description':{'type':'string'}}}}}},
                'responses':{'201':{'content':{'application/json':{'schema':{'type':'object','properties':{'id':{'type':'integer'}}}}}}}}},
            '/v3/items/{itemId}':{'get':{'responses':{'200':{}}}}}}
        cb=CodebeamerClient(HTTP(),{'operations':{
            'item_create':{'method':'POST','path':'/v3/trackers/{trackerId}/items'},
            'item_get':{'method':'GET','path':'/v3/items/{itemId}'}},'reused_successful_openapi':'saved.json'},False)
        cb.spec=spec
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory);wizard=ImportWizard(type('Jira',(),{})(),cb,state,{
                'jira':{'url':'https://jira.test'},'codebeamer':{'url':'https://cb.test'},'migration':{}})
            result=wizard.ensure_write_contracts()
            self.assertEqual(result['item_create']['method'],'POST')
            self.assertEqual(result['item_get']['method'],'GET')
            self.assertEqual(result['request_properties'],['description','name'])
            self.assertIsNotNone(state.load('wizard','write-contracts'));state.db.close()


if __name__=='__main__':unittest.main()
