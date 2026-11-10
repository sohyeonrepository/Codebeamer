import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock
from unittest.mock import patch

from clients.http import ApiError, Http, http_error_evidence
from mapping_helper import create_confirmed_field, creation_templates, read_stage
from migration.wizard import pending_field_message


class ErrorEvidenceTests(unittest.TestCase):
    def test_nested_error_keeps_constraints_without_echoed_values(self):
        body = json.dumps({'errors': {'fields[42].permission': 'must not be null secret-token'},
                           'request': {'password': 'secret', 'permission': 'access denied'},
                           'rejectedValue': 'secret'}).encode()
        evidence = http_error_evidence(body, 'application/json', 400)
        self.assertEqual(evidence['properties'], ['permission'])
        self.assertEqual(evidence['constraints'], ['not_null'])
        self.assertNotIn('secret', json.dumps(evidence))
        self.assertEqual(evidence['http_status'], 400)

    def test_permission_mention_alone_is_not_a_constraint(self):
        evidence = http_error_evidence(b'{"message":"permission"}', 'application/json', 400)
        self.assertEqual(evidence['constraints'], [])
        self.assertEqual(evidence['properties'], ['permission'])

    def test_unparsed_and_truncated_errors_remain_explicit(self):
        evidence = http_error_evidence(b'x' * 8193, 'text/html', 502)
        self.assertTrue(evidence['body_truncated'])
        self.assertEqual(evidence['format'], 'html')
        self.assertEqual(evidence['constraints'], [])

    def test_http_and_stage_keep_evidence_without_retrying_post(self):
        client = Http('https://cb.example', auth='none')
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError(
            'https://cb.example/v3/tracker/configuration', 400, 'Bad Request',
            {'Content-Type': 'application/json'},
            io.BytesIO(b'{"message":"permission must not be null secret-token"}'))
        with self.assertRaises(ApiError) as raised:
            read_stage('create', lambda: client.request('POST', '/v3/tracker/configuration', {}))
        self.assertEqual(raised.exception.evidence['constraints'], ['not_null'])
        self.assertIn('constraints=not_null', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))
        self.assertEqual(client.opener.open.call_count, 1)

    def test_failed_configuration_journal_keeps_evidence_and_blocks_repost(self):
        from tests import test_mapping_helper
        cb, baseline = test_mapping_helper.MappingHelperTests().creation_client()
        definition = creation_templates(baseline)[0]['definition']
        definition['label'] = 'New field'
        evidence = http_error_evidence(b'{"message":"permission must not be null"}', 'application/json', 400)
        cb.call = Mock(side_effect=ApiError('HTTP 400', evidence=evidence))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'journal.json'
            for _ in range(2):
                with self.assertRaises(ApiError):
                    create_confirmed_field(cb, 100, definition, baseline, path, approved=True)
            receipt = next(iter(json.loads(path.read_text()).values()))
            self.assertEqual(receipt['http_error_evidence'], evidence)
            self.assertEqual(receipt['status'], 'pending')
        self.assertEqual(cb.call.call_count, 1)
        self.assertFalse(cb.write)

    def test_pending_permission_mention_does_not_claim_validation_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'journal.json'
            path.write_text(json.dumps({'entry': {'tracker': 100, 'error':
                'HTTP 400; JSON 오류 응답에 permission 관련 내용 포함'}}))
            message = pending_field_message('Labels', 100, path)
        self.assertIn('확정되지 않았습니다', message)
        self.assertNotIn('검증 오류입니다', message)
        self.assertIn('자동 재실행하지 않습니다', message)

    def test_dedicated_failure_is_also_recorded_and_write_flag_restored(self):
        from mapping_helper import create_dedicated_field
        evidence = http_error_evidence(b'{"message":"permission denied"}', 'application/json', 403)
        cb = Mock()
        cb.write = False
        cb.config = {}
        cb.fields.return_value = [{'id': 1, 'label': 'Name'}]
        cb.call.side_effect = ApiError('HTTP 403', evidence=evidence)
        definition = {'label': 'New field', 'typeId': 0}
        with tempfile.TemporaryDirectory() as root, patch('mapping_helper.dedicated_field_contract',
                return_value=({'path': '/verified/fields', 'method': 'POST'}, definition)):
            path = Path(root) / 'journal.json'
            with self.assertRaises(ApiError):
                create_dedicated_field(cb, 100, definition, path)
            receipt = next(iter(json.loads(path.read_text()).values()))
            self.assertEqual(receipt['http_error_evidence'], evidence)
            self.assertEqual(receipt['status'], 'pending')
        self.assertFalse(cb.write)
        self.assertNotIn('helper_field_create', cb.config['operations'])
