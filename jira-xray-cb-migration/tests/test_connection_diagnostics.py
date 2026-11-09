import unittest
from clients.http import http_failure_detail
from pathlib import Path

class ConnectionDiagnostics(unittest.TestCase):
 def test_forbidden_reason_does_not_expose_token(self):
  result=http_failure_detail(b'{"errorMessages":["permission denied secret-token-123"]}','application/json','')
  self.assertIn('권한',result);self.assertNotIn('secret',result)
 def test_html_and_captcha_distinguished(self):
  self.assertIn('HTML',http_failure_detail(b'<html>login</html>','text/html',''))
  self.assertIn('CAPTCHA',http_failure_detail(b'', '', 'AUTHENTICATION_DENIED'))
 def test_unknown_is_not_diagnosed_as_permission(self):
  self.assertIn('미확인',http_failure_detail(b'', '', ''))
 def test_windows_entry_uses_canonical_wizard_without_live_test(self):
  text=(Path(__file__).resolve().parents[1]/'RUN_WINDOWS.bat').read_text()
  self.assertIn('import_wizard.py --config config.local.json --prompt-auth',text)
  self.assertNotIn('--live-test',text)
  self.assertIn('if not exist "config.local.json"',text)

class PermissionClassificationTests(__import__('unittest').TestCase):
 def test_permission_property_is_not_access_denial(self):
  result=http_failure_detail(b'{"message":"permission must not be null"}','application/json','')
  self.assertIn('속성 검증',result)
  self.assertNotIn('접근 거부 메시지 포함',result)
 def test_actual_denial_is_classified(self):
  result=http_failure_detail(b'{"message":"permission denied"}','application/json','')
  self.assertIn('접근 거부',result)
