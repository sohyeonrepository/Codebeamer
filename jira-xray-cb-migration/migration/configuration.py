"""Resolve manually assigned destination trackers without guessing Jira type IDs."""
from clients.http import ApiError
from clients.jira_client import segment


def tracker_for(settings, project, issue_type):
    mappings = settings.get('tracker_mapping', {})
    by_id = project + ':' + str(issue_type['id'])
    by_name = project + ':' + issue_type.get('name', '')
    if by_id in mappings:
        return by_id, mappings[by_id]
    if issue_type.get('name') and by_name in mappings:
        return by_name, mappings[by_name]
    raise ApiError('Missing tracker_mapping: ' + by_id + ' (' + issue_type.get('name', '') + ')')


def mapping_catalog(jira, settings):
    catalog = []
    for project in jira.projects():
        detail = jira.http.request('GET', '/rest/api/2/project/' + segment(project['key']))
        types = detail.get('issueTypes')
        if not isinstance(types, list):
            raise ApiError('Project response lacks issueTypes; cannot infer tracker mappings')
        names = [t['name'] for t in types]
        for issue_type in types:
            entry = {'project': project['key'], 'issue_type_id': str(issue_type['id']), 'issue_type_name': issue_type['name'],
                     'config_key': project['key'] + ':' + str(issue_type['id']), 'tracker_id': None}
            try:
                key, destination = tracker_for(settings, project['key'], issue_type)
                if key == project['key'] + ':' + issue_type['name'] and names.count(issue_type['name']) > 1:
                    raise ApiError('Ambiguous issue type name; use issue type IDs')
                entry.update(config_key=key, tracker_id=destination)
            except ApiError as exc:
                entry['error'] = str(exc)
            catalog.append(entry)
    return catalog
