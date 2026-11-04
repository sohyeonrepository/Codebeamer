import unittest
from copy import deepcopy
from types import SimpleNamespace
from clients.http import ApiError
from migration.xray_mapping import mapping_rows, save_field, writable_properties
from migration.wizard import ImportWizard
from tests import test_import_wizard as base

class XrayMappingFormTests(unittest.TestCase):
    setUp=base.WizardTests.setUp
    def contract(self):
        self.cb.spec={'paths':{'/verified/run':{'post':{'requestBody':{'content':{'application/json':{'schema':{
            'type':'object','properties':{'state':{'type':'string','enum':['PASS','FAIL']},
                'result':{'type':'string'},'id':{'type':'integer','readOnly':True}}}}}}}}}}
        self.cb.operation=lambda name:{'method':'POST','path':'/verified/run'}

    def test_record_forms_and_persisted_user_selection(self):
        self.contract()
        report={'kind':'run','run':{'id':77,'status':'PASS'},'reads':{'test_run_steps':{
            'items':[{'id':21,'actualResult':'observed'}]}}}
        rows=mapping_rows(report,{})
        self.assertTrue(any(r['category']=='run_step' and r['source']=='actualResult' for r in rows))
        self.assertTrue(any(r['source']=='_executionSource' and r['samples']==[] for r in rows))
        save_field(self.wizard,'run','status','state','text',{},['PASS'])
        self.assertFalse(self.cb.mutations)
        restarted=ImportWizard(self.wizard.jira,self.cb,self.state,self.config)
        self.assertEqual(restarted.config['migration']['run_adapter']['fields']['status']['target'],'state')
        save_field(restarted,'run','status','result','text',{},['PASS'])
        self.assertEqual(restarted.config['migration']['run_adapter']['fields']['status']['target'],'result')

    def test_readonly_duplicate_and_invalid_value_rejected(self):
        self.contract()
        self.assertNotIn('id',writable_properties(self.cb,'run'))
        with self.assertRaises(ApiError):save_field(self.wizard,'run','status','id','raw',{},[77])
        with self.assertRaises(ApiError):save_field(self.wizard,'run','status','state','text',{},['UNKNOWN'])
        save_field(self.wizard,'run','status','state','text',{},['PASS'])
        with self.assertRaisesRegex(ApiError,'already mapped'):
            save_field(self.wizard,'run','other','state','text',{},['PASS'])

    def test_missing_native_contract_does_not_invent_targets(self):
        self.cb.spec=None
        with self.assertRaisesRegex(ApiError,'쓰기 스키마 미확인'):
            writable_properties(self.cb,'run')
