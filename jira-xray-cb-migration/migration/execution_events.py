"""One Execution event stream, shared by History and known Activity projections.

These events are Execution context, NOT a reconstructed Run-status timeline.
"""
from copy import deepcopy
from datetime import datetime
from clients.http import ApiError


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ApiError('Execution history timestamp must contain a timezone')
    return parsed


def execution_events(issue):
    key = issue['key']
    changelog = issue.get('changelog', {})
    histories = changelog.get('histories')
    if not isinstance(histories, list) or changelog.get('total') != len(histories) or changelog.get('startAt', 0) != 0:
        raise ApiError('Execution changelog is truncated; cannot replay a partial event stream')
    fields = issue['fields']
    if not fields.get('created') or fields.get('creator') is None:
        raise ApiError('Execution Created metadata is missing')
    created = {'id': key + ':CREATED', 'kind': 'execution_created', 'scope': 'execution-context',
               'at': fields['created'], 'author': deepcopy(fields['creator']), 'activity_kinds': ['execution_created']}
    timestamp(created['at'])
    seen, state, events = set(), [], []
    ordered = sorted(histories, key=lambda h: (timestamp(h['created']), str(h['id'])))
    for history in ordered:
        event_id = key + ':' + str(history['id'])
        if event_id in seen:
            raise ApiError('Duplicate Execution changelog ID')
        seen.add(event_id)
        if timestamp(history['created']) < timestamp(created['at']):
            raise ApiError('Execution changelog predates Created')
        activity = []
        for item in history['items']:
            field = item.get('field', '')
            if field == 'Test':
                kind = 'test_added' if item.get('from') is None and item.get('to') is not None else 'test_changed'
                if kind not in activity:
                    activity.append(kind)
            if field == 'Tests association with a Test Execution' and 'tests_association' not in activity:
                activity.append('tests_association')
        previous = deepcopy(state)
        # Preserve every change in one History, including multiple Test additions.
        state.append({'id': event_id, 'at': history['created'], 'author': deepcopy(history.get('author')),
                      'items': deepcopy(history['items'])})
        events.append({'id': event_id, 'kind': 'execution_history', 'scope': 'execution-context',
                       'at': history['created'], 'author': deepcopy(history.get('author')),
                       'changes': [{'field': '_executionHistoryState', 'before': previous, 'after': deepcopy(state)}],
                       'activity_kinds': activity, 'raw': deepcopy(history)})
    return {'created': created, 'events': events, 'history_state': state,
            'history_count': len(events) + 1,
            'known_activity_count': 1 + sum(bool(event['activity_kinds']) for event in events),
            'activity_coverage': 'known-execution-projection-only', 'run_history_complete': False}
