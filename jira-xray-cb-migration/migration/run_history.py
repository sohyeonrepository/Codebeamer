"""Replay source Run history/activity through real updates, never History POST."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from clients.http import ApiError
from migration.fields import payload
from validation.compare_fields import compare_fields
from validation.compare_history import revision_ids


def event_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ApiError('Run event timestamp requires a timezone')
    return parsed


def snapshots(latest, events, created):
    if not created.get('at') or event_time(created['at']) > min((event_time(e['at']) for e in events), default=event_time(created['at'])):
        raise ApiError('Created timestamp is missing or later than a source event')
    ordered = sorted(events, key=lambda e: (event_time(e['at']), str(e['id'])))
    if len({e['id'] for e in ordered}) != len(ordered):
        raise ApiError('Duplicate source Run event IDs')
    initial = deepcopy(latest)
    for event in reversed(ordered):
        for change in reversed(event['changes']):
            field = change['field']
            if field not in initial or initial[field] != change['after']:
                raise ApiError('Run history does not reconstruct latest value: ' + field)
            initial[field] = deepcopy(change['before'])
    result = [('created', deepcopy(initial), created)]
    current = deepcopy(initial)
    for event in ordered:
        for change in event['changes']:
            field = change['field']
            if field not in current or current[field] != change['before']:
                raise ApiError('Run history has a discontinuity: ' + field)
            current[field] = deepcopy(change['after'])
        result.append((event['id'], deepcopy(current), event))
    if current != latest:
        raise ApiError('Run history final snapshot differs from current source')
    return result


def replay_run(cb, state, settings, source_id, latest, events, created, params, mappings, reference_context=None):
    if '_sourceEvent' not in mappings:
        raise ApiError('Run replay requires a mapped _sourceEvent field to preserve original time/author and non-field Activity')
    missing = set(latest) - set(mappings)
    if missing:
        raise ApiError('Unmapped Run fields: ' + ', '.join(sorted(missing)))
    timeline = snapshots(latest, events, created)
    for operation in ('run_lookup', 'run_create', 'run_get', 'run_update', 'run_history'):
        cb.operation(operation)
    def expected(values, metadata):
        values = deepcopy(values)
        if reference_context:
            from migration.run_references import resolve_references
            execution, definitions, adapter = reference_context
            values['_sourceReferences'] = resolve_references(cb, values, execution, definitions, adapter)
        return payload(dict(values, _sourceEvent=metadata), mappings, settings)
    # Validate conversions and reference resolution for every snapshot before creating a Run.
    prepared = {str(event_id): expected(values, metadata) for event_id, values, metadata in timeline}
    key = 'run-replay:' + str(source_id)
    existing = state.get(key)
    found = cb.call('run_lookup', dict(params, sourceId=source_id))
    if not isinstance(found, list) or len(found) > 1:
        raise ApiError('Run replay requires exact unique source-ID lookup')
    if existing:
        if existing[0] != 'done':
            raise ApiError('Uncertain Run creation; reconcile before replay resume')
        run_id = existing[1]['id']
        if not found or found[0]['id'] != run_id:
            raise ApiError('Run replay lookup differs from saved target mapping')
    else:
        if found:
            raise ApiError('Existing Run has no replay checkpoint; do not replay old history onto it')
        first_body = prepared['created']
        created_run = state.once(key, lambda: cb.call('run_create', params, first_body))
        run_id = created_run['id']
    target = dict(params, recordId=run_id, itemId=run_id)
    first_key = key + ':created'
    initial_digest = hashlib.sha256(json.dumps(prepared['created'], sort_keys=True).encode()).hexdigest()
    initial_checkpoint = state.get(first_key)
    if initial_checkpoint and initial_checkpoint[1].get('fingerprint') != initial_digest:
        raise ApiError('Initial historical snapshot changed; refusing to append out-of-order history')
    seen_unapplied = False
    for event_id, _, _ in timeline[1:]:
        applied = state.get(key + ':event:' + str(event_id))
        if applied and applied[0] == 'done' and seen_unapplied:
            raise ApiError('Late historical insertion would reorder existing revisions; reconciliation required')
        if not applied or applied[0] != 'done':
            seen_unapplied = True
    if not initial_checkpoint:
        before = cb.call('run_get', target)
        if compare_fields(prepared['created'], before):
            raise ApiError('Initial Run fields differ; cannot establish Created revision')
        revisions = revision_ids(cb.call('run_history', target))
        if len(revisions) != 1:
            raise ApiError('Created Run must have exactly one initial revision')
        state.mark(first_key, 'done', {'revision': next(iter(revisions)), 'source': created,
                                     'fingerprint': initial_digest, 'native_author_restored': False, 'native_timestamp_restored': False})
    for event_id, values, metadata in timeline[1:]:
        body = prepared[str(event_id)]
        event_key = key + ':event:' + str(event_id)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        checkpoint = state.get(event_key)
        if checkpoint and checkpoint[0] == 'done' and checkpoint[1].get('fingerprint') != digest:
            raise ApiError('Previously replayed source event changed; reconciliation required')
        def apply():
            prior = revision_ids(cb.call('run_history', target))
            cb.call('run_update', target, body)
            actual = cb.call('run_get', target)
            if compare_fields(body, actual):
                raise ApiError('Run revision field verification failed')
            revisions = revision_ids(cb.call('run_history', target))
            added = revisions - prior
            if len(added) != 1 or not prior.issubset(revisions):
                raise ApiError('Expected exactly one new Run revision per source event')
            return {'revision': next(iter(added)), 'fingerprint': digest, 'source': metadata,
                    'native_author_restored': False, 'native_timestamp_restored': False}
        state.once(event_key, apply)
    final = cb.call('run_get', target)
    if compare_fields(prepared[str(timeline[-1][0])], final):
        raise ApiError('Final replayed Run differs from source')
    state.save('target-runs', source_id, final)
    return run_id
