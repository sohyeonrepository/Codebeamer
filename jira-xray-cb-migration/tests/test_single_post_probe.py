from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error

from clients.http import ApiError
from test_field_post_once import prepare, execute, SinglePostHttp, CaptureOpener, POST_PATH


BEFORE = {'basicInformation': {'trackerId': 100}, 'fields': [
    {'id': 1, 'label': 'Name', 'typeId': 0, 'permission': {'type': 'PER_STATUS'}}]}
NEW = {'label': 'Test Labels', 'typeId': 6, 'multipleSelection': True,
       'permission': {'type': 'PER_STATUS'}, 'options': []}
BODY = dict(deepcopy(BEFORE), fields=deepcopy(BEFORE['fields']) + [NEW])


class Response(io.BytesIO):
    status = 200
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class FieldPostOnceTests(unittest.TestCase):
    def client(self, root, responses):
        http = SinglePostHttp('https://cb.example', auth='none')
        opener = Mock()
        opener.open.side_effect = responses
        http.opener = CaptureOpener(opener, root)
        cb = Mock()
        cb.http = http
        return cb, opener

    def test_preview_preserves_fresh_configuration_and_does_not_post(self):
        cb = Mock()
        cb.http.request.return_value = deepcopy(BEFORE)
        cb.spec = {'paths': {POST_PATH: {'post': {'requestBody': {'content': {
            'application/json': {'schema': {'type': 'object'}}}}}}}}
        journal = {'r': {'tracker': 100, 'request': {'method': 'POST', 'path': POST_PATH, 'body': BODY}}}
        original = deepcopy(journal)
        before, body = prepare(cb, journal, 100, 'Another Test')
        self.assertEqual(before, BEFORE)
        self.assertEqual(body['fields'][:-1], BEFORE['fields'])
        self.assertEqual(body['fields'][-1]['label'], 'Another Test')
        self.assertEqual(original, journal)
        cb.http.request.assert_called_once_with('GET', '/v3/tracker/100/configuration')

    def test_approval_and_drift_block_post(self):
        with tempfile.TemporaryDirectory() as root:
            cb, opener = self.client(Path(root), [Response(dict(BEFORE, changed=True))])
            with self.assertRaisesRegex(ApiError, 'approval'):
                execute(cb, 100, BEFORE, BODY, Path(root))
            opener.open.assert_not_called()
            with self.assertRaisesRegex(ApiError, 'changed'):
                execute(cb, 100, BEFORE, BODY, Path(root), approved=True)
            self.assertFalse((Path(root) / 'attempt.json').exists())
            self.assertFalse(cb.http.posted)

    def test_actual_400_body_is_saved_and_get_runs_without_repost(self):
        raw = b'{"message":"permission failure with server detail"}'
        error = urllib.error.HTTPError('https://cb.example'+POST_PATH, 400, 'Bad Request',
            {'Content-Type': 'application/json'}, io.BytesIO(raw))
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cb, opener = self.client(root, [Response(BEFORE), error, Response(BEFORE), Response(BEFORE)])
            result = execute(cb, 100, BEFORE, BODY, root, approved=True)
            self.assertFalse(result['write_verified'])
            self.assertEqual((root/'post-response.bin').read_bytes(), raw)
            self.assertEqual(json.loads((root/'http-status.json').read_text())['status'], 400)
            self.assertTrue((root/'configuration-after.json').exists())
            self.assertNotIn('server detail', json.dumps(result))
            with self.assertRaises(FileExistsError):
                execute(cb, 100, BEFORE, BODY, root, approved=True)
            self.assertEqual([c.args[0].get_method() for c in opener.open.call_args_list],
                             ['GET', 'POST', 'GET', 'GET'])

    def test_success_requires_actual_id_and_existing_preservation(self):
        after = deepcopy(BODY)
        after['fields'][-1]['id'] = 77
        with tempfile.TemporaryDirectory() as root:
            cb, opener = self.client(Path(root), [Response(BEFORE), Response({'id': 100}), Response(after)])
            result = execute(cb, 100, BEFORE, BODY, Path(root), approved=True)
            self.assertTrue(result['write_verified'])
            self.assertEqual(result['field_id'], 77)
            self.assertFalse(cb.http.armed)

    def test_permission_drift_after_post_is_not_success(self):
        after = deepcopy(BODY)
        after['fields'][-1]['id'] = 77
        after['fields'][0]['permission'] = {'type': 'CHANGED'}
        with tempfile.TemporaryDirectory() as root:
            cb, _ = self.client(Path(root), [Response(BEFORE), Response({}), Response(after)])
            result = execute(cb, 100, BEFORE, BODY, Path(root), approved=True)
            self.assertFalse(result['write_verified'])
            self.assertTrue(result['existing_configuration_changes'])

    def test_timeout_still_gets_current_state_and_never_retries_post(self):
        with tempfile.TemporaryDirectory() as root:
            cb, opener = self.client(Path(root), [Response(BEFORE), TimeoutError(), Response(BEFORE)])
            result = execute(cb, 100, BEFORE, BODY, Path(root), approved=True)
            self.assertFalse(result['write_verified'])
            self.assertIn('unknown', result['error'])
            self.assertEqual([c.args[0].get_method() for c in opener.open.call_args_list], ['GET', 'POST', 'GET'])

    def test_other_writes_and_unapproved_post_are_blocked(self):
        http = SinglePostHttp('https://cb.example', auth='none')
        http.opener = Mock()
        for method, path in [('POST', POST_PATH), ('PUT', POST_PATH), ('DELETE', POST_PATH)]:
            with self.assertRaises(ApiError): http.request(method, path, {})
        http.armed = True
        with self.assertRaises(ApiError): http.request('POST', '/v3/items', {})
        http.opener.open.assert_not_called()
