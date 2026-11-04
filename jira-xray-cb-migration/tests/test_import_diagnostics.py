"""Diagnostic file must identify the exact field contract without Jira values."""
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from pathlib import Path
import unittest
from migration.diagnostics import write_diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_blocking_field_summary_is_safe_and_precise(self):
        with TemporaryDirectory() as root:
            class FakeCB:
                spec = {'openapi':'3.0.0','components':{'schemas':{}}}
                config = {'profile':'3.0.0.7'}
                def operation(self, name):
                    raise ValueError('unconfigured')
            wizard = SimpleNamespace(
                cb=FakeCB(), state=SimpleNamespace(root=Path(root)),
                plan={'scopes':{'STGVSTAR:10000':{
                    'type':{'name':'TEST'}, 'tracker':123,
                    'analysis':{'fields':{'resolution':{'name':'Resolution'}}},
                    'rows':{
                        'resolution':{'selected':True,'status':'mapped',
                                      'spec':{'field_id':15,'target':'cb_field_15','type':'option'},
                                      'target':{'id':15,'label':'Resolution','typeName':'Choice',
                                                'typeId':8,'valueModel':'ChoiceFieldValue<ChoiceOptionReference>'}},
                        'secret_jira_field':{'selected':False,'target':{'id':88,'label':'secret'},
                                             'spec':{'field_id':88}}}}}}})
            path = write_diagnostic(wizard, 'Import', ValueError('SECRET TOKEN'),field_id=15)
            record = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['blocking_field_id'],15)
            fields = record['scopes']['STGVSTAR:10000']['fields']
            self.assertEqual([f['cb_field_id'] for f in fields],[15])
            self.assertEqual(fields[0]['cb_field_name'],'Resolution')
            self.assertFalse(fields[0]['field_schema']['schema_found'])
            self.assertNotIn('SECRET TOKEN',path.read_text(encoding='utf-8'))
            self.assertNotIn('secret_jira_field',path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
