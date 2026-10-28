import urllib.parse
from clients.http import ApiError


def segment(value):
    return urllib.parse.quote(str(value), safe='')


class JiraClient:
    def __init__(self, http, config, archive=None):
        self.http, self.config, self.archive = http, config, archive

    def fields(self):
        return self.http.request('GET', '/rest/api/2/field')

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
        while True:
            q = dict(params or {}, startAt=start, maxResults=self.config.get('page_size', 100))
            page = self.http.request('GET', path + '?' + urllib.parse.urlencode(q))
            rows = page.get(key)
            if not isinstance(rows, list) or 'total' not in page:
                raise ApiError(f'Missing pagination metadata for {key}')
            if page.get('startAt', start) != start:
                raise ApiError('Server ignored pagination offset')
            yield from rows
            start += len(rows)
            if start >= page['total']:
                break
            if not rows:
                raise ApiError('Truncated pagination')

    def issues(self, project):
        # Encode the literal as a JQL string; never interpolate raw project input.
        import json
        return self.pages('/rest/api/2/search', 'issues', {'jql': 'project = ' + json.dumps(project) + ' ORDER BY key', 'fields': '*all'})

    def bundle(self, key):
        root = '/rest/api/2/issue/' + segment(key)
        issue = self.http.request('GET', root + '?fields=*all&expand=changelog')
        if self.archive:
            self.archive('source-partial', key, issue)
        history = issue.get('changelog', {})
        rows = history.get('histories', [])
        if history.get('total') is None or history.get('startAt', 0) != 0 or len(rows) != history['total']:
            # Jira DC versions differ; do not guess a changelog pagination endpoint.
            raise ApiError('Changelog incomplete: supply a validated full-history adapter before migration')
        issue['_comments'] = list(self.pages(root + '/comment', 'comments'))
        if self.archive:
            self.archive('source-partial', key, issue)
        issue['_worklogs'] = list(self.pages(root + '/worklog', 'worklogs'))
        issue['_history'] = sorted(rows, key=lambda x: (x['created'], str(x['id'])))
        return issue
