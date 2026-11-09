import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clients.http import ApiError
from migration.engine import Engine
from migration.state import State


class FullXrayTests(unittest.TestCase):
    def run_profile(self, missing=False, fail=False, write=True, with_run=False, missing_contract=False, context_edge=False, duplicate_plan=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = State(temporary.name)
        self.addCleanup(state.db.close)
        objects = []
        for i, kind in enumerate(('Test', 'Test Execution', 'Test Set', 'Test Plan'), 1):
            objects.append({'id': str(i), 'key': 'P-' + str(i), 'fields': {
                'issuetype': {'id': str(i), 'name': kind}, 'members': []}, '_runs': []})
        if duplicate_plan: objects.append(objects[-1])
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
        if with_run:
            settings['xray_run_tracker'] = 200
            settings['run_adapter'] = {'execution_mode': 'source_metadata', 'test_issue_id_path': 'testKey',
                                       'fields': {'_executionSource': {'target':'context','type':'json'}}}
            if missing_contract:
                settings['run_adapter']['fields'] = {}
        if context_edge:
            objects[-1]['fields']['executions'] = ['P-2']
            settings['full_xray']['relationships'].append({'from':'plan','to':'execution',
                'path':'fields.executions','identity_path':'$'})
        cb = SimpleNamespace(discover=lambda: None, operation=lambda name: {})
        engine = Engine(SimpleNamespace(http=None, config={}), SimpleNamespace(runs=lambda *args: ([{'id':'7','data':{'id':7,'testKey':'P-1','testExecKey':'P-2','status':'PASS'}}] if with_run else [])), cb, state, settings, write)
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
        with patch('migration.full_xray.JiraClient', Jira), patch('migration.full_xray.mapped_record') as relation, patch('migration.records.test_run', side_effect=lambda *a: (writes.append('Test Run') or {'id':300})) as run_write:
            report = engine.migrate()
            if with_run and run_write.called:
                args = run_write.call_args.args
                self.assertIsNone(args[5])
                self.assertEqual(args[4]['_executionSourceContext']['key'], 'P-2')
                self.assertEqual(args[4]['_executionSourceContext']['id'], '2')
            return report, writes, relation.call_count

    def test_cases_sets_then_plan_and_memberships(self):
        report, writes, calls = self.run_profile()
        self.assertFalse(report['complete'])  # Execution has no individual Run details.
        self.assertTrue(any('Individual Test Runs' in r['reason'] for r in report['limitations']))
        self.assertEqual(writes, ['Test', 'Test Set', 'Test Plan'])
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

    def test_execution_is_preserved_without_target_item(self):
        report, writes, _ = self.run_profile(with_run=True)
        self.assertTrue(report['complete'])
        self.assertEqual(writes,['Test','Test Set','Test Run','Test Plan'])
        self.assertNotIn('Test Execution', writes)
        self.assertEqual(report['source_executions'][0]['key'], 'P-2')
        self.assertEqual(report['runs'][0]['target_id'], 300)
        self.assertEqual(report['runs'][0]['test_key'], 'P-1')

    def test_missing_context_mapping_blocks_all_item_writes(self):
        report, writes, calls = self.run_profile(with_run=True, missing_contract=True)
        self.assertFalse(report['complete'])
        self.assertEqual(writes, [])
        self.assertEqual(calls, 0)
        self.assertIn('_executionSource', report['errors'][0]['error'])

    def test_plan_execution_relationship_preserved_not_fabricated(self):
        report, writes, calls = self.run_profile(with_run=True, context_edge=True)
        self.assertEqual(calls, 1)  # only the actual Plan -> Test edge
        self.assertEqual(report['execution_relationships_preserved'][0]['source'], 'P-4')
        self.assertEqual(report['execution_relationships_preserved'][0]['target'], 'P-2')
        self.assertFalse(report['complete'])  # target relation still needs a verified contract

    def test_wizard_blocks_execution_tracker_creation_without_writes(self):
        from migration.wizard import ImportWizard
        wizard = object.__new__(ImportWizard)
        wizard.config = {'jira':{}, 'migration':{}}
        wizard.plan = {'projects':{'P':{'types':[{'id':'2','name':'Test Execution'}], 'trackers':[]}}}
        with self.assertRaisesRegex(ApiError, 'automatic Tracker creation is disabled'):
            wizard.map_tracker('P','2',name='Execution')

    def test_wizard_requires_context_mode_before_write_contract_checks(self):
        from migration.wizard import ImportWizard
        wizard = object.__new__(ImportWizard)
        wizard.config = {'jira':{}, 'migration':{}}
        wizard.cb = SimpleNamespace(write=True)
        wizard.visibility_review = lambda: {}
        wizard.plan = {'scopes':{'P:2':{'type':{'id':'2','name':'Test Execution'}}}}
        with self.assertRaisesRegex(ApiError, 'requires full-xray mode'):
            wizard.execute(approved=True)
        self.assertFalse(wizard.cb.write)

    def test_duplicate_plan_export_does_not_create_a_second_object(self):
        report, writes, calls = self.run_profile(duplicate_plan=True)
        self.assertFalse(report['complete'])
        self.assertEqual(writes, [])
        self.assertEqual(calls, 0)
        self.assertIn('Duplicate source identity', report['errors'][0]['error'])
