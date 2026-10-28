import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from clients.http import Http, ApiError
from clients.jira_client import JiraClient
from clients.codebeamer_client import CodebeamerClient
from migration.state import State
from migration.fields import payload
from migration.history_replay import timeline, replay
from migration.attachments import transfer


class Handler(BaseHTTPRequestHandler):
    requests = []
    revision = 1
    binary = b'actual binary\x00\xff'
    def log_message(self, *args):
        pass
    def do_GET(self):
        Handler.requests.append(('GET', self.path))
        if self.path.startswith('/comments'):
            start = 1 if 'startAt=1' in self.path else 0
            self.respond({'startAt': start, 'total': 2, 'comments': [{'id': str(start)}]})
        elif self.path == '/spec':
            self.respond({'paths': {'/item/{itemId}': {'get': {}, 'patch': {}}, '/history/{itemId}': {'get': {}}, '/upload/{itemId}': {'post': {}}, '/download/{attachmentId}': {'get': {}}}})
        elif self.path.startswith('/history/'):
            self.respond({'versions': [{'itemRevision': {'version': i}} for i in range(1, Handler.revision + 1)]})
        elif self.path.startswith(('/source', '/download/')):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(Handler.binary)
        elif self.path == '/basic':
            import base64
            expected = 'Basic ' + base64.b64encode(b'testuser:test-password').decode('ascii')
            if self.headers.get('Authorization') == expected:
                self.respond({'authenticated': True})
            else:
                self.send_response(401)
                self.end_headers()
        elif self.path == '/bad':
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'private-token-secret')
        elif self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'https://other.example/steal')
            self.end_headers()
        else:
            self.respond({'name': 'new'})
    def do_PATCH(self):
        Handler.requests.append(('PATCH', self.path))
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        Handler.revision += 1
        self.respond({'id': 7})
    def do_POST(self):
        Handler.requests.append(('POST', self.path))
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.respond({'id': 9})
    def respond(self, body):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
    def setUp(self):
        Handler.requests = []
        Handler.revision = 1
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name)
        self.addCleanup(self.state.db.close)
        self.http = Http(self.url, log=Path(self.tmp.name) / 'audit.jsonl')
    def cb(self, write=True):
        c = CodebeamerClient(self.http, {'swagger_url': '/spec', 'operations': {
            'item_update': {'path': '/item/{itemId}', 'method': 'PATCH'},
            'history': {'path': '/history/{itemId}', 'method': 'GET'},
            'attachment_upload': {'path': '/upload/{itemId}', 'method': 'POST'},
            'attachment_get': {'path': '/download/{attachmentId}', 'method': 'GET'}}}, write)
        c.discover()
        return c
    def test_paginated_comments(self):
        self.assertEqual([v['id'] for v in JiraClient(self.http, {'page_size': 1}).pages('/comments', 'comments')], ['0', '1'])
    def test_dry_run_never_writes(self):
        with self.assertRaises(ApiError):
            self.cb(False).call('item_update', {'itemId': 7}, {'name': 'x'})
        self.assertFalse(any(m != 'GET' for m, _ in Handler.requests))
    def test_unadvertised_endpoint_rejected(self):
        c = self.cb()
        c.config['operations']['item_update']['path'] = '/invented'
        with self.assertRaises(ApiError):
            c.call('item_update', {}, {})
    def test_history_replay_creates_revision_once(self):
        c = self.cb()
        issue = {'fields': {'name': 'new', 'created': '2020-01-01', 'creator': 'u'}, '_history': [
            {'id': 'h1', 'created': '2020-01-02', 'items': [{'fieldId': 'name', 'from': 'old', 'to': 'new'}]}]}
        events = timeline(issue, {'name': {'history_encoding': 'scalar'}})
        self.assertEqual(events[0][1]['name'], 'old')
        replay(c, self.state, 'X-1', 7, events, lambda f: {'name': f['name']})
        replay(c, self.state, 'X-1', 7, events, lambda f: {'name': f['name']})
        self.assertEqual(Handler.revision, 2)
        self.assertEqual(self.state.get('history:X-1:h1')[1]['revision'], 2)
        self.assertFalse(self.state.get('history:X-1:h1')[1]['native_timestamp_restored'])
    def test_uncertain_write_is_not_repeated(self):
        def uncertain():
            raise ApiError('timeout after remote create')
        with self.assertRaises(ApiError):
            self.state.once('create', uncertain)
        with self.assertRaises(ApiError):
            self.state.once('create', lambda: self.fail('unsafe retry'))
    def test_null_and_user_mapping(self):
        result = payload({'a': None, 'user': {'name': 'alice'}},
                         {'a': {'target': 'x', 'type': 'text'}, 'user': {'target': 'owner', 'type': 'user'}},
                         {'user_mapping': {'alice': {'id': 5}}})
        self.assertEqual(result, {'x': None, 'owner': {'id': 5}})
        with self.assertRaises(ApiError):
            payload({'user': {'name': 'missing'}}, {'user': {'target': 'owner', 'type': 'user'}}, {})
    def test_attachment_binary_roundtrip_and_dedup(self):
        c = self.cb()
        att = {'id': 'a1', 'filename': '증거.bin', 'size': len(Handler.binary), 'content': self.url + '/source'}
        a = transfer(self.http, c, self.state, 'X-1', 7, att)
        b = transfer(self.http, c, self.state, 'X-1', 7, att)
        self.assertEqual(a, b)
        self.assertEqual(len([m for m, _ in Handler.requests if m == 'POST']), 1)
        self.assertEqual(len(a['sha256']), 64)
    def test_attachment_size_mismatch_prevents_upload(self):
        with self.assertRaises(ApiError):
            transfer(self.http, self.cb(), self.state, 'X-1', 7, {'id': 1, 'filename': 'a', 'size': 0, 'content': '/source'})
        self.assertFalse(any(m == 'POST' for m, _ in Handler.requests))
    def test_cross_origin_and_redirect_refused(self):
        with self.assertRaises(ApiError):
            self.http.request('GET', 'https://other.example/data')
        with self.assertRaises(ApiError):
            self.http.request('GET', '/redirect')
    def test_error_response_and_body_not_logged(self):
        with self.assertRaises(ApiError) as error:
            self.http.request('GET', '/bad')
        audit = (Path(self.tmp.name) / 'audit.jsonl').read_text()
        self.assertNotIn('private-token-secret', audit + str(error.exception))
    def test_unsupported_history_does_not_write(self):
        issue = {'fields': {'x': {}}, '_history': [{'id': 1, 'created': '2020', 'items': [{'fieldId': 'x'}]}]}
        with self.assertRaises(ApiError):
            timeline(issue, {'x': {'type': 'raw'}})
        self.assertFalse(any(m != 'GET' for m, _ in Handler.requests))

if __name__ == '__main__':
    unittest.main()

class EngineTests(unittest.TestCase):
    def test_full_pipeline_create_verify_and_resume(self):
        from copy import deepcopy
        from migration.engine import Engine
        class Jira:
            config = {'xray_run_field': 'runs'}
            http = None
            def fields(self):
                return [{'id': 'summary'}, {'id': 'issuetype'}, {'id': 'runs'}]
            def projects(self):
                return [{'key': 'ANY'}]
            def issues(self, project):
                return [{'key': 'ANY-1'}]
            def bundle(self, key):
                return {'id': '101', 'key': key, 'fields': {'summary': 'Hello', 'issuetype': {'id': '2'}, 'runs': None},
                        '_history': [], '_comments': [], '_worklogs': []}
        class Xray:
            def runs(self, issue, field):
                return []
        class CB:
            created = 0
            item = None
            def discover(self):
                return []
            def fields(self, tracker):
                return [{'id': i, 'typeId': 0, 'mandatory': False, 'multipleSelection': False, 'options': None, 'reference': None} for i in range(3)]
            def call(self, name, params=None, data=None):
                if name == 'project_get':
                    return {'id': 10}
                if name == 'tracker_get':
                    return {'id': 20}
                if name == 'lookup':
                    return [{'id': 99}] if self.item else []
                if name == 'item_create':
                    self.created += 1
                    self.item = deepcopy(data)
                    return {'id': 99}
                if name == 'item_get':
                    return deepcopy(self.item)
                raise AssertionError(name)
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.addCleanup(state.db.close)
            mapping = {name: {'target': name, 'type': 'raw', 'field_id': i, 'expected_type_id': 0,
                             'expected_mandatory': False, 'expected_multiple': False, 'expected_options': None,
                             'expected_reference': None} for i, name in enumerate(['summary', 'issuetype', 'runs'])}
            settings = {'project_mapping': {'ANY': 10}, 'tracker_mapping': {'ANY:2': 20}, 'field_mapping': {'2': mapping}}
            cb = CB()
            first = Engine(Jira(), Xray(), cb, state, settings, True).migrate()
            self.assertTrue(first['complete'], first)
            second = Engine(Jira(), Xray(), cb, state, settings, True).migrate()
            self.assertTrue(second['complete'], second)
            self.assertEqual(cb.created, 1)
            self.assertEqual(second['counts'], {'source_issues': 1, 'verified_issues': 1})

    def test_target_mismatch_is_not_success(self):
        from migration.records import mapped_record
        class CB:
            def call(self, name, params=None, data=None):
                return [] if name.endswith('_lookup') else {'id': 1, 'body': 'wrong'}
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.addCleanup(state.db.close)
            with self.assertRaises(ApiError):
                mapped_record(CB(), state, {}, 'comment', '101:1', {'body': 'expected'}, {'itemId': 1},
                              {'body': {'type': 'text', 'target': 'body'}})


class AuthenticationTests(unittest.TestCase):
    setUpClass = classmethod(MigrationTests.setUpClass.__func__)
    tearDownClass = classmethod(MigrationTests.tearDownClass.__func__)
    setUp = MigrationTests.setUp
    def test_basic_auth_and_audit_redaction(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'TEST_CB_USER': 'testuser', 'TEST_CB_PASS': 'test-password'}):
            client = Http(self.url, auth='basic', username_env='TEST_CB_USER', password_env='TEST_CB_PASS',
                          log=Path(self.tmp.name) / 'basic-audit.jsonl')
        self.assertEqual(client.request('GET', '/basic'), {'authenticated': True})
        log = (Path(self.tmp.name) / 'basic-audit.jsonl').read_text()
        self.assertNotIn('test-password', log)
        self.assertNotIn('Authorization', log)

    def test_missing_basic_credentials_rejected(self):
        with self.assertRaises(ApiError):
            Http(self.url, auth='basic')

    def test_http_requires_opt_in_and_keeps_origin_boundary(self):
        client = Http('http://10.11.118.180:8081/cb', auth='none')
        with self.assertRaises(ApiError):
            client.url('/v3/projects')
        allowed = Http('http://10.11.118.180:8081/cb', auth='none', allow_http=True)
        self.assertEqual(allowed.url('/v3/projects'), 'http://10.11.118.180:8081/cb/v3/projects')
        with self.assertRaises(ApiError):
            allowed.url('http://other.example/data')

    def test_interactive_password_not_persisted(self):
        from unittest.mock import patch
        with patch('builtins.input', return_value='testuser'), patch('getpass.getpass', return_value='test-password'):
            client = Http(self.url, auth='basic', prompt_auth=True)
        self.assertEqual(client.request('GET', '/basic'), {'authenticated': True})
