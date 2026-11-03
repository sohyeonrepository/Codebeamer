import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from writer import AdapterError, render, item_id, process_items, reference_edges, build_history_bridge, verify_adapter, relation_stage

ISSUES = [{'key':'ABC-1','fields':{'summary':'One','description':'Hello','issuetype':{'name':'Test Plan'},'customfield_10000':'P1'}, '_history':[{'id':'2','created':'2026-02-02'},{'id':'1','created':'2026-01-01'}], '_comments':[], '_xray_relations':{'tests':{'keys':['ABC-2'],'raw':[]}}},
          {'key':'ABC-2','fields':{'summary':'Two','issuetype':{'name':'Test'}}, '_history':[], '_comments':[]}]
CFG = {'cb_tracker_ids':{'test_plan':100,'test_case':101},'field_map':{'test_plan':{'customfield_10000':999}},'kind_by_key':{'ABC-1':'test_plan','ABC-2':'test_case'}, 'cb_api':{'item_create_body':{'tracker':{'id':'{{tracker_id}}'},'name':'{{summary}}','jiraKey':'{{jira_key}}','mapped':'{{mapped_fields}}'},'create_item':{'method':'POST','path':'/v3/items'},'created_item_id_path':'id'},'relation_field_map':{'test_plan':{'tests':{'cb_reference_field_id':700}}}}

class TestMigrator(unittest.TestCase):
    def test_templates(self):
        self.assertEqual(render({'id':'{{tracker_id}}','value':'hello'}, {'tracker_id':42}), {'id':42,'value':'hello'})
    def test_creation_idempotent(self):
        from copy import deepcopy
        cfg=deepcopy(CFG);cfg['cb_api'].update(verified_against_3_0_0_7=True,
            read_item={'method':'GET','path':'/v3/items/{item_id}'},
            update_item={'method':'PUT','path':'/v3/items/{item_id}'},
            set_reference={'method':'PUT','path':'/v3/items/{item_id}'},
            create_association={'method':'POST','path':'/v3/associations'})
        store={};posts=[]
        cb=Mock()
        def request(method,path,**kwargs):
            if method=='POST':
                identity=201+len(store);store[identity]=dict(kwargs['json'],id=identity);posts.append(identity);return {'id':identity}
            return store[int(path.rsplit('/',1)[1])]
        cb.request.side_effect=request
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ids.json'
            self.assertEqual(process_items(ISSUES,cfg,cb,path,apply=True)['state'],{'ABC-1':201,'ABC-2':202})
            process_items(ISSUES,cfg,cb,path,apply=True)
            self.assertEqual(len(posts),2)
            self.assertEqual(store[201]['mapped'],[{'fieldId':999,'value':'P1'}])
    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cb=Mock()
            result=process_items(ISSUES,dict(CFG),cb,Path(tmp)/'ids.json',apply=False)
            self.assertEqual(result['state'],{})
            cb.request.assert_not_called()
    def test_reference_edges(self):
        self.assertEqual(reference_edges(ISSUES,CFG),[{'source':'ABC-1','target':'ABC-2','cb_reference_field_id':700,'relation_name':'tests'}])
    def test_history_order(self):
        q=build_history_bridge(ISSUES,{'ABC-1':201})
        self.assertEqual([v['id'] for v in q[0]['revisions']],['1','2'])
        self.assertEqual(q[0]['codebeamerItemId'],201)
    def test_bulk_reference_not_overwritten(self):
        issues=[{'key':'ABC-1','fields':{},'_xray_relations':{'tests':{'keys':['ABC-2','ABC-3']}}}]
        cfg={'kind_by_key':{'ABC-1':'test_plan'},'relation_field_map':{'test_plan':{'tests':{'cb_reference_field_id':700}}},
             'cb_api':{'reference_body_bulk':{'fieldId':'{{field_id}}','items':'{{target_item_ids}}'},
                       'set_reference':{'method':'PUT','path':'/v3/items/{item_id}'}}}
        cfg['cb_api'].update(verified_against_3_0_0_7=True,read_reference={'method':'GET','path':'/refs/{item_id}'})
        for name in ('create_item','update_item','read_item','create_association'):cfg['cb_api'][name]={'method':'GET','path':'/unused'}
        cb=Mock();cb.request.side_effect=[{'fieldId':700,'multiple':True,'values':[99]}, {}, {'fieldId':700,'multiple':True,'values':[22,33,99]}]
        ops=relation_stage(issues,cfg,{'ABC-1':11,'ABC-2':22,'ABC-3':33},cb,apply=True)
        self.assertEqual(len(ops),1)
        self.assertEqual(cb.request.call_count,3)
        self.assertEqual(cb.request.call_args_list[1].kwargs['json']['items'],[22,33,99])

    def test_adapter_locked_without_verification(self):
        with self.assertRaises(AdapterError): verify_adapter(CFG)
    def test_no_bad_id(self):
        with self.assertRaises(AdapterError):item_id({'id':None})

if __name__=='__main__': unittest.main()
