"""Selected field nulls and verified Tracker membership in 3.0.0.7."""
import unittest
from types import SimpleNamespace
from clients.http import ApiError
from migration.fields import payload
from migration.engine import Engine


class SelectedNullTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'wizard': {'enabled': True, 'selected_fields_only': True}}
        self.mapping = {
            'summary': {'type': 'text', 'target': 'name', 'expected_mandatory': True},
            'description': {'type': 'text', 'target': 'description', 'expected_mandatory': False},
            'resolution': {'type': 'option', 'target': 'resolutions', 'expected_mandatory': False,
                           'native_array': True, 'value_mapping': {}},
            'reporter': {'type': 'user', 'target': 'supervisor', 'expected_mandatory': False,
                         'value_mapping': {}},
            'duedate': {'type': 'date', 'target': 'endDate', 'expected_mandatory': False},
        }

    def test_optional_nulls_are_omitted_across_types(self):
        actual = {'summary': 'Required', 'description': None, 'resolution': None,
                  'reporter': None, 'duedate': None}
        self.assertEqual(payload(actual, self.mapping, self.settings), {'name': 'Required'})

    def test_required_null_is_not_silently_discarded(self):
        actual = {'summary': None, 'description': None, 'resolution': None,
                  'reporter': None, 'duedate': None}
        with self.assertRaisesRegex(ApiError, 'Mandatory'):
            payload(actual, self.mapping, self.settings)

    def test_nonnull_unmapped_user_still_fails(self):
        actual = {'summary':'Required', 'description':None, 'resolution':None,
                  'reporter':{'name':'unknown'}, 'duedate':None}
        with self.assertRaisesRegex(ApiError, 'Missing user mapping'):
            payload(actual, self.mapping, self.settings)

    def test_null_in_legacy_non_wizard_mode_is_unchanged(self):
        self.assertEqual(payload({'description':None}, {'description':self.mapping['description']}, {}),
                         {'description':None})


class SelectedTrackerTests(unittest.TestCase):
    def make_engine(self, trackers):
        class CB:
            def __init__(self):
                self.calls = []
            def call(self, name, params=None):
                self.calls.append((name, params))
                if name == 'trackers_get':
                    return trackers
                raise AssertionError('Undocumented call: ' + name)
        cb=CB()
        engine=Engine(None,None,cb,None,
                      {'wizard':{'enabled': True},
                       'project_mapping':{'STGVSTAR':123}},write=True)
        return engine,cb

    def test_tracker_get_not_required(self):
        engine,cb = self.make_engine([{'id':4874140}])
        self.assertEqual(engine.ensure('tracker','STGVSTAR:14012',4874140),4874140)
        self.assertEqual(cb.calls,[('trackers_get',{'projectId':123})])

    def test_missing_tracker_fails_membership(self):
        engine,cb = self.make_engine([{'id':88}])
        with self.assertRaisesRegex(ApiError, 'not found'):
            engine.ensure('tracker','STGVSTAR:14012',4874140)


if __name__ == '__main__':
    unittest.main()
