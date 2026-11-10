import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from migration.engine import Engine
from migration.state import State

class EmptyImportReportTests(unittest.TestCase):
    def test_empty_source_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as root:
            state=State(root)
            try:
                jira=Mock();jira.fields.return_value=[];jira.projects.return_value=[];jira.config={}
                engine=Engine(jira,Mock(),Mock(),state,{'wizard':{'enabled':True}},True)
                report=engine.migrate()
                self.assertFalse(report['complete'])
                self.assertEqual(report['counts']['source_issues'],0)
            finally:state.db.close()
