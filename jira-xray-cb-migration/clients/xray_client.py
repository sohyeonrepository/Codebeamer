from clients.http import ApiError
from clients.jira_client import segment


class XrayClient:
    def __init__(self, http, config=None, archive=None):
        self.http, self.config, self.archive = http, config or {}, archive

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

    @staticmethod
    def relationship_field(issue, preferred=None):
        if preferred and issue['fields'].get(preferred) is not None:
            return preferred
        matches = []
        for field_id, value in issue['fields'].items():
            if isinstance(value, list) and value and all(isinstance(entry, dict) and 'testRunId' in entry and 'testKey' in entry for entry in value):
                matches.append(field_id)
        if len(matches) > 1:
            raise ApiError('Multiple Run relationship fields have the validated testKey/testRunId shape')
        return matches[0] if matches else None

    def runs(self, issue, field):
        field = self.relationship_field(issue, field)
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
            if self.archive:
                self.archive('source-run-details', run_id, run)
            record = {'id': str(run_id), 'reference': reference, 'data': run}
            if str(run.get('id')) != str(run_id) or run.get('testKey') != reference.get('testKey') or run.get('testExecKey') != issue['key']:
                raise ApiError('Run detail does not match Execution/Test relationship')
            if self.config.get('history_source') == 'execution-changelog':
                from migration.execution_events import execution_events
                context = execution_events(issue)
                record.update(events=context['events'], created=context['created'], events_complete=True,
                              events_scope='execution-context', execution_history_state=context['history_state'],
                              history_summary={key: context[key] for key in ('history_count', 'known_activity_count', 'activity_coverage', 'run_history_complete')})
            elif self.config.get('collect_run_events'):
                from clients.run_events import collect
                events = []
                for kind in ('history', 'activity'):
                    collected = collect(self.http, run_id, self.config.get('run_events', {}).get(kind), kind)
                    if self.archive:
                        self.archive('source-run-' + kind, run_id, collected)
                    events.extend(collected)
                record['events'] = events
                record['events_complete'] = True
                record['created'] = {'id': 'created', 'kind': 'created',
                                     'at': self._value(run, self.config.get('run_created_at_path')),
                                     'author': self._value(run, self.config.get('run_creator_path'))}
            runs.append(record)
        return runs

    @staticmethod
    def _value(document, path):
        if not path:
            raise ApiError('Set run_created_at_path and run_creator_path for Created metadata')
        from clients.run_events import at
        return at(document, path)
