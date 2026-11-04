from clients.http import ApiError
from clients.jira_client import segment


class XrayClient:
    def __init__(self, http, config=None, archive=None):
        self.http, self.config, self.archive = http, config or {}, archive

    def collect_issue_data(self, issue, kind):
        """Optional verified DC reads; merge evidence without replacing Jira fields.

        source_reads maps test/set/plan/execution to operation -> envelope profile.
        No enabled operation is silently skipped on HTTP/schema failure.
        """
        from copy import deepcopy
        from clients.xray_dc_contract import collect_xray_dc
        profiles = self.config.get('source_reads', {}).get(kind, {})
        if not profiles:
            return issue
        if self.config.get('api_family', 'server-dc') != 'server-dc':
            raise ApiError('DC source reads cannot use Xray Cloud authentication/endpoints')
        result = deepcopy(issue)
        result.setdefault('_xray_dc', {})
        for operation, profile in profiles.items():
            if operation.startswith('test_run'):
                raise ApiError('Run reads require a Run ID, not a Jira issue key')
            result['_xray_dc'][operation] = collect_xray_dc(
                self.http, operation, issue['key'], profile, self.archive)
        if self.archive:
            self.archive('source-xray-issue', issue['key'], result)
        return result

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
        discovery = self.config.get('run_discovery')
        if (value is None or value == []) and discovery:
            from clients.xray_dc_contract import collect_xray_dc
            configured_type = discovery.get('execution_issue_type')
            actual_type = issue['fields'].get('issuetype', {})
            if configured_type is None or str(configured_type) not in (str(actual_type.get('id')), actual_type.get('name')):
                raise ApiError('Run discovery requires a verified Execution Issue Type')
            collected = collect_xray_dc(self.http, 'test_execution_tests', issue['key'], discovery, self.archive)
            run_key, test_key = discovery.get('run_id_key'), discovery.get('test_key_key')
            if not run_key or not test_key:
                raise ApiError('Configure actual Run ID and Test key members of Execution response')
            value = []
            for record in collected['items']:
                if record.get(run_key) is None or not record.get(test_key):
                    raise ApiError('Execution member lacks Run ID/Test key; cannot infer Runs from counts')
                value.append({'testRunId':record[run_key], 'testKey':record[test_key]})
        if value is None:
            return []
        if not isinstance(value, list):
            raise ApiError('Unsupported Xray relationship value; raw issue retained')
        runs = []
        seen = set()
        for reference in value:
            run_id = reference.get('testRunId') if isinstance(reference, dict) else None
            if run_id is None:
                raise ApiError('Relationship entry has no testRunId; requires actual-response adapter')
            if str(run_id) in seen:
                raise ApiError('Duplicate Xray Run ID in Execution relationship: ' + str(run_id))
            seen.add(str(run_id))
            from clients.xray_dc_contract import dc_read_path
            run = self.http.request('GET', dc_read_path('test_run', run_id))
            if self.archive:
                self.archive('source-run-details', run_id, run)
            if not isinstance(run, dict):
                raise ApiError('Xray Run detail must be an object')
            record = {'id': str(run_id), 'reference': reference, 'data': run}
            if str(run.get('id')) != str(run_id) or run.get('testKey') != reference.get('testKey') or run.get('testExecKey') != issue['key']:
                raise ApiError('Run detail does not match Execution/Test relationship')
            for operation, profile in self.config.get('source_reads', {}).get('run', {}).items():
                from clients.xray_dc_contract import collect_xray_dc
                if operation not in ('test_run_steps', 'test_run_evidence'):
                    raise ApiError('Unverified Xray Run collection operation: ' + operation)
                if self.config.get('api_family', 'server-dc') != 'server-dc':
                    raise ApiError('DC source reads cannot use Xray Cloud endpoints')
                record.setdefault('dc_collections', {})[operation] = collect_xray_dc(
                    self.http, operation, run_id, profile, self.archive)
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
