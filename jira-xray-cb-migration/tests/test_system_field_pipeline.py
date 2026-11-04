"""Real Wizard/Engine + Mock source HTTP and target adapter (not live CB)."""
import unittest
import tests.test_import_wizard as fixtures
from tests.test_system_field_mapping_contract import cb_field

class SystemPipelineTests(unittest.TestCase):
    setUp = fixtures.WizardTests.setUp

    def test_scoped_reads_plan_and_import_value_readback(self):
        additions=[fixtures.definition('assignee','Assignee','user'),
                   fixtures.definition('duedate','Due Date','date'),
                   fixtures.definition('priority','Priority','priority'),
                   {'id':'components','name':'Components','schema':{'type':'array','items':'component'}}]
        self.source.definitions.extend(additions)
        self.source.configured.extend(dict(d,hidden=False) for d in additions)
        for row in self.source.rows:
            row['fields'].update(assignee={'name':'legacy'},duedate='2026-11-01',
                                 priority={'id':'4','name':'High'},components=[{'id':'9','name':'Engine'}])
        priority=cb_field(42,'Priority','Choice');priority['options']=[{'id':81,'name':'High'}]
        components=cb_field(43,'Components','Reference',True);components['reference']={'trackerId':100}
        self.cb.definitions.extend([cb_field(40,'Assigned to','User'),cb_field(41,'End Date','Date'),priority,components])
        self.cb.items[901]={'name':'Component Engine'}
        self.wizard.config['migration']['user_mapping']={'legacy':{'id':12}}
        self.wizard.config['migration']['catalog_reference_mapping']={'components':{
            'field_id':43,'tracker_id':100,'target':'componentsRef','value_mapping':{'9':{'id':901}}}}
        self.wizard.discover();self.wizard.select_project('P',10)
        for kind in self.source.types:
            self.wizard.map_tracker('P',kind['id'],100)
            self.wizard.analyze('P:'+kind['id'])
        self.assertFalse(self.cb.mutations)
        report=self.wizard.execute(True)
        self.assertTrue(report['complete'],report)
        imported=[v for v in self.cb.items.values() if v.get('sourceId')]
        self.assertEqual(len(imported),2)
        for item in imported:
            self.assertEqual(item['cb_field_40'],{'id':12})
            self.assertEqual(item['cb_field_41'],'2026-11-01')
            self.assertEqual(item['cb_field_42'],{'id':81})
            self.assertEqual(item['componentsRef'],[{'id':901}])
        self.assertIn(('GET','/rest/api/2/field'),self.source.reads)
