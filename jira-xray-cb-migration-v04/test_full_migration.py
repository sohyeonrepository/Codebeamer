import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from full_migration import (run_status, build_run_groups, test_run_operations, apply_test_runs,
                            extract_xray_test_runs, execute_java_history_bridge,linked_issue_edges)
from writer import AdapterError

class FullMigrationTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(run_status({'status':'PASS'}),'PASSED')
        self.assertEqual(run_status({'status':{'name':'FAIL'}}),'FAILED')
        self.assertEqual(run_status({'status':'TODO'}),None)
    def test_run_group(self):
        runs=[{'test_key':'ABC-1','xray_run_id':'5','data':{'testExecKey':'ABC-2','status':'PASS'},'status':'PASSED'}]
        group,blocked=build_run_groups(runs,{'ABC-1':21,'ABC-2':22})
        self.assertEqual(list(group),['ABC-2']);self.assertEqual(blocked,[])
    def test_reject_unresolved(self):
        runs=[{'test_key':'ABC-1','xray_run_id':'5','data':{'status':'PASS'},'status':'PASSED'}]
        group,blocked=build_run_groups(runs,{'ABC-1':21})
        self.assertFalse(group);self.assertEqual(len(blocked),1)
    def test_apply_v1_documented_requests(self):
        runs=[{'test_key':'ABC-1','xray_run_id':'5','data':{'testExecKey':'ABC-2','status':'PASS'},'status':'PASSED'}]
        cb=Mock(); cb.request.side_effect=[{'uri':'/item/333'},[{'id':444}]]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(AdapterError,'BLOCKED'):
                apply_test_runs(cb,runs,{'ABC-1':21,'ABC-2':22},Path(d),{'cb_v1_prefix':'/cb/rest'},apply=True)
            cb.request.assert_not_called()
    def test_xray_export(self):
        jira=Mock()
        jira.request.side_effect=[[{'id':4017}],{'id':4017,'testExecKey':'ABC-2','status':'PASS'}]
        with tempfile.TemporaryDirectory() as d:
            x=extract_xray_test_runs(jira,[{'key':'ABC-1','fields':{'issuetype':{'name':'Test'}}}],Path(d))
            self.assertEqual(x,{'runs':1,'errors':0})
    def test_history_bridge_no_false_success(self):
        with tempfile.TemporaryDirectory() as d:
            q=[{'jiraKey':'ABC-1','revisions':[{'id':1}]}]
            self.assertEqual(execute_java_history_bridge(q,{},Path(d))['written'],0)
            with self.assertRaises(AdapterError):execute_java_history_bridge(q,{},Path(d),apply=True)
    def test_related_edges(self):
        issues=[{'key':'ABC-1','fields':{'issuelinks':[{'id':'1','outwardIssue':{'key':'ABC-2'}}]}}]
        self.assertEqual(len(linked_issue_edges(issues)),1)
    def test_duplicate_test_cases_latest_wins(self):
        r={'test_key':'ABC-1','xray_run_id':'5','data':{'testExecKey':'ABC-2','status':'PASS'},'status':'PASSED'}
        x,blocked=test_run_operations([r,dict(r,xray_run_id='6')],{'ABC-1':1,'ABC-2':2},{})
        self.assertEqual(len(x),1);self.assertEqual(len(blocked),1);self.assertEqual(x[0]['xray_runs'][0]['xray_run_id'],'6')

if __name__=='__main__':unittest.main()
