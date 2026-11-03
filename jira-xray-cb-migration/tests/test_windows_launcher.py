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
