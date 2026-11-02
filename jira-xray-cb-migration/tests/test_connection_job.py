import os
import json
import urllib.parse
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from connection_job import ConnectionJob
from clients.http import Http, ApiError


class ConnectionJobTests(unittest.TestCase):
    def test_deadline_and_late_result_cannot_become_success(self):
        gate=threading.Event()
        self.addCleanup(gate.set)
        job=ConnectionJob(lambda:gate.wait(2),timeout=30)
        job.deadline=time.monotonic()-1
        self.assertEqual(job.poll()[0],'timeout')
        self.assertTrue(job.cancel_event.is_set())
        gate.set()
        self.assertEqual(job.poll()[0],'finished')

    def test_cancel_and_http_budget_prevent_further_requests(self):
        client=Http('https://example.test',auth='none')
        gate=threading.Event();self.addCleanup(gate.set)
        job=ConnectionJob(lambda:gate.wait(2),clients=(client,))
        job.cancel()
        self.assertEqual(job.poll()[0],'cancelled')
        with patch.object(client.opener,'open') as request:
            with self.assertRaisesRegex(ApiError,'cancelled'):client.request('GET','/projects')
            request.assert_not_called()

    def test_background_failure_returns_exception(self):
        def fail():raise ApiError('HTTP 401')
        job=ConnectionJob(fail)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            outcome,result=job.poll()
            if outcome!='running':break
            threading.Event().wait(0.01)
        self.assertEqual(outcome,'error')
        self.assertEqual(str(result),'HTTP 401')


@unittest.skipUnless(os.environ.get('DISPLAY'),'Requires a display for actual Tk event-loop regression')
class ConnectionWindowTests(unittest.TestCase):
    def test_slow_swagger_keeps_window_alive_and_cancel_rejects_late_list(self):
        import tkinter as tk
        from tkinter import ttk
        from single_import import launch_live_setup
        gate=threading.Event();self.addCleanup(gate.set)
        original=tk.Tk
        errors=[]
        main_thread=threading.get_ident()
        class SlowCB:
            def __init__(self,http,config,write):self.http,self.config=http,config
            def discover(self):
                if threading.get_ident()==main_thread:raise AssertionError('Network work ran on UI thread')
                gate.wait(3)
                self.spec={'paths':{'/projects':{'get':{'operationId':'getProjects'}}}}
            def operation(self,name):return self.config['operations'][name]
            def call(self,name):return [{'id':123,'name':'late list'}]
        def descendants(w):
            yield w
            for c in w.winfo_children():yield from descendants(c)
        class Window(original):
            def __init__(window,*a,**k):
                super().__init__(*a,**k)
                window.after(100,window.connect)
                window.after(4000,window.destroy)
            def connect(window):
                widgets=list(descendants(window))
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text').startswith('연결 /')).invoke()
                window.after(100,window.cancel_and_check)
            def cancel_and_check(window):
                try:
                    button=next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='취소')
                    if str(button.cget('state'))!='normal':raise AssertionError('Cancel not enabled during blocked Swagger')
                    button.invoke();gate.set()
                    window.after(250,window.verify)
                except Exception as exc:errors.append(exc);window.destroy()
            def verify(window):
                try:
                    labels=[window.getvar(w.cget('textvariable')) for w in descendants(window) if isinstance(w,(ttk.Label,ttk.Entry)) and w.cget('textvariable')]
                    if not any('취소됨' in s for s in labels):raise AssertionError('Cancellation message missing')
                    copy_button=next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='오류 복사')
                    copy_button.invoke()
                    if '취소됨' not in window.clipboard_get():raise AssertionError('Status copy failed')
                    for w in descendants(window):
                        if isinstance(w,ttk.Combobox) and w.cget('values'):raise AssertionError('Cancelled late list was applied')
                except Exception as exc:errors.append(exc)
                finally:window.destroy()
        config={'jira':{'url':'https://jira.test','auth':'none'},'codebeamer':{'url':'https://cb.test','auth':'none'}}
        with tempfile.TemporaryDirectory() as directory,patch.object(tk,'Tk',Window),patch('clients.codebeamer_client.CodebeamerClient',SlowCB):
            launch_live_setup(config,directory)
        self.assertEqual(errors,[])


class TrackerPathTests(unittest.TestCase):
    def client(self,path):
        from clients.codebeamer_client import CodebeamerClient
        http=type('HTTP',(),{'request':lambda self,*a,**k: []})()
        client=CodebeamerClient(http,{'operations':{'trackers_get':{'method':'GET','path':path}}})
        client.spec={'paths':{path:{'get':{}}}}
        return client

    def test_generic_project_id_is_bound_for_tracker_list(self):
        cb=self.client('/v3/projects/{id}/trackers')
        with patch.object(cb.http,'request',return_value=[]) as request:
            cb.call('trackers_get',{'projectId':123})
        self.assertEqual(request.call_args.args[1],'/v3/projects/123/trackers')

    def test_other_missing_parameter_is_named_and_no_request_is_sent(self):
        cb=self.client('/v3/projects/{projectId}/trackers/{trackerTypeId}')
        with patch.object(cb.http,'request') as request:
            with self.assertRaisesRegex(ApiError,'trackers_get: trackerTypeId'):
                cb.call('trackers_get',{'projectId':123})
        request.assert_not_called()


class TrackerDiscoveryTests(unittest.TestCase):
    def test_project_reads_prefer_v3_and_bind_generic_detail_id(self):
        from clients.codebeamer_3007 import project_reads
        from clients.codebeamer_client import CodebeamerClient
        from unittest.mock import Mock
        http=Mock();http.request.side_effect=[[{'id':1}],{'id':1,'name':'Test'}]
        cb=CodebeamerClient(http,{'operations':{'prpject_get':{'method':'GET','path':'/wrong'}}})
        cb.spec={'paths':{p:{'get':{}} for p in ('/projects','/v3/projects','/projects/{projectId}','/v3/projects/{id}')}}
        selected=project_reads(cb)
        self.assertEqual(selected['projects_get']['path'],'/v3/projects')
        self.assertEqual(selected['project_get']['path'],'/v3/projects/{id}')
        self.assertEqual(cb.call('projects_get'),[{'id':1}])
        self.assertEqual(cb.call('project_get',{'projectId':1})['id'],1)
        self.assertEqual([call.args[:2] for call in http.request.call_args_list],
                         [('GET','/v3/projects'),('GET','/v3/projects/1')])

    def test_project_reads_do_not_choose_unrelated_project_route(self):
        from clients.codebeamer_3007 import project_reads
        from clients.codebeamer_client import CodebeamerClient
        cb=CodebeamerClient(None,{'operations':{}})
        cb.spec={'paths':{'/other/projects':{'get':{}},'/v3/projects/{id}':{'get':{}}}}
        with self.assertRaisesRegex(ApiError,'projects_get'):
            project_reads(cb)

    def test_typed_operation_id_is_replaced_with_actual_complete_list(self):
        from clients.codebeamer_client import CodebeamerClient
        from clients.codebeamer_3007 import project_tracker_list
        typed='/projects/{projectId}/trackers/types/{typeId}'
        full='/projects/{projectId}/trackers'
        cb=CodebeamerClient(None,{'operations':{'trackers_get':{'method':'GET','path':typed}}})
        cb.spec={'paths':{typed:{'get':{'operationId':'getTrackers'}},full:{'get':{'operationId':'differentServerName'}}}}
        self.assertEqual(project_tracker_list(cb)['path'],full)

    def test_type_id_is_not_invented_if_full_list_is_missing(self):
        from clients.codebeamer_client import CodebeamerClient
        from clients.codebeamer_3007 import project_tracker_list
        path='/projects/{projectId}/trackers/types/{typeId}'
        cb=CodebeamerClient(None,{'operations':{}})
        cb.spec={'paths':{path:{'get':{'operationId':'getTrackers'}}}}
        with self.assertRaisesRegex(ApiError,'typeId'):
            project_tracker_list(cb)
        self.assertNotIn('trackers_get',cb.config['operations'])

    def typed_client(self, prefix=''):
        from clients.codebeamer_client import CodebeamerClient
        from clients.codebeamer_3007 import project_tracker_list
        from unittest.mock import Mock
        cb=CodebeamerClient(Mock(),{'operations':{}})
        cb.spec={'paths':{prefix+'/trackers/types':{'get':{}},prefix+'/projects/{id}/trackers/types/{typeId}':{'get':{}}}}
        project_tracker_list(cb)
        return cb

    def test_types_are_read_and_trackers_deduplicated_using_selected_project(self):
        for prefix in ('','/v3'):
            cb=self.typed_client(prefix)
            cb.http.request.side_effect=[[{'id':7},{'id':9},{'id':7}], [{'id':21,'name':'A'}], [{'id':21,'name':'A'},{'id':22,'name':'B'}]]
            self.assertEqual([r['id'] for r in cb.call('trackers_get',{'projectId':123})],[21,22])
            self.assertEqual([(c.args[0],c.args[1]) for c in cb.http.request.call_args_list], [('GET',prefix+'/trackers/types'),('GET',prefix+'/projects/123/trackers/types/7'),('GET',prefix+'/projects/123/trackers/types/9')])

    def test_partial_type_failure_is_not_success(self):
        cb=self.typed_client()
        cb.http.request.side_effect=[[{'id':7},{'id':9}],[{'id':21}],ApiError('HTTP 403')]
        with self.assertRaisesRegex(ApiError,'typeId=9.*HTTP 403'):
            cb.call('trackers_get',{'projectId':123})

    def test_unknown_type_response_does_not_invent_ids(self):
        cb=self.typed_client()
        cb.http.request.return_value={'items':[{'id':7}],'total':10}
        with self.assertRaisesRegex(ApiError,'Tracker Type GET'):
            cb.call('trackers_get',{'projectId':123})
        self.assertEqual(cb.http.request.call_count,1)

    def test_duplicate_version_paths_select_v3_independent_of_order(self):
        from clients.codebeamer_3007 import project_tracker_list
        from clients.codebeamer_client import CodebeamerClient
        paths=['/projects/{projectId}/trackers','/v3/projects/{projectId}/trackers']
        for ordered in (paths,paths[::-1]):
            cb=CodebeamerClient(None,{'operations':{}})
            cb.spec={'paths':{p:{'get':{}} for p in ordered}}
            self.assertEqual(project_tracker_list(cb)['path'],paths[1])

    def test_typed_discovery_also_prefers_v3_aliases(self):
        from clients.codebeamer_3007 import project_tracker_list
        cb=self.typed_client()
        cb.spec['paths'].update({'/v3'+p:definition for p,definition in list(cb.spec['paths'].items())})
        self.assertEqual(project_tracker_list(cb)['path'],'/v3/projects/{id}/trackers/types/{typeId}')
        self.assertEqual(cb.operation('tracker_types_get')['path'],'/v3/trackers/types')

    def test_unrelated_prefix_remains_ambiguous(self):
        from clients.codebeamer_3007 import project_tracker_list
        cb=self.typed_client()
        cb.config['operations'] = {}
        cb.spec={'paths':{p:{'get':{}} for p in ('/other/projects/{projectId}/trackers','/v3/projects/{projectId}/trackers')}}
        with self.assertRaisesRegex(ApiError,'여러 개'):
            project_tracker_list(cb)


class SwaggerEditorDiscoveryTests(unittest.TestCase):
    class HTTP:
        base='https://cb.test/cb/api'
        def __init__(self,responses):self.responses,self.calls=responses,[]
        def request(self,method,path,raw=False):
            self.calls.append((method,path,raw))
            route=urllib.parse.urlsplit(path).path
            value=self.responses.get(route)
            if isinstance(value,Exception):raise value
            if value is None:raise ApiError('GET request failed: HTTP 404')
            if isinstance(value,bytes):return value
            return json.dumps(value).encode()

    def test_cb_api_base_reads_editor_link_and_actual_same_origin_json(self):
        from clients.codebeamer_client import CodebeamerClient
        html=b'<script>SwaggerUIBundle({url: "/cb/v3/swagger/openapi.json"})</script>'
        spec={'openapi':'3.0.0','paths':{'/v3/projects':{'get':{}}}}
        http=self.HTTP({'/cb/v3/swagger/editor.spr':html,'/cb/v3/swagger/openapi.json':spec})
        cb=CodebeamerClient(http,{'operations':{}});cb.discover()
        self.assertEqual(cb.spec,spec)
        self.assertEqual(cb.config['discovered_swagger_url'],'https://cb.test/cb/v3/swagger/openapi.json')
        editor_index=next(i for i,c in enumerate(http.calls) if c[1].endswith('/editor.spr'))
        self.assertTrue(all('/cb/' in c[1] and '/cb/api/' not in c[1] for c in http.calls[:editor_index+1]))

    def test_supplied_editor_url_and_embedded_openapi_are_supported(self):
        from clients.codebeamer_client import CodebeamerClient
        spec={'openapi':'3.0.0','paths':{'/v3/items':{'get':{}}}}
        page=('<html><script>window.spec = '+json.dumps(spec)+'</script></html>').encode()
        cb=CodebeamerClient(self.HTTP({'/cb/v3/swagger/editor.spr':page}),
                            {'swagger_url':'/cb/v3/swagger/editor.spr','operations':{}})
        cb.discover();self.assertEqual(cb.spec,spec)

    def test_editor_generic_url_key_is_followed_without_guessing_its_name(self):
        from clients.codebeamer_client import CodebeamerClient
        spec={'openapi':'3.0.0','paths':{'/v3/items':{'get':{}}}}
        page=b'<script>SwaggerUIBundle({url: "/cb/contracts/v3"})</script>'
        cb=CodebeamerClient(self.HTTP({'/cb/v3/swagger/editor.spr':page,'/cb/contracts/v3':spec}),
                            {'swagger_url':'/cb/v3/swagger/editor.spr','operations':{}})
        cb.discover();self.assertEqual(cb.config['discovered_swagger_url'],'https://cb.test/cb/contracts/v3')

    def test_cross_origin_or_linkless_html_fails_without_following_it(self):
        from clients.codebeamer_client import CodebeamerClient
        page=b'<script>SwaggerUIBundle({url:"https://evil.test/openapi.json"})</script>'
        http=self.HTTP({'/cb/v3/swagger/editor.spr':page})
        cb=CodebeamerClient(http,{'swagger_url':'/cb/v3/swagger/editor.spr','operations':{}})
        with self.assertRaisesRegex(ApiError,'HTML did not expose'):
            cb.discover()
        self.assertFalse(any('evil.test' in call[1] for call in http.calls))

    def test_invalid_swagger_file_has_controlled_error(self):
        from clients.codebeamer_client import CodebeamerClient
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,'invalid.json')
            with open(path,'w',encoding='utf-8') as stream:stream.write('<html>not json</html>')
            cb=CodebeamerClient(self.HTTP({}),{'swagger_url':'file:'+path,'operations':{}})
            with self.assertRaisesRegex(ApiError,'could not be read as JSON'):
                cb.discover()
