"""Source-only collection shared by extraction and target migration."""
from clients.http import ApiError
from clients.xray_client import XrayClient


def collect_sources(jira, xray, state):
    fields = jira.fields()
    state.save('source', 'field-definitions', fields)
    try:
        field = XrayClient.identify_field(fields, jira.config.get('xray_run_field'))
    except ApiError:
        if jira.config.get('xray_run_field'):
            raise
        field = None  # Auto-discover the validated relationship shape per Execution.
    runs, errors = {}, []
    for project in jira.projects():
        for summary in jira.issues(project['key']):
            key = summary['key']
            try:
                issue = jira.run_source(key)
                state.save('source', key, issue)
                if jira.config.get('history_source') == 'execution-changelog' and XrayClient.relationship_field(issue, field):
                    issue = jira.execution_context(issue)
                for run in xray.runs(issue, field):
                    run['field_definitions'] = fields
                    state.save('source-runs', run['id'], run)
                    if run['id'] in runs and runs[run['id']][0]['id'] != issue['id']:
                        raise ApiError('Same Run appears under multiple Executions; resolve source association')
                    runs[run['id']] = (issue, run)
            except (ApiError, KeyError, ValueError, TypeError) as exc:
                errors.append({'source': key, 'error': str(exc) if isinstance(exc, ApiError) else type(exc).__name__})
    return fields, runs, errors
