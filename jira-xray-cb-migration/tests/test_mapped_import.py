import hashlib
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from clients.http import ApiError
from clients.codebeamer_client import CodebeamerClient
from migration.state import State
from single_import import SingleImport
from mapped_import import bind_attachment_api


class MappedImportTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.state=State(self.directory.name);self.addCleanup(self.state.db.close)
        self.binary=b'\x00original binary\xff'
        self.issue={'id':'100','key':'DEMO-1','fields':{'summary':'Title','description':'Text !image.png!', 'estimate':3,
            'attachment':[{'id':'44','filename':'image.png','size':len(self.binary),'content':'https://jira.test/file'}]}}
        def body(schema, media='application/json'):
            return {'requestBody':{'content':{media:{'schema':schema}}}}
        create=body({'type':'object','required':['name'],'properties':{
            'name':{'type':'string'},'description':{'type':'string'},'estimatedHours':{'type':'number'}}})
        create['operationId']='createTrackerItem'
        upload=body({'type':'object','required':['files'],'properties':{
            'files':{'type':'array','items':{'type':'string','format':'binary'}}}}, 'multipart/form-data')
        self.paths={
            '/projects/{projectId}':{'get':{}},'/trackers/{trackerId}':{'get':{}},
            '/trackers/{trackerId}/fields':{'get':{}},
            '/items/{itemId}':{'get':{},'put':body({'type':'object','properties':{'description':{'type':'string'}}})},
            '/trackers/{trackerId}/items':{'post':create},
            '/items/{itemId}/attachments':{'get':{},'post':upload},
            '/items/{itemId}/attachments/{attachmentId}/content':{'get':{}}}
        operations={name:{'method':method,'path':path} for name,method,path in (
            ('project_get','GET','/projects/{projectId}'),('tracker_get','GET','/trackers/{trackerId}'),
            ('fields','GET','/trackers/{trackerId}/fields'),('item_create','POST','/trackers/{trackerId}/items'),
            ('item_get','GET','/items/{itemId}'),('item_update','PUT','/items/{itemId}'))}
        self.requests=[];self.item=None;self.uploaded=False;self.corrupt=False
        def request(method,path,data=None,raw=False,headers=None):
            self.requests.append((method,path,deepcopy(data)))
            if method=='GET':
                if path=='/projects/1':return {'id':1}
                if path=='/trackers/2':return {'id':2,'project':{'id':1}}
                if path=='/trackers/2/fields':return [{'id':1,'name':'Name'},{'id':2,'name':'Description'},{'id':3,'name':'Estimated Hours'},
                                                       {'id':4,'name':'Unknown Custom','type':'Mystery','multipleSelection':False}]
                if path=='/items/77':return deepcopy(self.item)
                if path=='/items/77/attachments':return [{'id':88,'name':'image.png','size':len(self.binary)}] if self.uploaded else []
                if path=='/items/77/attachments/88/content':return b'corrupt' if self.corrupt else self.binary
            if method=='POST' and path=='/trackers/2/items':
                self.item=dict(deepcopy(data),id=77,tracker={'id':2});return {'id':77}
            if method=='POST' and path=='/items/77/attachments':
                self.assertIn(b'name="files"',data);self.assertIn(self.binary,data)
                self.uploaded=True;return [{'id':88}]
            if method=='PUT' and path=='/items/77':self.item.update(data);return deepcopy(self.item)
            raise AssertionError((method,path))
        http=SimpleNamespace(request=request,url=lambda path:'https://cb.test'+path)
        self.cb=CodebeamerClient(http,{'operations':operations})
        def discover():self.cb.spec={'paths':self.paths};return []
        self.cb.discover=discover
        def jira_request(method,path,**kwargs):
            self.assertEqual(method,'GET')
            return self.binary if path=='https://jira.test/file' else deepcopy(self.issue)
        self.jira=SimpleNamespace(http=SimpleNamespace(request=jira_request),fields=lambda:[
            {'id':'summary','name':'Summary'}, {'id':'description','name':'Description'},
            {'id':'estimate','name':'Original Estimate'}, {'id':'attachment','name':'Attachment'}])
        self.config={'jira':{'url':'https://jira.test'},'codebeamer':{'url':'https://cb.test'},'single_import':{
            'mapped_attachments':True,'field_mapping':{'summary':{'target':'name','type':'text'},'description':{'target':'description','type':'text'},'estimate':{'target':'estimatedHours','type':'number'}}}}
        self.test=SingleImport(self.jira,self.cb,self.state,self.config,'DEMO-1',1,2)

    def test_all_visible_tracker_fields_are_listed_and_verified_type_template_enables_custom_mapping(self):
        from mapped_import import targets
        self.cb.spec={'paths':self.paths}
        fields=[{'id':1,'name':'Name'}, {'id':90,'name':'Business Value','type':'Integer','multipleSelection':False},
                {'id':91,'name':'Unknown Custom','type':'Mystery','multipleSelection':False},
                {'id':92,'name':'Hidden Custom','type':'Text','multipleSelection':False,'hidden':True}]
        shown=targets(self.test,fields)
        self.assertIn('name',shown)
        self.assertIn('unbound_90',shown)
        self.assertIn('unbound_91',shown)
        self.assertNotIn('unbound_92',shown)
        self.config.setdefault('migration',{}).setdefault('wizard',{})['field_types']={
            'number':{'binding':{'type':'IntegerFieldValue','value_property':'value'}}}
        self.test.config=self.config
        shown=targets(self.test,fields)
        self.assertTrue(shown['cb_field_90']['binding_ready'])
        self.assertEqual(self.cb.config['field_bindings']['cb_field_90'],
                         {'fieldId':90,'type':'IntegerFieldValue','value_property':'value'})
        self.assertFalse(shown['unbound_91']['binding_ready'])

    def test_unbound_visible_custom_field_gives_explicit_blocker(self):
        self.test.profile['field_mapping']['estimate']['target']='unbound_4'
        self.assertTrue(any('FieldValue binding' in b['reason'] for b in self.test.prepare()['blockers']))

    def test_jira_custom_field_report_has_human_name_and_stable_id(self):
        self.issue['fields']['customfield_12345']='customer value'
        self.jira.fields=lambda:[{'id':'summary','name':'Summary'},{'id':'description','name':'Description'},
            {'id':'estimate','name':'Original Estimate'},{'id':'attachment','name':'Attachment'},
            {'id':'customfield_12345','name':'Customer Requirement'}]
        report=self.test.prepare()
        self.assertEqual(report['jira_field_names']['customfield_12345'],'Customer Requirement')

    def test_approval_mapping_upload_inline_hash_and_resume(self):
        report=self.test.prepare()
        self.assertEqual(report['blockers'],[])
        self.assertTrue(all(r[0]=='GET' for r in self.requests))
        with self.assertRaises(ApiError):self.test.execute(False)
        report=self.test.execute(True)
        self.assertTrue(report['complete']);self.assertFalse(report['full_migration_complete'])
        self.assertEqual(self.item['estimatedHours'],3)
        self.assertEqual(self.item['description'],'Text !https://cb.test/items/77/attachments/88/content!')
        self.assertNotIn('single-import check',self.item['description'])
        self.assertEqual(report['attachments'][0]['sha256'],hashlib.sha256(self.binary).hexdigest())
        # A newly opened session reads the saved rewrite and rechecks remote bytes.
        reopened=SingleImport(self.jira,self.cb,self.state,self.config,'DEMO-1',1,2)
        reopened.prepare();reopened.execute(True)
        self.assertEqual(sum(m=='POST' and p=='/trackers/2/items' for m,p,_ in self.requests),1)
        self.assertEqual(sum(m=='POST' and p=='/items/77/attachments' for m,p,_ in self.requests),1)

    def test_unknown_multipart_contract_blocks_before_item_creation(self):
        self.paths['/items/{itemId}/attachments']['post']={}
        report=self.test.prepare()
        self.assertTrue(any('multipart' in b['reason'] for b in report['blockers']))
        with self.assertRaises(ApiError):self.test.execute(True)
        self.assertTrue(all(r[0]=='GET' for r in self.requests))

    def test_binary_corruption_is_failure_and_no_duplicate_upload_on_retry(self):
        self.test.prepare();self.corrupt=True
        with self.assertRaisesRegex(ApiError,'hash/size'):self.test.execute(True)
        self.assertFalse(self.test.report['complete']);self.assertEqual(self.test.report['item_id'],77)
        self.corrupt=False;self.test.prepare()
        self.assertTrue(self.test.execute(True)['complete'])
        self.assertEqual(sum(m=='POST' and p=='/items/77/attachments' for m,p,_ in self.requests),1)

    def test_changed_source_after_approval_blocks_writes(self):
        self.test.prepare();self.issue['fields']['estimate']=4
        with self.assertRaisesRegex(ApiError,'changed'):self.test.execute(True)
        self.assertTrue(all(r[0]=='GET' for r in self.requests))

    def test_duplicate_mapping_and_hidden_target_block(self):
        self.test.profile['field_mapping']['estimate']['target']='name'
        self.assertTrue(self.test.prepare()['blockers'])

    def test_required_multipart_metadata_is_not_ignored(self):
        schema=self.paths['/items/{itemId}/attachments']['post']['requestBody']['content']['multipart/form-data']['schema']
        schema['required'].append('metadata');schema['properties']['metadata']={'type':'string'}
        self.assertTrue(any('metadata' in b['reason'] for b in self.test.prepare()['blockers']))

    def test_unresolved_image_is_blocked_before_write(self):
        self.issue['fields']['description']='!missing.png!'
        self.assertTrue(any('image' in b['reason'] for b in self.test.prepare()['blockers']))
        self.assertTrue(all(r[0]=='GET' for r in self.requests))

    @unittest.skipUnless(__import__('os').environ.get('DISPLAY'),'Requires Tk display')
    def test_real_mapping_window_background_import_and_copy(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from mapped_import import launch
        root=tk.Tk();root.withdraw()
        failures=[];phase=[0];ticks=[0]
        def widgets(parent):
            yield parent
            for child in parent.winfo_children():yield from widgets(child)
        def tick():
            ticks[0]+=1
            windows=[w for w in root.winfo_children() if isinstance(w,tk.Toplevel)]
            if not windows:return
            window=windows[0]
            try:
                buttons={w.cget('text'):w for w in widgets(window) if isinstance(w,ttk.Button)}
                if phase[0]==0:
                    buttons['사전 조회 / 요청 확인'].invoke();phase[0]=1
                elif phase[0]==1 and str(buttons['Import…'].cget('state'))=='normal':
                    buttons['Import…'].invoke();phase[0]=2
                elif phase[0]==2 and str(buttons['Import…'].cget('state'))=='normal':
                    buttons['결과 / 오류 복사'].invoke()
                    self.assertIn('"complete": true',window.clipboard_get())
                    self.assertIn('"verified": 1',window.clipboard_get())
                    window.destroy();return
                if ticks[0]>150:raise AssertionError('UI background operation did not complete')
            except Exception as exc:
                failures.append(exc);window.destroy();return
            root.after(20,tick)
        root.after(20,tick)
        try:
            with patch('tkinter.messagebox.askyesno',return_value=True),patch('tkinter.messagebox.showerror') as error:
                launch(self.test,root)
            self.assertFalse(error.called)
            self.assertEqual(failures,[])
        finally:root.destroy()

    def test_uncertain_upload_without_receipt_never_reposts(self):
        original=self.cb.http.request
        def lost_response(method,path,*args,**kwargs):
            result=original(method,path,*args,**kwargs)
            if method=='POST' and path=='/items/77/attachments':
                raise ApiError('Upload response lost')
            return result
        self.cb.http.request=lost_response
        self.test.prepare()
        with self.assertRaisesRegex(ApiError,'response lost'):self.test.execute(True)
        self.cb.http.request=original;self.test.prepare()
        with self.assertRaisesRegex(ApiError,'reconciliation'):self.test.execute(True)
        self.assertEqual(sum(m=='POST' and p=='/items/77/attachments' for m,p,_ in self.requests),1)

    def test_hidden_source_values_are_reported_and_cannot_be_mapped(self):
        self.issue['fields'].update(project={'key':'DEMO'},issuetype={'id':'1'})
        self.jira.config={'field_configuration':{'path':'/configured'}}
        self.jira.configured_records=lambda *args:[{'id':'estimate','hidden':True}]
        report=self.test.prepare()
        self.assertEqual(report['hidden_source_value_risks'],['estimate'])
        self.assertTrue(report['blockers'])
        self.assertTrue(all(m=='GET' for m,_,_ in self.requests))
