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


    def test_issue_import_without_r4j_does_not_require_adapters(self):
        required = {'project_get':'GET','trackers_get':'GET','item_create':'POST',
                    'item_get':'GET','item_update':'PUT'}
        cb = Mock()
        cb.operation.side_effect = lambda name: {'method':required[name], 'path':'/v3/test'}
        plan = {'scopes': {'STGVSTAR:14012': {
            'tracker':100, 'new_tracker':None,
            'rows': {'summary':{'status':'mapped'}, 'description':{'status':'mapped'}}}}}
        preflight(cb, {'mode':'issues', 'wizard':{'enabled':True}}, plan)
        self.assertEqual({call.args[0] for call in cb.operation.call_args_list},
                         set(required))

    def test_explicit_r4j_requires_complete_adapters(self):
        from clients.http import ApiError
        required = {'project_get':'GET','trackers_get':'GET','item_create':'POST',
                    'item_get':'GET','item_update':'PUT'}
        cb = Mock()
        cb.operation.side_effect = lambda name: {'method':required[name], 'path':'/v3/test'}
        plan = {'scopes':{'STGVSTAR:14012':{'tracker':100,'new_tracker':None,'rows':{}}}}
        with self.assertRaisesRegex(ApiError, 'migration.r4j.trees'):
            preflight(cb, {'preserve_r4j':True}, plan)

    def test_no_r4j_collection_marks_not_requested(self):
        from migration.preservation import collect_r4j
        jira, state = Mock(), Mock()
        issue = {'key':'STGVSTAR-12','id':'12',
                 'fields':{'project':{'key':'STGVSTAR'}}}
        result = collect_r4j(jira, issue, {'mode':'issues'}, state)
        self.assertEqual(result, {'trees':[],'revisions':[],'baselines':[]})
        self.assertEqual(issue['_r4j_collection_status'], 'not_requested')
        jira.configured_records.assert_not_called()

    def test_partial_r4j_config_fails_instead_of_silently_skipping(self):
        from clients.http import ApiError
        from migration.preservation import r4j_requested
        self.assertTrue(r4j_requested({'r4j':{'trees':{'path':'/custom'}}}))
        with self.assertRaisesRegex(ApiError, 'migration.r4j.revisions'):
            required={'project_get':'GET','trackers_get':'GET','item_create':'POST',
                      'item_get':'GET','item_update':'PUT'}
            cb=Mock()
            cb.operation.side_effect=lambda name:{'method':required[name],'path':'/v3/test'}
            preflight(cb, {'r4j':{'trees':{'path':'/custom'}}},
                      {'scopes':{'STGVSTAR:14012':{'tracker':100,'rows':{}}}})


if __name__ == '__main__':
    unittest.main()
