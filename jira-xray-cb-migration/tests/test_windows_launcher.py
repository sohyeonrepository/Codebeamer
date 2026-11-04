import json
import tempfile
import unittest
from pathlib import Path
from windows_live_test import prepare_config

class WindowsLauncherTests(unittest.TestCase):
    def test_existing_local_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config.local.json').write_text('existing settings')
            self.assertEqual(prepare_config(root).read_text(),'existing settings')
    def test_new_config_has_no_preselected_server_or_swagger(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);template={'jira':{'url':'https://example.invalid'},'codebeamer':{'url':'https://example.invalid','swagger_url':'example'},'migration':{}}
            (root/'config.json').write_text(json.dumps(template))
            result=json.loads(prepare_config(root).read_text())
            self.assertEqual(result['jira']['url'],'')
            self.assertNotIn('swagger_url',result['codebeamer'])
            self.assertEqual(json.loads((root/'config.json').read_text()),template)
    def test_launcher_enables_authentication_dialogs(self):
        from unittest.mock import patch
        import windows_live_test
        with patch('windows_live_test.prepare_config',return_value=Path('config.local.json')), patch('import_wizard.main') as launch:
            windows_live_test.main()
        self.assertIn('--prompt-auth',launch.call_args.args[0])
    def test_http_prompts_for_pat_and_basic_when_environment_missing(self):
        from unittest.mock import patch,Mock
        from clients.http import Http
        with patch.dict('os.environ',{},clear=True):
            prompt=Mock(side_effect=['test-pat','test-user','test-password'])
            jira=Http('https://jira.invalid',auth='bearer',prompt_auth=True,credential_prompt=prompt)
            cb=Http('https://cb.invalid',auth='basic',prompt_auth=True,credential_prompt=prompt)
        self.assertEqual(jira.authorization,'Bearer test-pat')
        self.assertTrue(cb.authorization.startswith('Basic '))
        self.assertEqual(prompt.call_count,3)
