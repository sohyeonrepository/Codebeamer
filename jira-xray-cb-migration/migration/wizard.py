"""Read-only scoped planning and explicitly approved import orchestration."""
from copy import deepcopy
import hashlib
import json
from clients.http import ApiError
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient
from migration.engine import Engine
from mapping_helper import (field_identifier, label_tokens, build_spec, suggest_values,
                            source_choices)

SPECIAL = {'attachment', 'comment', 'issuelinks', 'worklog'}
CONTEXT = {'issuetype', 'project'}


def managed_fields(definitions, configured=None):
    result = set()
    if configured:
        result.add(configured)
    for field in definitions:
        custom = field.get('schema', {}).get('custom', '').casefold()
        if 'raven' in custom and 'testexec' in custom:
            result.add(field['id'])
    return result


def hidden(field):
    return field.get('hidden') is True or field.get('isHidden') is True or field.get('visible') is False


def has_value(value):
    return value is not None and value != '' and value != [] and value != {}


def semantic_type(field, jira=False):
    if jira:
        schema = field.get('schema', {})
        kind = schema.get('type')
        multiple = kind == 'array'
        kind = schema.get('items') if multiple else kind
        return {'string': 'text', 'integer': 'number', 'number': 'number', 'boolean': 'boolean',
                'date': 'date', 'datetime': 'datetime', 'option': 'option', 'priority': 'option',
                'status': 'status', 'user': 'user', 'version': 'option'}.get(kind), multiple
    # Numeric type IDs are deployment-specific; require named server metadata.
    kind = field.get('typeName', field.get('type'))
    if isinstance(kind, dict):
        kind = kind.get('name')
    return {'text': 'text', 'wikitext': 'text', 'integer': 'number', 'decimal': 'number',
            'bool': 'boolean', 'boolean': 'boolean', 'date': 'date', 'datetime': 'datetime',
            'choice': 'option', 'option': 'option', 'status': 'status', 'user': 'user',
            'members': 'user'}.get(str(kind).casefold()), field.get('multipleSelection')


def field_analysis(jira, project, issue_type):
    profile = jira.config.get('field_configuration')
    if not profile:
        raise ApiError('Configure jira.field_configuration: editmeta does not prove Field Configuration visibility')
    configured = jira.configured_records(profile, {'projectKey': project, 'issueTypeId': str(issue_type['id'])})
    definitions = {f['id']: f for f in jira.fields()}
    managed = managed_fields(definitions.values(), jira.config.get('xray_run_field'))
    fields = {}
    for f in configured:
        key = str(f['id'])
        if key in fields:
            raise ApiError('Duplicate Field Configuration entry')
        if not any(type(f.get(flag)) is bool for flag in ('hidden', 'isHidden', 'visible')):
            raise ApiError('Field Configuration must explicitly report field visibility: ' + key)
        fields[key] = dict(definitions.get(key, {}), **f)
    # Complete type-specific scan, including nulls; never use a capped sample as coverage.
    reader = JiraClient(jira.http, dict(jira.config, execution_issue_types=[str(issue_type['id'])]))
    values = {}
    count = 0
    for issue in reader.issues(project):
        if str(issue['fields']['issuetype']['id']) != str(issue_type['id']):
            raise ApiError('Jira ignored Issue Type filter')
        count += 1
        for key, value in issue['fields'].items():
            if key in SPECIAL | CONTEXT | managed:
                continue
            # A full Jira issue response often contains every global field as null.
            # Keep configured null fields, and add unconfigured fields only when
            # real values show they need attention. Configuration drives scope.
            if key in fields or has_value(value):
                fields.setdefault(key, definitions.get(key, {'id': key}))
            observations = values.setdefault(key, [])
            if value not in observations:
                observations.append(value)
    risks = []
    visible = {}
    excluded = []
    for key, field in fields.items():
        if key in SPECIAL | CONTEXT | managed:
            continue
        if hidden(field):
            remaining = any(has_value(v) for v in values.get(key, []))
            excluded.append({'field':key,'name':field.get('name',key),'has_value':remaining})
            if remaining:
                risks.append({'field': key, 'reason': 'hidden-source-with-values'})
        else:
            visible[key] = field
    return {'fields': visible, 'values': values, 'risks': risks, 'excluded':excluded,'issue_count': count,
            'configuration_count': len(configured), 'managed_fields': sorted(managed), 'complete': True}


def target_fields(cb, tracker):
    from clients.codebeamer_client import CodebeamerClient
    if isinstance(cb,CodebeamerClient) and cb.config.get('verified_reads'):
        from clients.codebeamer_verified_reads import fields as read_fields,configuration as read_configuration
        fields,configuration=read_fields(cb,tracker),read_configuration(cb,tracker)
    else:
        fields = cb.fields(tracker)
        configuration = cb.configuration(tracker)
    if not isinstance(fields, list) or not isinstance(configuration.get('fields'), list):
        raise ApiError('Complete target fields and configuration are required')
    by_id = {str(field_identifier(f)): f for f in configuration['fields']}
    # Configuration visibility wins over a shortened fields response.
    merged = [dict(f, **by_id.get(str(field_identifier(f)), {})) for f in fields]
    if any(not any(type(f.get(flag)) is bool for flag in ('hidden', 'isHidden', 'visible')) for f in merged):
        raise ApiError('Target configuration must explicitly report field visibility')
    return merged


def proposals(analysis, targets, config, existing=None):
    existing = existing or {}
    visible = [f for f in targets if not hidden(f)]
    bindings = config.get('codebeamer', {}).get('field_bindings', {})
    result = {}
    claimed = {str(s.get('field_id')) for s in existing.values()}
    for source, definition in analysis['fields'].items():
        kind, multiple = semantic_type(definition, True)
        candidates = [f for f in visible if label_tokens(source, definition) & label_tokens('', f)]
        entry = {'source': source, 'status': 'manual', 'mapping_mode': None,
                 'reason': '이름 또는 타입 확인 필요', 'spec': None}
        if source in existing:
            candidate = next((f for f in visible if str(field_identifier(f)) == str(existing[source]['field_id'])), None)
            if candidate and compatible(kind, multiple, candidate):
                entry.update(status='mapped', mapping_mode='manual', spec=deepcopy(existing[source]),
                             target=candidate, reason='저장된 수동 매핑')
            else:
                entry['reason'] = '기존 대상이 숨김/누락/타입 충돌: 재확인 필요'
        elif len(candidates) == 1 and kind and compatible(kind, multiple, candidates[0]):
            target = candidates[0]
            fid = field_identifier(target)
            keys = [k for k, b in bindings.items() if b.get('fieldId') == fid]
            canonical = label_tokens(source, definition) & label_tokens('', target) & {'name', 'description'}
            key = keys[0] if len(keys) == 1 else (next(iter(canonical)) if canonical else '')
            vals = list(analysis['values'].get(source, [])) + list(definition.get('allowedValues', []))
            mapping, unmatched = suggest_values(vals, target.get('options'))
            if kind == 'user':
                mapping = deepcopy(config['migration'].get('user_mapping', {}))
            if kind == 'status':
                mapping.update(config['migration'].get('status_mapping', {}))
            try:
                if kind in ('option', 'status') and unmatched:
                    raise ValueError('대상에 없는 원본 Choice 값: 수동 매핑 또는 선택지 준비 필요')
                spec = build_spec(source, target, kind, key, mapping)
                from migration.fields import convert
                for v in analysis['values'].get(source, []):
                    convert(v, kind, mapping)
                if str(fid) in claimed:
                    raise ValueError('이미 다른 원본에 연결된 대상')
                entry.update(status='mapped', mapping_mode='auto', spec=spec, target=target, reason='이름·타입 일치')
            except (ValueError, ApiError, TypeError) as exc:
                entry.update(target=target, reason=str(exc))
        elif not candidates:
            if kind is None or (multiple and kind not in ('option', 'status', 'user')):
                entry.update(status='error', reason='지원하지 않는 원본 타입: 자동 변환하지 않음')
            else:
                choices = source_choices(list(analysis['values'].get(source, [])) + list(definition.get('allowedValues', [])))
                entry.update(status='create', mapping_mode='create', reason='Import 승인 후 생성', creation={
                    'label': definition.get('name', source), 'semantic_type': kind,
                    'multipleSelection': multiple, 'choices': choices})
        result[source] = entry
    # Do not let loop order choose the winner of an ambiguous many-to-one match.
    owners = {}
    for source, entry in result.items():
        if entry['spec']:
            owners.setdefault(str(entry['spec']['field_id']), []).append(source)
    for sources in owners.values():
        if len(sources) > 1:
            for source in sources:
                result[source].update(status='manual', spec=None, reason='대상 필드 중복')
    return result


def compatible(kind, multiple, target):
    target_kind, target_multiple = semantic_type(target)
    return bool(kind and (kind == target_kind or kind == 'status' and target_kind == 'option')
                and type(target_multiple) is bool and multiple == target_multiple)


class ImportWizard:
    def __init__(self, jira, cb, state, config):
        self.jira, self.cb, self.state = jira, cb, state
        self.config = deepcopy(config)
        identity = {'jira': config['jira']['url'], 'codebeamer': config['codebeamer']['url']}
        prior = state.get('environment-binding')
        if prior and prior[1] != identity:
            raise ApiError('State belongs to different source/target URLs')
        state.mark('environment-binding', 'done', identity)
        resolved = state.load('wizard', 'resolved-mapping')
        if resolved:
            for section in ('project_mapping', 'tracker_mapping', 'field_mapping'):
                self.config['migration'].setdefault(section, {}).update(resolved.get(section, {}))
        self.plan = {'version': 1, 'projects': {}, 'scopes': {}, 'stages': []}

    def stage(self, name, result):
        self.plan['stages'].append({'stage': name, 'result': result})
        self.state.save('wizard', 'plan', self.plan)
        return result

    def limit_to_issue(self, key):
        import re
        from clients.jira_client import segment
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*', key):
            raise ApiError('Select exactly one Jira Issue key')
        if self.config['migration'].get('mode') == 'full-xray':
            raise ApiError('full-xray requires complete Plan/Test/Set/Execution dependencies; it cannot be declared complete for one isolated Issue')
        issue = self.jira.http.request('GET', '/rest/api/2/issue/' + segment(key) + '?fields=*all')
        project = issue['fields']['project']['key']
        scope = project + ':' + str(issue['fields']['issuetype']['id'])
        if issue.get('key') != key or scope not in self.plan['scopes']:
            raise ApiError('Map this Issue Type to a test Tracker in STEP 2 first')
        entry = self.plan['scopes'][scope]
        if not entry.get('tracker'):
            raise ApiError('Select an existing dedicated test Tracker for bounded one-Issue Import')
        self.plan['scopes'] = {scope:entry}
        selected = self.plan['projects'][project]
        selected['types'] = [issue['fields']['issuetype']]
        self.plan['projects'] = {project:selected}
        self.config['jira']['single_issue_key'] = key
        self.jira.config = dict(self.jira.config, single_issue_key=key)
        for name in ('analysis','rows','target_fields'):
            entry.pop(name,None)
        self.stage('single-issue-scope', {'issue_key':key,'project_id':entry['project_id'],'tracker_id':entry['tracker']})
        return scope

    def discover(self):
        self.cb.write = False
        from clients.codebeamer_client import CodebeamerClient
        if isinstance(self.cb,CodebeamerClient):
            from clients.codebeamer_verified_reads import projects as read_projects
            projects=read_projects(self.cb)
        else:
            self.cb.discover();projects=self.cb.call('projects_get')
        if not isinstance(projects, list):
            raise ApiError('projects_get must normalize a complete project array')
        return self.stage('projects', {'jira': self.jira.projects(), 'codebeamer': projects})

    def select_project(self, project, target):
        self.cb.write = False
        from clients.codebeamer_client import CodebeamerClient
        if isinstance(self.cb,CodebeamerClient):
            from clients.codebeamer_verified_reads import project as read_project,trackers as read_trackers
            actual=read_project(self.cb,target);trackers=read_trackers(self.cb,target)
        else:
            actual=self.cb.call('project_get',{'projectId':target});trackers=self.cb.call('trackers_get',{'projectId':target})
        if actual.get('id') != target:
            raise ApiError('Existing target project verification failed')
        types = self.jira.issue_types(project)
        if not isinstance(trackers, list):
            raise ApiError('trackers_get must normalize a complete project tracker array')
        self.plan['projects'][project] = {'target': target, 'types': types, 'trackers': trackers}
        # A changed project invalidates all previous scope choices for that project.
        self.plan['scopes'] = {k: v for k, v in self.plan['scopes'].items() if v['project'] != project}
        return self.stage('trackers', trackers)

    def map_tracker(self, project, type_id, tracker=None, name=None):
        if self.config['jira'].get('single_issue_key'):
            allowed = next(iter(self.plan['scopes'].values()))
            if project != allowed['project'] or str(type_id) != str(allowed['type']['id']) or tracker != allowed['tracker']:
                raise ApiError('Single-Issue test scope is fixed; start a separate test session to change its Tracker')
        selection = self.plan['projects'][project]
        kind = next(t for t in selection['types'] if str(t['id']) == str(type_id))
        if tracker is not None and tracker not in [t['id'] for t in selection['trackers']]:
            raise ApiError('Tracker must belong to the selected existing project')
        if tracker is None and not (name and name.strip()):
            raise ApiError('New Tracker name is required')
        self.plan['scopes'][project + ':' + str(type_id)] = {
            'project': project, 'type': deepcopy(kind), 'project_id': selection['target'],
            'tracker': tracker, 'new_tracker': name.strip() if tracker is None else None}
        self.state.save('wizard', 'plan', self.plan)

    def analyze(self, scope):
        self.cb.write = False
        entry = self.plan['scopes'][scope]
        analysis = field_analysis(self.jira, entry['project'], entry['type'])
        tracker = entry['tracker']
        if tracker is None:
            tracker = self.config['migration'].get('wizard', {}).get('tracker_template_id')
            if tracker not in [t['id'] for t in self.plan['projects'][entry['project']]['trackers']]:
                raise ApiError('Configure wizard.tracker_template_id using a template Tracker inside the selected project')
            entry['template_tracker'] = tracker
        fields = target_fields(self.cb, tracker)
        existing = self.config['migration'].get('field_mapping', {}).get(scope, {})
        entry.update(analysis=analysis, target_fields=fields,
                     rows=proposals(analysis, fields, self.config, existing))
        entry['target_risks'] = [{'field': source, 'reason': 'hidden-target-with-source-values'}
            for source, definition in analysis['fields'].items()
            if any(has_value(v) for v in analysis['values'].get(source, []))
            and any(hidden(f) and label_tokens(source, definition) & label_tokens('', f) for f in fields)]
        self.stage('fields:' + scope, {'source': len(analysis['fields']), 'target': len(fields),
                                      'issues': analysis['issue_count'], 'risks': analysis['risks'] + entry['target_risks']})
        return entry

    def manual_mapping(self, scope, source, target_id, target_key, values):
        entry = self.plan['scopes'][scope]
        target = next(f for f in entry['target_fields'] if field_identifier(f) == target_id and not hidden(f))
        kind, multiple = semantic_type(entry['analysis']['fields'][source], True)
        if not compatible(kind, multiple, target):
            raise ApiError('Incompatible source/target types; no implicit conversion')
        for other, row in entry['rows'].items():
            if other != source and row.get('spec') and (row['spec']['field_id'] == target_id or row['spec']['target'] == target_key):
                raise ApiError('Destination already mapped to another Jira field')
        spec = build_spec(source, target, kind, target_key, values)
        from migration.fields import convert
        for value in entry['analysis']['values'].get(source, []):
            convert(value, kind, values)
        entry['rows'][source].update(status='mapped', mapping_mode='manual', target=target,
                                     spec=spec, reason='수동 연결')
        self.state.save('wizard', 'plan', self.plan)

    def configure_creation(self, scope, source, label, semantic, multiple, choices):
        """Save a field proposal only. The actual server write remains in execute()."""
        entry = self.plan['scopes'][scope]
        row = entry['rows'][source]
        if row.get('status') != 'create' or row.get('target'):
            raise ApiError('신규 필드 생성은 매핑할 기존 CB 필드가 없는 Jira 필드에만 설정할 수 있습니다.')
        label = label.strip()
        if not label:
            raise ApiError('신규 필드 이름을 입력하세요.')
        source_kind, source_multiple = semantic_type(entry['analysis']['fields'][source], True)
        if semantic != source_kind or bool(multiple) != bool(source_multiple):
            raise ApiError('Jira 원본 타입과 다중 선택 여부를 변경할 수 없습니다.')
        profiles = self.config['migration'].get('wizard', {}).get('field_types', {})
        if semantic not in profiles:
            raise ApiError('검증된 신규 필드 타입 설정이 없습니다: ' + semantic)
        options = {}
        if semantic in ('option', 'status'):
            if not isinstance(choices, dict) or not choices or any(not str(v).strip() for v in choices.values()):
                raise ApiError('Choice 필드는 원본 값에 대응하는 선택 옵션이 필요합니다.')
            options = {str(k):str(v).strip() for k,v in choices.items()}
        elif choices:
            raise ApiError('Choice가 아닌 필드에는 선택 옵션을 설정할 수 없습니다.')
        row.update(mapping_mode='create', reason='Import 승인 후 생성', creation={
            'label':label, 'semantic_type':semantic, 'multipleSelection':bool(multiple),
            'choices':options})
        self.state.save('wizard', 'plan', self.plan)
        return deepcopy(row['creation'])

    def errors(self):
        errors = []
        if not self.plan['projects']:
            errors.append('프로젝트를 선택하세요.')
        if self.plan['scopes'] and not any(e.get('analysis', {}).get('issue_count', 0) for e in self.plan['scopes'].values()):
            errors.append('이관할 실제 Issue가 없습니다.')
        for project, selected in self.plan['projects'].items():
            for kind in selected['types']:
                scope = project + ':' + str(kind['id'])
                entry = self.plan['scopes'].get(scope, {})
                if 'analysis' not in entry:
                    errors.append(scope + ': 분석 필요')
                    continue
                if entry['analysis']['risks'] or entry.get('target_risks'):
                    errors.append(scope + ': Hidden 필드에 실제 값이 있어 누락 위험 해결 필요')
                for source, row in entry['rows'].items():
                    if row['status'] not in ('mapped', 'create'):
                        errors.append(scope + ':' + source + ': ' + row['reason'])
                keys = [row['spec']['target'] for row in entry['rows'].values() if row.get('spec')]
                if len(keys) != len(set(keys)):
                    errors.append(scope + ': 중복 대상 payload 키')
        return errors

    def create_field(self, scope, source, row, tracker):
        from mapping_helper import create_dedicated_field, dedicated_field_contract, create_confirmed_field
        proposal = row['creation']
        profile = self.config['migration'].get('wizard', {}).get('field_types', {}).get(proposal['semantic_type'])
        if not profile:
            raise ApiError('Configure a verified wizard.field_types template for ' + proposal['semantic_type'])
        definition = deepcopy(profile['definition'])
        definition.update(label=proposal['label'], multipleSelection=proposal['multipleSelection'])
        if proposal['semantic_type'] in ('option', 'status'):
            definition['options'] = [{'name': label} for label in dict.fromkeys(proposal['choices'].values())]
        if not compatible(proposal['semantic_type'], proposal['multipleSelection'], dict(definition, typeName=profile['type_name'])):
            raise ApiError('Creation template changes source type')
        identity = 'wizard-field:' + str(tracker) + ':' + source
        known = self.state.get(identity)
        fields = self.cb.fields(tracker)
        matches = [f for f in fields if f.get('label', f.get('name')) == proposal['label']]
        if known:
            if len(matches) != 1:
                raise ApiError('Uncertain field creation: reconcile server state')
            created = matches[0]
        else:
            if matches:
                raise ApiError('Field appeared after analysis; analyze again before reusing it')
            self.state.mark(identity, 'pending', None)
            journal = self.state.root / ('field-' + hashlib.sha256(identity.encode()).hexdigest() + '.json')
            try:
                dedicated_field_contract(self.cb,definition,tracker)
                direct = True
            except ApiError:
                from clients.codebeamer_3007 import configuration_field_contract
                expected = configuration_field_contract(self.cb,definition,tracker)
                direct = False
            if direct:
                created, _ = create_dedicated_field(self.cb,tracker,definition,journal)
            else:
                created, _ = create_confirmed_field(self.cb,tracker,definition,expected,journal,approved=True)
        # Re-read the actual server-assigned ID and full metadata, never trust a proposed ID.
        actual = next((f for f in target_fields(self.cb, tracker) if field_identifier(f) == field_identifier(created)), None)
        if not actual or not compatible(proposal['semantic_type'], proposal['multipleSelection'], actual):
            raise ApiError('Created field readback type/multiplicity mismatch')
        if known and known[0] == 'done' and known[1]['id'] != field_identifier(actual):
            raise ApiError('Created field ID changed')
        mapping, unmatched = suggest_values(entry_values(self.plan['scopes'][scope], source), actual.get('options'))
        if proposal['semantic_type'] in ('option', 'status') and unmatched:
            raise ApiError('Created Choice values do not cover the source')
        if proposal['semantic_type'] == 'user':
            mapping = deepcopy(self.config['migration'].get('user_mapping', {}))
        key = 'jira_' + source
        spec = build_spec(source, actual, proposal['semantic_type'], key, mapping)
        # The discriminator/value member come from a deployment-verified template.
        binding = deepcopy(profile['binding'])
        binding['fieldId'] = field_identifier(actual)
        self.cb.config.setdefault('field_bindings', {})[key] = binding
        self.state.mark(identity, 'done', {'id': field_identifier(actual)})
        return spec

    def ensure_write_contracts(self):
        from clients.codebeamer_client import CodebeamerClient
        if not isinstance(self.cb,CodebeamerClient):return {'test_double':True}
        if self.cb.spec is None:
            try:self.cb.discover()
            except ApiError as exc:
                raise ApiError('조회 기능은 정상이나 실제 Import 쓰기 계약이 부족합니다. 필요한 계약: '
                    'Item 생성 POST /v3/trackers/{trackerId}/items의 request/response schema와 Item GET; '
                    '신규 필드/Tracker가 있으면 POST /v3/tracker/configuration 전체 schema; '
                    'Attachment upload/download, Comment create/get, Association create/get, Legacy History 보존 API. '
                    '서버 OpenAPI 또는 이전 성공 실행의 discovery/single-openapi가 필요합니다. 원인: '+str(exc)) from exc
        try:
            from mapping_helper import request_schema,schema_object
            item=self.cb.operation('item_create');readback=self.cb.operation('item_get')
            if item['method'].upper()!='POST' or '{trackerId}' not in item['path'] or readback['method'].upper()!='GET':
                raise ApiError('Item create/readback method or Tracker scope mismatch')
            api=self.cb.spec['paths'][item['path']]['post']
            schema=schema_object(self.cb.spec,request_schema(self.cb.spec,api))
            if schema.get('type')!='object' or not isinstance(schema.get('properties'),dict) or not schema['properties']:
                raise ApiError('Item create request object schema missing')
            responses=api.get('responses',{})
            success=[value for code,value in responses.items() if str(code).startswith('2') and isinstance(value,dict)]
            if not success:raise ApiError('Item create success response schema missing')
            result={'openapi_source':self.cb.config.get('reused_successful_openapi',self.cb.config.get('discovered_swagger_url')),
                    'item_create':item,'item_get':readback,'request_properties':sorted(schema['properties'])}
            if any(row.get('status')=='create' for entry in self.plan['scopes'].values() for row in entry.get('rows',{}).values()):
                field=self.cb.operation('tracker_configuration_update')
                if field['method'].upper()!='POST' or field['path']!='/v3/tracker/configuration':
                    raise ApiError('New Field requires POST /v3/tracker/configuration')
                field_schema=schema_object(self.cb.spec,request_schema(self.cb.spec,self.cb.spec['paths'][field['path']]['post']))
                if not isinstance(field_schema.get('properties'),dict) or not field_schema['properties']:
                    raise ApiError('Tracker Configuration request schema missing')
                result['field_create']=field
        except (ApiError,KeyError,TypeError) as exc:
            raise ApiError('Item 1건 Import 쓰기 계약 미확인: '+str(exc)) from exc
        self.state.save('wizard','write-contracts',result)
        return result

    def execute(self, approved=False):
        self.cb.write = False
        if approved is not True:
            raise ApiError('Explicit Import approval is required')
        self.ensure_write_contracts()
        errors = self.errors()
        if self.config['jira'].get('single_issue_key') and (len(self.plan['scopes']) != 1 or len(self.plan['projects']) != 1):
            errors.append('Single-Issue test requires exactly one project/Tracker/Issue Type scope')
        if errors:
            raise ApiError('\n'.join(errors))
        config = deepcopy(self.config)
        settings = config['migration']
        settings.setdefault('project_mapping', {})
        settings.setdefault('tracker_mapping', {})
        settings.setdefault('field_mapping', {})
        settings.setdefault('wizard', {})['enabled'] = True
        if settings.get('mode') == 'test-runs':
            # Import Wizard imports the selected issues, unlike the preserved Run-only CLI.
            settings['mode'] = 'issues'
        config['jira']['projects'] = list(self.plan['projects'])
        config['jira'].pop('execution_issue_types', None)
        jira = JiraClient(self.jira.http, config['jira'], self.state.save)
        identity = {'jira': config['jira']['url'], 'codebeamer': config['codebeamer']['url']}
        prior = self.state.get('environment-binding')
        if prior and prior[1] != identity:
            raise ApiError('State belongs to different source/target URLs')
        self.state.mark('environment-binding', 'done', identity)
        # Refresh analysis before writes. Changes require a new explicit review.
        for scope, entry in self.plan['scopes'].items():
            latest = field_analysis(jira, entry['project'], entry['type'])
            if latest != entry['analysis']:
                raise ApiError('Source fields/values changed; analyze again: ' + scope)
            checked_tracker = entry['tracker'] or entry.get('template_tracker')
            if checked_tracker and target_fields(self.cb, checked_tracker) != entry['target_fields']:
                raise ApiError('Target configuration changed; analyze again: ' + scope)
        from migration.preservation import preflight
        preflight(self.cb, settings, self.plan)
        # Collect every selected bundle (including R4J) before any target write.
        bundles = {}
        runs = {}
        source_definitions = jira.fields()
        xray_fields = managed_fields(source_definitions, config['jira'].get('xray_run_field'))
        run_field = XrayClient.identify_field(source_definitions, config['jira'].get('xray_run_field')) if xray_fields else None
        xray = XrayClient(jira.http, config['jira'], self.state.save)
        for project in config['jira']['projects']:
            for summary in jira.issues(project):
                issue = jira.bundle(summary['key'])
                from migration.preservation import collect_r4j
                collect_r4j(jira, issue, settings, self.state)
                scope = project + ':' + str(issue['fields']['issuetype']['id'])
                entry = self.plan['scopes'].get(scope)
                if entry is None:
                    raise ApiError('Unmapped Issue Type in source selection')
                missing = {f for f, v in issue['fields'].items() if f not in entry['rows']
                           and f not in SPECIAL | CONTEXT | set(entry['analysis']['managed_fields']) and has_value(v)}
                if missing:
                    raise ApiError('Unmapped source values before Import: ' + ', '.join(sorted(missing)))
                from migration.preservation import rewrite_description
                files = issue['fields'].get('attachment', [])
                rewrite_description(issue['fields'].get('description'), files,
                                    {str(a['id']): 'https://target.invalid/' + str(a['id']) for a in files})
                runs[issue['key']] = xray.runs(issue, run_field)
                if config['jira'].get('single_issue_key'):
                    if runs[issue['key']]:
                        raise ApiError('Single-Issue live test cannot write Xray Runs into a separately configured Tracker; dedicated Run/dependency scope is required')
                    for link in issue['fields'].get('issuelinks',[]):
                        other = link.get('inwardIssue') or link.get('outwardIssue')
                        if not other or str(other.get('id')) != str(issue['id']):
                            raise ApiError('Single-Issue live test cannot create Associations to other Items; choose an isolated test Issue or use a separately approved multi-Item scope')
                for link in issue['fields'].get('issuelinks', []):
                    target_type = settings['wizard'].get('association_types', {}).get(str(link['type']['id']))
                    if type(target_type) is not int or target_type <= 0:
                        raise ApiError('Configure wizard.association_types for Jira link type ' + str(link['type']['id']))
                bundles[issue['key']] = issue
        original_bundle = jira.bundle
        jira.bundle = lambda key: deepcopy(bundles[key]) if key in bundles else original_bundle(key)
        self.cb.write = True
        try:
            for scope, entry in self.plan['scopes'].items():
                project_id = entry['project_id']
                settings['project_mapping'][entry['project']] = project_id
                # Re-read membership at the time of Import, not just during analysis.
                trackers = self.cb.call('trackers_get', {'projectId': project_id})
                tracker = entry['tracker']
                if tracker is None:
                    matches = [t for t in trackers if t.get('name') == entry['new_tracker']]
                    token = 'wizard-tracker:' + scope
                    known = self.state.get(token)
                    if matches:
                        if not known or len(matches) != 1:
                            raise ApiError('Tracker name collision; select the existing Tracker')
                        tracker = matches[0]['id']
                        found = self.cb.call('tracker_lookup', {'projectId': project_id, 'sourceKey': scope})
                        if not isinstance(found, list) or len(found) != 1 or found[0]['id'] != tracker:
                            raise ApiError('Uncertain Tracker source-ID lookup; reconcile before retry')
                        if known[0] == 'done' and tracker != known[1]['id']:
                            raise ApiError('Tracker identity changed')
                        self.state.mark(token, 'done', {'id': tracker})
                    else:
                        if self.cb.operation('tracker_create')['path'] == '/v3/tracker/configuration':
                            from clients.codebeamer_3007 import new_tracker_configuration
                            body = new_tracker_configuration(self.cb,entry['template_tracker'],project_id,entry['new_tracker'])
                        else:
                            body = deepcopy(settings['wizard'].get('tracker_template', {}))
                            body.update(name=entry['new_tracker'], project={'id': project_id}, sourceKey=scope)
                        created = self.state.once(token, lambda: self.cb.call('tracker_create', {'projectId': project_id}, body))
                        tracker = created['id']
                    verified = self.cb.call('trackers_get', {'projectId': project_id})
                    if tracker not in [t['id'] for t in verified]:
                        raise ApiError('Created Tracker not found inside selected project')
                elif tracker not in [t['id'] for t in trackers]:
                    raise ApiError('Tracker no longer belongs to selected project')
                settings['tracker_mapping'][scope] = tracker
                self.stage('tracker:' + scope, {'tracker_id': tracker, 'verified_project_id': project_id})
                mapping = {}
                actual_fields = target_fields(self.cb, tracker)
                for source, row in entry['rows'].items():
                    if row['status'] == 'create':
                        mapping[source] = self.create_field(scope, source, row, tracker)
                    else:
                        spec = deepcopy(row['spec'])
                        if entry['tracker'] is None:
                            template = row['target']
                            matches = [f for f in actual_fields if not hidden(f) and
                                label_tokens('', template) & label_tokens('', f) and
                                compatible(*semantic_type(entry['analysis']['fields'][source], True), f)]
                            if len(matches) != 1:
                                raise ApiError('New Tracker fields differ from its template; analyze actual Tracker before resuming')
                            spec = build_spec(source, matches[0], spec['type'], spec['target'], spec.get('value_mapping', {}))
                            binding = self.cb.config.get('field_bindings', {}).get(spec['target'])
                            if binding:
                                binding = deepcopy(binding)
                                binding['fieldId'] = spec['field_id']
                                spec['binding'] = binding
                        mapping[source] = spec
                    binding = self.cb.config.get('field_bindings', {}).get(mapping[source]['target'])
                    if binding and 'binding' not in mapping[source]:
                        mapping[source]['binding'] = deepcopy(binding)
                    self.stage('field:' + scope + ':' + source, {'field_id': mapping[source]['field_id'],
                               'tracker_id': tracker, 'status': 'verified'})
                settings['field_mapping'][scope] = mapping
            self.state.save('wizard', 'resolved-mapping', {k: settings[k] for k in ('project_mapping', 'tracker_mapping', 'field_mapping')})
            for section in ('project_mapping', 'tracker_mapping', 'field_mapping'):
                self.config['migration'][section] = deepcopy(settings[section])
            self.config['codebeamer']['field_bindings'] = deepcopy(self.cb.config.get('field_bindings', {}))
            for scope, entry in self.plan['scopes'].items():
                entry['tracker'] = settings['tracker_mapping'][scope]
                entry['new_tracker'] = None
                entry['target_fields'] = target_fields(self.cb, entry['tracker'])
                for source, row in entry['rows'].items():
                    spec = settings['field_mapping'][scope][source]
                    actual = next(f for f in entry['target_fields'] if field_identifier(f) == spec['field_id'])
                    row.update(status='mapped', spec=deepcopy(spec), target=actual)
            xray.runs = lambda issue, field: deepcopy(runs[issue['key']])
            report = Engine(jira, xray, self.cb,
                            self.state, settings, True).migrate()
            self.stage('import', report)
            return report
        finally:
            self.cb.write = False


def entry_values(entry, source):
    return list(entry['analysis']['values'].get(source, [])) + list(entry['analysis']['fields'][source].get('allowedValues', []))
