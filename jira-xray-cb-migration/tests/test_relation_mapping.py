import tempfile
import unittest
from clients.http import ApiError
from migration.relation_mapping import write_reference, catalogs, normalize
from migration.state import State

class RelationTests(unittest.TestCase):
    def test_reference_preserves_existing_and_checks_trackers_and_dedup(self):
        class CB:
            def __init__(self):self.values=[{'id':99}];self.writes=0;self.wrong=False
            def operation(self,name):return {'method':'PUT' if name.endswith('update') else 'GET'}
            def call(self,name,params,data=None):
                if name=='reference_field_get':return {'id':3,'hidden':False,'multiple':True,'targetTrackerIds':[20]}
                if name=='item_get':return {'id':params['itemId'],'tracker':{'id':999 if self.wrong else (10 if params['itemId']==1 else 20)}}
                if name=='reference_values_get':return {'fieldId':3,'values':list(self.values)}
                if name=='reference_values_update':self.values=data['values'];self.writes+=1
        cb=CB();mapping=normalize({'mode':'reference','tracker_id':10,'field_id':3,'target_tracker_id':20})
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            write_reference(cb,state,'link',1,2,mapping)
            self.assertEqual(cb.values,[{'id':99},{'id':2}])
            write_reference(cb,state,'link',1,2,mapping)
            self.assertEqual(cb.writes,1)
            cb.wrong=True
            with self.assertRaisesRegex(ApiError,'Tracker mismatch'):write_reference(cb,state,'other',1,2,mapping)
            self.assertEqual(cb.writes,1)
            state.db.close()

    def test_issue_picker_resolves_source_key_to_created_item_ids(self):
        from unittest.mock import patch
        from migration.relation_mapping import picker_references
        from migration.fields import payload
        first={'id':'1','key':'P-1','fields':{'project':{'key':'P'},'issuetype':{'id':'1'},'picker':{'key':'P-2'}}}
        second={'id':'2','key':'P-2','fields':{'project':{'key':'P'},'issuetype':{'id':'2'}}}
        spec={'field_id':3,'target':'cb_field_3','type':'reference','deferred_reference':True}
        settings={'tracker_mapping':{'P:1':10,'P:2':20},'field_mapping':{'P:1':{'picker':spec}}}
        self.assertEqual(payload(first['fields'],{'picker':spec},settings),{})
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            state.mark('item:1','done',{'id':101});state.mark('item:2','done',{'id':202})
            with patch('migration.relation_mapping.write_reference') as write:
                picker_references(object(),state,[({'key':'P'},first),({'key':'P'},second)],settings)
                self.assertEqual(write.call_args.args[3:5],(101,202))
                self.assertEqual(write.call_args.args[5]['target_tracker_id'],20)
            with self.assertRaisesRegex(ApiError,'outside selected'):
                picker_references(object(),state,[({'key':'P'},first)],settings)
            state.db.close()

    def test_partial_catalog_is_not_success(self):
        class Http:
            def request(self,*args):return {'values':[],'total':10}
        client=type('Client',(),{'http':Http()})()
        with self.assertRaises(ApiError):catalogs(client,client)
