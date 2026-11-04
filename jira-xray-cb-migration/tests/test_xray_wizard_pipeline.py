"""Synthetic DC HTTP -> real Wizard/Engine -> stateful Mock CB -> GET readback.
No server-specific REST schema or real Windows GUI compatibility is asserted.
"""
import unittest
from copy import deepcopy
from clients.http import ApiError
from clients.jira_client import JiraClient
from import_wizard import issue_type_display
from migration.wizard import ImportWizard
from tests import test_import_wizard as base
from tests.test_mock_five_stage import MockJiraDCXray


def fields(*names):
    return {name:{'target':name,'type':'raw'} for name in names}

class Source(MockJiraDCXray):
    def __init__(self):
        super().__init__()
        self.rows[-1]['fields'].update(xray=[{'testRunId':77,'testKey':'P-2'}],
            created='2019-01-01T00:00:00Z',creator={'name':'legacy'},environment='SILS')
        extra=[base.definition('customfield_tc','TC Procedure'), base.definition('reporter','Reporter','user')]
        self.definitions.extend(extra);self.configured.extend(dict(f,hidden=False) for f in extra)
        for row in self.rows:
            row['fields'].update(customfield_tc='Procedure\nExpected result' if row['key']=='P-2' else None,
                                 reporter={'name':'legacy'} if row['key']=='P-3' else None)
        self.run.pop('steps')
        self.calls=[]
    def request(self,method,path,*args,**kwargs):
        self.calls.append((method,path))
        assert method=='GET'
        if path.endswith('/comment') or path.endswith('/worklog') or '/comment?' in path or '/worklog?' in path:
            return {'startAt':0,'total':0,'comments':[],'worklogs':[]}
        pages={
            '/rest/raven/1.0/api/testplan/P-1/test':[{'key':'P-2'}],
            '/rest/raven/1.0/api/testplan/P-1/testexecution':[{'key':'P-4'}],
            '/rest/raven/1.0/api/testset/P-3/test':[{'key':'P-2'}],
            '/rest/raven/1.0/api/test/P-2/step':[{'id':11,'action':'measure','expectedResult':'within range'}],
            '/rest/raven/1.0/api/testrun/77/step':[{'id':21,'status':'PASS','actualResult':'within range','defects':[]}],
            '/rest/raven/1.0/api/testrun/77/attachment':[{'id':'proof','filename':'proof.png','size':3,'content':'/binary'}],
        }
        if path in pages:return deepcopy(pages[path])
        return super().request(method,path,*args,**kwargs)

class Target(base.Target):
    def __init__(self):
        super().__init__();self.native={};self.calls=[]
        self.definitions.extend([base.field(55,'TC Procedure'),dict(base.field(56,'Submitter'),typeName='User')])
    def call(self,name,params=None,data=None,**kwargs):
        params=params or {};self.calls.append((name,deepcopy(params)))
        for category in ('run','run_step','test_step','relation'):
            if name==category+'_lookup':
                return [{'id':i} for i,row in self.native.items() if row['category']==category and
                        row['source']==params['sourceId']]
            if name==category+'_create':
                if not self.write:raise ApiError('Mock write refused')
                identity=self.current_source(category,params)
                i=len(self.native)+1000
                self.native[i]={'category':category,'source':identity,'params':deepcopy(params),'data':deepcopy(data)}
                return {'id':i}
            if name==category+'_get':return deepcopy(self.native[params['recordId']]['data'])
            if name==category+'_update':
                if not self.write:raise ApiError('Mock write refused')
                self.native[params['recordId']]['data']=deepcopy(data);return {'id':params['recordId']}
        return super().call(name,params,data,**kwargs)
    def current_source(self,category,params):
        # The prior exact external-ID lookup is part of this mock operation contract.
        return next(p['sourceId'] for name,p in reversed(self.calls) if name==category+'_lookup')

class XrayWizardPipelineTests(unittest.TestCase):
    setUp=base.WizardTests.setUp
    def prepare(self):
        self.source=Source();self.cb=Target()
        self.config['migration'].update(mode='full-xray',history_replay=False,xray_run_tracker=200,user_mapping={'legacy':{'id':701}})
        relation_fields=fields('targetId','relationKind')
        self.config['migration']['full_xray']={
            'issue_types':{'plan':'1','test':'2','set':'3','execution':'4'},
            'relationships':[
                {'from':'plan','to':'test','path':'_xray_dc.test_plan_tests.items','identity_path':'key'},
                {'from':'set','to':'test','path':'_xray_dc.test_set_tests.items','identity_path':'key'},
                {'from':'plan','to':'execution','path':'_xray_dc.test_plan_executions.items','identity_path':'key'}]}
        self.config['migration']['relation_adapter']={'fields':relation_fields}
        self.config['migration']['plan_run_adapter']={'name':'planned-run','fields':relation_fields}
        self.config['migration']['run_adapter']={
            'test_issue_id_path':'testKey','execution_mode':'source_metadata','activity_inventory_verified':True,
            'fields':fields('id','testKey','testExecKey','status','_testItem','_executionItem','_executionSource')}
        self.config['migration']['run_step_adapter']={'fields':fields('id','status','actualResult','defects')}
        self.config['migration']['test_step_adapter']={'fields':fields('id','action','expectedResult')}
        self.jira_config['source_reads']={kind:{op:{'verified':True,'unpaginated':True,'identity_key':identity}
            for op,identity in ops} for kind,ops in {
                'plan':[('test_plan_tests','key'),('test_plan_executions','key')],
                'set':[('test_set_tests','key')], 'test':[('test_steps','id')],
                'run':[('test_run_steps','id'),('test_run_evidence','id')]}.items()}
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.config)
        self.wizard.discover();self.wizard.select_project('P',10)
        shown=self.wizard.plan['projects']['P']['display_types']
        self.assertTrue(all(issue_type_display(t)[2]=='프로젝트 사용 가능' for t in shown))
        for typ in self.source.types:
            if typ['id']=='4':self.wizard.map_execution_context('P','4')
            else:self.wizard.map_tracker('P',typ['id'],100)
            self.wizard.analyze('P:'+typ['id'])
            if typ['id']!='4':
                self.wizard.select_field('P:'+typ['id'],'empty',False)
        self.assertEqual(self.cb.mutations,[])
        self.assertFalse(self.cb.native)

    def test_wizard_full_import_steps_evidence_relations_readback_and_resume(self):
        self.prepare()
        self.assertIsNone(self.wizard.plan['scopes']['P:4']['tracker'])
        self.assertEqual(self.wizard.plan['scopes']['P:4']['rows'],{})
        report=self.wizard.execute(True,visibility_review=self.wizard.visibility_review())
        self.assertTrue(report['complete'],report)
        self.assertEqual(len(self.cb.items),3) # no target Execution Item
        case=self.cb.items[self.state.get('item:P-2')[1]['id']]
        test_set=self.cb.items[self.state.get('item:P-3')[1]['id']]
        self.assertEqual(case['cb_field_55'],'Procedure\nExpected result')
        self.assertEqual(test_set['cb_field_56'],{'id':701})
        self.assertEqual(report['counts']['verified_runs'],1)
        self.assertEqual(report['counts']['relationships'],3)
        self.assertEqual(len(report['runs'][0]['verified_step_ids']),1)
        self.assertEqual(report['runs'][0]['evidence_count'],1)
        run=next(row for row in self.cb.native.values() if row['category']=='run')
        self.assertEqual(run['data']['_testItem']['id'],self.state.get('item:P-2')[1]['id'])
        self.assertEqual(run['data']['_executionSource']['key'],'P-4')
        self.assertEqual(run['data']['_executionSource']['fields']['environment'],'SILS')
        self.assertEqual(run['data']['_executionSource']['legacy_history']['total'],1)
        self.assertEqual(len(report['source_target_map']),3)
        self.assertEqual(run['data']['_executionSource']['relationships'][0]['source'],'P-1')
        relations=[r for r in self.cb.native.values() if r['category']=='relation']
        self.assertEqual(len(relations),3)
        self.assertNotIn('set:run',{r['data']['relationKind'] for r in relations})
        self.assertTrue(any(f['filename']=='proof.png' and f['binary']==b'abc' for f in self.cb.files.values()))
        self.assertTrue(any(name=='run_step_get' for name,_ in self.cb.calls))
        self.assertTrue(any(name=='test_step_get' for name,_ in self.cb.calls))
        count=(len(self.cb.items),len(self.cb.native),len(self.cb.files))
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.wizard.config)
        self.assertTrue(self.wizard.execute(True,visibility_review=self.wizard.visibility_review())['complete'])
        self.assertEqual(count,(len(self.cb.items),len(self.cb.native),len(self.cb.files)))

    def test_missing_step_adapter_blocks_before_target_item_creation(self):
        self.prepare()
        self.wizard.config['migration'].pop('run_step_adapter')
        with self.assertRaisesRegex(ApiError,'run_step_adapter'):
            self.wizard.execute(True,visibility_review=self.wizard.visibility_review())
        self.assertFalse(self.cb.items)
        self.assertFalse(self.cb.native)
        self.assertFalse(self.cb.mutations)

    def test_unverified_security_level_is_not_turned_into_cb_permission(self):
        self.prepare()
        definition=base.definition('security','Security Level','securitylevel',required=True,hidden=False)
        self.source.definitions.append(definition);self.source.configured.append(definition)
        for issue in self.source.rows:
            issue['fields']['security']={'id':'700','name':'Synthetic restricted'} if issue['key']=='P-3' else None
        for typ in self.source.types:self.wizard.analyze('P:'+typ['id'])
        with self.assertRaises(ApiError):
            self.wizard.execute(True,visibility_review=self.wizard.visibility_review())
        self.assertFalse(self.cb.items)
        self.assertFalse(self.cb.mutations)
