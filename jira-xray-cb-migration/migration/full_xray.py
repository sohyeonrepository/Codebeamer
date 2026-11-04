"""Plan-first Xray migration using explicitly verified membership field adapters."""
import json
from clients.http import ApiError
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient
from migration.records import mapped_record, read_path

ORDER = ('plan', 'test', 'set', 'execution')


def migrate_full(engine):
    report = engine.report
    report.update(scope='full-xray', creation_order=list(ORDER), relationships=[])
    bundles = []
    target_bundles = []
    try:
        profile = engine.settings.get('full_xray', {})
        types = profile.get('issue_types', {})
        execution_target = profile.get('create_execution_items', False)
        report['execution_policy'] = 'target-item' if execution_target else 'source-metadata'
        report['source_executions'] = []
        report['runs'] = []
        if set(types) != set(ORDER) or len(set(types.values())) != 4:
            raise ApiError('full_xray.issue_types must map plan/test/set/execution to four distinct Jira type names or IDs')
        relations = profile.get('relationships')
        if not isinstance(relations, list) or not relations:
            raise ApiError('Configure full_xray.relationships from actual Xray membership fields; Jira issuelinks alone are insufficient')
        for relation in relations:
            if relation.get('from') not in ORDER or relation.get('to') not in ORDER or not relation.get('path') or not relation.get('identity_path'):
                raise ApiError('Each membership adapter requires from, to, path and identity_path')
        config = dict(engine.jira.config, execution_issue_types=list(types.values()))
        jira = JiraClient(engine.jira.http, config, engine.state.save)
        definitions = jira.fields()
        engine.cb.discover()
        identities = {}
        for project in jira.projects():
            for summary in jira.issues(project['key']):
                issue = jira.bundle(summary['key'])
                if engine.wizard:
                    from migration.preservation import collect_r4j
                    collect_r4j(jira, issue, engine.settings, engine.state)
                kind = next((k for k, v in types.items() if str(v) in
                             (str(issue['fields']['issuetype']['id']), issue['fields']['issuetype']['name'])), None)
                if kind is None:
                    raise ApiError('Unexpected source issue type')
                if getattr(engine.xray, 'config', {}).get('source_reads'):
                    issue = engine.xray.collect_issue_data(issue, kind)
                issue['_runs'] = []
                if kind == 'execution':
                    issue = jira.execution_context(issue)
                    issue['_runs'] = engine.xray.runs(issue, config.get('xray_run_field'))
                engine.state.save('source-full', issue['key'], issue)
                bundles.append((kind, project, issue))
                for identity in (str(issue['id']), issue['key']):
                    if identity in identities:
                        raise ApiError('Duplicate source identity')
                    identities[identity] = (kind, issue)
        if not bundles:
            raise ApiError('No Xray objects selected')
        target_bundles = [b for b in bundles if execution_target or b[0] != 'execution']
        edges = []
        context_edges = []
        for kind, _, issue in bundles:
            for adapter in relations:
                if adapter['from'] != kind:
                    continue
                values = read_path(issue, adapter['path'])
                if not isinstance(values, list):
                    raise ApiError('Membership field must be an array; configure its actual path')
                for value in values:
                    identity = str(read_path(value, adapter['identity_path'])) if adapter['identity_path'] != '$' else str(value)
                    target = identities.get(identity)
                    if not target or target[0] != adapter['to']:
                        raise ApiError('Membership target absent or wrong type; include all dependencies in source selection')
                    if not execution_target and 'execution' in (kind, target[0]):
                        context_edges.append({'source': issue['key'], 'target': target[1]['key'], 'adapter': adapter})
                    else:
                        edges.append((issue, target[1], adapter))
        seen_runs = set()
        for kind, _, issue in bundles:
            if kind != 'execution' or execution_target:
                continue
            context = {'id': issue['id'], 'key': issue['key'], 'fields': issue['fields'],
                       'relationships': [e for e in context_edges if issue['key'] in (e['source'], e['target'])]}
            issue['_executionSourceContext'] = context
            engine.state.save('source-execution-context', issue['key'], issue)
            report['source_executions'].append({'id': issue['id'], 'key': issue['key'], 'status': 'source-preserved'})
            if not issue['_runs']:
                report['limitations'].append({'source':issue['key'], 'reason':'Individual Test Runs not collected; aggregate results do not verify migration'})
            for run in issue['_runs']:
                data = run['data']
                if data.get('status') is None:
                    raise ApiError('Individual Run status unavailable; aggregate results cannot substitute')
                if str(run['id']) in seen_runs or data.get('testExecKey') != issue['key'] or str(data.get('id')) != str(run['id']):
                    raise ApiError('Duplicate or inconsistent Xray Run identity')
                seen_runs.add(str(run['id']))
                identity = {'test': data.get('testKey'), 'execution': issue['key']}
                prior = engine.state.get('run-source-identity:' + str(run['id']))
                if prior and prior[1] != identity:
                    raise ApiError('Run ID is already bound to another Test/Execution')
                engine.state.mark('run-source-identity:' + str(run['id']), 'done', identity)
                test = identities.get(data.get('testKey'))
                if not test or test[0] != 'test':
                    raise ApiError('Xray Run Test Case is outside selected source scope')
                adapter = engine.settings.get('run_adapter', {})
                if adapter.get('execution_mode') != 'source_metadata':
                    raise ApiError('Test Execution is source-only: configure run_adapter.execution_mode=source_metadata')
                if not any(k == '_executionSource' or k.startswith('_executionSource.') for k in adapter.get('fields', {})):
                    raise ApiError('Map _executionSource metadata before importing source-only Executions')
                from migration.records import run_tracker_id
                run_tracker_id(engine.settings)
                if not adapter.get('test_issue_id_path'):
                    raise ApiError('Configure Run Test Case identity path')
                if str(read_path(data, adapter['test_issue_id_path'])) not in (str(test[1]['id']), test[1]['key']):
                    raise ApiError('Run Test Case identity path conflicts with testKey')
                report['runs'].append({'source_run_id': str(run['id']), 'test_key': data['testKey'],
                                       'execution_key': issue['key'], 'status': 'planned'})
        report['execution_relationships_preserved'] = context_edges
        if context_edges:
            report['limitations'].append({'reason': 'Execution relationships preserved; native Plan-to-Run relation write contract not configured'})
        # Validate every object before the first target write.
        original_write = engine.write
        try:
            engine.write = False
            for kind in ORDER:
                for current, project, issue in target_bundles:
                    if current == kind:
                        engine.issue(project, issue, definitions)
        finally:
            engine.write = original_write
        report['issues'] = []
        if original_write:
            for name in ('item_create', 'lookup', 'item_get'):
                engine.cb.operation(name)
            if report['runs']:
                for name in ('run_lookup', 'run_create', 'run_get'):
                    engine.cb.operation(name)
            if edges:
                for name in ('relation_lookup', 'relation_create', 'relation_get', 'relation_update'):
                    engine.cb.operation(name)
                if not engine.settings.get('relation_adapter', {}).get('fields'):
                    raise ApiError('Configure relation_adapter.fields before creating objects')
        for kind in ORDER:
            for current, project, issue in target_bundles:
                if current == kind:
                    engine.issue(project, issue, definitions)
                    if original_write:
                        saved = engine.state.get('item:' + str(issue['id']))
                        engine.state.mark('item:' + issue['key'], saved[0], saved[1])
        # Memberships are connected only after all four object kinds have IDs.
        for issue, other, adapter in edges:
            record = {'source': issue['key'], 'target': other['key'], 'kind': adapter.get('name', adapter['from'] + ':' + adapter['to']), 'status': 'planned'}
            report['relationships'].append(record)
            if original_write:
                source = engine.state.get('item:' + str(issue['id']))[1]['id']
                destination = engine.state.get('item:' + str(other['id']))[1]['id']
                identity = json.dumps([issue['id'], other['id'], record['kind']])
                mapped_record(engine.cb, engine.state, engine.settings, 'relation', identity,
                              {'targetId': destination, 'relationKind': record['kind']}, {'itemId': source},
                              engine.settings['relation_adapter']['fields'])
                record['status'] = 'verified'
        if original_write:
            for _, _, issue in target_bundles:
                engine.children(issue, engine.state.get('item:' + str(issue['id']))[1]['id'])
            if not execution_target:
                from migration.records import test_run
                for kind, _, issue in bundles:
                    if kind == 'execution':
                        for run in issue['_runs']:
                            result = test_run(engine.cb, engine.state, engine.settings, engine.jira.http, issue, None, run)
                            row = next(r for r in report['runs'] if r['source_run_id'] == str(run['id']))
                            row.update(status='verified', target_id=result['id'])
            engine.relations([(project, issue) for _, project, issue in target_bundles])
    except (ApiError, KeyError, ValueError, TypeError) as exc:
        engine.error('full-xray', exc)
    report['counts'] = {'source_issues': len(bundles), 'target_issues': len(target_bundles), 'verified_issues': sum(r['status'] == 'verified' for r in report['issues']),
                        'relationships': len(report['relationships'])}
    report['complete'] = bool(engine.write and bundles and not report['errors'] and not report['limitations']
                              and report['counts']['target_issues'] == report['counts']['verified_issues'])
    engine.state.save('reports', 'latest', report)
    (engine.state.root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
