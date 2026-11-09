import unittest
from unittest.mock import Mock
from copy import deepcopy
from migration.wizard import ImportWizard
from clients.http import ApiError

class TrackerProfileTests(unittest.TestCase):
    def setup_case(self):
        wizard=ImportWizard.__new__(ImportWizard)
        row={'creation':{'label':'Labels','semantic_type':'text'},
             'creation_profile':{'source_tracker_id':100,'definition':{'permission':{'old':True}}}}
        wizard.plan={'scopes':{'s':{'tracker':200,'rows':{'f':row}}}}
        wizard.config={'migration':{}}
        wizard.state=Mock()
        wizard.creation_profiles=Mock(return_value={'text':{'source_tracker_id':200,'definition':{'typeId':9}}})
        return wizard,row

    def test_other_tracker_refreshes_without_changing_plan(self):
        wizard,row=self.setup_case();plan=deepcopy(row['creation'])
        profile=wizard._creation_profile_for_tracker('s',row,200)
        self.assertEqual(profile['source_tracker_id'],200)
        self.assertEqual(row['creation'],plan)
        self.assertNotIn('permission',profile['definition'])
        wizard.creation_profiles.assert_called_once_with('s')

    def test_failed_refresh_does_not_reuse_old_template(self):
        wizard,row=self.setup_case();wizard.creation_profiles.return_value={}
        with self.assertRaisesRegex(ApiError,'POST 미실행'):
            wizard._creation_profile_for_tracker('s',row,200)
        self.assertNotIn('creation_profile',row)

    def test_same_tracker_string_id_does_not_refresh(self):
        wizard,row=self.setup_case();row['creation_profile']['source_tracker_id']='200'
        wizard._creation_profile_for_tracker('s',row,200)
        wizard.creation_profiles.assert_not_called()
