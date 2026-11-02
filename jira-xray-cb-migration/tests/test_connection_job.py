import os
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
