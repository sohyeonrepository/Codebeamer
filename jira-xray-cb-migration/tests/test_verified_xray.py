"""Sanitized, synthetic IDs with the source shapes verified in the supplied analysis."""
import copy
import tempfile
import unittest
from clients.http import ApiError
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient
from migration.execution_events import execution_events
from migration.run_history import snapshots
from migration.source_runs import collect_sources
from migration.state import State


def execution():
    return {'id': '700', 'key': 'DEMO-20', 'fields': {
        'created': '2025-06-18T14:48:05.330+0900', 'creator': {'name': 'source-user'},
        'customfield_99999': [{'testKey': 'DEMO-10', 'testRunId': 17}], 'attachment': []},
        'changelog': {'startAt': 0, 'total': 5, 'histories': [
            {'id': '101', 'created': '2025-06-18T14:48:05.371+0900', 'author': {'name': 'source-user'},
             'items': [{'field': 'Test', 'from': None, 'to': 'DEMO-10', 'fromString': None, 'toString': 'Added new Test'},
                       {'field': 'Test', 'from': None, 'to': 'DEMO-11', 'fromString': None, 'toString': 'Added new Test'},
                       {'field': 'Test', 'from': None, 'to': 'DEMO-12', 'fromString': None, 'toString': 'Added new Test'}]},
            {'id': '102', 'created': '2025-06-18T14:48:33.629+0900', 'items': [{'field': 'Tests association with a Test Execution', 'from': None, 'to': None}]},
            {'id': '103', 'created': '2026-10-12T11:29:42+0900', 'items': [{'field': 'Workflow', 'from': 'a', 'to': 'b'}]},
            {'id': '104', 'created': '2026-10-12T15:22:46+0900', 'items': [{'field': 'Workflow', 'from': 'b', 'to': 'c'}]},
            {'id': '105', 'created': '2026-10-13T13:04:47+0900', 'items': [{'field': 'Workflow', 'from': 'c', 'to': 'd'}]}]}}


def run_detail():
    return {'id': 17, 'testKey': 'DEMO-10', 'testExecKey': 'DEMO-20', 'executedBy': 'source-user',
            'startedOn': '2025-06-18T14:48:42+09:00', 'finishedOn': '2025-06-18T14:48:42+09:00',
            'startedOnIso': '2025-06-18T14:48:42+09:00', 'finishedOnIso': '2025-06-18T14:48:42+09:00',
            'duration': 0, 'evidences': [], 'testVersion': 'v1', 'steps': [], 'status': 'PASS', 'color': '#95C160',
            'defects': [], 'testEnvironments': [], 'fixVersions': [], 'archived': False}


class VerifiedXrayTests(unittest.TestCase):
    def test_six_history_three_activity_without_duplicate_events(self):
        context = execution_events(execution())
        self.assertEqual(context['history_count'], 6)
        self.assertEqual(context['known_activity_count'], 3)
        self.assertEqual(len(context['events']), 5)
        self.assertFalse(context['run_history_complete'])
        self.assertEqual(len(context['events'][0]['raw']['items']), 3)
        timeline = snapshots({'status': 'PASS', '_executionHistoryState': context['history_state']}, context['events'], context['created'])
        self.assertEqual(len(timeline), 6)
        self.assertEqual(timeline[0][1]['_executionHistoryState'], [])
        self.assertEqual(timeline[-1][1]['_executionHistoryState'][-1]['items'][0]['to'], 'd')
        self.assertTrue(all(values['status'] == 'PASS' for _, values, _ in timeline))

    def test_excerpt_is_not_a_complete_history(self):
        issue = execution()
        issue['changelog']['histories'] = issue['changelog']['histories'][:2]
        with self.assertRaises(ApiError):
            execution_events(issue)

    def test_relationship_is_detected_by_actual_shape_without_pinned_customfield_id(self):
        self.assertEqual(XrayClient.relationship_field(execution()), 'customfield_99999')
        self.assertIsNone(XrayClient.relationship_field({'fields': {'other': []}}))
        issue = execution()
        issue['fields']['customfield_88888'] = copy.deepcopy(issue['fields']['customfield_99999'])
        with self.assertRaises(ApiError):
            XrayClient.relationship_field(issue)

    def test_no_failed_run_activity_endpoint_is_called(self):
        class HTTP:
            def __init__(self):
                self.calls = []
            def request(self, method, path):
                self.calls.append(path)
                if '/api/testrun/' in path:
                    return run_detail()
                raise AssertionError('Unsupported Activity request: ' + path)
        http = HTTP()
        config = {'collect_run_events': True, 'history_source': 'execution-changelog'}
        result = XrayClient(http, config).runs(execution(), None)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(result[0]['history_summary']['history_count'], 6)
        self.assertEqual(result[0]['events_scope'], 'execution-context')
        self.assertEqual(result[0]['data']['duration'], 0)
        self.assertEqual(result[0]['data']['steps'], [])

    def test_source_only_collection_reads_context_comments_worklogs(self):
        class HTTP:
            def request(self, method, path):
                if path == '/rest/api/2/field':
                    return [{'id': 'customfield_99999', 'name': 'Tests'}]
                if path == '/rest/api/2/project/DEMO':
                    return {'key': 'DEMO'}
                if path.startswith('/rest/api/2/search?'):
                    return {'startAt': 0, 'total': 1, 'issues': [{'key': 'DEMO-20'}]}
                if '/comment?' in path:
                    return {'startAt': 0, 'total': 1, 'comments': [{'id': 'c1', 'body': 'source'}]}
                if '/worklog?' in path:
                    return {'startAt': 0, 'total': 0, 'worklogs': []}
                if '/issue/DEMO-20?' in path:
                    issue = execution()
                    if 'expand=changelog' not in path:
                        issue.pop('changelog')
                    return issue
                if '/api/testrun/17' in path:
                    return run_detail()
                raise AssertionError(path)
        config = {'projects': ['DEMO'], 'history_source': 'execution-changelog', 'execution_issue_types': ['Test Execution']}
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.addCleanup(state.db.close)
            http = HTTP()
            fields, runs, errors = collect_sources(JiraClient(http, config, state.save), XrayClient(http, config, state.save), state)
            self.assertFalse(errors)
            self.assertEqual(len(runs), 1)
            issue, run = runs['17']
            self.assertEqual(len(issue['_comments']), 1)
            self.assertEqual(issue['_worklogs'], [])
            self.assertEqual(run['data']['testKey'], 'DEMO-10')

    def test_extract_command_requires_no_codebeamer_auth_or_api(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch
        import main
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / 'config.json'
            config_path.write_text(json.dumps({'jira': {'url': 'https://source.example', 'token_env': 'TEST_SOURCE_TOKEN'},
                'codebeamer': {'url': '', 'auth': 'basic'},
                'migration': {'state_dir': str(Path(root) / 'state'), 'mode': 'test-runs'}}), encoding='utf-8')
            data = ({'id': '700', 'key': 'DEMO-20', 'fields': {}}, {'id': '17', 'data': run_detail()})
            output = io.StringIO()
            with patch('sys.argv', ['main.py', 'extract-runs', '--config', str(config_path), '--prompt-auth']), \
                 patch('getpass.getpass', return_value='test-source-token') as secret, \
                 patch('builtins.input', side_effect=AssertionError('Must not prompt for Codebeamer')), \
                 patch('migration.source_runs.collect_sources', return_value=([], {'17': data}, [])), \
                 contextlib.redirect_stdout(output):
                result = main.main()
            self.assertEqual(result, 0)
            self.assertEqual(secret.call_count, 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report['writes'], 0)
            self.assertFalse(report['migration_complete'])
            self.assertEqual(report['runs'], 1)
