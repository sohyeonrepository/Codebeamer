"""Verified child-record adapters; mappings are derived from real deployment schemas."""
import hashlib
import json
from clients.http import ApiError
from migration.fields import payload
from migration.attachments import transfer
from validation.compare_fields import compare_fields


def read_path(document, dotted):
    value = document
    for part in dotted.split('.'):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def mapped_record(cb, state, settings, category, source_id, source, params, mappings):
    if not mappings:
        raise ApiError('Missing ' + category + ' field adapter')
    missing = set(source) - set(mappings)
    if missing:
        raise ApiError('Unmapped ' + category + ' fields: ' + ', '.join(sorted(missing)))
    expected = payload(source, mappings, settings)
    fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    identity = category + ':' + str(source_id)
    previous = state.get(identity)
    if previous:
        if previous[0] != 'done':
            raise ApiError('Uncertain child record requires reconciliation: ' + identity)
        created = previous[1]
    else:
        # Require an exact external-ID lookup for resumed/lost local state.
        found = cb.call(category + '_lookup', dict(params, sourceId=source_id))
        if not isinstance(found, list) or len(found) > 1:
            raise ApiError('Child lookup must return zero or one exact source-ID match')
        if found:
            created = found[0]
            state.mark(identity, 'done', created)
        else:
            created = state.once(identity, lambda: cb.call(category + '_create', params, expected))
    target_params = dict(params, recordId=created['id'])
    actual = cb.call(category + '_get', target_params)
    if compare_fields(expected, actual):
        state.once(identity + ':update:' + fingerprint, lambda: cb.call(category + '_update', target_params, expected))
        actual = cb.call(category + '_get', target_params)
    state.save('target', identity, actual)
    if compare_fields(expected, actual):
        raise ApiError(category + ' verification mismatch')
    return created['id']


def test_run(cb, state, settings, source_http, issue, item_id, run):
    adapter = settings.get('run_adapter', {})
    if not adapter:
        raise ApiError('Missing run_adapter: Test Run schemas must be verified from actual APIs')
    data = run['data']
    test_key = str(read_path(data, adapter['test_issue_id_path']))
    test = state.get('item:' + test_key)
    if not test or test[0] != 'done':
        raise ApiError('Test Run test-case reference pending: ' + test_key)
    source = dict(data)
    source['_testItem'] = {'id': test[1]['id']}
    source['_executionItem'] = {'id': item_id}
    run_id = mapped_record(cb, state, settings, 'run', run['id'], source,
                          {'trackerId': adapter['tracker_id'], 'executionId': item_id, 'testId': test[1]['id']}, adapter['fields'])
    # Evidence format and its exact paths vary by Xray release; never guess a download URL.
    evidence_count = 0
    for evidence_list in adapter.get('evidence_lists', []):
        rows = read_path(data, evidence_list['path'])
        if not isinstance(rows, list):
            raise ApiError('Evidence collection is not an array')
        for evidence in rows:
            metadata = {target: read_path(evidence, path) for target, path in evidence_list['fields'].items()}
            transfer(source_http, cb, state, 'run:' + run['id'], run_id, metadata)
            evidence_count += 1
    if not adapter.get('evidence_inventory_verified', False):
        raise ApiError('Run evidence inventory has not been verified against the source API')
    if not adapter.get('activity_inventory_verified', False):
        raise ApiError('Xray Activity completeness has not been verified; do not infer it from Jira history')
    return {'id': run_id, 'evidence': evidence_count}
