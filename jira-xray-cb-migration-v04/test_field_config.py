import unittest
from unittest.mock import Mock
from pathlib import Path
from tempfile import TemporaryDirectory
from field_config import build_field_config, FieldCreationError, propose_and_apply_field
BASE={'basicInformation':{'trackerId':10161,'projectId':3,'name':'test'},'fields':[{'referenceId':3,'typeId':0,'label':'Name','position':20}]}
class FieldTest(unittest.TestCase):
 def test_creates_without_modifying_others(self):
  a=build_field_config(BASE,{'label':'Migration_Test_Text','type':'Text','referenceId':10005})
  self.assertEqual(a['fields'][-1]['typeId'],0)
  self.assertEqual(a['fields'][:1],BASE['fields'])
  self.assertEqual(len(BASE['fields']),1)
 def test_reject_duplicate(self):
  with self.assertRaises(FieldCreationError):build_field_config(BASE,{'label':'Name','type':'Text','referenceId':10005})
 def test_reject_duplicate_id(self):
  with self.assertRaises(FieldCreationError):build_field_config(BASE,{'label':'Other','type':'Text','referenceId':3})
 def test_never_posts_on_preview(self):
  cb=Mock();cb.request.return_value=BASE
  with TemporaryDirectory() as d:
   result=propose_and_apply_field(cb,10161,{'label':'Other','type':'Integer','referenceId':10005},Path(d))
   self.assertEqual(result['status'],'PREVIEW')
   self.assertEqual(cb.request.call_count,1)
 def test_apply_readback(self):
  cb=Mock(); candidate=build_field_config(BASE,{'label':'Other','type':'Integer','referenceId':10005})
  cb.request.side_effect=[BASE,BASE,{},candidate]
  with TemporaryDirectory() as d:
   with self.assertRaisesRegex(FieldCreationError,'BLOCKED'):
    propose_and_apply_field(cb,10161,{'label':'Other','type':'Integer','referenceId':10005},Path(d),apply=True)
   cb.request.assert_not_called()
if __name__=='__main__':unittest.main()
