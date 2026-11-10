"""Interactive, single POST field test; never resets Import state."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
import time
from pathlib import Path
import urllib.error

from clients.http import Http, ApiError
from clients.codebeamer_client import CodebeamerClient
from diagnose_field_creation import read_journal
from mapping_helper import _configuration_drift, field_identifier, request_schema
from single_import import validate

POST_PATH = '/v3/tracker/configuration'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class CaptureOpener:
    def __init__(self, opener, directory):
        self.opener, self.directory = opener, directory

    def open(self, request, **kwargs):
        try:
            response = self.opener.open(request, **kwargs)
            if request.get_method() == 'POST':
                save(self.directory / 'http-status.json', {'status': response.status})
            return response
        except urllib.error.HTTPError as exc:
            if request.get_method() != 'POST':
                raise
            # Store the real response locally, not in console output/audit logs.
            body = exc.read(1024 * 1024 + 1)
            self.directory.joinpath('post-response.bin').write_bytes(body[:1024 * 1024])
            save(self.directory / 'http-status.json', {'status': exc.code,
                 'response_truncated': len(body) > 1024 * 1024})
            replacement = urllib.error.HTTPError(exc.url, exc.code, exc.reason,
                                                exc.headers, io.BytesIO(body))
            exc.close()
            raise replacement from None


class SinglePostHttp(Http):
    armed = False
    posted = False

    def request(self, method, path, *args, **kwargs):
        if method != 'GET':
            if method != 'POST' or path != POST_PATH or not self.armed or self.posted:
                raise ApiError('Only one approved Configuration POST is allowed')
            self.posted = True
        return super().request(method, path, *args, **kwargs)


def prepare(cb, journal, tracker, label):
    if not label.strip():
        raise ApiError('New test field name required')
    records = [r for r in journal.values() if isinstance(r, dict) and r.get('tracker') == tracker]
    if len(records) != 1:
        raise ApiError('Exactly one source creation record for this Tracker is required')
    request = records[0].get('request', {})
    old = request.get('body', {})
    if request.get('path') != POST_PATH or request.get('method') != 'POST' or not old.get('fields'):
        raise ApiError('Configuration POST evidence missing in source journal')
    if old.get('basicInformation', {}).get('trackerId') != tracker:
        raise ApiError('Source Configuration belongs to another Tracker')
    definition = deepcopy(old['fields'][-1])
    if any(key in definition for key in ('id', 'fieldId', 'referenceId')):
        raise ApiError('Source new-field definition contains an ID; manual contract review required')
    definition['label'] = label.strip()
    current = cb.http.request('GET', f'/v3/tracker/{tracker}/configuration')
    if not isinstance(current, dict) or current.get('basicInformation', {}).get('trackerId') != tracker or not isinstance(current.get('fields'), list):
        raise ApiError('Current Configuration does not identify selected Tracker and fields')
    if any(f.get('label', f.get('name', '')).strip().casefold() == label.strip().casefold() for f in current['fields']):
        raise ApiError('Test field already exists; no POST. Select/map the existing field instead')
    # Preserve fresh fields, permissions and all other settings verbatim.
    body = deepcopy(current)
    body['fields'].append(definition)
    cb.discover()
    operation = cb.spec.get('paths', {}).get(POST_PATH, {}).get('post')
    if not operation:
        raise ApiError('Server OpenAPI does not declare Configuration POST')
    validate(cb.spec, body, request_schema(cb.spec, operation))
    return current, body


def execute(cb, tracker, before, body, directory, approved=False):
    if not approved:
        raise ApiError('Explicit approval required; no POST')
    current = cb.http.request('GET', f'/v3/tracker/{tracker}/configuration')
    if _configuration_drift(before, current):
        raise ApiError('Configuration changed after preview; no POST')
    # Exclusive durable marker: even a crash/timeout cannot cause an automatic repost.
    with (directory / 'attempt.json').open('x', encoding='utf-8') as stream:
        json.dump({'tracker': tracker, 'status': 'pending', 'automatic_retry': False}, stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    result = {'post_attempted': True, 'write_verified': False}
    cb.http.armed = True
    try:
        response = cb.http.request('POST', POST_PATH, body, raw=True)
        (directory / 'post-response.bin').write_bytes(response)
        result['post_returned_success'] = True
    except (ApiError, OSError, ValueError) as exc:
        result.update(post_returned_success=False,
                      error=str(exc) if isinstance(exc, ApiError) else type(exc).__name__,
                      evidence=exc.evidence if isinstance(exc, ApiError) else None)
    finally:
        cb.http.armed = False
        save(directory / 'result.json', result)
    try:
        after = cb.http.request('GET', f'/v3/tracker/{tracker}/configuration')
        save(directory / 'configuration-after.json', after)
        definition = body['fields'][-1]
        matches = [f for f in after.get('fields', []) if f.get('label') == definition['label']]
        result['matching_fields'] = len(matches)
        if len(matches) == 1:
            field = matches[0]
            preserved = deepcopy(after)
            preserved['fields'].remove(field)
            result['existing_configuration_changes'] = _configuration_drift(before, preserved)
            result['field_id'] = field_identifier(field)
            result['new_definition_changes'] = [k for k, v in definition.items() if field.get(k) != v]
            result['write_verified'] = (result['field_id'] is not None
                and not result['existing_configuration_changes'] and not result['new_definition_changes'])
        else:
            result['existing_configuration_changes'] = _configuration_drift(before, after)
    except (ApiError, ValueError, TypeError, KeyError) as exc:
        result['readback_error'] = str(exc) if isinstance(exc, ApiError) else type(exc).__name__
    save(directory / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.local.json')
    parser.add_argument('--journal')
    parser.add_argument('--swagger')
    args = parser.parse_args()
    journal, digest = read_journal(Path(args.journal or input('Failed journal JSON/ZIP full path: ').strip().strip('"')))
    tracker = int(input('Test Tracker ID (e.g. 4874140): ').strip())
    if tracker <= 0: raise ValueError('Positive Tracker ID required')
    label = input('New field name [Migration_API_Test_Labels]: ').strip() or 'Migration_API_Test_Labels'
    settings = deepcopy(json.loads(Path(args.config).read_text(encoding='utf-8-sig'))['codebeamer'])
    settings.pop('profile', None)
    if args.swagger: settings['swagger_url'] = args.swagger
    url = settings.get('url') or input('Codebeamer API base URL (including /cb/api): ').strip()
    http = SinglePostHttp(url, settings.get('token_env'), auth=settings.get('auth', 'basic'),
        username_env=settings.get('username_env'), password_env=settings.get('password_env'),
        allow_http=settings.get('allow_http', False), prompt_auth=True, timeout=30)
    identity = hashlib.sha256(json.dumps([url.rstrip('/'), tracker, label.strip().casefold()]).encode()).hexdigest()[:24]
    directory = Path('diagnostics') / ('post-once-' + identity)
    if (directory / 'attempt.json').exists():
        raise ApiError('Previous attempt exists; inspect its result. This tool will not POST again: '+str(directory.resolve()))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    http.opener = CaptureOpener(http.opener, directory)
    cb = CodebeamerClient(http, settings, write=False)
    http.read_deadline = time.monotonic() + 60
    try:
        before, body = prepare(cb, journal, tracker, label)
    finally:
        http.read_deadline = None
    save(directory / 'configuration-before.json', before)
    save(directory / 'request.json', body)
    save(directory / 'preview.json', {'tracker': tracker, 'source_sha256': digest,
        'before_field_count': len(before['fields']), 'after_field_count': len(body['fields']),
        'existing_configuration_changes': [], 'new_field': body['fields'][-1]})
    print('Server:', url, '\nTracker:', tracker, '\nNew field:', label)
    print('Preview / backup:', directory.resolve())
    print('Existing fields and permissions are preserved. One POST, then GET verification.')
    print('Response/configuration files may contain customer data. Review before sharing.')
    phrase = f'POST {tracker} {label}'
    if input('Approve by typing exactly "'+phrase+'": ').strip() != phrase:
        print('Cancelled. No POST.'); return
    result = execute(cb, tracker, before, body, directory, approved=True)
    print('Field creation verified:', result['write_verified'])
    print('Result / actual HTTP response:', directory.resolve())
    print('Do not delete attempt.json or rerun to repeat the POST.')


if __name__ == '__main__':
    try:
        main()
    except (ApiError, ValueError, OSError, KeyError) as exc:
        print('Test stopped:', str(exc))
        raise SystemExit(1)
