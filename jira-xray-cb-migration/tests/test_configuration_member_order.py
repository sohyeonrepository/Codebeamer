import unittest
import tempfile
from pathlib import Path
from copy import deepcopy
from mapping_helper import _configuration_drift, create_confirmed_field, creation_templates
from tests import test_mapping_helper as fixtures
from clients.http import ApiError

class MemberOrderTests(unittest.TestCase):
    def baseline(self):
        return {'fields':[{'id':i,'permission':{'type':'UNRESTRICTED'},
            'choiceOptionSetting':{'type':'ChoiceMembers','membersTypeDataSourceTypes':['USERS','GROUPS','ROLES']}}
            for i in (1009,1011,1012)]}

    def test_unselected_member_order_only_is_equal_without_mutation(self):
        before=self.baseline();after=deepcopy(before)
        for f in after['fields']:f['choiceOptionSetting']['membersTypeDataSourceTypes'].reverse()
        snapshot=deepcopy(after)
        self.assertEqual(_configuration_drift(before,after),[])
        self.assertEqual(after,snapshot)

    def test_real_changes_still_block(self):
        for value in (['USERS'],['USERS','USERS','ROLES'],['UNKNOWN'],None):
            before=self.baseline();after=deepcopy(before)
            after['fields'][0]['choiceOptionSetting']['membersTypeDataSourceTypes']=value
            self.assertTrue(_configuration_drift(before,after))
        before=self.baseline();after=deepcopy(before)
        after['fields'][0]['permission']['type']='RESTRICTED'
        self.assertTrue(_configuration_drift(before,after))

    def test_other_array_order_is_not_ignored(self):
        before=self.baseline();before['fields'][0]['options']=[{'id':1},{'id':2}]
        after=deepcopy(before);after['fields'][0]['options'].reverse()
        self.assertTrue(_configuration_drift(before,after))

    def test_create_preserves_latest_get_and_accepts_readback_order(self):
        cb,baseline=fixtures.MappingHelperTests().creation_client()
        baseline['fields'][0]['choiceOptionSetting']={'type':'ChoiceMembers',
            'membersTypeDataSourceTypes':['USERS','GROUPS','ROLES']}
        template=creation_templates(baseline)[0]['definition'];template['label']='New Text'
        original=cb.configuration
        def read(tracker):
            result=original(tracker)
            if not cb.writes:result['fields'][0]['choiceOptionSetting']['membersTypeDataSourceTypes'].reverse()
            return result
        cb.configuration=read
        original_call=cb.call
        sent=[]
        def call(name,params,body):
            sent.append(deepcopy(body))
            result=original_call(name,params,body)
            cb.actual['fields'][0]['choiceOptionSetting']['membersTypeDataSourceTypes'].reverse()
            return result
        cb.call=call
        with tempfile.TemporaryDirectory() as root:
            created,_=create_confirmed_field(cb,100,template,baseline,Path(root)/'journal.json',approved=True)
        self.assertEqual(created['id'],99)
        self.assertEqual(cb.writes,1)
        self.assertEqual(sent[0]['fields'][0]['choiceOptionSetting']['membersTypeDataSourceTypes'],['ROLES','GROUPS','USERS'])

    def test_real_drift_no_post_and_diagnostic(self):
        cb,baseline=fixtures.MappingHelperTests().creation_client()
        expected=deepcopy(baseline)
        expected['fields'][0]['permission']={'type':'OTHER'}
        template=creation_templates(baseline)[0]['definition'];template['label']='New Text'
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ApiError):
                create_confirmed_field(cb,100,template,expected,Path(root)/'journal.json',approved=True)
            self.assertTrue((Path(root)/'journal.drift.json').exists())
        self.assertEqual(cb.writes,0)
