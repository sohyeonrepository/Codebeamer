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
        from migration.source_runs import collect_sources
        fields, runs, errors = collect_sources(engine.jira, engine.xray, engine.state)
        report['errors'].extend(errors)
        adapter = engine.settings.get('run_adapter', {})
        from migration.run_schema import sync_run_fields
        if engine.settings.get('history_replay'):
            if any(not run.get('events_complete') for _, run in runs.values()):
                raise ApiError('Run History/Activity incomplete; refusing all target writes')
        if report['errors'] and engine.write:
            raise ApiError('Source extraction failed; refusing target writes until complete')
        report['field_plan'] = sync_run_fields(engine.cb, engine.state, tracker, engine.settings, engine.write)
        if engine.settings.get('history_replay'):
            report['limitations'].append({'reason': 'Original native revision author/timestamp restoration is unverified'})
            report['native_revision_metadata'] = {'author_restored': False, 'timestamp_restored': False,
                                                'source_metadata_preserved_in': '_sourceEvent'}
        for issue, run in runs.values():
            record = {'source_run_id': run['id'], 'execution_key': issue['key'], 'status': 'planned'}
            if run.get('history_summary'):
                record['history'] = run['history_summary']
                report['limitations'].append({'source_run_id': run['id'], 'reason': 'Execution context History and known Activity only; historical Run status/Step changes and other Activity are not available'})
            report['runs'].append(record)
            try:
                if not engine.write:
                    if adapter.get('reference_mode') == 'field-values':
                        from migration.run_references import resolve_references
                        record['references'] = resolve_references(engine.cb, run['data'], issue, fields, adapter)
                        continue
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
                record.update(status='verified', target_id=result['id'], evidence_count=result['evidence'],
                              execution_attachment_count=result.get('execution_attachments', 0))
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
