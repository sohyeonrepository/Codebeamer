import unittest
from copy import deepcopy
from clients.http import ApiError
from migration.fields import payload
from migration.wizard import selected_target_changes


class SelectedTargetRegressionTests(unittest.TestCase):
    def setUp(self):
        self.before = [
            {'id':3,'label':'Name','typeName':'Text','typeId':0,'multipleSelection':False},
            {'id':15,'label':'Resolution','typeName':'Choice','typeId':6,'multipleSelection':False},
        ]
        self.rows = {'summary':{'selected':True,'status':'mapped',
                                'spec':{'field_id':3,'target':'name'}},
                     'resolution':{'selected':False,'status':'mapped',
                                   'spec':{'field_id':15,'target':'cb_field_15'}}}

    def test_unselected_field_change_and_reorder_do_not_block(self):
        after = deepcopy(list(reversed(self.before)))
        after[0]['typeId'] = 9  # resolution is unselected
        after.append({'id':99,'label':'Unrelated','typeName':'Text'})
        self.assertEqual(selected_target_changes(self.before,after,self.rows),[])

    def test_selected_field_type_change_blocks(self):
        after=deepcopy(self.before)
        after[0]['typeId']=99
        self.assertIn('summary:target.typeId',
                      selected_target_changes(self.before,after,self.rows))

    def test_new_field_name_collision_blocks(self):
        self.rows['extra']={'selected':True,'status':'create',
                            'creation':{'label':'NewField'}}
        after=deepcopy(self.before)+[{'id':99,'label':'NewField'}]
        self.assertIn('extra:planned_field_name_collision',
                      selected_target_changes(self.before,after,self.rows))

    def test_resolution_without_source_values_is_not_written(self):
        mapping={'resolution':{'type':'option','target':'cb_field_15',
                               'skip_when_source_empty':True}}
        self.assertEqual(payload({'resolution':None},mapping,{}),{})
        with self.assertRaises(ApiError):
            payload({'resolution':{'id':'3','name':'Fixed'}},mapping,{})


if __name__ == '__main__':
    unittest.main()
