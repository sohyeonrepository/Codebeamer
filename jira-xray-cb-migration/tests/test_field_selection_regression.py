"""Regression: partial imports must start unchecked and never self-conflict."""
import unittest
from migration.wizard import ImportWizard, proposals


def cb_field(fid, label):
    return {'id':fid, 'typeName':'Text','typeId':0,'label':label,
            'hidden':False,'multipleSelection':False,'mandatory':False,
            'options':None,'reference':None}


class SaveCounter:
    def __init__(self):self.count=0
    def save(self, *args):self.count+=1


class FieldSelectionRegression(unittest.TestCase):
    def test_existing_summary_to_name_is_not_its_own_duplicate(self):
        source={'fields':{'summary':{'id':'summary','name':'Summary',
                                    'schema':{'type':'string'}}},
                'values':{'summary':['test']}}
        config={'migration':{},'codebeamer':{'field_bindings':{}}}
        original=proposals(source,[cb_field(3,'Name')],config)['summary']
        self.assertIsNotNone(original.get('spec'),original)
        repeat=proposals(source,[cb_field(3,'Name')],config,
                         {'summary':original['spec']})['summary']
        self.assertEqual(repeat['status'],'mapped',repeat)
        self.assertEqual(repeat['spec']['field_id'],3)

    def test_unselected_conflict_does_not_block_selected_summary(self):
        wizard=object.__new__(ImportWizard)
        wizard.state=SaveCounter()
        wizard.plan={'projects':{'STGVSTAR':{}},'scopes':{'STGVSTAR:14012':{
            'project':'STGVSTAR','type':{'id':'14012','name':'TEST'},
            'analysis':{'issue_count':2,'risks':[]},'target_risks':[],
            'rows':{
                'summary':{'selected':True,'status':'mapped',
                           'spec':{'field_id':3,'target':'name'}},
                'other':{'selected':False,'status':'mapped',
                         'spec':{'field_id':3,'target':'name'}}}}}}
        self.assertEqual(wizard.errors(),[])
        wizard.plan['scopes']['STGVSTAR:14012']['rows']['other']['selected']=True
        errors=wizard.errors()
        self.assertTrue(any('중복 Codebeamer' in e for e in errors),errors)

    def test_bulk_selection_saves_plan_only_once(self):
        wizard=object.__new__(ImportWizard)
        wizard.state=SaveCounter()
        wizard.plan={'scopes':{'P:1':{'rows':{
            'summary':{'selected':True},'description':{'selected':True},
            'resolution':{'selected':True}}}}}
        self.assertEqual(wizard.select_all_fields('P:1',False),0)
        self.assertEqual(wizard.state.count,1)
        self.assertTrue(all(not v['selected'] for v in wizard.plan['scopes']['P:1']['rows'].values()))


if __name__=='__main__':
    unittest.main()
