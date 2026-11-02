"""Offline schema checks only; these do not establish live server readiness."""
import unittest
import tempfile
from pathlib import Path
from clients.http import ApiError
from single_import import validate
from single_import import scoped_state_dir


class RequestContractTests(unittest.TestCase):
    def test_live_state_directory_is_scoped_by_issue_and_target(self):
        config={'jira':{'url':'https://jira.test/'},'codebeamer':{'url':'https://cb.test/'}}
        with tempfile.TemporaryDirectory() as root:
            first=scoped_state_dir(root,'abc-1',10,20,config)
            self.assertEqual(first,scoped_state_dir(root,'ABC-1',10,20,config))
            self.assertNotEqual(first,scoped_state_dir(root,'ABC-2',10,20,config))
            self.assertNotEqual(first,scoped_state_dir(root,'ABC-1',10,21,config))
            self.assertEqual(first.parent,Path(root))
            self.assertTrue(first.name.startswith('ABC-1-p10-t20-'))

    def test_live_state_directory_rejects_invalid_issue_before_creation(self):
        config={'jira':{'url':'https://jira.test'},'codebeamer':{'url':'https://cb.test'}}
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ApiError,'Exactly one'):
                scoped_state_dir(root,'ABC-1 OR ABC-2',10,20,config)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_missing_required_server_field_blocks_request(self):
        with self.assertRaisesRegex(ApiError,'required by Swagger'):
            validate({}, {'name':'Jira summary'}, {'type':'object','required':['status'],
                'properties':{'name':{'type':'string'},'status':{'type':'integer'}}})

    def test_readonly_and_ambiguous_schema_are_blockers(self):
        with self.assertRaisesRegex(ApiError,'read-only'):
            validate({}, {'id':123}, {'type':'object','properties':{'id':{'type':'integer','readOnly':True}}})
        with self.assertRaises(ApiError):
            validate({}, {'name':'test'}, {'oneOf':[{'type':'object'},{'type':'string'}]})

    def test_native_request_is_checked_against_actual_enum_and_nested_types(self):
        schema={'type':'object','required':['name','status'],'properties':{
            'name':{'type':'string'},'status':{'type':'object','properties':{
                'id':{'type':'integer','enum':[1,2]}}}}}
        validate({}, {'name':'Issue','status':{'id':1}},schema)
        with self.assertRaisesRegex(ApiError,'Swagger enum'):
            validate({}, {'name':'Issue','status':{'id':999}},schema)


if __name__ == '__main__':unittest.main()
