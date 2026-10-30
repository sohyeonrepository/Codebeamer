import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from clients.http import ApiError
from migration.wizard import (plan_mapping, capabilities, analyze, fingerprint,
    assert_importable, public_profile, declared_get, full_catalog)
from mapping_helper import apply_overlay


def fixture():
    field = {'id': 1, 'name': 'Name', 'type': 'Text', 'typeId': 7, 'mandatory': False,
             'multipleSelection': False, 'options': None, 'reference': None}
    config = {'jira': {'url': 'https://jira.example', 'projects': ['DEMO']},
              'codebeamer': {'url': 'https://cb.example'},
              'migration': {'tracker_mapping': {}, 'field_mapping': {}}}
    catalog = {'definitions': [{'id': 'summary', 'name': 'Summary', 'schema': {'type': 'string'}}],
               'projects': [{'key': 'DEMO', 'types': [{'id': '1', 'name': 'Test', 'fields': {'summary': ['a', 'b']}}]}],
               'targets': [{'id': 20, 'name': 'Test', 'fields': [field], 'configuration': {}}], 'warnings': []}
    return config, catalog


class WizardPlanningTests(unittest.TestCase):
    def test_exact_unique_mapping_and_overlay_compatibility(self):
        config, catalog = fixture()
        original = copy.deepcopy(config)
        plan = plan_mapping(config, catalog, 100)
        self.assertEqual(config, original)
        self.assertEqual(plan['summary'], {'total': 1, 'auto': 1, 'create': 0, 'review': 0})
        self.assertEqual(plan['blockers'], [])
        self.assertEqual(apply_overlay(config, plan['overlay'])['migration']['tracker_mapping']['DEMO:1'], 20)

    def test_wrong_type_is_review_even_when_name_matches(self):
        config, catalog = fixture()
        catalog['targets'][0]['fields'][0]['type'] = 'Integer'
        plan = plan_mapping(config, catalog, 100)
        self.assertEqual(plan['rows'][0]['state'], 'review')
        self.assertNotIn('summary', plan['overlay']['migration']['field_mapping']['DEMO:1'])
        self.assertTrue(plan['blockers'])

    def test_ambiguous_tracker_never_connected(self):
        config, catalog = fixture()
        catalog['targets'].append(dict(catalog['targets'][0], id=21))
        plan = plan_mapping(config, catalog, 100)
        self.assertEqual(plan['rows'][0]['state'], 'review')
        self.assertEqual(plan['overlay']['migration']['tracker_mapping'], {})

    def test_missing_tracker_is_plan_only_and_old_mapping_never_redirected(self):
        config, catalog = fixture()
        config['migration']['tracker_mapping']['DEMO:1'] = 999
        plan = plan_mapping(config, catalog, 100)
        self.assertEqual(plan['rows'][0]['state'], 'create')
        self.assertEqual(plan['overlay']['migration']['tracker_mapping']['DEMO:1'], 999)
        self.assertTrue(plan['blockers'])

    def test_missing_value_anywhere_in_full_inventory_blocks(self):
        config, catalog = fixture()
        field = catalog['targets'][0]['fields'][0]
        field.update(type='Choice', name='Status', options=[{'id': 5, 'name': 'Passed'}])
        config['codebeamer']['field_bindings'] = {'status': {'fieldId': 1}}
        catalog['definitions'] = [{'id': 'status', 'name': 'Status', 'schema': {'type': 'status'}}]
        catalog['projects'][0]['types'][0]['fields'] = {'status': [{'id': '1', 'name': 'Passed'}, {'id': '2', 'name': 'Unknown'}]}
        plan = plan_mapping(config, catalog, 100)
        self.assertTrue(plan['blockers'])
        self.assertEqual(plan['summary']['auto'], 0)

    def test_no_claim_that_config_flags_implement_missing_features(self):
        config, _ = fixture()
        config['migration']['r4j_supported'] = True
        cb = Mock()
        stages, blockers = capabilities(config, cb)
        self.assertTrue(any('R4J' in text for text in blockers))
        self.assertTrue(any('History' in text for text in blockers))
        self.assertEqual(next(s for s in stages if s['id'] == 'r4j')['status'], 'blocked')

    def test_even_forged_ready_plan_cannot_enable_unimplemented_import(self):
        config, _ = fixture()
        plan = {'config_fingerprint': fingerprint(config), 'ready': True, 'source_complete': True, 'blockers': []}
        with self.assertRaises(ApiError): assert_importable(plan, config)
        changed = copy.deepcopy(config); changed['jira']['projects'] = ['OTHER']
        with self.assertRaisesRegex(ApiError, '설정이 변경'): assert_importable(plan, changed)

    def test_profile_never_contains_secrets(self):
        config, _ = fixture()
        config['jira'].update(token='NEVER-SAVE', password='NEVER-SAVE', token_env='JIRA_TOKEN')
        profile = public_profile(config)
        self.assertNotIn('NEVER-SAVE', json.dumps(profile))
        self.assertEqual(profile['jira']['token_env'], 'JIRA_TOKEN')
        config['jira']['url'] = 'https://user:password@jira.example'
        with self.assertRaises(ApiError): public_profile(config)

    def test_declared_get_rejects_partial_pages_and_undeclared_paths(self):
        cb = Mock(spec=['spec', 'discover', 'http'])
        cb.spec = {'paths': {'/v3/projects': {'get': {}}}}
        cb.http.request.return_value = {'projects': [{'id': 1}], 'total': 2}
        with self.assertRaises(ApiError): declared_get(cb, '/v3/projects')
        cb.http.request.assert_called_once_with('GET', '/v3/projects')
        cb.http.request.reset_mock()
        with self.assertRaises(ApiError): declared_get(cb, '/v3/unknown')
        cb.http.request.assert_not_called()

    def test_all_issues_are_read_not_only_first_twenty(self):
        config, catalog = fixture()
        http = Mock()
        http.request.side_effect = lambda method, path: {'issueTypes': [{'id': '1', 'name': 'Test'}]}
        reader = Mock()
        reader.http = http
        reader.fields.return_value = catalog['definitions']
        reader.projects.return_value = [{'key': 'DEMO'}]
        reader.issues.return_value = [{'key': 'DEMO-' + str(i)} for i in range(25)]
        reader.bundle.side_effect = lambda key: {'key': key, 'fields': {'issuetype': {'id': '1', 'name': 'Test'}, 'summary': key}}
        jira = Mock(http=http, config=config['jira'])
        with patch('migration.wizard.JiraClient', return_value=reader):
            result, bundles = full_catalog(jira, Mock(), [])
        self.assertEqual(len(bundles), 25)
        self.assertEqual(len(result['projects'][0]['types'][0]['fields']['summary']), 25)
        self.assertTrue(result['values_complete'])
        self.assertEqual(reader.bundle.call_count, 25)

    def test_analyze_is_readonly_and_keeps_missing_features_blocked(self):
        config, catalog = fixture()
        cb = Mock(write=False)
        with patch('migration.wizard.full_catalog', return_value=(catalog, [(None, {})])):
            plan, _ = analyze(config, Mock(), cb, 100, [{'id': 20}])
        self.assertEqual(plan['writes'], 0)
        self.assertTrue(plan['source_complete'])
        self.assertFalse(plan['ready'])
        self.assertTrue(any('R4J' in text for text in plan['blockers']))
        cb.call.assert_not_called()
        cb.write = True
        with self.assertRaises(ApiError): analyze(config, Mock(), cb, 100, [])

    def test_choice_creation_plan_preserves_source_label_without_writing(self):
        config, catalog = fixture()
        field = catalog['targets'][0]['fields'][0]
        field.update(type='Choice', name='Status', options=[{'id': 5, 'name': 'Passed'}])
        config['codebeamer']['field_bindings'] = {'status': {'fieldId': 1}}
        catalog['definitions'] = [{'id': 'status', 'name': 'Status', 'schema': {'type': 'status'}}]
        catalog['projects'][0]['types'][0]['fields'] = {'status': [{'id': '2', 'name': 'Failed'}]}
        plan = plan_mapping(config, catalog, 100)
        choice = next(row for row in plan['creations'] if row['kind'] == 'choice')
        self.assertEqual(choice['proposal']['label'], 'Failed')
        self.assertEqual(choice['proposal']['execution'], 'blocked')
        self.assertEqual(plan['additional_creations']['choices'], 1)
        self.assertEqual(plan['summary']['total'], 1)

    def test_cli_plan_rejected_before_http_or_state_creation(self):
        import main
        config, _ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'config.json'; cfg.write_text(json.dumps(config))
            plan = Path(directory) / 'plan.json'; plan.write_text('{}')
            with patch.object(main, 'Http') as http, patch.object(main, 'State') as state:
                self.assertEqual(main.main(['migrate', '--config', str(cfg), '--apply', '--wizard-plan', str(plan)]), 2)
            http.assert_not_called(); state.assert_not_called()


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk requires DISPLAY')
class WizardUITests(unittest.TestCase):
    def test_default_exceptions_only_and_import_disabled(self):
        import tkinter as tk
        from import_wizard import Wizard
        root = tk.Tk()
        try:
            with tempfile.TemporaryDirectory() as directory:
                config, catalog = fixture()
                config['jira']['auth'] = 'bearer'
                config['codebeamer']['auth'] = 'basic'
                path = Path(directory) / 'config.json'; path.write_text(json.dumps(config))
                ui = Wizard(root, path, Path(directory) / 'workspace')
                ui.plan = plan_mapping(config, catalog, 100)
                ui.plan.update(stages=[], ready=False)
                ui.render_plan(); root.update()
                self.assertEqual(ui.table.get_children(), ())
                self.assertIn('disabled', ui.import_button.state())
                ui.details.set(True); ui.render_plan()
                self.assertEqual(len(ui.table.get_children()), 1)
                ui.connection_vars['jira']['token'].set('secret')
                self.assertIsNone(ui.plan)
                self.assertIn('disabled', ui.import_button.state())
                self.assertFalse((Path(directory) / 'workspace').exists())
        finally: root.destroy()
