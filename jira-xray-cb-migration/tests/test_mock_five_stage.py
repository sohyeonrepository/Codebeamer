"""Offline service E2E: real Wizard/Engine/SQLite, stateful mock API boundary.
Not a real Codebeamer schema certification or Tk interaction test.
"""
import unittest
from copy import deepcopy
from urllib.parse import urlsplit,parse_qs
from unittest.mock import Mock
from tests import test_import_wizard as fixtures
from clients.http import ApiError
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient

class MockJiraDCXray(fixtures.Source):
    def __init__(self):
        super().__init__()
        original=deepcopy(self.rows[0]);self.rows=[]
        self.types=[{'id':str(i),'name':name} for i,name in enumerate(('Test Plan','Test','Test Set','Test Execution'),1)]
        for kind in self.types:
            row=deepcopy(original);row.update(id=kind['id'],key='P-'+kind['id'])
            row['fields'].update(issuetype=kind,summary=kind['name'],issuelinks=[],attachment=[],description=None)
            self.rows.append(row)
        self.run={'id':77,'testKey':'P-2','testExecKey':'P-4','status':'PASS','steps':[{'action':'check','result':'PASS'}]}
    def request(self,method,path,*args,**kwargs):
        if path=='/rest/raven/1.0/api/testrun/77':return deepcopy(self.run)
        if path.startswith('/rest/api/2/search'):
            jql=parse_qs(urlsplit(path).query)['jql'][0]
            rows=[r for r in self.rows if '"'+r['fields']['issuetype']['id']+'"' in jql or 'issuetype' not in jql]
            return {'startAt':0,'total':len(rows),'issues':deepcopy(rows)}
        return super().request(method,path,*args,**kwargs)

class FiveStageE2E(unittest.TestCase):
    setUp=fixtures.WizardTests.setUp
    def test_four_categories_review_import_readback_restart(self):
        # Explicit legacy opt-in: this regression exercises four destination categories.
        self.config['migration'].setdefault('full_xray', {})['create_execution_items'] = True
        self.wizard.config['migration'].setdefault('full_xray', {})['create_execution_items'] = True
        self.source=MockJiraDCXray();self.wizard.jira=JiraClient(self.source,self.jira_config,self.state.save)
        self.wizard.discover();self.wizard.select_project('P',10)
        for typ in self.source.types:
            self.wizard.map_tracker('P',typ['id'],100)
            self.wizard.analyze('P:'+typ['id'])
            self.wizard.select_field('P:'+typ['id'],'empty',False)
        self.assertEqual(self.cb.mutations,[])
        with self.assertRaises(ApiError):self.wizard.execute(False)
        result=self.wizard.execute(True)
        self.assertTrue(result['complete'],result)
        self.assertEqual(len(self.cb.items),4)
        self.assertEqual({x['name'] for x in self.cb.items.values()},{'Test Plan','Test','Test Set','Test Execution'})
        count=len(self.cb.files)
        from migration.wizard import ImportWizard
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.config)
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(len(self.cb.items),4);self.assertEqual(len(self.cb.files),count)
    def test_xray_run_actual_client_validates_identity(self):
        source=MockJiraDCXray();execution=deepcopy(source.rows[-1])
        execution['fields']['xray']=[{'testRunId':77,'testKey':'P-2'}]
        runs=XrayClient(source).runs(execution,'xray')
        self.assertEqual(runs[0]['data']['steps'][0]['result'],'PASS')
        source.run['testExecKey']='P-999'
        with self.assertRaises(ApiError):XrayClient(source).runs(execution,'xray')
    def test_lost_commit_response_resumes_without_duplicate_comment(self):
        fixtures.WizardTests.plan(self);self.cb.lost_comment_response=True
        self.assertFalse(self.wizard.execute(True)['complete'])
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(len(self.cb.items),2)
        self.assertEqual(sum(r['kind']=='comment' for r in self.cb.records.values()),2)
    def test_timeout_during_analysis_has_no_writes(self):
        self.wizard.discover();self.wizard.select_project('P',10);self.wizard.map_tracker('P','1',100)
        self.source.request=Mock(side_effect=ApiError('Timeout'))
        with self.assertRaises(ApiError):self.wizard.analyze('P:1')
        self.assertEqual(self.cb.mutations,[])
    def test_duplicate_and_changed_total_pages_block(self):
        for pages in ([{'startAt':0,'total':2,'values':[{'id':1}]},{'startAt':1,'total':2,'values':[{'id':1}]}],
                      [{'startAt':0,'total':2,'values':[{'id':1}]},{'startAt':1,'total':1,'values':[]}],
                      [{'startAt':0,'total':2,'values':[]}]):
            http=Mock();http.request.side_effect=pages
            with self.assertRaises(ApiError):list(JiraClient(http,{}).pages('/data','values'))
    def test_run_sync_preserves_unselected_permissions(self):
        from migration.run_schema import sync_run_fields
        config={'fields':[{'id':1,'label':'existing','typeId':10,'permission':'Restricted'}]}
        class CB:
            def configuration(self,tracker):return deepcopy(config)
            def operation(self,name):return {}
            def call(self,name,params,body):config.update(deepcopy(body));return {}
        sync_run_fields(CB(),self.state,100,{'field_sync':{'enabled':True,'unrestricted_permission':'Unrestricted',
            'definitions':[{'label':'new','typeId':10}]}},True)
        self.assertEqual(config['fields'][0]['permission'],'Restricted')
        self.assertEqual(config['fields'][1]['permission'],'Unrestricted')
