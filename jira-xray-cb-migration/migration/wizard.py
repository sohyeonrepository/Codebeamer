"""Read-only wizard planning. Incomplete coverage is a blocker, never an omission."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from clients.http import ApiError
from clients.jira_client import JiraClient, segment
from mapping_helper import (collect_catalog, existing_overlay, auto_connect_scope,
    scope_mappings, suggest_scope, normalized_label, field_type_label, selected_tracker,
    field_identifier, validate_overlay, creation_templates, source_choices,
    source_observations, matching_option)
from migration.fields import convert, compare_definitions

STAGES = [
    ('trackers', 'Tracker 생성 및 확인'), ('fields', 'Field 생성 및 설정'),
    ('issues', 'Jira Issue 데이터 이관'), ('description', 'Description 및 이미지'),
    ('children', 'Attachment · Comment · Worklog'), ('relations', 'Issue Link · Parent-Child'),
    ('r4j', 'R4J Tree · Revision · Baseline'), ('xray', 'Xray 전체 객체 · Test Run'),
    ('history', 'History 원본 보존 및 대상 조회'), ('verification', '전체 검증 및 보고')]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def public_profile(config):
    """Persist connection selectors, never secrets or URL credentials/query strings."""
    from urllib.parse import urlsplit
    result = {}
    for service in ('jira', 'codebeamer'):
        settings = config.get(service, {})
        url = settings.get('url', '')
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ApiError('연결 URL에 인증 정보나 쿼리를 넣을 수 없습니다.')
        result[service] = {key: deepcopy(settings[key]) for key in
            ('url', 'auth', 'token_env', 'username_env', 'password_env', 'allow_http') if key in settings}
    return result


def declared_get(cb, path, params=None):
    if cb.spec is None:
        cb.discover()
    definition = cb.spec['paths'].get(path, {}).get('get')
    if not isinstance(definition, dict):
        raise ApiError('서버 Swagger에서 목록 조회 API를 확인할 수 없습니다: ' + path)
    for key, value in (params or {}).items():
        path = path.replace('{' + key + '}', segment(value))
    if '{' in path:
        raise ApiError('목록 조회 경로 미해결')
    result = cb.http.request('GET', path)
    # Do not silently accept a partial page or infer a server-specific response path.
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        candidates = [result[k] for k in ('projects', 'trackers', 'content', 'items') if isinstance(result.get(k), list)]
        if len(candidates) == 1 and isinstance(result.get('total'), int) and len(candidates[0]) == result['total']:
            return candidates[0]
    raise ApiError('전체 목록 또는 페이지 처리 계약을 확인할 수 없습니다. 부분 목록 사용을 차단했습니다.')


def project_lists(jira, cb):
    reader = JiraClient(jira.http, dict(jira.config, projects=[]))
    return reader.projects(), declared_get(cb, '/v3/projects')


def tracker_list(cb, project_id):
    rows = declared_get(cb, '/v3/projects/{projectId}/trackers', {'projectId': project_id})
    if any(type(row.get('id')) is not int for row in rows):
        raise ApiError('트래커 목록에 고유 ID가 없습니다.')
    return rows


def full_catalog(jira, cb, trackers, progress=lambda **kw: None):
    """Read every issue/field/value, including types absent from the helper's sample."""
    reader = JiraClient(jira.http, dict(jira.config, execution_issue_types=[]))
    definitions = reader.fields()
    projects, bundles = [], []
    for project in reader.projects():
        types = {}
        detail = reader.http.request('GET', '/rest/api/2/project/' + segment(project['key']))
        if not isinstance(detail.get('issueTypes'), list):
            raise ApiError('프로젝트 Issue Type 전체 목록을 확인할 수 없습니다.')
        for kind in detail['issueTypes']:
            types[str(kind['id'])] = {'id': str(kind['id']), 'name': kind['name'], 'fields': {}}
        for summary in reader.issues(project['key']):
            issue = reader.bundle(summary['key'])
            bundles.append((project, issue))
            kind = issue['fields']['issuetype']
            entry = types.setdefault(str(kind['id']), {'id': str(kind['id']), 'name': kind['name'], 'fields': {}})
            for key, value in issue['fields'].items():
                observed = entry['fields'].setdefault(key, [])
                if value not in observed:
                    observed.append(value)
            progress(stage='analysis', processed=len(bundles), success=0, failed=0)
        projects.append({'key': project['key'], 'types': list(types.values()), 'sample_count': len(bundles)})
    # Reuse the existing target metadata merger without sampling the source again.
    class EmptySource:
        http = None
        config = {}
        def fields(self): return definitions
        def projects(self): return []
    if trackers:
        catalog = collect_catalog(EmptySource(), cb, [row['id'] for row in trackers])
    else:
        catalog = {'targets': [], 'warnings': [], 'definitions': definitions}
    names = {row['id']: row.get('name', '') for row in trackers}
    for target in catalog['targets']:
        target['name'] = target.get('name') or names[target['id']]
    catalog.update(projects=projects, values_complete=True, sample_limit=None)
    return catalog, bundles


def compatible(spec, field):
    allowed = {'text': ('Text', 'Wikitext', 'Wiki Link'), 'number': ('Integer', 'Decimal', 'Duration'),
               'boolean': ('Bool',), 'date': ('Date',), 'datetime': ('Date',),
               'option': ('Choice',), 'status': ('Choice',), 'user': ('User', 'Members'),
               'reference': ('References', 'Choice')}
    label = field_type_label(field)
    return any(label.startswith(prefix) for prefix in allowed.get(spec.get('type'), ()))


def field_creation_proposal(source, definition, target):
    schema = definition.get('schema', {})
    wanted = {'string': ('Text', 'Wikitext'), 'number': ('Decimal', 'Integer'),
              'integer': ('Integer',), 'boolean': ('Bool',), 'date': ('Date',),
              'datetime': ('Date',), 'option': ('Choice',), 'status': ('Choice',),
              'priority': ('Choice',), 'user': ('User',)}.get(schema.get('type'), ())
    if schema.get('type') == 'array':
        wanted = {'option': ('Choice',), 'version': ('Choice',), 'user': ('Members',)}.get(schema.get('items'), ())
    templates = creation_templates(target.get('configuration', {}), target['fields'])
    for prefix in wanted:
        matches = [item for item in templates if item['type_verified'] and item['name'].startswith(prefix)]
        if len(matches) == 1:
            proposal = deepcopy(matches[0]['definition'])
            proposal['label'] = definition.get('name', source)
            if schema.get('type') == 'array': proposal['multipleSelection'] = True
            return {'type_name': matches[0]['name'], 'definition': proposal,
                    'execution': 'blocked', 'reason': '생성 후 API 키 바인딩 및 서버 스키마 검증 필요'}
    return {'type_name': None, 'execution': 'blocked', 'reason': '서버에서 유일한 호환 타입을 확인하지 못함'}


def plan_mapping(config, catalog, target_project, saved=None):
    overlay = existing_overlay(config, saved)
    rows, blockers, creations = [], [], []
    targets = catalog['targets']
    definitions = {row['id']: row for row in catalog['definitions']}
    if type(target_project) is not int or target_project <= 0:
        raise ApiError('조회한 기존 Codebeamer 프로젝트를 선택하세요.')
    for project in catalog['projects']:
        for kind in project['types']:
            scope = project['key'] + ':' + kind['id']
            tracker_id = selected_tracker(overlay, project, kind)
            matches = [target for target in targets if target['id'] == tracker_id] if tracker_id else [
                target for target in targets if normalized_label(target.get('name', '')) == normalized_label(kind['name'])]
            if len(matches) != 1:
                item = {'scope': scope, 'source': kind['name'], 'kind': 'tracker',
                        'state': 'create' if not matches else 'review',
                        'reason': 'Tracker 생성 API/템플릿 계약 필요' if not matches else '동일 이름 Tracker 충돌',
                        'proposal': {'name': kind['name'], 'project_id': target_project, 'execution': 'blocked'}}
                rows.append(item)
                if not matches: creations.append(item)
                blockers.append(scope + ': ' + item['reason'])
                for source in kind['fields']:
                    if source not in ('attachment', 'comment', 'worklog', 'issuelinks'):
                        rows.append({'scope': scope, 'source': source, 'label': definitions.get(source, {}).get('name', source),
                                     'kind': 'field', 'state': 'review', 'target': '', 'reason': '대상 Tracker 해결 후 매핑 가능'})
                continue
            target = matches[0]
            overlay['migration'].setdefault('tracker_mapping', {})[scope] = target['id']
            scoped_definitions = dict(definitions, **kind.get('definitions', {}))
            before = deepcopy(scope_mappings(overlay, project, kind))
            auto_connect_scope(overlay, project, kind, target['fields'], scoped_definitions, config)
            mappings = scope_mappings(overlay, project, kind)
            proposals = suggest_scope(kind, scoped_definitions, target['fields'], config, mappings)
            for source, values in kind['fields'].items():
                if source in ('attachment', 'comment', 'worklog', 'issuelinks'):
                    continue
                spec = mappings.get(source)
                field = next((f for f in target['fields'] if str(field_identifier(f)) == str((spec or {}).get('field_id'))), None)
                reason, state = '', 'auto'
                if spec and field:
                    try:
                        if not compatible(spec, field):
                            raise ApiError('서버 필드 타입 호환성 확인 필요')
                        if compare_definitions(catalog['definitions'], target['fields'], {source: spec}):
                            raise ApiError('기존 필드 정의 변경 또는 충돌')
                        for value in values:
                            convert(value, spec['type'], spec.get('value_mapping', {}))
                    except (ApiError, TypeError, ValueError, KeyError) as exc:
                        reason, state = str(exc) if isinstance(exc, ApiError) else '값 변환 확인 필요', 'review'
                        if source not in before:
                            overlay['migration']['field_mapping'][scope].pop(source, None)
                else:
                    proposal = proposals.get(source, {})
                    state = 'create' if not proposal.get('field') and not spec else 'review'
                    reason = '신규 필드 생성안: 서버 타입/쓰기 계약 필요' if state == 'create' else proposal.get('reason', '기존 대상 정의 확인 필요')
                row = {'scope': scope, 'source': source, 'label': definitions.get(source, {}).get('name', source),
                       'kind': 'field', 'state': state, 'target': (spec or {}).get('target', ''), 'reason': reason}
                rows.append(row)
                if state == 'create':
                    row['proposal'] = field_creation_proposal(source, scoped_definitions.get(source, {}), target)
                    creations.append(row)
                if state != 'auto': blockers.append(scope + ': ' + source + ': ' + reason)
                choice_field = field or proposals.get(source, {}).get('field')
                source_type = scoped_definitions.get(source, {}).get('schema', {})
                if choice_field and source_type.get('type') in ('option', 'status', 'priority', 'array') and isinstance(choice_field.get('options'), list):
                    for identity, label in source_choices(source_observations(kind, source)).items():
                        try: found = matching_option(choice_field, label)
                        except ApiError: found = None
                        if not found:
                            option = {'scope': scope, 'source': source, 'label': row['label'] + ' / ' + label,
                                      'kind': 'choice', 'state': 'create', 'target': row['target'],
                                      'reason': '없는 선택지 생성안 · 실제 생성 전 서버 계약 검증 필요',
                                      'proposal': {'label': label, 'source_identity': identity, 'execution': 'blocked'}}
                            rows.append(option); creations.append(option)
                            blockers.append(scope + ': ' + source + ': 선택지 생성 필요: ' + label)
    blockers.extend(validate_overlay(overlay, catalog))
    return {'overlay': overlay, 'rows': rows, 'blockers': list(dict.fromkeys(blockers)), 'creations': creations,
            'summary': {'total': sum(r['kind'] == 'field' for r in rows),
                        'auto': sum(r['state'] == 'auto' and r['kind'] == 'field' for r in rows),
                        'create': sum(r['state'] == 'create' and r['kind'] == 'field' for r in rows),
                        'review': sum(r['state'] == 'review' and r['kind'] == 'field' for r in rows)},
            'additional_creations': {'trackers': sum(r['kind'] == 'tracker' for r in creations),
                                     'choices': sum(r['kind'] == 'choice' for r in creations)}}


def capabilities(config, cb):
    """Declare implementation gaps regardless of flags or claimed API availability."""
    settings = config['migration']
    blockers = [
        'R4J Tree / Revision / Baseline: 추출·대상 복원 어댑터 미구현',
        'History: 원본 로컬 보존은 지원, Codebeamer 별도 조회 기능 미구현',
        'Description: Jira 이미지/내부 링크의 Codebeamer URL 재작성 미구현',
        'Parent-Child: 부모 의존성 순서 및 대상 관계 검증 미구현',
        '전체 Jira 이슈와 Xray 전체 객체를 함께 처리하는 통합 엔진 미구현',
    ]
    for category in ('comment', 'worklog', 'relation'):
        if not settings.get(category + '_adapter', {}).get('fields'):
            blockers.append(category + ': 서버별 필드 어댑터 없음')
    required = ['project_get', 'tracker_get', 'item_create', 'item_update', 'item_get', 'lookup',
                'attachment_upload', 'attachment_get', 'history']
    for category in ('comment', 'worklog', 'relation'):
        required.extend(category + '_' + suffix for suffix in ('lookup', 'create', 'get', 'update'))
    for name in required:
        try: cb.operation(name)
        except (ApiError, KeyError, TypeError): blockers.append('API 계약 미확인: ' + name)
    return [{'id': key, 'name': name, 'status': 'blocked' if key in ('description', 'relations', 'r4j', 'xray', 'history') else 'adapter-required'} for key, name in STAGES], blockers


def analyze(config, jira, cb, target_project, trackers, saved=None, progress=lambda **kw: None):
    if cb.write:
        raise ApiError('사전 분석에는 읽기 전용 클라이언트가 필요합니다.')
    catalog, bundles = full_catalog(jira, cb, trackers, progress)
    plan = plan_mapping(config, catalog, target_project, saved)
    stages, gaps = capabilities(config, cb)
    plan['blockers'].extend(catalog.get('warnings', []))
    plan['blockers'].extend(gaps)
    if not bundles: plan['blockers'].append('조회된 원본 이슈가 없습니다.')
    plan.update(stages=stages, source_count=len(bundles), writes=0, source_complete=True,
                ready=not plan['blockers'], config_fingerprint=fingerprint(config),
                selection={'projects': config['jira'].get('projects', []), 'target_project': target_project,
                           'trackers': [t['id'] for t in trackers]})
    # Only structural results leave memory. Raw source data uses Engine's local archive on import.
    return plan, catalog


def assert_importable(plan, config):
    if plan.get('config_fingerprint') != fingerprint(config):
        raise ApiError('설정이 변경되었습니다. 사전 분석을 다시 실행하세요.')
    if not plan.get('source_complete') or not plan.get('ready') or plan.get('blockers'):
        raise ApiError('미해결 매핑 또는 미지원 기능이 있어 Import를 차단했습니다.')
    # A serialized plan cannot assert that unimplemented capabilities exist.
    raise ApiError('전체 범위 Import는 R4J/History/이미지/통합 엔진 구현 후 활성화됩니다.')
