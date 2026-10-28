from copy import deepcopy
from clients.http import ApiError
from validation.compare_history import revision_ids


def timeline(issue, mappings):
    current = deepcopy(issue['fields'])
    histories = issue['_history']
    for event in reversed(histories):
        for change in reversed(event['items']):
            field = change.get('fieldId') or change.get('field')
            if field not in mappings:
                raise ApiError('Unmapped historical field: ' + str(field))
            # Jira from/to strings are not necessarily current field JSON values.
            spec = mappings[field]
            if spec.get('history_encoding') != 'scalar':
                raise ApiError('Historical value needs a verified scalar adapter: ' + field)
            current[field] = change.get('fromString') if spec.get('history_use_display') else change.get('from')
    result = [('created', deepcopy(current), {'author': issue['fields'].get('creator'), 'created': issue['fields'].get('created')})]
    for event in histories:
        for change in event['items']:
            field = change.get('fieldId') or change.get('field')
            current[field] = change.get('toString') if mappings[field].get('history_use_display') else change.get('to')
        result.append((str(event['id']), deepcopy(current), {'author': event.get('author'), 'created': event['created']}))
    return result


def replay(cb, state, issue_key, item_id, events, make_payload):
    for source_id, fields, metadata in events:
        if source_id == 'created':
            continue
        key = f'history:{issue_key}:{source_id}'
        def apply():
            before = revision_ids(cb.call('history', {'itemId': item_id}))
            cb.call('item_update', {'itemId': item_id}, make_payload(fields))
            after = revision_ids(cb.call('history', {'itemId': item_id}))
            added = after - before
            if len(added) != 1:
                raise ApiError('Expected exactly one new revision per Jira history event')
            return {'revision': next(iter(added)), 'source': metadata,
                    'native_author_restored': False, 'native_timestamp_restored': False}
        state.once(key, apply)
