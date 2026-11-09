import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from clients.http import ApiError
from migration.simple_item_import import _read_selected_issue

class TypeGuidanceTests(unittest.TestCase):
    def test_execution_cannot_be_imported_as_test_case(self):
        http=Mock()
        http.request.return_value={'key':'STGVSTAR-2721','fields':{
            'project':{'key':'STGVSTAR'},'issuetype':{'id':'14014','name':'Test Execution'}}}
        entry={'project':'STGVSTAR','type':{'id':'14012','name':'TEST'}}
        wizard=NS(config={'jira':{'single_issue_key':'STGVSTAR-2721'}},
                  plan={'scopes':{'s':entry}},jira=NS(http=http))
        with self.assertRaisesRegex(ApiError,'개별 Test Run'):
            _read_selected_issue(wizard,'s',{'summary'})
        self.assertEqual(http.request.call_args.args[0],'GET')
        self.assertEqual(entry['type']['id'],'14012')
