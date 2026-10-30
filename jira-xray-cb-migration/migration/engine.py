import json
from clients.http import ApiError
from clients.xray_client import XrayClient
from migration.fields import payload, compare_definitions
from migration.configuration import tracker_for
from migration.history_replay import timeline, replay
from migration.attachments import transfer
from validation.compare_fields import compare_fields


class Engine:
    def __init__(self, jira, xray, cb, state, settings, write=False, progress=None):
        self.jira, self.xray, self.cb, self.state = jira, xray, cb, state
        self.settings, self.write = settings, write
        self.progress = progress or (lambda **event: None)
        self.report = {'mode': 'apply' if write else 'dry-run', 'complete': False, 'issues': [], 'errors': [], 'limitations': []}

    def notify(self, stage):
        self.progress(stage=stage, processed=len(self.report['issues']),
                      success=sum(row.get('status') == 'verified' for row in self.report['issues']),
                      failed=len(self.report['errors']))

    def ensure(self, kind, key, spec):
        if isinstance(spec, int):
            existing = self.cb.call(kind + '_get', {kind + 'Id': spec})
            if existing.get('id') != spec:
                raise ApiError(kind + ' mapping verification failed')
            return spec
        if not isinstance(spec, dict) or 'create_payload' not in spec:
            raise ApiError(f'Missing {kind} mapping for {key}')
        found = self.cb.call(kind + '_lookup', {'sourceKey': key})
        if not isinstance(found, list) or len(found) > 1:
            raise ApiError('Ambiguous ' + kind + ' lookup')
        if found:
            return found[0]['id']
        return self.state.once(f'{kind}:{key}', lambda: self.cb.call(kind + '_create', data=spec['create_payload']))['id']

    def migrate(self):
        self.notify('preflight')
        if self.settings.get('mode') == 'full-xray':
            from migration.full_xray import migrate_full
            return migrate_full(self)
        if self.settings.get('mode') == 'test-runs':
            from migration.run_only import migrate_runs
            return migrate_runs(self)
        try:
            source_fields = self.jira.fields()
            self.state.save('source', 'field-definitions', source_fields)
            self.cb.discover()
            try:
                run_field = XrayClient.identify_field(source_fields, self.jira.config.get('xray_run_field'))
            except ApiError as exc:
                run_field = None
                self.report['limitations'].append({'source': 'Xray', 'reason': str(exc)})
            projects = self.jira.projects()
            self.state.save('source', 'projects', projects)
            bundles = []
            for project in projects:
                for summary in self.jira.issues(project['key']):
                    key = summary['key']
                    try:
                        bundle = self.jira.bundle(key)
                        self.state.save('source', key, bundle)
                        bundle['_runs'] = self.xray.runs(bundle, run_field) if run_field else []
                        self.state.save('source', key, bundle)
                        bundles.append((project, bundle))
                    except (ApiError, KeyError, ValueError) as exc:
                        self.error(key, exc)
            # All source items are archived before any target write.
            for project, issue in bundles:
                try:
                    self.issue(project, issue, source_fields)
                except (ApiError, KeyError, ValueError, TypeError) as exc:
                    self.error(issue['key'], exc)
            if self.write:
                for _, issue in bundles:
                    item = self.state.get('item:' + str(issue['id']))
                    if item and item[0] == 'done':
                        try:
                            self.children(issue, item[1]['id'])
                        except (ApiError, KeyError, ValueError, TypeError) as exc:
                            self.error(issue['key'] + ':children', exc)
            self.relations(bundles)
        except (ApiError, KeyError, ValueError, TypeError) as exc:
            self.error('preflight', exc)
        self.report['counts'] = {'source_issues': len(bundles) if 'bundles' in locals() else 0,
                                 'verified_issues': sum(r['status'] == 'verified' for r in self.report['issues'])}
        self.report['complete'] = (self.write and not self.report['errors'] and not self.report['limitations']
                                   and self.report['counts']['source_issues'] == self.report['counts']['verified_issues'])
        self.state.save('reports', 'latest', self.report)
        (self.state.root / 'report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
        return self.report

    def error(self, key, error):
        # ApiError messages are controlled; arbitrary payload exceptions may contain source values.
        self.report['errors'].append({'source': key, 'error': str(error) if isinstance(error, ApiError) else type(error).__name__})
        self.notify('error')

    def issue(self, project, issue, definitions):
        self.notify('issues')
        key = issue['key']
        type_id = str(issue['fields']['issuetype']['id'])
        tracker_key, tracker_spec = tracker_for(self.settings, project['key'], issue['fields']['issuetype'])
        if not (type(tracker_spec) is int and tracker_spec > 0) and not (isinstance(tracker_spec, dict) and 'create_payload' in tracker_spec):
            raise ApiError('Set a positive target Tracker ID for ' + tracker_key)
        mappings = self.settings.get('field_mapping', {})
        mapping = mappings.get(project['key'] + ':' + type_id, mappings.get(type_id, mappings.get(issue['fields']['issuetype'].get('name'), {})))
        record = {'key': key, 'planned_fields': len(mapping), 'comments': len(issue['_comments']),
                  'worklogs': len(issue['_worklogs']), 'runs': len(issue['_runs']), 'status': 'planned'}
        self.report['issues'].append(record)
        # Coverage is explicit: no silent loss of nulls, custom fields, or system fields.
        missing = set(issue['fields']) - set(mapping) - {'attachment', 'comment', 'worklog', 'issuelinks'}
        if missing:
            raise ApiError('Unmapped source fields: ' + ', '.join(sorted(missing)))
        if not mapping:
            raise ApiError('No field mappings')
        make_payload = lambda fields: payload(fields, mapping, self.settings)
        latest = make_payload(issue['fields'])
        if not self.write:
            if isinstance(tracker_spec, int):
                target = self.cb.fields(tracker_spec)
                self.state.save('target', f'fields:{tracker_spec}', target)
            return
        project_id = self.ensure('project', project['key'], self.settings.get('project_mapping', {}).get(project['key']))
        tracker_id = self.ensure('tracker', tracker_key, tracker_spec)
        definitions_target = self.cb.fields(tracker_id)
        if not isinstance(definitions_target, list):
            raise ApiError('Field response must be a full array; configure an adapter for this server')
        # Missing fields may be created only with an explicit validated API/payload.
        existing = {str(f.get('id', f.get('referenceId'))) for f in definitions_target}
        for fid, spec in mapping.items():
            if str(spec.get('field_id')) not in existing and spec.get('create_payload'):
                self.state.once(f'field:{tracker_id}:{fid}', lambda s=spec: self.cb.call('field_create', {'trackerId': tracker_id}, s['create_payload']))
        definitions_target = self.cb.fields(tracker_id)
        conflicts = compare_definitions(definitions, definitions_target, mapping)
        if conflicts:
            self.state.save('conflicts', key, conflicts)
            raise ApiError('Field configuration mismatch; see conflicts archive')
        events = timeline(issue, mapping) if self.settings.get('history_replay') else []
        if not events and issue['_history']:
            self.report['limitations'].append({'source': key, 'reason': 'History replay disabled'})
        # External source-key lookup is mandatory before initial create or a changed snapshot.
        source_key = str(issue['id'])
        known = self.state.get('item:' + source_key)
        lookup = self.cb.call('lookup', {'sourceId': source_key, 'sourceKey': key, 'trackerId': tracker_id, 'projectId': project_id})
        if not isinstance(lookup, list) or len(lookup) > 1:
            raise ApiError('Lookup must return zero or one exact source-ID match')
        if known:
            if known[0] != 'done':
                raise ApiError('Uncertain item create: reconcile state before resuming')
            item_id = known[1]['id']
            if not lookup or lookup[0]['id'] != item_id:
                raise ApiError('Source-ID lookup disagrees with saved mapping')
        elif lookup:
            # Do not replay old events into arbitrary existing items.
            if events:
                raise ApiError('Existing target without replay mapping requires reconciliation')
            item_id = lookup[0]['id']
            self.state.mark('item:' + source_key, 'done', {'id': item_id})
        else:
            body = make_payload(events[0][1]) if events else latest
            created = self.state.once('item:' + source_key, lambda: self.cb.call('item_create', {'trackerId': tracker_id}, body))
            item_id = created['id']
            if events:
                from validation.compare_history import revision_ids
                revisions = revision_ids(self.cb.call('history', {'itemId': item_id}))
                if len(revisions) != 1:
                    raise ApiError('Created event requires exactly one initial revision')
                self.state.mark(f'history:{key}:created', 'done', {'revision': next(iter(revisions)), 'source': events[0][2], 'native_author_restored': False, 'native_timestamp_restored': False})
        if events:
            replay(self.cb, self.state, key, item_id, events, make_payload)
            self.report['limitations'].append({'source': key, 'reason': 'Original revision author/time preserved in state only; native restoration unverified'})
        actual = self.cb.call('item_get', {'itemId': item_id})
        if compare_fields(latest, actual):
            if events:
                raise ApiError('Final replay fields differ from Jira current values')
            import hashlib
            fingerprint = hashlib.sha256(json.dumps(latest, sort_keys=True).encode()).hexdigest()
            self.state.once(f'update:{source_key}:{fingerprint}', lambda: self.cb.call('item_update', {'itemId': item_id}, latest))
            actual = self.cb.call('item_get', {'itemId': item_id})
        differences = compare_fields(latest, actual)
        self.state.save('target', key, actual)
        if differences:
            raise ApiError('Target field verification failed')
        record.update(status='verified', target_id=item_id)
        self.notify('issues')

    def children(self, issue, item_id):
        self.notify('children')
        key = issue['key']
        from migration.records import mapped_record, test_run
        for category, records in [('comment', issue['_comments']), ('worklog', issue['_worklogs'])]:
            adapter = self.settings.get(category + '_adapter')
            if not adapter and records:
                self.report['limitations'].append({'source': key, 'reason': category + ' raw data preserved; target adapter missing'})
                continue
            for record in records:
                mapped_record(self.cb, self.state, self.settings, category, key + ':' + str(record['id']),
                              record, {'itemId': item_id}, adapter['fields'])
        for attachment in issue['fields'].get('attachment', []):
            transfer(self.jira.http, self.cb, self.state, key, item_id, attachment)
        for run in issue['_runs']:
            test_run(self.cb, self.state, self.settings, self.jira.http, issue, item_id, run)

    def relations(self, bundles):
        self.notify('relations')
        if not self.write:
            return
        for _, issue in bundles:
            links = issue['fields'].get('issuelinks', [])
            source = self.state.get('item:' + str(issue['id']))
            for link in links:
                other = link.get('outwardIssue') or link.get('inwardIssue')
                if not source or source[0] != 'done' or not other:
                    self.report['limitations'].append({'source': issue['key'], 'reason': 'Unresolved relationship'})
                    continue
                target = self.state.get('item:' + str(other['id']))
                if not target or target[0] != 'done':
                    self.report['limitations'].append({'source': issue['key'], 'reason': 'Relationship target pending', 'target': other['key']})
                    continue
                try:
                    from migration.records import mapped_record
                    adapter = self.settings.get('relation_adapter', {})
                    mapped_record(self.cb, self.state, self.settings, 'relation', str(link['id']),
                        {'targetId': target[1]['id'], 'sourceLink': link},
                        {'itemId': source[1]['id']}, adapter.get('fields', {}))
                except (ApiError, KeyError, ValueError) as exc:
                    self.error(issue['key'], exc)
