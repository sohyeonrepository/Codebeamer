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
    target_params = dict(params, recordId=created['id'], itemId=created['id'])
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
    tracker_id = run_tracker_id(settings)
    for category, records in [('comment', issue.get('_comments', [])), ('worklog', issue.get('_worklogs', []))]:
        if records and not settings.get(category + '_adapter', {}).get('fields'):
            raise ApiError('Execution ' + category + ' records were collected; configure target adapter before creating a Run')
    data = run['data']
    source = dict(data)
    if run.get('events_scope') == 'execution-context':
        source['_executionHistoryState'] = run['execution_history_state']
    if adapter.get('reference_mode') == 'field-values':
        from migration.run_references import resolve_references
        source['_sourceReferences'] = resolve_references(cb, data, issue, run.get('field_definitions', []), adapter)
        test_id = None
    else:
        test_key = str(read_path(data, adapter['test_issue_id_path']))
        if settings.get('mode') == 'test-runs':
            destination = settings.get('test_mapping', {}).get(test_key)
            if type(destination) is not int or destination <= 0:
                raise ApiError('Set test_mapping for source Test ' + test_key)
            actual_test = cb.call('item_get', {'itemId': destination})
            if actual_test.get('id') != destination:
                raise ApiError('Existing Test item verification failed')
            test = ('done', {'id': destination})
        else:
            test = state.get('item:' + test_key)
            if not test or test[0] != 'done':
                raise ApiError('Test Run test-case reference pending: ' + test_key)
        source['_testItem'] = {'id': test[1]['id']}
        test_id = test[1]['id']
    source['_executionItem'] = {'id': item_id} if item_id is not None else None
    if item_id is None:
        source['_executionSource'] = {'id': issue['id'], 'key': issue['key']}
        if adapter.get('execution_mode') != 'source_metadata':
            raise ApiError('No destination Execution Item: set run_adapter.execution_mode=source_metadata and map _executionSource/_executionItem explicitly')
    parameters = {'trackerId': tracker_id, 'executionId': item_id, 'testId': test_id}
    if settings.get('history_replay'):
        if not run.get('events_complete'):
            raise ApiError('Complete Run History and Activity must be collected before writes')
        from migration.run_history import replay_run
        run_id = replay_run(cb, state, settings, run['id'], source, run['events'], run['created'], parameters, adapter['fields'],
                            (issue, run.get('field_definitions', []), adapter) if adapter.get('reference_mode') == 'field-values' else None)
    else:
        run_id = mapped_record(cb, state, settings, 'run', run['id'], source, parameters, adapter['fields'])
    evidence_count = 0
    documents = [data]
    if settings.get('history_replay'):
        from migration.run_history import snapshots
        documents = [values for _, values, _ in snapshots(dict(data, _executionHistoryState=run['execution_history_state']) if run.get('events_scope') == 'execution-context' else data, run['events'], run['created'])]
    explicit = adapter.get('evidence_lists', []) + adapter.get('attachment_lists', [])
    seen_files = {}
    for document in documents:
        metadata_list = []
        if explicit:
            for file_list in explicit:
                for evidence in select_records(document, file_list['path']):
                    metadata_list.append({target: read_path(evidence, path) for target, path in file_list['fields'].items()})
        else:
            from migration.run_files import inventory
            metadata_list = inventory(document)
        for metadata in metadata_list:
            identity = str(metadata['id'])
            if identity in seen_files:
                if seen_files[identity] != metadata:
                    raise ApiError('Same source Attachment ID has conflicting historical metadata')
                continue
            seen_files[identity] = metadata
            transfer(source_http, cb, state, 'run:' + run['id'], run_id, metadata)
            evidence_count += 1
    if explicit and not adapter.get('evidence_inventory_verified', False):
        raise ApiError('Explicit attachment paths require a verified complete inventory')
    if not run.get('events_complete') and not adapter.get('activity_inventory_verified', False):
        raise ApiError('Xray Activity completeness has not been verified; do not infer it from Jira history')
    execution_attachment_count = 0
    if adapter.get('include_execution_attachments', True):
        for attachment in issue['fields'].get('attachment', []):
            transfer(source_http, cb, state, 'run:' + run['id'] + ':execution:' + str(issue['id']), run_id, attachment)
            execution_attachment_count += 1
    for category, records in [('comment', issue.get('_comments', [])), ('worklog', issue.get('_worklogs', []))]:
        for record in records:
            mapped_record(cb, state, settings, category, 'run:' + run['id'] + ':execution:' + str(issue['id']) + ':' + str(record['id']),
                          record, {'itemId': run_id}, settings[category + '_adapter']['fields'])
    return {'id': run_id, 'evidence': evidence_count, 'execution_attachments': execution_attachment_count}


def run_tracker_id(settings):
    configured = settings.get('xray_run_tracker')
    legacy = settings.get('run_adapter', {}).get('tracker_id')
    if configured is not None and legacy is not None and configured != legacy:
        raise ApiError('Conflicting xray_run_tracker and run_adapter.tracker_id')
    tracker = configured if configured is not None else legacy
    if type(tracker) is not int or tracker <= 0:
        raise ApiError('Set migration.xray_run_tracker to a positive Test Run Tracker ID')
    return tracker


def select_records(document, path):
    nodes = [document]
    for part in path.split('.'):
        next_nodes = []
        for node in nodes:
            if part == '*':
                if not isinstance(node, list):
                    raise ApiError('Wildcard attachment path requires an array')
                next_nodes.extend(node)
            else:
                next_nodes.append(node[int(part)] if isinstance(node, list) else node[part])
        nodes = next_nodes
    records = []
    for node in nodes:
        if isinstance(node, list):
            records.extend(node)
        elif isinstance(node, dict):
            records.append(node)
        else:
            raise ApiError('Attachment path must select objects or arrays')
    return records
