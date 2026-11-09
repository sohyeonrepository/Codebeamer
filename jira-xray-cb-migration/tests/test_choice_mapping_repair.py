import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from clients.http import ApiError
from migration.option_batch import option_default, resolve_option
from migration.choice_option_creation import verify_option_readback

class ChoiceMappingRepairTests(unittest.TestCase):
    def setUp(self):
        self.options=[{'id':7,'name':'Target A','type':'ChoiceOptionReference'}]

    def test_saved_id_restores_target_instead_of_source_label(self):
        self.assertEqual(option_default(self.options,{'Source A':{'id':7}},'Source A','Source A'),'Target A')

    def test_legacy_name_is_preserved(self):
        self.assertEqual(option_default(self.options,{'Source A':'Target A'},'Source A','Source A'),'Target A')

    def test_deleted_id_requires_review_not_new_option(self):
        self.assertEqual(option_default(self.options,{'Source A':{'id':99}},'Source A','Source A'),'')

    def test_duplicate_names_require_id(self):
        opts=self.options+[dict(self.options[0],id=8)]
        with self.assertRaises(ApiError):resolve_option(opts,'Target A')
        self.assertEqual(resolve_option(opts,{'id':7})['id'],7)

    def test_single_and_multi_readback_retry_get_only(self):
        for multiple in (False,True):
            with self.subTest(multiple=multiple), tempfile.TemporaryDirectory() as root:
                detail={'id':12,'multipleValues':multiple,'options':self.options}
                cb=SimpleNamespace(http=Mock(),call=Mock())
                cb.http.request.side_effect=[[],dict(detail,options=[]),detail]
                self.assertEqual(verify_option_readback(cb,100,12,'Target A',root),detail)
                self.assertEqual(cb.http.request.call_count,3)
                self.assertTrue(all(c.args[0]=='GET' for c in cb.http.request.call_args_list))
                cb.call.assert_not_called()

    def test_failed_readback_records_shape_without_raw_values(self):
        with tempfile.TemporaryDirectory() as root:
            cb=SimpleNamespace(http=Mock(),call=Mock())
            cb.http.request.return_value={'id':999,'options':self.options}
            with self.assertRaises(ApiError):verify_option_readback(cb,100,12,'Target A',root)
            report=json.loads(next(Path(root).glob('*.json')).read_text())
            self.assertEqual(len(report['get_attempts']),3)
            self.assertNotIn('Target A',json.dumps(report))
            cb.call.assert_not_called()

    def test_multi_import_uses_real_ids_and_single_rejects_many(self):
        from migration.simple_item_import import _supported_custom_field_values
        opts=self.options+[{'id':8,'name':'Target B','type':'ChoiceOptionReference'}]
        entry={'rows':{'f':{'selected':True,'spec':{'field_id':12,
            'value_mapping':{'Source A':{'id':7},'Source B':{'id':8}}}}}}
        detail={'id':12,'valueModel':'ChoiceFieldValue','referenceType':'ChoiceOptionReference',
                'multipleValues':True,'options':opts}
        source={'f':[{'value':'Source A'},{'value':'Source B'}]}
        result=_supported_custom_field_values(entry,[detail],source,999)
        self.assertEqual([v['id'] for v in result[0]['values']],[7,8])
        detail['multipleValues']=False
        with self.assertRaises(ApiError):_supported_custom_field_values(entry,[detail],source,999)
