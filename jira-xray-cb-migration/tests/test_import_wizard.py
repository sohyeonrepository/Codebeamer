"""Wizard coverage uses the actual Engine, SQLite journal and binary transfer path."""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import patch
from clients.http import ApiError
from clients.jira_client import JiraClient
from migration.state import State
from migration.wizard import ImportWizard, field_analysis, proposals
from migration.preservation import rewrite_description


def field(fid, label, kind='Text', multiple=False, **extra):
    return dict(id=fid, label=label, typeName=kind, typeId=10, mandatory=False,
                multipleSelection=multiple, options=None, reference=None, **dict({'hidden':False},**extra))


def definition(fid, name, kind='string', **extra):
    return dict(id=fid, name=name, schema={'type': kind}, **extra)


class Source:
    def __init__(self):
        self.definitions = [definition('summary','Summary'), definition('description','Description'),
                            definition('empty','Empty'), definition('hidden','Hidden',hidden=True),
                            dict(definition('xray','Xray','array'), schema={'type':'array','custom':'raven:testexec'})]
        self.configured = [dict(f, hidden=f.get('hidden',False)) for f in self.definitions[:4]]
        self.types = [{'id':'1','name':'Task'}, {'id':'2','name':'Empty type'}]
        self.rows = []
        self.reads = []
        for number in (1,2):
            self.rows.append({'id':str(number), 'key':'P-'+str(number), 'fields': {
                'issuetype':self.types[0], 'project':{'key':'P'}, 'summary':'Item '+str(number),
                'description':'Image !image.png!' if number == 1 else None, 'empty':None,
                'hidden':None, 'xray':[], 'attachment': ([{'id':'a1','filename':'image.png',
                    'size':3,'content':'/binary'}] if number == 1 else []),
                'issuelinks':[{'id':'l1','type':{'id':'blocks','name':'Blocks','inward':'is blocked by','outward':'blocks'},
                              ('outwardIssue' if number==1 else 'inwardIssue'): {'id':str(3-number),'key':'P-'+str(3-number)}}]},
                'changelog':{'startAt':0,'total':1,'histories':[{'id':'h'+str(number),
                    'created':'2020-01-01T00:00:00Z','author':{'name':'original'},
                    'items':[{'fieldId':'summary','fromString':'old','toString':'Item '+str(number)}]}]}})
    def request(self, method, path, data=None, raw=False, **kwargs):
        self.reads.append((method,path))
        assert method == 'GET', 'Analysis must never write Jira'
        if path == '/binary': return b'abc'
        if path == '/rest/api/2/field': return deepcopy(self.definitions)
        if path == '/rest/api/2/project': return [{'key':'P'}]
        if path == '/rest/api/2/project/P': return {'key':'P','issueTypes':self.types}
        if path.startswith('/field-config/'):
            return deepcopy(self.configured)
        if path.startswith('/r4j/revisions/'):
            return [{'id':'rev','author':'original','created':'2020-01-01','fields':{'summary':'old'}}]
        if path.startswith('/r4j/baselines/'):
            return [{'id':'baseline','name':'Original baseline','revision':'rev'}]
        if path.startswith('/rest/api/2/search'):
            from urllib.parse import parse_qs, urlsplit
            jql = parse_qs(urlsplit(path).query)['jql'][0]
            rows = [] if '"2"' in jql else self.rows
            return {'startAt':0,'total':len(rows),'issues':deepcopy(rows)}
        for row in self.rows:
            base = '/rest/api/2/issue/' + row['key']
            if path.startswith(base+'/comment'):
                return {'startAt':0,'total':1,'comments':[{'id':'c1','body':'Full comment\nsecond line',
                      'author':{'name':'legacy'},'created':'2019-01-01','updated':'2019-01-02'}]}
            if path.startswith(base+'/worklog'): return {'startAt':0,'total':0,'worklogs':[]}
            if path.startswith(base+'?'): return deepcopy(row)
        raise AssertionError(path)


class Target:
    def __init__(self):
        self.write = False
        self.http = type('HTTP', (), {'base':'https://cb.test'})()
        self.config = {'operations':{},'field_bindings':{'empty':{'fieldId':3,'type':'TextFieldValue','value_property':'value'}}}
        self.tracker_rows = [{'id':100,'name':'Existing'}]
        self.definitions = [field(1,'Name'),field(2,'Description'),field(3,'Empty'),field(4,'Hidden CB',hidden=True)]
        self.items, self.files, self.records = {}, {}, {}
        self.mutations = []
        self.fail_comment = False
        self.lost_comment_response = False
        self.discover()
    def discover(self):
        schema = {'type':'object','properties':{'label':{'type':'string'},'typeId':{'type':'integer'},
            'multipleSelection':{'type':'boolean'},'options':{'type':'array'},'reference':{},'mandatory':{}},
            'required':['label','typeId','multipleSelection']}
        self.spec = {'paths':{'/v3/trackers/{trackerId}/fields':{'post':{'requestBody':{'content':{
                'application/json':{'schema':schema}}}}}}}
        return []
    def fields(self, tracker): return deepcopy(self.definitions)
    def configuration(self, tracker): return {'fields':deepcopy(self.definitions)}
    def operation(self, name):
        if name == 'field_create':
            return {'path':'/v3/trackers/{trackerId}/fields','method':'POST'}
        if name == 'tracker_create': return {'path':'/projects/{projectId}/trackers','method':'POST'}
        return {'path':'/'+name,'method':'GET' if name.endswith(('_get','_lookup')) or name=='lookup' else 'POST'}
    def call(self, name, params=None, data=None, raw=False, headers=None):
        p = params or {}
        method = self.operation(name)['method']
        if method != 'GET':
            if not self.write: raise ApiError('Write refused')
            self.mutations.append((name,deepcopy(p)))
        if name=='projects_get': return [{'id':10,'name':'Existing project'}]
        if name=='project_get': return {'id':p['projectId']}
        if name=='tracker_get': return {'id':p['trackerId']}
        if name=='trackers_get': return deepcopy(self.tracker_rows)
        if name=='tracker_lookup': return [r for r in self.tracker_rows if r.get('sourceKey')==p['sourceKey']]
        if name=='tracker_create':
            assert p['projectId']==10 and data['project']['id']==10
            result=dict(data,id=101);self.tracker_rows.append(result);return result
        if name=='helper_field_create':
            created = field(10,data['label'],multiple=data['multipleSelection'])
            created.update(data)
            if data.get('typeId')==20:
                created['typeName']='Choice'
                created['options']=[dict(o,id=100+i) for i,o in enumerate(data.get('options',[]))]
            self.definitions.append(created);return {'id':10}
        if name=='lookup': return [dict(v,id=k) for k,v in self.items.items() if v.get('sourceId')==p['sourceId']]
        if name=='item_create':
            iid=len(self.items)+200;self.items[iid]=deepcopy(dict(data,sourceId=p['sourceId']));return {'id':iid}
        if name=='item_update': self.items[p['itemId']].update(data);return {'id':p['itemId']}
        if name=='item_get': return deepcopy(dict(self.items[p['itemId']],id=p['itemId'],tracker={'id':100}))
        if name=='attachment_lookup':
            return [{'id':k} for k,v in self.files.items() if v['item']==p['itemId'] and v['sourceId']==p['sourceId']]
        if name=='attachment_upload':
            message=BytesParser(policy=default).parsebytes(('Content-Type: '+headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+data)
            part=next(message.iter_parts());binary=part.get_payload(decode=True)
            aid=len(self.files)+300
            self.files[aid]={'binary':binary,'filename':part.get_filename(),'size':len(binary),'item':p['itemId'],'sourceId':p['sourceId']}
            return {'id':aid}
        if name=='attachment_get': return self.files[p['attachmentId']]['binary']
        if name=='attachment_metadata_get': return self.files[p['attachmentId']]
        if name=='attachment_url_get': return {'url':'https://cb.test/files/'+str(p['attachmentId'])}
        for category in ('comment','association'):
            if name==category+'_lookup':
                return [{'id':k} for k,v in self.records.items() if v['kind']==category and v['data']['sourceId']==p['sourceId']]
            if name==category+'_create':
                if category=='comment' and self.fail_comment:
                    self.fail_comment=False
                    raise ApiError('Simulated uncertain request')
                rid=len(self.records)+400;self.records[rid]={'kind':category,'data':deepcopy(data)}
                if category=='comment' and self.lost_comment_response:
                    self.lost_comment_response=False;raise ApiError('Lost response after server commit')
                return {'id':rid}
            if name==category+'_get': return deepcopy(self.records[p['recordId']]['data'])
            if name==category+'_update':
                self.records[p['recordId']]['data'].update(deepcopy(data));return {'id':p['recordId']}
        raise AssertionError(name)


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=State(self.tmp.name);self.addCleanup(self.state.db.close)
        self.source=Source();self.cb=Target()
        self.jira_config={'url':'https://jira.test','field_configuration':{
            'path':'/field-config/{projectKey}/{issueTypeId}','pagination':'complete'}}
        self.config={'jira':self.jira_config,'codebeamer':dict(self.cb.config,url='https://cb.test'),
            'migration':{'mode':'issues','history_replay':True,'field_mapping':{
                'P:1':{'summary':dict(target='name',type='text',field_id=1,expected_type_id=10,expected_mandatory=False,
                expected_multiple=False,expected_options=None,expected_reference=None,value_mapping={},sourceId='unused')}},
                'r4j':{c:{'path':'/r4j/'+c+'/{issueKey}','pagination':'complete'} for c in ('revisions','baselines')},
                'wizard':{'association_types':{'blocks':7},'tracker_template_id':100,'field_types':{'text':{'type_name':'Text','definition':{'typeId':10,'mandatory':False,
                    'multipleSelection':False,'reference':None,'options':None},
                    'binding':{'type':'TextFieldValue','value_property':'value'}}}}}}
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.config)
    def plan(self, new=False):
        self.wizard.discover();self.wizard.select_project('P',10)
        for kind in self.source.types:
            self.wizard.map_tracker('P',kind['id'],None if new else 100,'New '+kind['name'])
            self.wizard.analyze('P:'+kind['id'])
    def test_analysis_is_read_only_and_all_types_include_empty_type(self):
        self.plan()
        self.assertEqual(len(self.wizard.plan['scopes']),2)
        analysis=self.wizard.plan['scopes']['P:1']['analysis']
        self.assertIn('empty',analysis['fields'])
        self.assertNotIn('hidden',analysis['fields'])
        for special in ('attachment','comment','issuelinks','project','issuetype'):
            self.assertNotIn(special,analysis['fields'])
        self.assertFalse(self.cb.mutations)
        with self.assertRaisesRegex(ApiError,'approval'):self.wizard.execute()
        self.assertFalse(self.cb.mutations)
    def test_scope_and_hidden_value_risks_block_import(self):
        self.source.rows[0]['fields']['hidden']='must not lose'
        self.plan()
        self.assertTrue(self.wizard.plan['scopes']['P:1']['analysis']['risks'])
        with self.assertRaises(ApiError):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
        with self.assertRaises(ApiError):self.wizard.map_tracker('P','1',999)
    def test_null_custom_field_creates_proposal_and_no_type_guess(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['empty']['status'],'create')
        self.source.configured.append(definition('table','Steps','table',hidden=False))
        self.wizard.analyze('P:1')
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['table']['status'],'error')
    def test_collision_and_type_conflict_are_not_automatic(self):
        self.plan();a=self.wizard.plan['scopes']['P:1']['analysis']
        a['fields']['title']=definition('title','Title');a['values']['title']=['x']
        rows=proposals(a,self.cb.definitions,self.config)
        self.assertEqual(rows['summary']['status'],'manual')
        self.assertEqual(rows['title']['status'],'manual')
        fields=[dict(f,typeName='Integer') if f['id']==1 else f for f in self.cb.definitions]
        self.assertNotEqual(proposals(a,fields,self.config)['summary']['status'],'mapped')
    def test_configuration_adapter_is_required_instead_of_editmeta_guess(self):
        with self.assertRaisesRegex(ApiError,'Field Configuration'):
            field_analysis(JiraClient(self.source,{}),'P',self.source.types[0])
    def test_full_import_preserves_history_comments_binary_and_directed_link(self):
        self.plan()
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertEqual(len(self.cb.items),2)
        self.assertEqual(report['legacy_history'][0]['history_source'],1)
        self.assertEqual(report['legacy_history'][0]['history_preserved'],1)
        self.assertEqual(report['legacy_history'][0]['r4j_baselines_preserved'],1)
        self.assertIn('https://cb.test/files/',self.cb.items[200]['description'])
        archives=[json.loads(v['binary']) for v in self.cb.files.values() if v['filename'].endswith('.json')]
        self.assertEqual(archives[0]['history'][0]['author'],{'name':'original'})
        self.assertFalse(archives[0]['native_author_restored'])
        comments=[v for v in self.cb.records.values() if v['kind']=='comment']
        self.assertEqual(len(comments),2)
        self.assertEqual(comments[0]['data']['body'],'Full comment\nsecond line')
        associations_=[v for v in self.cb.records.values() if v['kind']=='association']
        self.assertEqual(len(associations_),1)
        self.assertEqual((associations_[0]['data']['fromItemId'],associations_[0]['data']['toItemId']),(200,201))
        report2=self.wizard.execute(True)
        self.assertTrue(report2['complete'],report2)
        self.assertEqual(len(self.cb.items),2)
        self.assertEqual(len(self.cb.records),3)
        self.assertEqual(len(self.cb.files),3)
    def test_creation_happens_only_after_approval_and_uses_actual_field_id(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertEqual([f['id'] for f in self.cb.definitions if f['label']=='Empty'],[10])
        self.assertTrue(any(name=='helper_field_create' for name,_ in self.cb.mutations))
        self.assertEqual(self.cb.config['field_bindings']['jira_empty']['fieldId'],10)
    def test_new_tracker_is_created_in_selected_existing_project(self):
        # Use one type because the fake target shares field definitions across trackers.
        self.source.types=self.source.types[:1]
        self.plan(new=True)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        new=self.cb.tracker_rows[-1]
        self.assertEqual(new['project'],{'id':10})
        self.assertEqual(new['sourceKey'],'P:1')
    def test_failed_source_scan_or_changed_values_prevents_all_writes(self):
        self.plan();self.source.rows[0]['fields']['summary']='changed'
        with self.assertRaisesRegex(ApiError,'changed'):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
    def test_missing_r4j_configuration_blocks_before_target_write(self):
        self.plan();self.wizard.config['migration'].pop('r4j')
        with self.assertRaisesRegex(ApiError,'r4j'):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
    def test_lost_comment_response_reconciles_on_retry_without_duplicate(self):
        self.plan();self.cb.lost_comment_response=True
        self.assertFalse(self.wizard.execute(True)['complete'])
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(sum(v['kind']=='comment' for v in self.cb.records.values()),2)
    def test_ambiguous_inlined_filename_and_missing_images_fail(self):
        with self.assertRaisesRegex(ApiError,'Ambiguous'):
            rewrite_description('!a.png!',[{'id':'1','filename':'a.png','content':'/one'},
                {'id':'2','filename':'a.png','content':'/two'}],{'1':'https://cb/a','2':'https://cb/b'})
        with self.assertRaises(ApiError):rewrite_description('!missing.png!',[],{})
    def test_multiselect_choice_creation_preserves_options_and_uses_real_ids(self):
        source={'id':'choices','name':'Choices','schema':{'type':'array','items':'option'},
                'hidden':False,'allowedValues':[{'id':'a','value':'One'},{'id':'b','value':'Two'}]}
        self.source.definitions.append(source);self.source.configured.append(source)
        self.source.rows[0]['fields']['choices']=[{'id':'a','value':'One'}]
        self.source.rows[1]['fields']['choices']=None
        self.wizard.config['migration']['wizard']['field_types']['option']={
            'type_name':'Choice','definition':{'typeId':20,'mandatory':False,
              'multipleSelection':True,'options':[],'reference':None},
            'binding':{'type':'ChoiceFieldValue','value_property':'values'}}
        self.plan()
        row=self.wizard.plan['scopes']['P:1']['rows']['choices']
        self.assertTrue(row['creation']['multipleSelection'])
        self.assertEqual(set(row['creation']['choices'].values()),{'One','Two'})
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        actual=next(f for f in self.cb.definitions if f['label']=='Choices')
        self.assertEqual(actual['typeName'],'Choice')
        self.assertTrue(actual['multipleSelection'])
        self.assertEqual([o['name'] for o in actual['options']],['One','Two'])
        self.assertEqual(self.cb.items[200]['jira_choices'],[{'id':100}])

    def test_changed_comment_updates_existing_target_without_duplicate(self):
        self.plan();self.assertTrue(self.wizard.execute(True)['complete'])
        original=self.source.request
        def request(method,path,*args,**kwargs):
            result=original(method,path,*args,**kwargs)
            if '/comment' in path:
                result['comments'][0]['body']='Updated complete body'
            return result
        self.source.request=request
        self.assertTrue(self.wizard.execute(True)['complete'])
        comments=[v for v in self.cb.records.values() if v['kind']=='comment']
        self.assertEqual(len(comments),2)
        self.assertEqual(comments[0]['data']['body'],'Updated complete body')

    def test_one_issue_bound_uses_direct_get_and_creates_only_selected_item(self):
        self.source.rows[0]['fields']['issuelinks']=[]
        self.plan()
        scope=self.wizard.limit_to_issue('P-1')
        self.source.reads.clear()
        self.wizard.analyze(scope)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertEqual(report['counts']['source_issues'],1)
        self.assertEqual(len(self.cb.items),1)
        self.assertFalse(any('/search' in path for _,path in self.source.reads))
        self.assertFalse(any('/issue/P-2' in path for _,path in self.source.reads))

    def test_unconfigured_global_null_is_not_displayed_but_configured_null_is(self):
        self.source.definitions.append(definition('global_unused','Unused Global'))
        self.source.rows[0]['fields']['global_unused']=None
        self.plan()
        fields=self.wizard.plan['scopes']['P:1']['analysis']['fields']
        self.assertNotIn('global_unused',fields)
        self.assertIn('empty',fields)

    def test_hidden_target_with_actual_source_values_is_separate_risk(self):
        self.cb.definitions[0]['hidden']=True
        self.plan()
        self.assertEqual(self.wizard.plan['scopes']['P:1']['target_risks'][0]['field'],'summary')
        with self.assertRaises(ApiError):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)

    def test_source_visibility_unknown_is_not_assumed_visible(self):
        self.source.configured[0].pop('hidden')
        with self.assertRaisesRegex(ApiError,'visibility'):self.plan()
        self.assertFalse(self.cb.mutations)

    def test_new_field_mapping_survives_reanalysis_and_restart(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.wizard.analyze('P:1')
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['empty']['status'],'mapped')
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        self.assertEqual(restarted.config['migration']['field_mapping']['P:1']['empty']['field_id'],10)

    def test_source_and_target_bindings_cannot_reuse_another_environment_state(self):
        config=deepcopy(self.config);config['jira']['url']='https://another.test'
        with self.assertRaisesRegex(ApiError,'different source/target'):
            ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,config)

    def test_paginated_changelog_adapter_and_duplicate_detection(self):
        self.source.rows[0]['changelog']['total']=2
        jira=JiraClient(self.source,dict(self.jira_config,changelog_adapter={
            'path':'/history/{issueKey}','pagination':'complete'}))
        rows=[{'id':'1','created':'2020','items':[]},{'id':'2','created':'2021','items':[]}]
        original=self.source.request
        self.source.request=lambda method,path,*a,**k: rows if path.startswith('/history/') else original(method,path,*a,**k)
        self.assertEqual(len(jira.bundle('P-1')['_history']),2)
        rows[1]['id']='1'
        with self.assertRaisesRegex(ApiError,'Duplicate'):jira.bundle('P-1')


@unittest.skipUnless(os.environ.get('DISPLAY'), 'A GUI display is required for Wizard UI interaction')
class WizardUITests(unittest.TestCase):
    def setUp(self):
        WizardTests.setUp(self)

    def test_project_tracker_scoped_columns_and_explicit_import(self):
        import tkinter as tk
        from tkinter import ttk
        from import_wizard import launch
        original = tk.Tk
        errors = []
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)
        class Window(original):
            def __init__(window, *args, **kwargs):
                super().__init__(*args, **kwargs)
                window.after(100, window.exercise)
                window.after(10000, window.destroy)
            def exercise(window):
                try:
                    widgets = list(descendants(window))
                    def button(text):
                        return next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text') == text)
                    button('프로젝트 조회').invoke()
                    combos = [w for w in widgets if isinstance(w,ttk.Combobox)]
                    combos[0].current(0); combos[1].current(0)
                    button('Issue Type / Tracker 조회').invoke()
                    trees = [w for w in widgets if isinstance(w,ttk.Treeview)]
                    self.assertEqual(len(trees[0].get_children()), 2)
                    for iid in trees[0].get_children():
                        trees[0].selection_set(iid); combos[2].current(0)
                        button('이 Issue Type의 Tracker 매핑 저장').invoke()
                    for index in range(2):
                        combos[3].current(index)
                        button('선택 조합 분석').invoke()
                    combos[3].current(0); button('선택 조합 분석').invoke()
                    self.assertEqual(tuple(trees[1]['columns']), ('cb','jira'))
                    self.assertIn('Codebeamer', trees[1].heading('cb')['text'])
                    self.assertEqual(len(trees[1].get_children()), 3)
                    self.assertFalse(self.cb.mutations)
                    button('Import…').invoke()
                    self.assertFalse(self.cb.mutations)
                    button('Import…').invoke()
                    self.assertEqual(len(self.cb.items), 2)
                    self.assertFalse(self.cb.write)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    window.after(10,window.destroy)
        with patch.object(tk,'Tk',Window), patch('tkinter.messagebox.askyesno',side_effect=[False,True]) as approval:
            launch(self.wizard)
        self.assertEqual(errors,[])
        self.assertEqual(approval.call_count,2)


if __name__ == '__main__':
    unittest.main()
