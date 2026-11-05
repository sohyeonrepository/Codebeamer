"""Import preflight must not require imaginary Codebeamer lookup endpoints.

Run from jira-xray-cb-migration:
    python -m unittest discover -s tests -p "test_preservation_preflight.py"
"""
import unittest
from unittest.mock import Mock
from migration.preservation import preflight


class PreservationPreflightTest(unittest.TestCase):
    def test_existing_tracker_requires_real_core_operations_only(self):
        required = {
            'project_get': 'GET',
            'trackers_get': 'GET',
            'item_create': 'POST',
            'item_get': 'GET',
            'item_update': 'PUT',
        }
        cb = Mock()
        cb.operation.side_effect = lambda name: {
            'method': required[name], 'path': '/v3/test'
        }
        settings = {'r4j': {
            'trees': {'path': '/r4j/trees'},
            'revisions': {'path': '/r4j/revisions'},
            'baselines': {'path': '/r4j/baselines'},
        }}
        plan = {'scopes': {'STGVSTAR:14012': {
            'tracker': 100, 'new_tracker': None,
            'rows': {'summary': {'status': 'mapped'}, 'description': {'status': 'mapped'}}
        }}}
        preflight(cb, settings, plan)
        self.assertEqual(set(call.args[0] for call in cb.operation.call_args_list),
                         set(required))
        self.assertNotIn('lookup', required)
        self.assertNotIn('comment_lookup', required)
        self.assertNotIn('attachment_lookup', required)
        self.assertNotIn('tracker_lookup', required)


if __name__ == '__main__':
    unittest.main()
