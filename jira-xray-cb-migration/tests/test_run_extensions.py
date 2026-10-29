import copy
import tempfile
import unittest
from clients.http import ApiError
from clients.run_events import collect
from migration.run_history import snapshots, replay_run
from migration.run_references import candidates, resolve_references
from migration.run_schema import sync_run_fields
from migration.state import State
from migration.records import select_records


class RunExtensions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name)
        self.addCleanup(self.state.db.close)

    def test_plan_and_test_set_field_values_resolve_without_test_mapping(self):
        run = {'Test Set': [{'key': 'P-2'}]}
        execution = {'fields': {'customfield_8': ['P-1']}}
        definitions = [{'id': 'customfield_8', 'name': 'Test Plan'}]
        class CB:
            def call(self, name, params):
                if name == 'reference_lookup':
                    return [{'id': {'P-1': 11, 'P-2': 22}[params['sourceValue']]}]
                return {'id': params['itemId']}
        refs = resolve_references(CB(), run, execution, definitions, {})
        self.assertEqual({r['role']: r['target']['id'] for r in refs}, {'test_plan': 11, 'test_set': 22})
        self.assertEqual(len(candidates(run, execution, definitions)), 2)

    def test_ambiguous_reference_is_not_linked(self):
        class CB:
            def call(self, *args):
                return [{'id': 1}, {'id': 2}]
        with self.assertRaises(ApiError):
            resolve_references(CB(), {'Test Plan': 'P-1'}, {'fields': {}}, [], {})

    def test_field_creation_update_and_all_permissions(self):
        class CB:
            def __init__(self):
                self.config = {'fields': [{'label': 'status', 'typeId': 1, 'mandatory': False, 'permission': 'Restricted'},
                                          {'label': 'existing system', 'typeId': 9, 'permission': 'Restricted'}]}
                self.writes = 0
            def configuration(self, tracker):
                return copy.deepcopy(self.config)
            def operation(self, name):
                self.assert_name = name
            def call(self, name, params, data):
                self.writes += 1
                self.config = copy.deepcopy(data)
                return {}
        cb = CB()
        settings = {'field_sync': {'enabled': True, 'unrestricted_permission': 'Unrestricted',
                    'definitions': [{'label': 'status', 'typeId': 1, 'mandatory': True}, {'label': 'new', 'typeId': 2}]}}
        plan = sync_run_fields(cb, self.state, 7, settings, False)
        self.assertEqual(cb.writes, 0)
        self.assertEqual([p['action'] for p in plan], ['update', 'create'])
        sync_run_fields(cb, self.state, 7, settings, True)
        self.assertEqual(cb.writes, 1)
        self.assertTrue(all(f['permission'] == 'Unrestricted' for f in cb.config['fields']))
        sync_run_fields(cb, self.state, 7, settings, True)
        self.assertEqual(cb.writes, 1)
        self.assertEqual(cb.config['fields'][0]['mandatory'], True)

    def test_type_conflict_does_not_overwrite(self):
        class CB:
            def configuration(self, tracker):
                return {'fields': [{'label': 'x', 'typeId': 1}]}
            def call(self, *args):
                raise AssertionError('No writes allowed')
        with self.assertRaises(ApiError):
            sync_run_fields(CB(), self.state, 7, {'field_sync': {'enabled': True, 'unrestricted_permission': 'Unrestricted',
                'definitions': [{'label': 'x', 'typeId': 2}]}}, True)

    def test_history_and_non_field_activity_generate_real_revisions_and_resume(self):
        events = [{'id': 'history:1', 'at': '2026-01-02T00:00:00Z', 'author': 'u1',
                   'changes': [{'field': 'status', 'before': 'TODO', 'after': 'PASS'}]},
                  {'id': 'activity:2', 'at': '2026-01-03T00:00:00Z', 'author': 'u2', 'changes': []}]
        created = {'id': 'created', 'at': '2026-01-01T00:00:00Z', 'author': 'creator'}
        latest = {'status': 'PASS'}
        mappings = {'status': {'target': 'status', 'type': 'raw'}, '_sourceEvent': {'target': 'event', 'type': 'raw'}}
        class CB:
            def __init__(self):
                self.body, self.revision, self.writes = None, 0, []
            def operation(self, name):
                if name not in ('run_lookup', 'run_create', 'run_update', 'run_get', 'run_history'):
                    raise AssertionError(name)
            def call(self, name, params, data=None):
                if name == 'run_lookup':
                    return [{'id': 8}] if self.body else []
                if name == 'run_create':
                    self.body = copy.deepcopy(data)
                    self.revision = 1
                    self.writes.append(name)
                    return {'id': 8}
                if name == 'run_update':
                    self.body = copy.deepcopy(data)
                    self.revision += 1
                    self.writes.append(name)
                    return {}
                if name == 'run_get':
                    return copy.deepcopy(self.body)
                if name == 'run_history':
                    return {'versions': [{'itemRevision': {'version': i}} for i in range(1, self.revision + 1)]}
                raise AssertionError(name)
        cb = CB()
        self.assertEqual(snapshots(latest, events, created)[0][1]['status'], 'TODO')
        for _ in range(2):
            replay_run(cb, self.state, {}, 'r1', latest, events, created, {'trackerId': 7}, mappings)
        self.assertEqual(cb.revision, 3)
        self.assertEqual(cb.writes, ['run_create', 'run_update', 'run_update'])
        self.assertEqual(cb.body['event']['id'], 'activity:2')
        checkpoint = self.state.get('run-replay:r1:event:history:1')[1]
        self.assertFalse(checkpoint['native_timestamp_restored'])

    def test_inconsistent_history_rejected_before_write(self):
        events = [{'id': 'h1', 'at': '2026-01-02T00:00:00Z', 'changes': [{'field': 'status', 'before': 'TODO', 'after': 'FAIL'}]}]
        with self.assertRaises(ApiError):
            snapshots({'status': 'PASS'}, events, {'at': '2026-01-01T00:00:00Z'})

    def test_event_pagination_completeness(self):
        class HTTP:
            def request(self, method, path):
                offset = 1 if 'startAt=1' in path else 0
                return {'total': 2, 'rows': [{'id': str(offset), 'at': '2026-01-02T00:00:00Z', 'author': 'u'}]}
        profile = {'path': '/actual/{runId}', 'items_path': 'rows', 'total_path': 'total', 'pagination': True,
                   'fields': {'id': 'id', 'at': 'at', 'author': 'author'}}
        events = collect(HTTP(), 'r1', profile, 'activity')
        self.assertEqual([e['id'] for e in events], ['activity:0', 'activity:1'])
        profile['pagination'] = False
        with self.assertRaises(ApiError):
            collect(HTTP(), 'r1', profile, 'activity')

    def test_nested_step_evidence_inventory(self):
        data = {'steps': [{'evidence': [{'id': 1}]}, {'evidence': [{'id': 2}, {'id': 3}]}]}
        self.assertEqual([x['id'] for x in select_records(data, 'steps.*.evidence')], [1, 2, 3])

    def test_auto_attachment_inventory_finds_step_evidence(self):
        from migration.run_files import inventory
        data = {'attachments': [], 'steps': [{'evidences': [{'id': 'e1', 'fileName': 'proof.bin', 'fileSize': 3, 'downloadUrl': '/download/e1'}]}]}
        self.assertEqual(inventory(data), [{'id': 'e1', 'filename': 'proof.bin', 'size': 3, 'content': '/download/e1'}])
        with self.assertRaises(ApiError):
            inventory({'evidence': [{'id': 'e1', 'filename': 'proof.bin'}]})

    def test_unrestricted_object_representation(self):
        class CB:
            def configuration(self, tracker):
                return {'fields': [{'label': 'known', 'typeId': 1, 'permission': {'name': 'Unrestricted', 'code': 'U'}}]}
        plan = sync_run_fields(CB(), self.state, 7, {'field_sync': {'enabled': True,
            'unrestricted_permission': 'Unrestricted', 'definitions': [{'label': 'new', 'typeId': 2}]}}, False)
        self.assertEqual(plan[0]['action'], 'create')

    def test_unrestricted_readback_failure_is_not_success(self):
        class CB:
            def configuration(self, tracker):
                return {'fields': [{'label': 'status', 'typeId': 1, 'permission': 'Restricted'}]}
            def operation(self, name):
                pass
            def call(self, *args):
                return {}
        with self.assertRaises(ApiError):
            sync_run_fields(CB(), self.state, 7, {'field_sync': {'enabled': True,
                'unrestricted_permission': 'Unrestricted', 'definitions': [{'label': 'status', 'typeId': 1}]}}, True)

    def test_swagger_auto_discovery_is_read_only_and_run_history_forbids_post(self):
        from clients.codebeamer_client import CodebeamerClient
        class HTTP:
            base = 'https://example.test/cb/api'
            def __init__(self):
                self.calls = []
            def request(self, method, path):
                self.calls.append((method, path))
                if path.endswith('/swagger.json'):
                    return {'paths': {'/actual/{recordId}': {'get': {'operationId': 'historyRead'}, 'post': {}}}}
                raise ApiError('Not found')
        http = HTTP()
        cb = CodebeamerClient(http, {'operations': {'run_history': {'operationId': 'historyRead'}}}, True)
        cb.discover()
        self.assertTrue(all(method == 'GET' for method, _ in http.calls))
        self.assertEqual(cb.operation('run_history')['method'], 'GET')
        cb.config['operations']['run_history'] = {'path': '/actual/{recordId}', 'method': 'POST'}
        with self.assertRaises(ApiError):
            cb.operation('run_history')
