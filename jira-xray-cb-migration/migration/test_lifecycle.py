"""Preflight for Case/Set/Run/Release using configured, verified adapters only."""
from clients.http import ApiError
from migration.records import read_path
from migration.relation_mapping import normalize, verify_reference


def validate(wizard):
    settings=wizard.config['migration']
    profile=settings.get('full_xray',{})
    if settings.get('mode')!='full-xray':
        raise ApiError('전체 테스트 이관은 migration.mode=full-xray 설정이 필요합니다. 1건 Item POST로 대체하지 않습니다.')
    if wizard.config.get('jira',{}).get('single_issue_key'):
        raise ApiError('전체 이관에는 single_issue_key 제한을 해제하고 모든 관계 대상을 선택해야 합니다.')
    types=profile.get('issue_types',{})
    if set(types)!={'test','set','plan','execution'} or profile.get('create_execution_items',False):
        raise ApiError('test/set/plan/execution 유형 매핑 및 source-only Test Execution 정책이 필요합니다.')
    scopes=wizard.plan.get('scopes',{})
    grouped={kind:[] for kind in types}
    for entry in scopes.values():
        t=entry.get('type',{})
        for kind,value in types.items():
            if str(value) in (str(t.get('id')),t.get('name')):grouped[kind].append(entry)
    if any(not entries for entries in grouped.values()):
        raise ApiError('Test Case/Set/Plan/Execution 원본 범위를 모두 선택하세요.')
    if any(not e.get('source_only') for e in grouped['execution']):
        raise ApiError('Test Execution은 별도 Tracker가 아닌 source-only 실행 맥락으로 선택하세요.')
    contract=profile.get('release_type_contract',{})
    if not contract.get('path') or contract.get('value') is None:
        raise ApiError('full_xray.release_type_contract에 실제 Tracker Configuration의 Release 타입 path/value가 필요합니다.')
    for entry in grouped['plan']:
        if not entry.get('tracker'):raise ApiError('Test Plan 대상 기존 Release Tracker를 선택하세요.')
        config=wizard.cb.configuration(entry['tracker'])
        if read_path(config,contract['path'])!=contract['value']:
            raise ApiError('Test Plan 대상 Tracker의 Release 타입이 확인되지 않았습니다.')
    relations=profile.get('relationships',[])
    for pair in [('set','test'),('plan','test')]:
        if not any((r.get('from'),r.get('to'))==pair for r in relations):
            raise ApiError('Xray 원본 구성 관계 어댑터 필요: '+str(pair))
    backlog=profile.get('backlog_relation_name')
    if not backlog or not any(r.get('from')=='plan' and r.get('to')=='test' and r.get('name')==backlog for r in relations):
        raise ApiError('Release backlog_relation_name을 검증된 Plan→Test 관계 어댑터에 연결하세요.')
    bindings=profile.get('typed_link_fields',{})
    mappings=settings.get('wizard',{}).get('association_types',{})
    for kind in ('clone','tests'):
        link_id=bindings.get(kind)
        if link_id is None:raise ApiError('typed_link_fields.'+kind+'에 실제 Jira Link Type ID가 필요합니다.')
        mapping=mappings.get(str(link_id))
        if not mapping or mapping.get('mode')!='reference':
            raise ApiError(kind+' 관계를 기존 CB Reference 필드로 매핑하세요.')
        verify_reference(wizard.cb,normalize(mapping))
    for operation in ('relation_lookup','relation_create','relation_get','relation_update',
                      'attachment_upload','comment_create'):
        wizard.cb.operation(operation)
    return {'mode':'full-xray','order':['test','set','run','plan','relationships'],
            'execution':'source-only','history':'legacy','writes':0}
