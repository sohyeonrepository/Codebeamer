import urllib.parse
from clients.http import ApiError


def segment(value):
    return urllib.parse.quote(str(value), safe='')


class JiraClient:
    def __init__(self, http, config, archive=None):
        self.http, self.config, self.archive = http, config, archive

    def fields(self):
        return self.http.request('GET', '/rest/api/2/field')

    def issue_types(self, project):
        detail = self.http.request('GET', '/rest/api/2/project/' + segment(project))
        if not isinstance(detail.get('issueTypes'), list):
            raise ApiError('Project response lacks complete issueTypes')
        return detail['issueTypes']

    def global_catalog(self):
        """Read Jira DC global definitions, not project-scoped availability.

        Permissions may restrict some endpoints. Security Levels are scoped by
        security schemes, so do not infer those from a global status list.
        """
        paths = {
            'issue_types': '/rest/api/2/issuetype',
            'priorities': '/rest/api/2/priority',
            'statuses': '/rest/api/2/status',
            'resolutions': '/rest/api/2/resolution',
        }
        result = {}
        for key, path in paths.items():
            values = self.http.request('GET', path)
            if not isinstance(values, list) or any(
                not isinstance(row, dict) or row.get('id') is None or not row.get('name')
                for row in values
            ):
                raise ApiError('Incomplete Jira global definition catalog: ' + key)
            ids = [str(row['id']) for row in values]
            if len(set(ids)) != len(ids):
                raise ApiError('Duplicate Jira global definition IDs: ' + key)
            result[key] = values
        return result

    def project_issue_type_catalog(self, project):
        """Return the complete project issue-type configuration, not issue samples."""
        types = self.issue_types(project)
        if any(not isinstance(row, dict) or row.get('id') is None or not row.get('name')
               for row in types):
            raise ApiError('Invalid project Issue Type definition')
        ids = [str(row['id']) for row in types]
        if len(set(ids)) != len(ids):
            raise ApiError('Duplicate project Issue Type IDs')
        return types

    def creation_fields(self, project, issue_type):
        query = urllib.parse.urlencode({'projectKeys':project, 'issuetypeIds':str(issue_type),
                                       'expand':'projects.issuetypes.fields'})
        data = self.http.request('GET', '/rest/api/2/issue/createmeta?' + query)
        projects = [p for p in data.get('projects', []) if p.get('key') == project]
        types = [t for p in projects for t in p.get('issuetypes', []) if str(t.get('id')) == str(issue_type)]
        if len(types) != 1 or not isinstance(types[0].get('fields'), dict):
            raise ApiError('CreateMeta missing selected project/Issue Type fields')
        return types[0]['fields']

    def configured_records(self, profile, context):
        """Read a deployment-specific paginated API, never guess plugin endpoints."""
        from clients.codebeamer_client import render, resolve
        import re
        path = re.sub(r'\{([^}]+)\}', lambda m: segment(context[m[1]]), profile['path'])
        if not path.startswith('/') or path.startswith('//'):
            raise ApiError('Source adapter requires a same-origin relative path')
        query = {k: render(v, context) for k, v in profile.get('query', {}).items()}
        if profile.get('pagination') == 'jira':
            rows = list(self.pages(path, profile['records_key'], query))
            if profile.get('record_fields'):
                rows = [dict(row, **{target: resolve(row, source) for target, source in profile['record_fields'].items()}) for row in rows]
            return rows
        if profile.get('pagination') != 'complete':
            raise ApiError('Source adapter requires jira pagination or an explicit complete response contract')
        data = self.http.request('GET', path + ('?' + urllib.parse.urlencode(query) if query else ''))
        rows = resolve(data, profile.get('response_path', ''))
        if not isinstance(rows, list):
            raise ApiError('Source adapter must return a full array')
        if profile.get('total_path') and len(rows) != resolve(data, profile['total_path']):
            raise ApiError('Source adapter returned incomplete records')
        if profile.get('record_fields'):
            rows = [dict(row, **{target: resolve(row, source) for target, source in profile['record_fields'].items()}) for row in rows]
        return rows

    def projects(self):
        keys = self.config.get('projects', [])
        if keys:
            return [self.http.request('GET', '/rest/api/2/project/' + segment(k)) for k in keys]
        result = self.http.request('GET', '/rest/api/2/project')
        if not isinstance(result, list):
            raise ApiError('Expected Jira DC project list; configure explicit projects for another API shape')
        return result

    def pages(self, path, key, params=None):
        start = 0
        expected_total = None
        seen = set()
        while True:
            q = dict(params or {}, startAt=start, maxResults=self.config.get('page_size', 100))
            page = self.http.request('GET', path + '?' + urllib.parse.urlencode(q))
            rows = page.get(key)
            if not isinstance(rows, list) or 'total' not in page:
                raise ApiError(f'Missing pagination metadata for {key}')
            if page.get('startAt', start) != start:
                raise ApiError('Server ignored pagination offset')
            total=page['total']
            if type(total) is not int or total < 0 or start+len(rows)>total:
                raise ApiError('Invalid pagination total')
            if expected_total is not None and total!=expected_total:
                raise ApiError('Pagination total changed during export')
            expected_total=total
            for row in rows:
                identity=row.get('id',row.get('key')) if isinstance(row,dict) else None
                if identity is not None:
                    if str(identity) in seen:raise ApiError('Duplicate record across pages')
                    seen.add(str(identity))
            yield from rows
            start += len(rows)
            if start >= page['total']:
                break
            if not rows:
                raise ApiError('Truncated pagination')

    def issues(self, project):
        selected = self.config.get('single_issue_key')
        if selected:
            issue = self.http.request('GET', '/rest/api/2/issue/' + segment(selected) + '?fields=*all')
            if issue.get('key') != selected or issue.get('fields', {}).get('project', {}).get('key') != project:
                raise ApiError('Selected single Issue does not belong to the chosen Jira project')
            types = self.config.get('execution_issue_types')
            kind = issue['fields']['issuetype']
            if not types or str(kind['id']) in [str(t) for t in types] or kind.get('name') in types:
                yield issue
            return
        # Encode the literal as a JQL string; never interpolate raw project input.
        import json
        jql = 'project = ' + json.dumps(project)
        types = self.config.get('execution_issue_types')
        if types:
            jql += ' AND issuetype in (' + ', '.join(json.dumps(str(t)) for t in types) + ')'
        yield from self.pages('/rest/api/2/search', 'issues', {'jql': jql + ' ORDER BY key', 'fields': '*all'})

    def run_source(self, key):
        # Run-only migration does not need issue changelog/comments/worklogs.
        return self.http.request('GET', '/rest/api/2/issue/' + segment(key) + '?fields=*all')

    def execution_context(self, issue):
        key = issue['key']
        root = '/rest/api/2/issue/' + segment(key)
        if 'changelog' not in issue:
            issue = self.http.request('GET', root + '?fields=*all&expand=changelog')
        if self.archive:
            self.archive('source-execution-context', key, issue)
        from migration.execution_events import execution_events
        execution_events(issue)  # Reject incomplete history before fetching dependent data.
        if self.config.get('include_comments', True):
            issue['_comments'] = list(self.pages(root + '/comment', 'comments'))
        if self.config.get('include_worklogs', True):
            issue['_worklogs'] = list(self.pages(root + '/worklog', 'worklogs'))
        if self.archive:
            self.archive('source-execution-context', key, issue)
        return issue

    def bundle(self, key):
        root = '/rest/api/2/issue/' + segment(key)
        issue = self.http.request('GET', root + '?fields=*all&expand=changelog')
        if self.archive:
            self.archive('source-partial', key, issue)
        history = issue.get('changelog', {})
        rows = history.get('histories', [])
        if history.get('total') is None or history.get('startAt', 0) != 0 or len(rows) != history['total']:
            # Jira DC versions differ; do not guess a changelog pagination endpoint.
            profile = self.config.get('changelog_adapter')
            if not profile:
                raise ApiError('Changelog incomplete: supply a validated full-history adapter before migration')
            rows = self.configured_records(profile, {'issueKey': key})
            if history.get('total') is not None and len(rows) != history['total']:
                raise ApiError('Changelog count differs from source total')
        if len({str(row['id']) for row in rows}) != len(rows):
            raise ApiError('Duplicate changelog IDs')
        issue['_comments'] = list(self.pages(root + '/comment', 'comments'))
        if self.archive:
            self.archive('source-partial', key, issue)
        issue['_worklogs'] = list(self.pages(root + '/worklog', 'worklogs'))
        issue['_history'] = sorted(rows, key=lambda x: (x['created'], str(x['id'])))
        return issue
