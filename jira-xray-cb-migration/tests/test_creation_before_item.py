import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from migration.simple_item_import import pending_creation_fields, require_created_fields, run
from clients.http import ApiError

class CreationOrderTests(unittest.TestCase):
    def test_selected_plan_included_without_value_and_despite_stale_status(self):
        entry={'rows':{'empty':{'selected':True,'creation':{'label':'Empty'},'status':'manual'},
                       'off':{'selected':False,'creation':{'label':'Off'},'status':'create'}}}
        self.assertEqual(pending_creation_fields(entry),['empty'])
        with self.assertRaises(ApiError):require_created_fields(entry)

    def test_item_writer_blocks_before_any_api_for_unfinished_creation(self):
        cb=Mock();jira=Mock()
        entry={'tracker':100,'rows':{'summary':{'selected':True},
               'f':{'selected':True,'status':'create','creation':{'label':'F'}}}}
        wizard=NS(plan={'scopes':{'s':entry}},cb=cb,jira=jira)
        with self.assertRaisesRegex(ApiError,'Item POST 미실행'):run(wizard)
        self.assertEqual(cb.mock_calls,[])
        self.assertEqual(jira.mock_calls,[])

    def test_verified_creation_can_proceed(self):
        entry={'rows':{'f':{'selected':True,'status':'mapped','spec':{'field_id':12}}}}
        require_created_fields(entry)

    def test_missing_plan_still_blocks(self):
        entry={'rows':{'f':{'selected':True,'status':'create'}}}
        with self.assertRaises(ApiError):require_created_fields(entry)
