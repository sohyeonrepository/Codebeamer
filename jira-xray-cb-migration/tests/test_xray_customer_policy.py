import unittest
from types import SimpleNamespace
from clients.http import ApiError
from clients.xray_client import XrayClient
from migration.xray_review import collect_review, field_group, issue_kind

class CustomerPolicyTests(unittest.TestCase):
    def test_plan_issue_cannot_be_relabelled_run(self):
        http=SimpleNamespace(request=lambda *args:self.fail('No request for invalid Run ID'))
        with self.assertRaises(ApiError):
            collect_review(None,XrayClient(http),'DEMO-2718','run')

    def test_issue_payload_not_accepted_as_run_detail(self):
        http=SimpleNamespace(request=lambda *args:{'id':'2718','key':'DEMO-2718','fields':{'summary':'Plan'}})
        with self.assertRaisesRegex(ApiError,'Run detail requires'):
            collect_review(None,XrayClient(http),'2718','run')

    def test_execution_aggregate_not_complete(self):
        issue={'id':'20','key':'DEMO-20','fields':{'issuetype':{'name':'Test Execution'},
              'environment':'SILS','PASS':481,'FAIL':19}}
        http=SimpleNamespace(request=lambda *args:[])
        result=collect_review(SimpleNamespace(config={},bundle=lambda key:issue),XrayClient(http),'DEMO-20','execution')
        self.assertFalse(result['complete'])
        self.assertEqual(result['runs'],[])
        self.assertIn('aggregate',result['errors'][0]['error'])
        self.assertEqual(result['issue']['fields']['environment'],'SILS')

    def test_execution_membership_collects_actual_runs_and_steps(self):
        calls=[];saved=[]
        def request(method,path):
            calls.append((method,path))
            if path.endswith('/testexec/DEMO-20/test'):
                return [{'testKey':'DEMO-10','runId':71}]
            if path.endswith('/testrun/71'):
                return {'id':71,'testKey':'DEMO-10','testExecKey':'DEMO-20','status':'PASS',
                        'startedOn':'2026-01-01','finishedOn':'2026-01-02','executedBy':'legacy'}
            if path.endswith('/testrun/71/step'):
                return [{'id':1,'actualResult':'observed','status':'PASS','defects':['DEMO-30']}]
            if path.endswith('/testrun/71/attachment'):
                return [{'id':2,'filename':'proof.png'}]
            raise AssertionError(path)
        http=SimpleNamespace(request=request)
        config={'run_discovery':{'verified':True,'unpaginated':True,'identity_key':'runId',
                'execution_issue_type':'Test Execution','run_id_key':'runId','test_key_key':'testKey'},
                'source_reads':{'run':{k:{'verified':True,'unpaginated':True,'identity_key':'id'}
                                     for k in ('test_run_steps','test_run_evidence')}}}
        client=XrayClient(http,config,lambda *args:saved.append(args))
        issue={'id':'20','key':'DEMO-20','fields':{'issuetype':{'name':'Test Execution'}}}
        runs=client.runs(issue,None)
        self.assertEqual(len(runs),1)
        self.assertEqual(runs[0]['data']['testKey'],'DEMO-10')
        result=collect_review(None,client,'71','run')
        self.assertTrue(result['complete'])
        self.assertFalse(result['target_migration_verified'])
        self.assertEqual(result['reads']['test_run_steps']['items'][0]['defects'],['DEMO-30'])
        self.assertTrue(all(method=='GET' for method,_ in calls))
        self.assertTrue(saved)

    def test_type_specific_relationship_reads_and_no_implicit_links(self):
        for kind,name,operations in [('test','Test',3),('set','Test Set',1),('plan','Test Plan',2)]:
            with self.subTest(kind=kind):
                calls=[]
                def request(method,path):
                    calls.append(path);return []
                issue={'key':'DEMO-1','fields':{'issuetype':{'name':name},'issuelinks':[{'id':'not-xray'}]}}
                result=collect_review(SimpleNamespace(config={},bundle=lambda key:issue),
                                      XrayClient(SimpleNamespace(request=request)),'DEMO-1',kind)
                self.assertEqual(len(calls),operations)
                self.assertFalse(result['complete']) # no verified pagination contract
                self.assertNotIn('not-xray',str(result['reads']))

    def test_field_sections_use_schema_not_customer_label(self):
        self.assertEqual(field_group('customfield_1',{'name':'TC Procedure','schema':{'type':'string'}}),'일반 사용자 정의 필드')
        self.assertEqual(field_group('reporter',{'schema':{'type':'user'}}),'User')
        self.assertEqual(field_group('due',{'schema':{'type':'date'}}),'Date / Datetime')
        self.assertEqual(field_group('comment',{}),'Comment / Attachment / History')
        self.assertEqual(issue_kind({'id':'42','name':'Customer TC'},{'full_xray':{'issue_types':{'test':'42'}}}),'test')

    def test_date_and_datetime_same_numeric_type_keep_separate_contracts(self):
        from migration.wizard import ImportWizard
        from unittest.mock import patch
        fields=[{'id':i,'label':kind,'typeName':kind,'typeId':3,'permission':{},
                 'reference':None,'options':None,'multipleSelection':False,'mandatory':False}
                for i,kind in ((10,'Date'),(11,'Datetime'))]
        wizard=object.__new__(ImportWizard)
        wizard.config={'migration':{}}
        wizard.plan={'scopes':{'P:1':{'tracker':100}}}
        wizard.cb=SimpleNamespace(spec={},config={},configuration=lambda _: {'fields':fields})
        wizard.state=SimpleNamespace(save=lambda *args:None)
        with patch('migration.wizard.target_fields',return_value=fields), \
             patch('clients.codebeamer_3007.configuration_field_contract'):
            profiles=wizard.creation_profiles('P:1')
        self.assertEqual(profiles['date']['type_name'],'Date')
        self.assertEqual(profiles['datetime']['type_name'],'Datetime')
        self.assertEqual(profiles['date']['value_contract'],'unverified')
        self.assertNotIn('id',profiles['date']['definition'])
