import unittest
from unittest.mock import Mock
from types import SimpleNamespace as NS
from migration.test_lifecycle import validate
from clients.http import ApiError

class LifecycleTests(unittest.TestCase):
    def case(self):
        types={'test':'1','set':'2','execution':'3','plan':'4'}
        scopes={kind:{'tracker':None if kind=='execution' else 100,'source_only':kind=='execution',
                       'type':{'id':value,'name':kind}} for kind,value in types.items()}
        relations=[{'from':'set','to':'test','name':'members'}, {'from':'plan','to':'test','name':'backlog'}]
        mappings={str(i):{'mode':'reference','tracker_id':100,'field_id':i,'target_tracker_id':100} for i in (5,6)}
        settings={'mode':'full-xray','full_xray':{'issue_types':types,'relationships':relations,
                  'release_type_contract':{'path':'basicInformation.type.name','value':'Release'},
                  'backlog_relation_name':'backlog','typed_link_fields':{'clone':'5','tests':'6'}},
                  'wizard':{'association_types':mappings}}
        cb=Mock();cb.configuration.return_value={'basicInformation':{'type':{'name':'Release'}}}
        cb.operation.side_effect=lambda n:{'method':'PUT' if n.endswith('update') else 'GET'}
        cb.call.side_effect=lambda n,p:{'id':p['fieldId'],'multiple':True,'hidden':False,'targetTrackerIds':[100]}
        return NS(config={'migration':settings},plan={'scopes':scopes},cb=cb)
    def test_read_only_contract_checks(self):
        w=self.case();self.assertEqual(validate(w)['writes'],0)
        self.assertEqual([c.args[0] for c in w.cb.call.call_args_list],['reference_field_get']*2)
    def test_wrong_release_blocks(self):
        w=self.case();w.cb.configuration.return_value={'basicInformation':{'type':{'name':'Task'}}}
        with self.assertRaises(ApiError):validate(w)
    def test_clone_cannot_silently_be_association(self):
        w=self.case();w.config['migration']['wizard']['association_types']['5']['mode']='association'
        with self.assertRaisesRegex(ApiError,'clone'):validate(w)
    def test_execution_tracker_not_allowed(self):
        w=self.case();w.plan['scopes']['execution']['source_only']=False
        with self.assertRaisesRegex(ApiError,'source-only'):validate(w)
    def test_unconfigured_backlog_blocks(self):
        w=self.case();w.config['migration']['full_xray'].pop('backlog_relation_name')
        with self.assertRaisesRegex(ApiError,'backlog_relation_name'):validate(w)
