import unittest
from types import SimpleNamespace
from clients.http import ApiError
from clients.jira_client import JiraClient
from migration.wizard import ImportWizard
from import_wizard import issue_type_display

class WizardGlobalCatalogTests(unittest.TestCase):
    def wizard(self, denied=False):
        names=['Test Plan','Test Set','Test Execution','기능안전_SSR','SW-CR (Sub-task)']
        all_types=[{'id':str(i),'name':names[i] if i<5 else 'Custom '+str(i),'subtask':i%2==0} for i in range(100)]
        calls=[]
        def request(method,path):
            calls.append(path)
            if path.endswith('/project/P'): return {'issueTypes':all_types[:20]}
            if path.endswith('/issuetype'):
                if denied: raise ApiError('HTTP 403')
                return all_types
            raise AssertionError(path)
        wizard=object.__new__(ImportWizard)
        wizard.jira=JiraClient(SimpleNamespace(request=request),{})
        wizard.cb=SimpleNamespace(write=False,call=lambda name,args: {'id':10} if name=='project_get' else [{'id':100}])
        wizard.plan={'projects':{},'scopes':{}}
        wizard.config={'jira':{},'migration':{}}
        wizard.stage=lambda name,value:value
        wizard.select_project('P',10)
        return wizard,calls

    def test_100_definitions_20_applicable_rendered_with_identity(self):
        wizard,calls=self.wizard()
        rows=wizard.plan['projects']['P']['display_types']
        self.assertEqual(len(rows),100)
        self.assertEqual(sum(r['project_available'] for r in rows),20)
        displayed=[issue_type_display(r) for r in rows]
        self.assertEqual({r[0] for r in displayed},{str(i) for i in range(100)})
        self.assertEqual(sum(r[2]=='프로젝트 미적용' for r in displayed),80)
        self.assertEqual(displayed[0][1],'Sub-task')
        self.assertEqual(displayed[1][1],'Regular')
        self.assertEqual(rows[3]['name'],'기능안전_SSR')
        self.assertEqual(calls,['/rest/api/2/project/P','/rest/api/2/issuetype'])
        with self.assertRaisesRegex(ApiError,'프로젝트 미적용'):
            wizard.map_tracker('P','80',100)
        self.assertEqual(wizard.plan['scopes'],{})
        self.assertFalse(wizard.cb.write)

    def test_global_denied_keeps_project_list_with_visible_warning(self):
        wizard,_=self.wizard(denied=True)
        project=wizard.plan['projects']['P']
        self.assertEqual(len(project['display_types']),20)
        self.assertIn('403',project['catalog_warnings'][0])

    def test_unknown_subtask_not_mislabelled_regular(self):
        self.assertEqual(issue_type_display({'id':'1','project_available':True})[1],'미확인')

    def test_priority_failure_does_not_block_issue_type_read(self):
        calls=[]
        def request(method,path):
            calls.append(path)
            if path.endswith('/issuetype'):return [{'id':'1','name':'Test'}]
            raise ApiError('HTTP 403')
        result=JiraClient(SimpleNamespace(request=request),{}).global_catalog(['issue_types'])
        self.assertEqual(result['issue_types'][0]['id'],'1')
        self.assertEqual(calls,['/rest/api/2/issuetype'])
