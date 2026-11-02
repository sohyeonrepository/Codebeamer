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

    def test_partial_catalog_is_not_success(self):
        class Http:
            def request(self,*args):return {'values':[],'total':10}
        client=type('Client',(),{'http':Http()})()
        with self.assertRaises(ApiError):catalogs(client,client)
