import json
import tempfile
import unittest
from pathlib import Path
from migration.wizard import pending_field_message

class PendingFieldErrorTests(unittest.TestCase):
    def test_recorded_status_is_distinguished_and_raw_body_not_shown(self):
        for status,expected in [('400','요청 형식/값'),('401','인증이 거부'),('403','접근이 거부'),('500','확정할 수 없습니다')]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as root:
                path=Path(root)/'journal.json'
                path.write_text(json.dumps({'entry':{'tracker':100,'error':'POST request failed: HTTP '+status+' secret-body'}}))
                result=pending_field_message('Labels',100,path)
                self.assertIn('HTTP '+status,result)
                self.assertIn(expected,result)
                self.assertNotIn('secret-body',result)
                self.assertIn('자동 재실행하지 않습니다',result)

    def test_missing_corrupt_or_other_tracker_evidence_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'journal.json'
            for body in (None,'invalid',json.dumps({'entry':{'tracker':999,'error':'HTTP 403'}})):
                if body is not None:path.write_text(body)
                result=pending_field_message('Labels',100,path)
                self.assertIn('판단할 수 없습니다',result)
                self.assertNotIn('HTTP 403',result)
