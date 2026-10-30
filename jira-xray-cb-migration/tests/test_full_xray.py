import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clients.http import ApiError
from migration.engine import Engine
from migration.state import State


class FullXrayTests(unittest.TestCase):
    def run_profile(self, missing=False, fail=False, write=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = State(temporary.name)
        self.addCleanup(state.db.close)
        objects = []
        for i, kind in enumerate(('Test', 'Test Execution', 'Test Set', 'Test Plan'), 1):
            objects.append({'id': str(i), 'key': 'P-' + str(i), 'fields': {
                'issuetype': {'id': str(i), 'name': kind}, 'members': []}, '_runs': []})
        objects[-1]['fields']['members'] = ['P-404' if missing else 'P-1']
        class Jira:
            def __init__(self, *args): pass
            def fields(self): return []
            def projects(self): return [{'key': 'P'}]
            def issues(self, project): return objects
            def bundle(self, key): return next(x for x in objects if x['key'] == key)
            def execution_context(self, issue): return issue
        settings = {'mode': 'full-xray', 'full_xray': {
            'issue_types': {'plan': 'Test Plan', 'test': 'Test', 'set': 'Test Set', 'execution': 'Test Execution'},
            'relationships': [{'from': 'plan', 'to': 'test', 'path': 'fields.members', 'identity_path': '$'}]},
            'relation_adapter': {'fields': {'targetId': {'target': 'targetId', 'type': 'raw'},
                                             'relationKind': {'target': 'kind', 'type': 'text'}}}}
        cb = SimpleNamespace(discover=lambda: None, operation=lambda name: {})
        engine = Engine(SimpleNamespace(http=None, config={}), SimpleNamespace(runs=lambda *args: []), cb, state, settings, write)
        writes = []
        def issue(project, obj, definitions):
            if fail and obj['key'] == 'P-1': raise ApiError('bad mapping')
            engine.report['issues'].append({'status': 'verified' if engine.write else 'planned'})
            if engine.write:
                writes.append(obj['fields']['issuetype']['name'])
                state.mark('item:' + obj['id'], 'done', {'id': int(obj['id']) + 100})
        engine.issue = issue
        engine.children = lambda *args: None
        engine.relations = lambda *args: None
        with patch('migration.full_xray.JiraClient', Jira), patch('migration.full_xray.mapped_record') as relation:
            report = engine.migrate()
            return report, writes, relation.call_count

    def test_plan_first_then_memberships(self):
        report, writes, calls = self.run_profile()
        self.assertTrue(report['complete'])
        self.assertEqual(writes, ['Test Plan', 'Test', 'Test Set', 'Test Execution'])
        self.assertEqual(calls, 1)
        self.assertEqual(report['relationships'][0]['status'], 'verified')

    def test_missing_dependency_blocks_all_writes(self):
        report, writes, calls = self.run_profile(missing=True)
        self.assertFalse(report['complete'])
        self.assertEqual((writes, calls), ([], 0))

    def test_invalid_object_blocks_plan_creation(self):
        report, writes, calls = self.run_profile(fail=True)
        self.assertFalse(report['complete'])
        self.assertEqual((writes, calls), ([], 0))

    def test_dry_run_never_writes(self):
        report, writes, calls = self.run_profile(write=False)
        self.assertFalse(report['complete'])
        self.assertEqual((writes, calls), ([], 0))
        self.assertEqual(report['relationships'][0]['status'], 'planned')
