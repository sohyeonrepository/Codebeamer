import unittest
import tempfile
import json
import zipfile
from pathlib import Path
from copy import deepcopy
from unittest.mock import Mock
from diagnose_field_creation import ReadOnlyHttp, read_journal, collect
from clients.http import ApiError

class DiagnosticScriptTests(unittest.TestCase):
    def test_all_writes_blocked_before_transport(self):
        client=ReadOnlyHttp('https://cb.example',auth='none')
        client.opener=Mock()
        for method in ('POST','PUT','PATCH','DELETE'):
            with self.assertRaises(ApiError):client.request(method,'/v3/tracker/configuration',{})
        client.opener.open.assert_not_called()

    def test_json_and_zip_have_same_journal_fingerprint(self):
        with tempfile.TemporaryDirectory() as root:
            raw=b'{"receipt":{"tracker":100}}'
            p=Path(root)/'field.json';p.write_bytes(raw)
            z=Path(root)/'field.zip'
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('field.json',raw)
            self.assertEqual(read_journal(p),read_journal(z))

    def test_collect_is_readonly_and_excludes_body_values(self):
        cb=Mock();cb.spec={'paths':{}}
        cb.http.request.side_effect=[{'fields':[{'id':1,'permission':{'type':'SINGLE','accessPermissions':[]}}]},[]]
        journal={'x':{'tracker':100,'status':'pending','error':'secret raw error',
            'request':{'body':{'fields':[{'id':1,'permission':{'type':'SINGLE','accessPermissions':[]}},
                {'label':'Labels','typeId':6,'permission':{'type':'PER_STATUS','accessPermissionsPerStatusMap':{}},
                 'options':[{'name':'secret value'}]}]}}}}
        before=deepcopy(journal)
        result=collect(cb,100,journal,'digest')
        self.assertEqual(result['writes'],0)
        self.assertFalse(result['records'][0]['post_response_recorded'])
        self.assertEqual(result['records'][0]['permission_changes'],[])
        self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(before,journal)
        cb.call.assert_not_called()
        self.assertTrue(all(c.args[0]=='GET' for c in cb.http.request.call_args_list))
