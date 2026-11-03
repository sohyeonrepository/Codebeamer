import unittest,tempfile,copy
from pathlib import Path
from unittest.mock import Mock
from migrator import paginate
from writer import AdapterError,process_items,relation_stage
from full_migration import run_status,linked_issue_edges,apply_related_associations,execute_java_history_bridge,extract_xray_test_runs
from test_pipeline import CFG,ISSUES

def config():
 c=copy.deepcopy(CFG);c['cb_api']['verified_against_3_0_0_7']=True
 for name in ('read_item','update_item','set_reference','create_association'):
  c['cb_api'][name]={'method':'GET','path':'/items/{item_id}' if name!='create_association' else '/associations'}
 return c

class Regression(unittest.TestCase):
 def test_unknown_verdict_is_error(self):
  with self.assertRaises(AdapterError):run_status({'status':'CUSTOM_UNKNOWN'})
 def test_empty_page_does_not_complete_history(self):
  cb=Mock();cb.request.return_value={'values':[],'total':2}
  with self.assertRaisesRegex(ValueError,'Incomplete'):paginate(cb,'/history','values')
 def test_lost_item_response_never_reposts(self):
  cb=Mock();cb.request.side_effect=TimeoutError('lost')
  with tempfile.TemporaryDirectory() as d:
   state=Path(d)/'ids.json'
   with self.assertRaises(TimeoutError):process_items(ISSUES,config(),cb,state,apply=True)
   with self.assertRaisesRegex(AdapterError,'Uncertain'):process_items(ISSUES,config(),cb,state,apply=True)
   self.assertEqual(cb.request.call_count,1)
 def test_four_categories_offline(self):
  c=config();c['cb_tracker_ids'].update(test_set=102,test_execution=103)
  issues=[{'key':'P-'+str(i),'fields':{'summary':name,'issuetype':{'name':name}}} for i,name in enumerate(['Test Plan','Test','Test Set','Test Execution'],1)]
  with tempfile.TemporaryDirectory() as d:
   r=process_items(issues,c,None,Path(d)/'ids.json',apply=False)
   self.assertEqual([o['tracker_id'] for o in r['operations']],[100,101,102,103])
 def test_directed_links_dedup_and_types_preserved(self):
  t={'id':'7','name':'Blocks'}
  issues=[{'key':'P-1','fields':{'issuelinks':[{'id':'1','type':t,'outwardIssue':{'key':'P-2'}}]}}, {'key':'P-2','fields':{'issuelinks':[{'id':'1','type':t,'inwardIssue':{'key':'P-1'}}]}}]
  e=linked_issue_edges(issues);self.assertEqual(len(e),1);self.assertEqual(e[0]['source'],'P-1');self.assertEqual(e[0]['jira_link_type'],t)
 def test_association_and_native_history_no_unverified_writes(self):
  cb=Mock()
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(AdapterError,'BLOCKED'):apply_related_associations(cb,[],{},Path(d),apply=True)
   with self.assertRaisesRegex(AdapterError,'BLOCKED'):execute_java_history_bridge([],{'java_history_command':['invalid']},Path(d),apply=True)
   cb.request.assert_not_called()
 def test_xray_partial_page_reports_error(self):
  cb=Mock();cb.request.return_value={'results':[{'id':1}],'total':2}
  with tempfile.TemporaryDirectory() as d:
   r=extract_xray_test_runs(cb,[ISSUES[1]],Path(d));self.assertEqual(r,{'runs':0,'errors':1})
 def test_reference_retry_uses_existing_values_without_write(self):
  c=config();c['cb_api'].update(reference_body_bulk={'fieldId':'{{field_id}}','values':'{{target_item_ids}}'},read_reference={'method':'GET','path':'/refs/{item_id}'})
  cb=Mock();cb.request.return_value={'fieldId':700,'multiple':True,'values':[22,99]}
  r=relation_stage(ISSUES,c,{'ABC-1':11,'ABC-2':22},cb,apply=True)
  self.assertEqual(r[0]['status'],'READBACK_VERIFIED');self.assertTrue(all(x.args[0]=='GET' for x in cb.request.call_args_list))
