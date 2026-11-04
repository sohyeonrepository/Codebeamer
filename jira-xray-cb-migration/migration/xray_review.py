"""Read-only, type-specific Xray review. Issue keys are never Run identifiers."""
from clients.http import ApiError

READS = {
    'test': ('test_steps', 'test_sets_for_test', 'test_plans_for_test'),
    'set': ('test_set_tests',),
    'plan': ('test_plan_tests', 'test_plan_executions'),
    'execution': ('test_execution_tests',),
    'run': ('test_run_steps', 'test_run_evidence'),
}
TITLES = {'test':'Test Case','set':'Test Set','plan':'Test Plan',
          'execution':'Test Execution (원본 맥락 전용)','run':'Test Run'}

def issue_kind(issue_type, settings):
    configured = settings.get('full_xray', {}).get('issue_types', {})
    for kind, value in configured.items():
        if str(value) in (str(issue_type.get('id')), issue_type.get('name')):
            return kind
    return {'Test':'test','Test Case':'test','Test Set':'set','Test Plan':'plan',
            'Test Execution':'execution'}.get(issue_type.get('name'))

def field_group(source, definition):
    from migration.system_fields import field_policy
    from migration.field_semantics import jira_field_semantics
    policy = field_policy(source, definition)
    if policy == 'relationship': return 'Xray 전용 관계'
    if policy == 'dedicated': return 'Comment / Attachment / History'
    kind, _, _ = jira_field_semantics(definition)
    if kind == 'user': return 'User'
    if kind in ('option', 'status'): return 'Choice / Multi Choice'
    if kind in ('date', 'datetime'): return 'Date / Datetime'
    return '기본 시스템 필드' if policy == 'standard' else '일반 사용자 정의 필드'

def collect_review(jira, xray, identifier, kind, settings=None):
    """Archive raw reads. Unconfigured endpoint shape is evidence, not completeness."""
    from clients.xray_dc_contract import inspect_xray_dc, collect_xray_dc
    if kind not in READS: raise ApiError('Select an actual Xray source type')
    if xray.config.get('api_family', 'server-dc') != 'server-dc':
        raise ApiError('Xray Cloud configuration cannot be used for DC source review')
    report = {'kind':kind,'source_id':str(identifier),'reads':{},'errors':[], 'complete':False}
    if kind == 'run':
        record = inspect_xray_dc(xray.http, 'test_run', identifier, xray.archive)
        data = record['raw']
        if not isinstance(data,dict) or str(data.get('id')) != str(identifier) or not data.get('testKey') or not data.get('testExecKey') or 'status' not in data:
            raise ApiError('Run detail requires actual ID, Test/Execution keys and status; an Issue/aggregate is not a Run')
        report['run'] = data
    else:
        report['issue'] = jira.bundle(identifier)
        actual_kind = issue_kind(report['issue'].get('fields', {}).get('issuetype', {}), settings or {})
        if actual_kind != kind:
            raise ApiError('Selected Xray category does not match source Issue Type; configure actual Issue Type IDs')
        if report['issue'].get('key') != identifier:
            raise ApiError('Source Issue identity mismatch')
    for operation in READS[kind]:
        profile = xray.config.get('source_reads', {}).get(kind, {}).get(operation)
        try:
            if profile:
                report['reads'][operation] = collect_xray_dc(xray.http,operation,identifier,profile,xray.archive)
            else:
                report['reads'][operation] = inspect_xray_dc(xray.http,operation,identifier,xray.archive)
        except ApiError as exc:
            report['errors'].append({'operation':operation,'error':str(exc)})
    if kind == 'execution':
        try:
            report['runs'] = xray.runs(report['issue'], jira.config.get('xray_run_field'))
            if not report['runs']:
                report['errors'].append({'operation':'runs','error':'Individual Run IDs/details unavailable; aggregate PASS/FAIL is insufficient'})
        except ApiError as exc:
            report['errors'].append({'operation':'runs','error':str(exc)})
    report['complete'] = not report['errors'] and all(r.get('complete') for r in report['reads'].values())
    report['target_migration_verified'] = False
    if xray.archive: xray.archive('source-xray-review',kind+'-'+str(identifier),report)
    return report
