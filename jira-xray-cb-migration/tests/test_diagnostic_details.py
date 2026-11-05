"""Diagnostic logs must locate failures without recording issue values or secrets."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from clients.http import ApiError
from migration.diagnostics import safe_exception_details, error_classification, write_diagnostic


class DetailedDiagnosticTests(unittest.TestCase):
    def test_unconfigured_capability_has_safe_traceback_and_code(self):
        try:
            raise ApiError('Unconfigured API capability: tracker_get')
        except ApiError as error:
            details = safe_exception_details(error)
            code, summary = error_classification(error)
        self.assertEqual(code, 'UNCONFIGURED_API_CAPABILITY')
        self.assertEqual(details['summary'], 'Unconfigured API capability: tracker_get')
        self.assertTrue(any(frame['function'] == 'test_unconfigured_capability_has_safe_traceback_and_code'
                            for frame in details['frames']))
        self.assertTrue(all(set(frame) == {'file','function','line'} for frame in details['frames']))

    def test_exception_body_and_credentials_not_logged(self):
        secret = 'this-is-private-issue-content'
        try:
            raise ApiError('Unexpected Jira value '+secret)
        except ApiError as error:
            details = safe_exception_details(error)
        self.assertNotIn(secret, json.dumps(details))
        self.assertEqual(details['summary'], 'Exception message shown only in Wizard UI')


if __name__ == '__main__':
    unittest.main()
