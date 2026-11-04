"""Regression tests: choosing TEST must not demand all project Issue Types."""
import unittest
from migration.wizard import ImportWizard


class SavedState:
    def __init__(self):
        self.writes = {}
    def save(self, section, key, value):
        self.writes[(section, key)] = value


class SelectedIssueTypeTests(unittest.TestCase):
    def make_wizard(self):
        wizard = object.__new__(ImportWizard)
        wizard.state = SavedState()
        project = {'target': 1,
                   'trackers': [{'id': 10, 'name': 'Test Case'}],
                   'types': [{'id': '10000', 'name': 'TEST'},
                             {'id': '10001', 'name': 'Bug'}]}
        scope = 'STGVSTAR:10000'
        wizard.plan = {'projects': {'STGVSTAR': project},
                       'scopes': {scope: {'project': 'STGVSTAR',
                                          'type': project['types'][0],
                                          'tracker': 10,
                                          'analysis': {'issue_count': 1, 'risks': []},
                                          'target_risks': [],
                                          'rows': {}}},
                       'stages': []}
        return wizard, scope

    def test_unselected_project_issue_types_are_not_required_for_import(self):
        wizard, scope = self.make_wizard()
        self.assertEqual([], wizard.errors())
        self.assertNotIn('STGVSTAR:10001: 분석 필요', '\n'.join(wizard.errors()))

    def test_user_can_explicitly_restrict_import_to_current_issue_type(self):
        wizard, scope = self.make_wizard()
        wizard.plan['scopes']['STGVSTAR:10001'] = {'project': 'STGVSTAR',
                                                   'type': {'id':'10001', 'name':'Bug'},
                                                   'tracker':10}
        wizard.keep_only_scope(scope)
        self.assertEqual([scope], list(wizard.plan['scopes']))
        self.assertEqual(['10000'], [t['id'] for t in wizard.plan['projects']['STGVSTAR']['types']])
        self.assertEqual([], wizard.errors())
        self.assertIn(('wizard','plan'), wizard.state.writes)

    def test_no_tracker_selection_is_a_validation_error(self):
        wizard, scope = self.make_wizard()
        wizard.plan['scopes'] = {}
        self.assertTrue(any('Issue Type' in e for e in wizard.errors()))

    def test_only_selected_fields_are_validated(self):
        wizard, scope = self.make_wizard()
        wizard.plan['scopes'][scope]['rows'] = {
            'customfield_1': {'selected': False, 'status':'error', 'reason':'irrelevant'},
            'summary': {'selected': True, 'status':'mapped',
                        'spec': {'target':'name', 'field_id':1}}}
        self.assertEqual([], wizard.errors())


if __name__ == '__main__':
    unittest.main()
