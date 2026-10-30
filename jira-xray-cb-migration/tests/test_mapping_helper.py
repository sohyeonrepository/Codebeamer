import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mapping_helper import collect_catalog, build_spec, apply_overlay, validate_overlay
from migration.fields import payload


FIELD = {'id': 5, 'label': 'Result', 'typeId': 7, 'mandatory': True,
         'multipleSelection': False, 'options': [{'id': 9, 'name': 'Pass'}], 'reference': None}


class MappingHelperTests(unittest.TestCase):
    def test_read_only_collection_and_no_mutation(self):
        config = {'execution_issue_types': ['Test Execution']}
        calls = []
        issue = {'key': 'P-1', 'fields': {'issuetype': {'id': '1', 'name': 'Test'}, 'summary': 'sample'}}
        class Reader:
            def __init__(self, http, configuration):
                calls.append(configuration)
            def issues(self, project): return iter([issue, issue])
        jira = SimpleNamespace(config=config, http=None, fields=lambda: [{'id': 'summary', 'name': 'Summary'}],
                               projects=lambda: [{'key': 'P'}])
        cb = SimpleNamespace(fields=lambda tracker: [FIELD], configuration=lambda tracker: {'fields': [FIELD]})
        with patch('mapping_helper.JiraClient', Reader):
            result = collect_catalog(jira, cb, [100], 1)
        self.assertEqual(result['projects'][0]['sample_count'], 1)
        self.assertEqual(result['targets'][0]['fields'][0]['id'], 5)
        self.assertFalse(result['values_complete'])
        self.assertNotIn('execution_issue_types', calls[0])
        self.assertEqual(config, {'execution_issue_types': ['Test Execution']})

    def test_field_local_value_maps_do_not_collide(self):
        a = build_spec('a', FIELD, 'option', 'a', {'1': 9})
        b = build_spec('b', dict(FIELD, id=6), 'option', 'b', {'1': 10})
        self.assertEqual(payload({'a': {'id': '1'}, 'b': {'id': '1'}}, {'a': a, 'b': b}, {}), {'a': 9, 'b': 10})

    def test_missing_server_type_not_guessed(self):
        with self.assertRaises(ValueError):
            build_spec('a', {'id': 5}, 'text', 'name', {})
        with self.assertRaises(ValueError):
            build_spec('a', FIELD, 'option', 'result', {})

    def test_overlay_preserves_credentials_and_does_not_modify_input(self):
        original = {'jira': {'url': 'https://example.test', 'token_env': 'JIRA_TOKEN'},
                    'migration': {'field_mapping': {'existing': {}}, 'mode': 'full-xray'}}
        overlay = {'migration': {'field_mapping': {'P:1': {'summary': build_spec('summary', FIELD, 'text', 'name', {})}}}}
        result = apply_overlay(original, overlay)
        self.assertEqual(result['jira'], original['jira'])
        self.assertNotIn('P:1', original['migration']['field_mapping'])
        self.assertIn('existing', result['migration']['field_mapping'])
        with self.assertRaises(ValueError):
            apply_overlay(original, {'migration': {'mode': 'apply'}})
        with self.assertRaises(ValueError):
            apply_overlay(original, {'jira': {'password': 'not-accepted'}})

    def test_validation_finds_unmapped_choice_and_required_fields(self):
        catalog = {'projects': [{'key': 'P', 'types': [{'id': '1', 'fields': {'result': [{'id': '2'}], 'summary': ['s']}}]}],
                   'targets': [{'id': 100, 'fields': [FIELD, dict(FIELD, id=6, label='Required')]}]}
        spec = build_spec('result', FIELD, 'option', 'result', {'1': 9})
        overlay = {'migration': {'field_mapping': {'P:1': {'result': spec}}, 'tracker_mapping': {'P:1': 100}}}
        errors = validate_overlay(overlay, catalog)
        self.assertTrue(any('summary' in e for e in errors))
        self.assertTrue(any('값 매핑' in e for e in errors))
        self.assertTrue(any('Required' in e for e in errors))

    def test_empty_source_is_not_ready(self):
        self.assertTrue(validate_overlay({'migration': {}}, {'projects': [], 'targets': []}))
