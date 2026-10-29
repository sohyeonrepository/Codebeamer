"""Migrate Xray run records only; all destination Tests already exist."""
import json
from clients.http import ApiError
from clients.xray_client import XrayClient
from migration.records import test_run, read_path, run_tracker_id


def migrate_runs(engine):
    report = engine.report
    report['scope'] = 'test-runs-only'
    report['runs'] = []
    try:
        tracker = run_tracker_id(engine.settings)
        bound = engine.state.get('run-target-tracker')
        if bound and bound[1] != tracker:
            raise ApiError('State is bound to another Test Run tracker; reconcile or use a separate state_dir')
        engine.state.mark('run-target-tracker', 'done', tracker)
        engine.cb.discover()
        fields = engine.jira.fields()
        engine.state.save('source', 'field-definitions', fields)
        field = XrayClient.identify_field(fields, engine.jira.config.get('xray_run_field'))
        runs = {}
        for project in engine.jira.projects():
            for summary in engine.jira.issues(project['key']):
                key = summary['key']
                try:
                    issue = engine.jira.run_source(key)
                    engine.state.save('source', key, issue)
                    for run in engine.xray.runs(issue, field):
                        engine.state.save('source-runs', run['id'], run)
                        if run['id'] in runs and runs[run['id']][0]['id'] != issue['id']:
                            raise ApiError('Same Run appears under multiple Executions; resolve source association')
                        runs[run['id']] = (issue, run)
                except (ApiError, KeyError, ValueError, TypeError) as exc:
                    engine.error(key, exc)
        adapter = engine.settings.get('run_adapter', {})
        for issue, run in runs.values():
            record = {'source_run_id': run['id'], 'execution_key': issue['key'], 'status': 'planned'}
            report['runs'].append(record)
            try:
                if not engine.write:
                    source_test = str(read_path(run['data'], adapter['test_issue_id_path']))
                    destination = engine.settings.get('test_mapping', {}).get(source_test)
                    if type(destination) is not int or destination <= 0:
                        raise ApiError('Set test_mapping for source Test ' + source_test)
                    actual = engine.cb.call('item_get', {'itemId': destination})
                    if actual.get('id') != destination:
                        raise ApiError('Existing Test item verification failed')
                    record['target_test_id'] = destination
                    continue
                result = test_run(engine.cb, engine.state, engine.settings, engine.jira.http, issue, None, run)
                record.update(status='verified', target_id=result['id'], evidence_count=result['evidence'])
            except (ApiError, KeyError, ValueError, TypeError) as exc:
                engine.error('run:' + run['id'], exc)
        if not runs:
            report['limitations'].append({'reason': 'No Test Runs discovered; source relationship field/selection must be verified'})
    except (ApiError, KeyError, ValueError, TypeError) as exc:
        engine.error('run-preflight', exc)
    report['counts'] = {'source_runs': len(report['runs']),
                        'verified_runs': sum(r['status'] == 'verified' for r in report['runs'])}
    report['complete'] = bool(engine.write and report['runs'] and not report['errors'] and not report['limitations']
                              and report['counts']['source_runs'] == report['counts']['verified_runs'])
    engine.state.save('reports', 'latest', report)
    (engine.state.root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
