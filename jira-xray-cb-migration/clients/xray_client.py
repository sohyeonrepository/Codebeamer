from clients.http import ApiError
from clients.jira_client import segment


class XrayClient:
    def __init__(self, http):
        self.http = http

    @staticmethod
    def identify_field(fields, override=None):
        if override:
            if override not in {f['id'] for f in fields}:
                raise ApiError('Configured Xray relationship field does not exist')
            return override
        matches = [f['id'] for f in fields if 'raven' in f.get('schema', {}).get('custom', '').lower()
                   and 'testexec' in f.get('schema', {}).get('custom', '').lower()]
        if len(matches) != 1:
            raise ApiError('Xray run relationship field is ambiguous; set jira.xray_run_field from actual field discovery')
        return matches[0]

    def runs(self, issue, field):
        value = issue['fields'].get(field)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ApiError('Unsupported Xray relationship value; raw issue retained')
        runs = []
        for reference in value:
            run_id = reference.get('testRunId') if isinstance(reference, dict) else None
            if run_id is None:
                raise ApiError('Relationship entry has no testRunId; requires actual-response adapter')
            run = self.http.request('GET', '/rest/raven/1.0/api/testrun/' + segment(run_id))
            runs.append({'id': str(run_id), 'reference': reference, 'data': run})
        return runs
