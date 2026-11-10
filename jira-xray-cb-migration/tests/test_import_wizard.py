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
        if path == '/rest/api/2/issuetype': return self.types
        if path.startswith('/rest/api/2/issue/createmeta?'): raise ApiError('HTTP 404')
        if path == '/rest/api/2/project/P': return {'key':'P','issueTypes':self.types}
        if path.startswith('/field-config/'):
            return deepcopy(self.configured)
        if path.startswith('/r4j/trees/'):
            return [{'id':'tree','name':'Original tree','parent':None}]
        if path.startswith('/r4j/revisions/'):
            return [{'id':'rev','author':'original','created':'2020-01-01','fields':{'summary':'old'}}]
        if path.startswith('/r4j/baselines/'):
            return [{'id':'baseline','name':'Original baseline','revision':'rev'}]
        if path.startswith('/rest/api/2/search'):
            from urllib.parse import parse_qs, urlsplit
            jql = parse_qs(urlsplit(path).query)['jql'][0]
            rows = [] if '"2"' in jql and '"1"' not in jql else self.rows
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
        self.http.request=self.request
        self.config = {'operations':{},'field_bindings':{'empty':{'fieldId':3,'type':'TextFieldValue','value_property':'value'}}}
        self.tracker_rows = [{'id':100,'name':'Existing'}]
        self.definitions = [field(1,'Name'),field(2,'Description'),field(3,'Empty'),field(4,'Hidden CB',hidden=True)]
        self.items, self.files, self.records = {}, {}, {}
        self.mutations = []
        self.fail_comment = False
        self.lost_comment_response = False
        self.discover()
    def request(self,method,path,*args,**kwargs):
        if method!='GET':raise AssertionError('Unexpected raw write')
        if '/items?' in path:
            return {'itemRefs':[{'id':i} for i in self.items], 'total':len(self.items)}
        if path.startswith('/v3/items/'):
            return self.call('item_get',{'itemId':int(path.rsplit('/',1)[1])})
        raise AssertionError(path)
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
        if name=='item_get':
            result=deepcopy(dict(self.items[p['itemId']],id=p['itemId'],tracker={'id':100}))
            result['customFields']=[{'fieldId':900,'type':'TextFieldValue','value':result.get('cb_field_900')}]
            return result
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


class RejectedFieldRetryTests(unittest.TestCase):
    def _setup(self, root, *, status=400, existing=False):
        from types import SimpleNamespace
        from pathlib import Path
        from migration.wizard import field_identity
        import hashlib
        wizard=ImportWizard.__new__(ImportWizard)
        row={'selected':True,'status':'create',
             'creation':{'label':'Labels','semantic_type':'option','profile_key':'option',
                         'multipleSelection':True,'choices':{'SILS':'SILS'}}}
        entry={'tracker':4874140,'rows':{'labels':row}}
        wizard.plan={'scopes':{'P:1':entry}}
        identity=field_identity(4874140,'labels',row)
        receipt_path=Path(root)/('field-'+hashlib.sha256(identity.encode()).hexdigest()+'.json')
        receipt_path.write_text(json.dumps({'old':{'tracker':4874140,'status':'pending',
            'request':{'method':'POST','path':'/v3/tracker/configuration',
                       'body':{'fields':[{'label':'existing'},{'label':'Labels'}]}},
            'error':'POST /v3/tracker/configuration failed: HTTP '+str(status),
            'http_error_evidence':{'http_status':status}}}),encoding='utf-8')
        events=[]
        wizard.state=SimpleNamespace(root=Path(root),
            get=lambda key:('pending',None) if key==identity else None,
            save=lambda *args:events.append(args))
        fields=[{'label':'Labels','id':19}] if existing else []
        wizard.cb=SimpleNamespace(configuration=lambda tracker:{
            'basicInformation':{'trackerId':tracker},'fields':fields})
        return wizard,row,events,receipt_path

    def test_confirmed_400_requires_approval_without_deleting_old_journal(self):
        with TemporaryDirectory() as d:
            wizard,row,events,path=self._setup(d)
            first=wizard.rearm_rejected_field('P:1','labels',4874140,approved=False)
            self.assertFalse(first['new_post_attempted'])
            self.assertNotIn('creation_token',row)
            self.assertFalse(events)
            result=wizard.rearm_rejected_field('P:1','labels',4874140,approved=True)
            self.assertTrue(result['rearmed'])
            self.assertTrue(row['creation_token'].startswith('retry-'))
            self.assertTrue(path.exists())
            self.assertTrue(any(a[0]=='field-creation-rearm' for a in events))

    def test_non_400_and_existing_field_cannot_be_rearmed(self):
        for status,existing in ((500,False),(400,True)):
            with self.subTest(status=status,existing=existing), TemporaryDirectory() as d:
                wizard,row,events,path=self._setup(d,status=status,existing=existing)
                with self.assertRaises(ApiError):
                    wizard.rearm_rejected_field('P:1','labels',4874140,approved=True)
                self.assertNotIn('creation_token',row)
                self.assertFalse(events)


class PendingCreationDiagnosticTests(unittest.TestCase):
    def test_old_json_record_reports_its_timestamp_not_current_import_time(self):
        from pathlib import Path
        from migration.wizard import pending_field_message
        with TemporaryDirectory() as directory:
            receipt=Path(directory)/'field-old.json'
            receipt.write_text(json.dumps({
                'old': {'tracker': 4874140, 'status': 'pending',
                        'error': 'POST failed: HTTP 400; permission related'}
            }), encoding='utf-8')
            result=pending_field_message('Labels', 4874140, receipt, 'pending')
            self.assertIn('이번 Import에서는 신규 필드 POST를 보내지 않았습니다',result)
            self.assertIn('파일 수정 시각:', result)
            self.assertIn('HTTP 400',result)
            self.assertIn(str(receipt.resolve()),result)

    def test_missing_json_does_not_claim_receipt_exists(self):
        from pathlib import Path
        from migration.wizard import pending_field_message
        with TemporaryDirectory() as directory:
            receipt=Path(directory)/'field-absent.json'
            result=pending_field_message('Labels',4874140,receipt,'pending')
            self.assertIn('JSON 파일이 현재 경로에 없습니다',result)
            self.assertIn('state.sqlite3',result)
            self.assertIn('이전 HTTP 상태를 확인할 수 없습니다',result)


class DeferredFieldCreationTests(unittest.TestCase):
    def _wizard(self, receipt=None):
        from types import SimpleNamespace
        wizard=ImportWizard.__new__(ImportWizard)
        row={"selected":True, "status":"manual"}
        wizard.plan={"projects":{}, "scopes":{"P:1":{
            "project":"P", "tracker":4872985,
            "analysis":{"fields":{"customfield_19806":{
                "name":"[TC] 판정 조건",
                "schema":{"type":"string",
                          "custom":"com.atlassian.jira.plugin.system.customfieldtypes:textarea"}}},
                "values":{"customfield_19806":["PASS"]}},
            "rows":{"customfield_19806":row}
        }}}
        actions=[]
        wizard.state=SimpleNamespace(
            get=lambda key:receipt,
            save=lambda *args:actions.append(("save",args[0])),
            field_phase=lambda *args,**kw:actions.append(("phase",args[1])))
        wizard.config={"migration":{"wizard":{"field_types":{}}}}
        return wizard,row,actions

    def test_save_text_creation_without_preconfigured_template(self):
        wizard,row,actions=self._wizard()
        proposal=wizard.configure_creation("P:1","customfield_19806",
            "[TC] 판정 조건","text",False,{})
        self.assertEqual(proposal["profile_key"],"text")
        self.assertEqual(proposal["semantic_type"],"text")
        self.assertTrue(row["creation_saved"])
        self.assertNotIn("creation_profile",row)
        self.assertEqual(row["creation_stage"],"PLANNED")
        self.assertTrue(any(kind=="phase" for kind,_ in actions))

    def test_pending_creation_cannot_be_overwritten(self):
        wizard,row,_=self._wizard(("pending",None))
        with self.assertRaises(ApiError):
            wizard.configure_creation("P:1","customfield_19806",
                "[TC] 판정 조건","text",False,{})
        self.assertNotIn("creation",row)


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
                'r4j':{c:{'path':'/r4j/'+c+'/{issueKey}','pagination':'complete'} for c in ('trees','revisions','baselines')},
                'wizard':{'association_types':{'blocks':7},'tracker_template_id':100,'field_types':{'text':{'type_name':'Text','definition':{'typeId':10,'mandatory':False,
                    'multipleSelection':False,'reference':None,'options':None},
                    'binding':{'type':'TextFieldValue','value_property':'value'}}}}}}
        self.config['migration']['wizard']['identity_bindings']={
            'P:'+str(kind['id']):{'field_id':900,'payload_key':'cb_field_900',
                'binding':{'fieldId':900,'type':'TextFieldValue','value_property':'value'}} for kind in self.source.types}
        self.wizard=ImportWizard(JiraClient(self.source,self.jira_config,self.state.save),self.cb,self.state,self.config)
    def plan(self, new=False):
        self.wizard.discover();self.wizard.select_project('P',10)
        for kind in self.source.types:
            self.wizard.map_tracker('P',kind['id'],None if new else 100,'New '+kind['name'])
            scope='P:'+kind['id']
            entry=self.wizard.analyze(scope)
            # Integration fixtures explicitly choose mappings; production analysis no longer chooses.
            choices=proposals(entry['analysis'],entry['target_fields'],self.wizard.config)
            for source,row in choices.items():
                if row.get('spec'):
                    spec=row['spec']
                    self.wizard.manual_mapping(scope,source,spec['field_id'],spec['target'],spec['value_mapping'])
                elif row['status']=='create':
                    creation=row['creation']
                    self.wizard.configure_creation(scope,source,creation['label'],creation['semantic_type'],creation['multipleSelection'],creation['choices'] if creation['semantic_type'] in ('option','status') else {})
                self.wizard.select_field(scope,source,True)
    def test_unselected_stays_blank_and_user_can_switch_creation_and_existing(self):
        self.wizard.discover();self.wizard.select_project('P',10)
        self.wizard.map_tracker('P','1',100)
        entry=self.wizard.analyze('P:1')
        self.assertIsNotNone(entry['rows']['summary']['spec'])  # saved config mapping
        self.assertEqual(entry['rows']['description']['spec']['field_id'],2)
        self.assertFalse(entry['rows']['description']['selected'])
        self.wizard.select_field('P:1','description',True)
        self.wizard.configure_creation('P:1','summary','My Summary','text',False,{})
        self.wizard.analyze('P:1')
        row=self.wizard.plan['scopes']['P:1']['rows']['summary']
        self.assertEqual(row['creation']['label'],'My Summary')
        self.assertIsNone(row['spec'])
        self.wizard.manual_mapping('P:1','summary',1,'name',{})
        self.wizard.analyze('P:1')
        row=self.wizard.plan['scopes']['P:1']['rows']['summary']
        self.assertEqual(row['spec']['field_id'],1)
        self.assertFalse(row.get('creation_saved'))
        self.assertFalse(self.cb.mutations)

    def test_review_selection_persists_and_excludes_values(self):
        self.plan()
        self.wizard.select_field('P:1','description',False)
        self.wizard.select_field('P:1','empty',False)
        self.wizard.analyze('P:1')
        self.assertFalse(self.wizard.plan['scopes']['P:1']['rows']['description']['selected'])
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        self.assertFalse(restarted.plan['scopes']['P:1']['rows']['empty']['selected'])
        self.assertFalse(self.cb.mutations)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertNotIn('description',self.wizard.config['migration']['field_mapping']['P:1'])
        self.assertNotIn('empty',self.wizard.config['migration']['field_mapping']['P:1'])

    def test_analysis_is_read_only_and_all_types_include_empty_type(self):
        self.plan()
        self.assertEqual(len(self.wizard.plan['scopes']),2)
        analysis=self.wizard.plan['scopes']['P:1']['analysis']
        self.assertIn('empty',analysis['fields'])
        self.assertNotIn('hidden',analysis['fields'])
        self.assertEqual(analysis['excluded'],[{'field':'hidden','name':'Hidden','has_value':False}])
        for special in ('attachment','comment','issuelinks','project','issuetype'):
            self.assertNotIn(special,analysis['fields'])
        self.assertFalse(self.cb.mutations)
        with self.assertRaisesRegex(ApiError,'approval'):self.wizard.execute()
        self.assertFalse(self.cb.mutations)
    def test_scope_and_hidden_value_risks_block_import(self):
        self.source.rows[0]['fields']['hidden']='must not lose'
        self.plan()
        self.assertTrue(self.wizard.plan['scopes']['P:1']['analysis']['risks'])
        self.assertTrue(self.wizard.plan['scopes']['P:1']['analysis']['excluded'][0]['has_value'])
        with self.assertRaises(ApiError):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
        with self.assertRaises(ApiError):self.wizard.map_tracker('P','1',999)
    def test_null_custom_field_creates_proposal_and_no_type_guess(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['empty']['status'],'create')
        self.source.configured.append(definition('table','Steps','table',hidden=False))
        self.wizard.analyze('P:1')
        self.assertIsNone(self.wizard.plan['scopes']['P:1']['rows']['table']['spec'])

    def test_creation_popup_settings_are_plan_only_and_preserve_source_type(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        before=list(self.cb.mutations)
        proposal=self.wizard.configure_creation('P:1','empty','Planned Empty','text',False,{})
        self.assertEqual(proposal['label'],'Planned Empty')
        self.assertEqual(self.cb.mutations,before)
        with self.assertRaises(ApiError):
            self.wizard.configure_creation('P:1','empty','Wrong','table',False,{})
        self.wizard.configure_creation('P:1','summary','New Summary','text',False,{})
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['summary']['creation']['label'],'New Summary')
        self.wizard.analyze('P:1')
        self.assertIsNone(self.wizard.plan['scopes']['P:1']['rows']['summary']['spec'])
    def test_collision_and_type_conflict_are_not_automatic(self):
        self.plan();a=self.wizard.plan['scopes']['P:1']['analysis']
        a['fields']['title']=definition('title','Title');a['values']['title']=['x']
        rows=proposals(a,self.cb.definitions,self.config)
        self.assertEqual(rows['summary']['status'],'mapped') # Standard Summary owns Name before custom aliases.
        self.assertEqual(rows['title']['status'],'mapped') # Suggestions remain available; selected duplicates are checked separately.
        fields=[dict(f,typeName='Integer') if f['id']==1 else f for f in self.cb.definitions]
        self.assertNotEqual(proposals(a,fields,self.config)['summary']['status'],'mapped')
    def test_configuration_adapter_is_required_instead_of_editmeta_guess(self):
        analysis = field_analysis(JiraClient(self.source,{}),'P',self.source.types[0])
        self.assertEqual(analysis['configuration_status'], 'unavailable')
        self.assertEqual(analysis['fields']['summary']['visibility'], 'unknown')
        self.assertIn('empty', analysis['fields'])
        self.assertFalse(self.cb.mutations)
    def test_full_import_preserves_history_comments_binary_and_directed_link(self):
        self.plan()
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        self.assertEqual(len(self.cb.items),2)
        self.assertEqual(report['legacy_history'][0]['history_source'],1)
        self.assertEqual(report['legacy_history'][0]['history_preserved'],1)
        self.assertEqual(report['legacy_history'][0]['r4j_baselines_preserved'],1)
        self.assertEqual(report['legacy_history'][0]['r4j_trees_preserved'],1)
        self.assertEqual(report['legacy_history'][0]['worklogs_preserved'],0)
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
    def test_validation_failure_does_not_leave_pending_creation(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        row=self.wizard.plan['scopes']['P:1']['rows']['empty']
        with patch('mapping_helper.dedicated_field_contract',side_effect=ApiError('schema missing')), patch(
                'clients.codebeamer_3007.configuration_field_contract',side_effect=ApiError('schema missing')):
            with self.assertRaisesRegex(ApiError,'schema missing'):
                self.wizard.create_field('P:1','empty',row,100)
        self.assertIsNone(self.state.get('wizard-field:100:empty'))
        self.assertEqual(self.state.get('field-phase:P:1:empty')[0],'FAILED')
        self.assertFalse(self.cb.mutations)

    def test_created_field_resumes_after_value_mapping_failure(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        with patch('migration.wizard.build_spec',side_effect=ApiError('value mapping failed')):
            with self.assertRaisesRegex(ApiError,'value mapping failed'):self.wizard.execute(True)
        self.assertEqual(self.state.get('wizard-field:100:empty'),('done',{'id':10}))
        self.assertEqual(self.state.get('field-phase:P:1:empty')[0],'VALUE_MAPPING_PENDING')
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(sum(name=='helper_field_create' for name,_ in self.cb.mutations),1)
        self.assertEqual(self.state.get('field-phase:P:1:empty')[0],'COMPLETED')

    def test_lost_field_response_reconciles_without_repost(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.plan()
        original=self.cb.call
        def lost(name,*args,**kwargs):
            result=original(name,*args,**kwargs)
            if name=='helper_field_create':raise ApiError('Lost POST response')
            return result
        self.cb.call=lost
        with self.assertRaisesRegex(ApiError,'Lost POST'):self.wizard.execute(True)
        self.assertEqual(self.state.get('field-phase:P:1:empty')[0],'RECONCILIATION_REQUIRED')
        self.cb.call=original
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(sum(name=='helper_field_create' for name,_ in self.cb.mutations),1)

    def test_definition_created_without_value_binding_is_not_complete(self):
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        self.config['migration']['wizard']['field_types']['text'].pop('binding')
        self.wizard.config['migration']['wizard']['field_types']['text'].pop('binding')
        self.plan()
        self.wizard.configure_creation('P:1','empty','Empty','text',False,{})
        report=self.wizard.execute(True)
        self.assertFalse(report['complete'])
        self.assertIn('FieldValue',report['errors'][0]['error'])
        self.assertEqual(len(self.cb.items),0)
        spec=self.state.load('wizard','resolved-mapping')['field_mapping']['P:1']['empty']
        self.assertTrue(spec['value_contract_unverified'])
        self.assertEqual(spec['field_id'],10)
        self.assertEqual([f['id'] for f in self.cb.definitions if f['label']=='Empty'],[10])

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
        self.plan();self.wizard.config['migration'].pop('r4j');self.wizard.config['migration']['preserve_r4j']=True
        with self.assertRaisesRegex(ApiError,'r4j'):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
    def test_missing_r4j_tree_contract_blocks_before_target_write(self):
        self.plan();self.wizard.config['migration']['r4j'].pop('trees')
        with self.assertRaisesRegex(ApiError,'r4j.trees'):self.wizard.execute(True)
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
    def test_relation_catalog_save_restore_reverse_and_dedup(self):
        original=self.source.request
        link_type={'id':'blocks','name':'Blocks','outward':'blocks','inward':'is blocked by'}
        def read(method,path,*args,**kwargs):
            if path=='/rest/api/2/issueLinkType':return {'issueLinkTypes':[deepcopy(link_type)]}
            return original(method,path,*args,**kwargs)
        self.source.request=read
        self.cb.http.request=lambda method,path: ([{'id':7,'name':'Blocks'},{'id':8,'name':'Other'}] if path.endswith('/associations/types') else self.cb.request(method,path))
        self.wizard.config['migration']['wizard']['association_types']={}
        catalog=self.wizard.relation_catalog()
        self.assertEqual(catalog['jira'][0]['inward'],'is blocked by')
        self.assertEqual(self.wizard.config['migration']['wizard']['association_types'],{})
        self.wizard.save_relation_mapping('blocks',{'type_id':8,'reverse':True})
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        self.assertEqual(restarted.config['migration']['wizard']['association_types']['blocks']['type_id'],8)
        self.plan()
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        links=[r for r in self.cb.records.values() if r['kind']=='association']
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['data']['typeId'],8)
        self.assertEqual((links[0]['data']['fromItemId'],links[0]['data']['toItemId']),(201,200))
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(sum(r['kind']=='association' for r in self.cb.records.values()),1)
        with self.assertRaises(ApiError):self.wizard.save_relation_mapping('blocks',{'type_id':999})

    def test_components_creation_id_mapping_and_item_readback(self):
        source={'id':'components','name':'Components','schema':{'type':'array','items':'component','system':'components'},
                'hidden':False,'allowedValues':[{'id':'101','name':'ECU'},{'id':'102','name':'Diagnostics'}]}
        self.source.definitions.append(source);self.source.configured.append(source)
        self.source.rows[0]['fields']['components']=deepcopy(source['allowedValues'])
        self.source.rows[1]['fields']['components']=[]
        self.wizard.config['migration']['wizard']['field_types']['option']={
            'type_name':'Choice','definition':{'typeId':20,'mandatory':False,
                'multipleSelection':True,'options':[],'reference':None},
            'binding':{'type':'ChoiceFieldValue','value_property':'values'}}
        self.plan()
        row=self.wizard.plan['scopes']['P:1']['rows']['components']
        self.assertEqual(row['creation']['choices'],{'101':'ECU','102':'Diagnostics'})
        self.assertTrue(row['creation']['multipleSelection'])
        self.assertFalse(self.cb.mutations)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        actual=next(f for f in self.cb.definitions if f['label']=='Components')
        self.assertTrue(actual['multipleSelection'])
        self.assertEqual(self.cb.items[200]['jira_components'],[{'id':100},{'id':101}])
        evidence=self.state.load('component-mapping','P:1')
        self.assertEqual(evidence['components'],{'101':'ECU','102':'Diagnostics'})
        self.assertEqual({k:evidence['option_mapping'][k] for k in ('101','102')},{'101':{'id':100},'102':{'id':101}})
        self.assertEqual(evidence['option_mapping']['ECU'],{'id':100})
        self.assertEqual(evidence['field_id'],actual['id'])
        self.assertTrue(self.wizard.execute(True)['complete'])
        self.assertEqual(len([f for f in self.cb.definitions if f['label']=='Components']),1)

    def test_components_duplicate_names_do_not_collapse_ids(self):
        source={'id':'components','name':'Components','schema':{'type':'array','items':'component'},'hidden':False}
        self.source.definitions.append(source);self.source.configured.append(source)
        self.source.rows[0]['fields']['components']=[{'id':'1','name':'same'},{'id':'2','name':'same'}]
        with self.assertRaisesRegex(ApiError,'서로 다른 ID'):self.plan()
        self.assertFalse(self.cb.mutations)

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
        self.plan()
        self.assertEqual(self.wizard.plan['scopes']['P:1']['analysis']['fields']['summary']['visibility'], 'unknown')
        with self.assertRaisesRegex(ApiError,'표시 상태 미확인'):self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)

    def test_unavailable_configuration_keeps_actual_values_and_nulls(self):
        original = self.source.request
        def failing(method, path, *args, **kwargs):
            if path.startswith('/field-config/'):
                raise ApiError('HTTP 403')
            return original(method, path, *args, **kwargs)
        self.source.request = failing
        analysis = field_analysis(JiraClient(self.source, self.jira_config), 'P', self.source.types[0])
        self.assertEqual(analysis['configuration_status'], 'unavailable')
        self.assertIn('summary', analysis['fields'])
        self.assertIn('empty', analysis['fields'])
        self.assertEqual(analysis['excluded'], [])
        self.assertTrue(all(f['visibility'] == 'unknown' for f in analysis['fields'].values()))
        self.assertFalse(self.cb.mutations)

    def test_unknown_visibility_review_is_bound_to_current_analysis(self):
        self.source.configured[0].pop('hidden')
        self.plan()
        review = deepcopy(self.wizard.visibility_review())
        with self.assertRaisesRegex(ApiError, '표시 상태 미확인'):
            self.wizard.execute(True)
        self.assertFalse(self.cb.mutations)
        self.assertTrue(self.wizard.execute(True, visibility_review=review)['complete'])
        review['P:1']['issue_count'] += 1
        with self.assertRaisesRegex(ApiError, '표시 상태 미확인'):
            self.wizard.execute(True, visibility_review=review)

    def test_createmeta_scopes_null_fields_without_hiding_values(self):
        original = self.source.request
        def metadata(method, path, *args, **kwargs):
            if path.startswith('/rest/api/2/issue/createmeta?'):
                return {'projects':[{'key':'P','issuetypes':[{'id':'1','fields':{
                    'empty':{'name':'Screen empty','schema':{'type':'string'}}}}]}]}
            return original(method,path,*args,**kwargs)
        self.source.request=metadata
        analysis=field_analysis(JiraClient(self.source,{}),'P',self.source.types[0])
        self.assertIn('empty',analysis['fields'])
        self.assertIn('summary',analysis['fields'])
        self.assertEqual(analysis['fields']['empty']['visibility'],'unknown')
        self.assertEqual(analysis['createmeta_status'],'available')

    def test_manual_and_mapped_fields_can_change_and_persist(self):
        self.cb.definitions.extend([field(8,'Alternate title'),field(9,'Another title')])
        self.plan()
        entry=self.wizard.plan['scopes']['P:1']
        self.assertEqual(entry['rows']['summary']['status'],'mapped')
        # Auto -> manual, then existing manual -> another Field ID.
        self.wizard.manual_mapping('P:1','summary',8,'cb_field_8',{})
        self.assertEqual(entry['rows']['summary']['spec']['field_id'],8)
        self.wizard.manual_mapping('P:1','summary',9,'cb_field_9',{})
        self.assertEqual(entry['rows']['summary']['spec']['field_id'],9)
        # Unresolved/manual row uses the same save path.
        entry['rows']['summary'].update(status='manual',spec=None)
        self.wizard.manual_mapping('P:1','summary',8,'cb_field_8',{})
        self.wizard.analyze('P:1')
        self.assertEqual(self.wizard.plan['scopes']['P:1']['rows']['summary']['spec']['field_id'],8)
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        restarted.analyze('P:1')
        self.assertEqual(restarted.plan['scopes']['P:1']['rows']['summary']['spec']['field_id'],8)
        self.assertFalse(self.cb.mutations)

    def test_reference_selection_saves_sparse_definition_without_fabrication(self):
        self.plan()
        entry=self.wizard.plan['scopes']['P:1']
        entry['analysis']['fields']['picker']={'id':'picker','name':'Related issue','schema':{'type':'issue'}}
        entry['analysis']['values']['picker']=[{'key':'P-2'}]
        entry['rows']['picker']={'status':'manual','spec':None}
        target={'id':8,'name':'Related','type':'ReferenceField','multipleValues':False,'hidden':False,
                'reference':{'actualServerProperty':{'trackers':[100]}}}
        entry['target_fields'].append(target)
        self.wizard.manual_mapping('P:1','picker',8,'cb_field_8',{})
        spec=entry['rows']['picker']['spec']
        self.assertTrue(spec['deferred_reference'])
        self.assertNotIn('expected_options',spec)
        self.assertNotIn('expected_mandatory',spec)
        self.assertEqual(spec['expected_reference'],target['reference'])
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        self.assertEqual(restarted.plan['scopes']['P:1']['rows']['picker']['spec'],spec)
        self.assertFalse(self.cb.mutations)

    def test_sparse_existing_text_selection_is_saved_but_import_is_blocked(self):
        self.plan()
        entry=self.wizard.plan['scopes']['P:1']
        entry['target_fields'].append({'id':8,'name':'Other','typeName':'Text','multipleSelection':False,'hidden':False})
        self.wizard.manual_mapping('P:1','summary',8,'cb_field_8',{})
        spec=entry['rows']['summary']['spec']
        self.assertEqual(spec['field_id'],8)
        self.assertIn('mandatory',spec['missing_definition'])
        self.assertNotIn('expected_options',spec)
        self.assertTrue(any('Field ID 8' in error for error in self.wizard.errors()))
        self.assertFalse(self.cb.mutations)

    def test_reference_creation_plan_requires_explicit_server_template(self):
        self.plan()
        entry=self.wizard.plan['scopes']['P:1']
        entry['analysis']['fields']['picker']={'id':'picker','name':'Related issue','schema':{'type':'issue'}}
        entry['analysis']['values']['picker']=[{'key':'P-2'}]
        entry['rows']['picker']={'status':'manual','spec':None}
        self.wizard.config['migration']['wizard']['field_types']['reference']={
            'type_name':'Reference','definition':{'typeId':55,'mandatory':False,'multipleSelection':False,
                'options':None,'permission':{'type':'UNRESTRICTED'}},
            'reference_template':{'trackerIds':['${targetTrackerId}']}}
        self.wizard.configure_creation('P:1','picker','Related','reference',False,{},reference_tracker=100)
        self.assertEqual(entry['rows']['picker']['creation_profile']['definition']['reference'],{'trackerIds':[100]})
        self.assertFalse(self.cb.mutations)

    def test_manual_choice_mapping_is_saved_with_field_id(self):
        self.plan()
        entry=self.wizard.plan['scopes']['P:1']
        entry['target_fields'].append(dict(field(8,'Choice target'),typeName='Choice',options=[{'id':81,'name':'A'}]))
        entry['analysis']['fields']['choice']={'id':'choice','name':'Choice','schema':{'type':'option'}}
        entry['analysis']['values']['choice']=[{'id':'a','value':'A'}]
        entry['rows']['choice']={'status':'manual','spec':None}
        values={'a':{'id':81}}
        self.wizard.manual_mapping('P:1','choice',8,'cb_field_8',values)
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        spec=restarted.plan['scopes']['P:1']['rows']['choice']['spec']
        self.assertEqual(spec['field_id'],8)
        self.assertEqual(spec['value_mapping'],values)

    def test_candidate_rejections_are_visible_and_do_not_replace_mapping(self):
        self.cb.definitions.extend([dict(field(8,'Number'),typeName='Integer'),
                                    dict(field(9,'Unknown'),typeName='UnknownModel')])
        self.plan()
        before=deepcopy(self.wizard.plan['scopes']['P:1']['rows']['summary'])
        reasons={c['field']['id']:c['reason'] for c in self.wizard.mapping_candidates('P:1','summary')}
        self.assertIn('Hidden',reasons[4])
        self.assertIn('타입 불일치',reasons[8])
        self.assertIn('미확인',reasons[9])
        self.assertIn('사용 중',reasons[2])
        for identifier in (2,4,8,999):
            with self.subTest(identifier=identifier), self.assertRaises(ApiError):
                self.wizard.manual_mapping('P:1','summary',identifier,'cb_field_'+str(identifier),{})
        self.assertEqual(before,self.wizard.plan['scopes']['P:1']['rows']['summary'])

    def test_manual_selection_survives_restart_and_reanalysis(self):
        self.plan()
        self.wizard.manual_mapping('P:1','summary',1,'name',{})
        restarted=ImportWizard(JiraClient(self.source,self.jira_config),self.cb,self.state,self.config)
        self.assertEqual(restarted.plan['scopes']['P:1']['rows']['summary']['spec']['field_id'],1)
        restarted.analyze('P:1')
        self.assertEqual(restarted.plan['scopes']['P:1']['rows']['summary']['mapping_mode'],'manual')

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

    def test_connection_screen_collects_credentials_in_memory_and_passes_projects(self):
        import tkinter as tk
        from tkinter import ttk
        import import_wizard as ui
        original=tk.Tk;captured={};errors=[]
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():yield from descendants(child)
        class FakeHttp:
            def __init__(fake,base,*args,auth=None,credential_prompt=None,**kwargs):
                fake.base=base;fake.read_deadline=None;fake.cancel_event=None;fake.timeout=10
                if auth=='bearer':captured['jira_token']=credential_prompt('token',True)
                if auth=='basic':
                    captured['cb_user']=credential_prompt('username',False)
                    captured['cb_password']=credential_prompt('password',True)
        class Window(original):
            def __init__(window,*args,**kwargs):
                super().__init__(*args,**kwargs);window.after(50,window.exercise);window.after(5000,window.destroy)
            def exercise(window):
                try:
                    entries=[w for w in descendants(window) if isinstance(w,ttk.Entry) and str(w.cget('state'))!='readonly']
                    for index,value in ((1,'jira-secret'),(3,'cb-user'),(4,'cb-secret')):
                        entries[index].delete(0,'end');entries[index].insert(0,value)
                    next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text').startswith('연결 /')).invoke()
                except Exception as exc:errors.append(exc);window.destroy()
        def opened(wizard,projects):
            captured['wizard']=wizard;captured['projects']=projects
        jira_factory=lambda http,config,archive=None:JiraClient(self.source,config,archive)
        with patch.object(tk,'Tk',Window),patch.object(ui,'Http',FakeHttp),patch.object(ui,'JiraClient',side_effect=jira_factory),\
             patch.object(ui,'CodebeamerClient',return_value=self.cb),\
             patch('clients.codebeamer_verified_reads.projects',side_effect=lambda cb:cb.call('projects_get')),\
             patch('clients.codebeamer_verified_reads.load_successful_openapi',return_value=None),patch.object(ui,'launch',side_effect=opened):
            ui.launch_connected(self.config,self.state)
        self.assertEqual(errors,[])
        self.assertEqual((captured['jira_token'],captured['cb_user'],captured['cb_password']),('jira-secret','cb-user','cb-secret'))
        self.assertEqual(captured['projects']['codebeamer'][0]['id'],10)
        self.assertNotIn('jira-secret',str(captured['wizard'].config))
        self.assertNotIn('cb-secret',str(captured['wizard'].config))

    def test_project_tracker_scoped_columns_and_explicit_import(self):
        import tkinter as tk
        from tkinter import ttk
        from import_wizard import launch
        # Make one real Jira custom field lack a CB counterpart so the creation
        # popup is exercised without changing the production write boundary.
        self.cb.definitions=[f for f in self.cb.definitions if f['id']!=3]
        for collection in (self.source.definitions,self.source.configured):
            for value in collection:
                if value['id']=='empty':value.update(id='customfield_10001',name='Business Value')
        for issue in self.source.rows:
            issue['fields']['customfield_10001']=issue['fields'].pop('empty')
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
                    self.assertEqual(tuple(trees[1]['columns']), ('jira','cb'))
                    self.assertIn('Codebeamer', trees[1].heading('cb')['text'])
                    self.assertIn('Jira 필드', trees[1].heading('jira')['text'])
                    self.assertEqual(len(trees[1].get_children()), 3)
                    creation=next(i for i in trees[1].get_children() if 'customfield_10001' in trees[1].set(i,'jira'))
                    self.assertIn('Business Value',trees[1].set(creation,'jira'))
                    self.assertEqual(trees[1].set(creation,'cb'),'')
                    trees[1].selection_set(creation);window.update()
                    widgets=list(descendants(window));button('＋ 신규 필드 생성').invoke();window.update()
                    dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                    name=next(w for w in descendants(dialog) if isinstance(w,ttk.Entry))
                    name.delete(0,'end');name.insert(0,'Planned Business Value')
                    save=next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='생성 설정 저장')
                    save.invoke();window.update()
                    self.wizard.configure_creation('P:2','customfield_10001','Planned Business Value','text',False,{})
                    self.assertFalse(self.cb.mutations)
                    for scope in self.wizard.plan['scopes']:
                        self.wizard.manual_mapping(scope,'description',2,'description',{})
                    self.assertEqual(self.wizard.errors(),[])
                    button('다음 (Import 승인)  ›').invoke()
                    self.assertFalse(self.cb.mutations)
                    button('다음 (Import 승인)  ›').invoke()
                    messages=[window.getvar(w.cget('textvariable')) for w in descendants(window)
                              if isinstance(w,(ttk.Label,ttk.Entry)) and w.cget('textvariable')]
                    self.assertEqual(len(self.cb.items),2,messages)
                    self.assertTrue(any(f['label']=='Planned Business Value' for f in self.cb.definitions))
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
