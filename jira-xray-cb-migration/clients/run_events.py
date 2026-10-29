"""Collect complete Xray event pages through deployment-validated GET routes."""
from urllib.parse import urlencode
from clients.http import ApiError
from clients.jira_client import segment


def at(document, path):
    if not path:
        return document
    for key in path.split('.'):
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


def collect(http, run_id, profile, kind):
    if not profile or not profile.get('path'):
        raise ApiError('Missing source ' + kind + ' endpoint; Jira changelog is not Run history/activity')
    if '{runId}' not in profile['path']:
        raise ApiError('Source event endpoint must identify the Run')
    offset, output, seen = 0, [], set()
    while True:
        query = dict(profile.get('query', {}))
        if profile.get('pagination'):
            query[profile.get('offset_param', 'startAt')] = offset
            query[profile.get('limit_param', 'maxResults')] = profile.get('page_size', 100)
        path = profile['path'].replace('{runId}', segment(run_id))
        if query:
            path += '?' + urlencode(query)
        response = http.request('GET', path)
        rows = at(response, profile.get('items_path', ''))
        if not isinstance(rows, list):
            raise ApiError('Source event response is not an array')
        for row in rows:
            fields = profile['fields']
            source_id = str(at(row, fields['id']))
            if source_id in seen:
                raise ApiError('Duplicate event across pages; pagination may be ignored')
            seen.add(source_id)
            changes = []
            raw_changes = at(row, fields['changes']) if fields.get('changes') else []
            if not isinstance(raw_changes, list):
                raise ApiError('Event changes must be an array')
            for change in raw_changes:
                changes.append({key: at(change, path) for key, path in profile['change_fields'].items()})
            output.append({'id': kind + ':' + source_id, 'at': at(row, fields['at']),
                           'author': at(row, fields['author']), 'changes': changes, 'kind': kind, 'raw': row})
        if profile.get('total_path'):
            total = at(response, profile['total_path'])
            offset += len(rows)
            if offset == total:
                break
            if not rows or offset > total or not profile.get('pagination'):
                raise ApiError('Source event collection is incomplete')
        elif profile.get('complete_single_response') is True and not profile.get('pagination'):
            break
        else:
            raise ApiError('Event completeness requires total metadata or a verified single-response API')
    return output
