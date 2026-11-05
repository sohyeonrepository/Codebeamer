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

SPECIAL = {'attachment', 'comment', 'issuelinks', 'worklog', 'changelog'}
CONTEXT = {'issuetype', 'project'}


def field_identity(tracker, source, row):
    return 'wizard-field:'+str(tracker)+':'+source+(':'+row['creation_token'] if row.get('creation_token') else '')


def managed_fields(definitions, configured=None):
    result = set()
    if configured:
        result.add(configured)
    for field in definitions:
        custom = field.get('schema', {}).get('custom', '').casefold()
        if 'raven' in custom and any(token in custom for token in ('testexec', 'testset', 'testplan', 'testrun')):
            result.add(field['id'])
    return result


def hidden(field):
    return field.get('hidden') is True or field.get('isHidden') is True or field.get('visible') is False


def has_value(value):
    return value is not None and value != '' and value != [] and value != {}


def semantic_type(field, jira=False):
    if jira:
        from migration.field_semantics import jira_field_semantics
        kind, multiple, evidence = jira_field_semantics(field)
        return kind, multiple
    # Numeric type IDs are deployment-specific; require named server metadata.
    from clients.tracker_fields import normalize
    field = normalize(field)
    kind = field.get('typeName', field.get('type'))
    if isinstance(kind, dict):
        kind = kind.get('name')
    return {'text': 'text', 'wikitext': 'text', 'integer': 'number', 'decimal': 'number',
            'bool': 'boolean', 'boolean': 'boolean', 'date': 'date', 'datetime': 'datetime',
            'choice': 'option', 'option': 'option', 'status': 'status', 'user': 'user',
            'members': 'user', 'reference':'reference', 'references':'reference'}.get(str(kind).casefold()), field.get('multipleSelection')


def text_conversion(kind, multiple, target=None):
    """Choose a writable value encoding based on source/target cardinality.

    Jira array[string] is distinct from the common Jira textarea (one string
    containing line breaks). For scalar CB Text/WikiText, join array items with
    newlines. Source arrays remain available in original extracted JSON.
    """
    if kind == 'text' and multiple:
        if target is not None and semantic_type(target)[1] is True:
            return 'text_list'
        return 'text_multi'
    return kind


def creation_kind(field):
    """Preserve the concrete CB creation type rather than collapsing variants."""
    from mapping_helper import field_type_label
    from mapping_helper import normalized_label
    label = field_type_label(field)
    if not label:
        return None
    key = normalized_label(label.split(' (', 1)[0])
    aliases = {'choice':'option', 'bool':'boolean', 'members':'user',
               'users':'user', 'references':'reference', 'wikilinkurl':'wikilink',
               'workconfigitems':'reference', 'projects':'project',
               'trackers':'tracker', 'repositories':'repository'}
    return aliases.get(key, key)


def selected_conversion(source_kind, source_multiple, target_kind, target_multiple):
    """Explicit, loss-aware conversion for user-selected Codebeamer types."""
    if target_kind in ('text', 'wikitext', 'wikilink'):
        if source_kind == 'text':
            return 'text_list' if source_multiple and target_multiple else (
                'text_multi' if source_multiple else 'text')
        return 'stringify'
    if target_kind in ('integer', 'decimal', 'number'):
        if source_kind == 'number':
            return 'number'
        if source_kind == 'text':
            return 'parse_number'
    if target_kind in ('boolean', 'bool'):
        if source_kind == 'boolean':
            return 'boolean'
        if source_kind == 'text':
            return 'parse_boolean'
    if target_kind in ('date', 'datetime'):
        if source_kind in ('date', 'datetime'):
            return 'date' if target_kind == 'date' else 'datetime'
    if target_kind in ('option', 'status') and source_kind in ('option', 'status', 'text'):
        return 'status' if target_kind == 'status' else 'option'
    if target_kind == 'user' and source_kind == 'user':
        return 'user'
    if target_kind == 'reference' and source_kind == 'reference':
        return 'reference'
    if source_kind == target_kind:
        return source_kind
    return None


def target_multiple_for(kind, multiple):
    # Multi-text arrays stored in a single CB Text/WikiText field.
    return False if kind == 'text' and multiple else multiple


def field_analysis(jira, project, issue_type):
    profile = jira.config.get('field_configuration')
    configured = []
    configuration_status = 'unavailable'
    if profile:
        try:
            configured = jira.configured_records(profile, {'projectKey': project, 'issueTypeId': str(issue_type['id'])})
            configuration_status = 'available'
        except ApiError:
            configuration_status = 'unavailable'
    # Global field metadata and editmeta never establish configuration visibility.
    definitions = {f['id']: f for f in jira.fields()}
    managed = managed_fields(definitions.values(), jira.config.get('xray_run_field'))
    try:
        screen_fields = jira.creation_fields(project, issue_type['id'])
        screen_status = 'available'
    except ApiError:
        screen_fields, screen_status = {}, 'unavailable'
    fields = {key:dict(definitions.get(key, {}), **dict(value, id=key, visibility='unknown'))
              for key, value in screen_fields.items()}
    configured_ids = set()
    for f in configured:
        key = str(f['id'])
        if key in configured_ids:
            raise ApiError('Duplicate Field Configuration entry')
        configured_ids.add(key)
        fields[key] = dict(fields.get(key, definitions.get(key, {})), **f)
        fields[key]['visibility'] = ('hidden' if hidden(f) else 'visible') if any(
            type(f.get(flag)) is bool for flag in ('hidden', 'isHidden', 'visible')) else 'unknown'
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
            if key in fields or has_value(value) or (configuration_status == 'unavailable' and screen_status == 'unavailable'):
                fields.setdefault(key, dict(definitions.get(key, {'id': key}), visibility='unknown'))
            observations = values.setdefault(key, [])
            if value not in observations:
                observations.append(value)
    # A configured field is not necessarily a field used by this Issue Type.
    # Retain standard/system and required fields even when every issue value is empty.
    from migration.system_fields import STANDARD, REFERENCE_CATALOGS
    core_fields = STANDARD | {'created', 'creator', 'updated', 'updater', 'id', 'key'}
    risks = []
    visible = {}
    excluded = []
    for key, field in fields.items():
        if key in SPECIAL | CONTEXT | managed:
            continue
        if field['visibility'] == 'hidden':
            remaining = any(has_value(v) for v in values.get(key, []))
            excluded.append({'field':key,'name':field.get('name',key),'has_value':remaining})
            if remaining:
                risks.append({'field': key, 'reason': 'hidden-source-with-values'})
        else:
            field['configured'] = key in configured_ids or key in screen_fields
            field['has_value'] = any(has_value(v) for v in values.get(key, []))
            field['default_selected'] = bool(field['has_value'] or field.get('required') or key in core_fields)
            field['used_in_issues'] = field['has_value']
            field['show_by_default'] = bool(field['default_selected'] or key in REFERENCE_CATALOGS and field['has_value'])
            visible[key] = field
    if 'components' in visible:
        components = {}
        for sample in values.get('components', []) + visible['components'].get('allowedValues', []):
            for component in sample if isinstance(sample,list) else [sample]:
                if component is None:continue
                if not isinstance(component,dict) or component.get('id') is None or not isinstance(component.get('name'),str):
                    raise ApiError('Components 원본 ID/Name 응답이 불완전합니다.')
                identity, name = str(component['id']), component['name']
                if identity in components and components[identity] != name:
                    raise ApiError('Components 동일 ID의 이름이 조회 중 변경됐습니다.')
                components[identity] = name
        if len(set(components.values())) != len(components):
            raise ApiError('Components 이름이 같은 서로 다른 ID: Choice 옵션을 합치지 않도록 별도 검토가 필요합니다.')
    # A Jira issue response can omit empty system fields. Surface expected core
    # fields from metadata without inventing their schema.
    for key in ('summary', 'description'):
        if key not in visible and key not in excluded:
            definition = definitions.get(key)
            if definition:
                normalized = dict(definition)
                # Jira's two standard textual fields have fixed Jira data
                # semantics even when /field omits the schema object.
                if not isinstance(normalized.get('schema'),dict) or not normalized['schema'].get('type'):
                    normalized['schema'] = {'type':'string'}
                visible[key] = dict(normalized, visibility='unknown', configured=False,
                                    has_value=False, used_in_issues=False,
                                    default_selected=True, show_by_default=True)
    return {'fields': visible, 'values': values, 'risks': risks, 'excluded':excluded,'issue_count': count,
            'configuration_status': configuration_status, 'createmeta_status': screen_status,
            'visibility_unknown': sorted(k for k, f in visible.items() if f['visibility'] == 'unknown'),
            'configuration_count': len(configured), 'managed_fields': sorted(managed), 'complete': True}


def target_fields(cb, tracker):
    from clients.codebeamer_client import CodebeamerClient
    from clients.tracker_fields import normalize, read
    if isinstance(cb, CodebeamerClient):
        return read(cb, tracker)
    fields, configuration = cb.fields(tracker), cb.configuration(tracker)
    by_id = {str(field_identifier(f)): f for f in configuration['fields']}
    return [normalize(dict(f, **by_id.get(str(field_identifier(f)), {}))) for f in fields]


def checked_value(value, kind, mapping, target, identity=None):
    from migration.fields import convert
    converted = convert(value, kind, mapping, identity)
    if kind in ('option', 'status') and has_value(converted):
        options = target.get('options')
        if not isinstance(options, list):
            raise ApiError('실제 대상 Choice 옵션 목록을 확인할 수 없습니다.')
        allowed = {str(o['id']) for o in options if isinstance(o,dict) and o.get('id') is not None}
        for selected in converted if isinstance(converted,list) else [converted]:
            if not isinstance(selected,dict) or str(selected.get('id')) not in allowed:
                raise ApiError('실제 대상 옵션 목록에 없는 선택지 ID입니다.')
    return converted


def proposals(analysis, targets, config, existing=None):
    existing = existing or {}
    visible = [f for f in targets if not hidden(f)]
    bindings = config.get('codebeamer', {}).get('field_bindings', {})
    result = {}
    claimed = {str(s.get('field_id')) for s in existing.values()}
    for source, definition in analysis['fields'].items():
        from migration.system_fields import field_policy
        policy = field_policy(source, definition)
        if policy in ('dedicated', 'relationship'):
            continue
        kind, multiple = semantic_type(definition, True)
        candidates = [f for f in visible if label_tokens(source, definition) & label_tokens('', f)]
        # Built-in mappings take precedence over fuzzy label matching. The
        # Intland Connector ID values are NOT assumed to be REST field IDs.
        builtin_key = {'summary':'name','description':'description','assignee':'assignedTo',
                       'reporter':'supervisor','duedate':'endDate','startdate':'startDate',
                       'priority':'priority','status':'status','resolution':'resolution'}.get(source)
        aliases = {'summary': ('name', '제목', 'title'),
                   'description': ('description', '설명'),
                   'assignee': ('assignedto', 'assigned to', '담당자', 'assigned'),
                   'reporter': ('supervisor', 'reporter', 'submitter'),
                   'created': ('submittedat', 'submitted at'),
                   'updated': ('modifiedat', 'modified at')}
        # Configured bindings are authoritative only when the target field
        # exists on this Tracker and has a compatible type.
        binding_keys = [builtin_key] if builtin_key else []
        if source == 'reporter': binding_keys += ['submittedBy']
        verified = [f for f in visible if any(
                    str(field_identifier(f)) == str(bindings.get(k, {}).get('fieldId'))
                    for k in binding_keys if bindings.get(k, {}).get('fieldId') is not None)
                    and compatible(kind, multiple, f)]
        if len(verified) == 1:
            candidates = verified
        else:
            # A single exact target name is stronger than multiple fuzzy label hits.
            from mapping_helper import normalized_label
            expected = {normalized_label(n) for n in aliases.get(source, ())}
            exact = [f for f in visible if normalized_label(f.get('label') or f.get('name')) in expected
                     and compatible(kind, multiple, f)]
            matching = [f for f in candidates if compatible(kind, multiple, f)]
            if len(exact) == 1:
                candidates = exact
            elif len(matching) == 1:
                candidates = matching
        entry = {'source': source, 'status': 'manual', 'mapping_mode': None,
                 'reason': '이름 또는 타입 확인 필요', 'spec': None}
        if not kind:
            entry['reason'] = 'Jira 원본 필드 타입 미확인: schema.type/items/custom 확인 필요'
        elif not candidates:
            preferred = {'summary': 'Name (Text)', 'description': 'Description (Text/WikiText)',
                         'assignee': 'Assigned to (User/Members)'}.get(source)
            entry['reason'] = ('대상 기본 필드 조회 안 됨: ' + preferred if preferred
                               else '동일 이름/별칭 대상 필드 없음')
        elif len(candidates) == 1 and not compatible(kind, multiple, candidates[0]):
            target_kind, target_multiple = semantic_type(candidates[0])
            entry['reason'] = ('대상 필드 타입/다중 여부 불일치: Jira '+
                               str((kind, multiple))+' → Codebeamer '+
                               str((target_kind, target_multiple)))
        elif len(candidates) > 1:
            entry['reason'] = '동일 이름 또는 별칭의 대상 후보가 여러 개: 명시적으로 선택 필요'
        profile = config['migration'].get('catalog_reference_mapping', {}).get(source)
        if profile:
            from migration.system_fields import catalog_reference_spec
            try:
                target = next(f for f in targets if field_identifier(f) == profile.get('field_id'))
                spec = catalog_reference_spec(source, definition, analysis['values'].get(source, []), target, profile)
                entry.update(status='mapped', mapping_mode='manual', spec=spec, target=target, reason='명시적 원본 ID → Reference 매핑; Import 전 대상 조회 검증')
            except (ApiError, ValueError, KeyError, StopIteration) as exc:
                entry['reason'] = str(exc) or 'Reference 대상 필드 없음'
            result[source] = entry
            continue
        # Older Wizard plans may contain stale auto-generated mappings.
        # For the two core text fields a unique, type-compatible built-in
        # match supersedes an invalid saved Field ID, without silently
        # replacing an otherwise valid user-selected mapping.
        if source in ('summary', 'description') and source in existing and len(candidates) == 1:
            saved = next((f for f in visible if str(field_identifier(f)) == str(existing[source].get('field_id'))), None)
            if saved is None or not compatible(kind, multiple, saved):
                existing = dict(existing)
                existing.pop(source)
        if source in existing:
            candidate = next((f for f in targets if str(field_identifier(f)) == str(existing[source]['field_id'])), None)
            spec=deepcopy(existing[source])
            if candidate:
                refreshed=build_spec(source,candidate,text_conversion(kind,multiple,candidate) or spec['type'],spec['target'],spec.get('value_mapping',{}),existing=True)
                for key in list(spec):
                    if key.startswith('expected_') or key in ('missing_definition','selection_unverified'):spec.pop(key)
                spec.update(refreshed)
                if spec.get('deferred_reference'):spec.pop('missing_definition',None)
                if not compatible(kind,multiple,candidate) or hidden(candidate):spec['selection_unverified']='Hidden 또는 타입/다중 선택 미확인·불일치'
                if not spec.get('selection_unverified') and not spec.get('deferred_reference'):
                    try:
                        for sample in analysis['values'].get(source, []):
                            checked_value(sample,spec['type'],spec.get('value_mapping',{}),candidate,spec.get('source_identity'))
                    except ApiError as exc:
                        spec['selection_unverified'] = str(exc)
                entry.update(status='mapped', mapping_mode='manual', spec=spec,target=candidate,reason='저장된 선택')
            else:
                spec['selection_unverified']='선택한 Field ID가 현재 Tracker에 없음'
                entry.update(status='mapped',mapping_mode='manual',spec=spec,reason=spec['selection_unverified'])
        elif len(candidates) == 1 and kind and compatible(kind, multiple, candidates[0]):
            target = candidates[0]
            fid = field_identifier(target)
            keys = [k for k, b in bindings.items() if b.get('fieldId') == fid]
            canonical = label_tokens(source, definition) & label_tokens('', target) & {'name', 'description'}
            key = keys[0] if len(keys) == 1 else (next(iter(canonical)) if canonical else 'cb_field_'+str(fid))
            vals = list(analysis['values'].get(source, []))
            mapping, unmatched = suggest_values(vals, target.get('options'), 'id' if source == 'components' else None)
            if kind == 'user':
                # Match observed usernames only; never infer identity from displayName or equal numeric IDs.
                usernames = []
                for sample in vals:
                    for user in sample if isinstance(sample,list) else [sample]:
                        if isinstance(user,dict) and user.get('name'):
                            usernames.append({k:user[k] for k in ('accountId','name','id') if k in user})
                mapping, _ = suggest_values(usernames, target.get('options'))
                mapping.update(deepcopy(config['migration'].get('user_mapping', {})))
            if kind == 'status':
                mapping.update(config['migration'].get('status_mapping', {}))
            try:
                converter = text_conversion(kind, multiple, target)
                spec = build_spec(source, target, converter, key, mapping, existing=True)
                if source == 'components': spec['source_identity'] = 'id'
                for v in analysis['values'].get(source, []):
                    checked_value(v, converter, mapping, target, 'id' if source == 'components' else None)
                if str(fid) in claimed:
                    raise ValueError('이미 다른 원본에 연결된 대상')
                if key in bindings: spec['binding'] = deepcopy(bindings[key])
                entry.update(status='mapped', mapping_mode='auto', spec=spec, target=target, reason='이름·타입 일치')
            except (ValueError, ApiError, TypeError) as exc:
                entry.update(target=target, reason=('사용자 식별자 매핑 필요: ' if kind == 'user' else '') + str(exc))
        elif not candidates:
            if source in ('summary', 'description', 'assignee', 'reporter', 'priority', 'status'):
                entry['reason'] = '기존 CB 기본 필드 확인 필요: ' + source
                result[source] = entry
                continue
            if kind is None or (multiple and kind not in ('text', 'option', 'status', 'user')):
                entry.update(status='error', reason='지원하지 않는 원본 타입: 자동 변환하지 않음')
            else:
                choices = source_choices(list(analysis['values'].get(source, [])) + list(definition.get('allowedValues', [])), 'id' if source in ('components','versions','fixVersions') else None) if kind in ('option','status') else {}
                entry.update(status='create', mapping_mode='create', reason='Import 승인 후 생성', creation={
                    'label': definition.get('name', source), 'semantic_type': kind,
                    'multipleSelection': target_multiple_for(kind, multiple), 'choices': choices})
        if entry.get('spec'):
            entry['spec']['source_field_id'] = source
        entry['field_policy'] = policy
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
    if kind == 'text' and multiple and target_kind == 'text':
        # A Jira multi-text array can be explicitly converted to CB scalar
        # Text/WikiText, or kept as an array if the field supports it.
        return type(target_multiple) is bool
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
        saved_xray = state.load('wizard','xray-record-mapping') or {}
        for category in ('run','run_step','test_step'):
            if category in saved_xray:
                self.config['migration'].setdefault(category+'_adapter', {}).setdefault('fields', {}).update(saved_xray[category])
        saved_relations = state.load('wizard','relation-mapping')
        if saved_relations is not None:
            self.config['migration'].setdefault('wizard',{})['association_types'] = saved_relations
        self.plan = state.load('wizard', 'plan') or {'version': 1, 'projects': {}, 'scopes': {}, 'stages': []}
        for entry in self.plan['scopes'].values():
            for row in entry.get('rows', {}).values():
                if row.get('creation_profile'):
                    self.config['migration'].setdefault('wizard', {}).setdefault('field_types', {}).setdefault(
                        row['creation']['semantic_type'], deepcopy(row['creation_profile']))

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
        for name in ('analysis','target_fields'):
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
        catalog, warnings = [], []
        try:
            catalog = self.jira.global_catalog(categories=['issue_types'])['issue_types']
        except ApiError as exc:
            warnings.append('Jira 전체 Issue Type 조회 실패: ' + str(exc))
        except AttributeError:
            warnings.append('Jira 전체 Issue Type 조회 기능 미지원')
        project_ids = {str(t['id']) for t in types}
        display = [dict(t, project_available=True) for t in types]
        display.extend(dict(t, project_available=False) for t in catalog if str(t['id']) not in project_ids)
        self.plan['projects'][project] = {'target': target, 'types': types, 'trackers': trackers,
                                         'display_types': display, 'catalog_warnings': warnings,
                                         'global_types': catalog}

        # A changed project invalidates all previous scope choices for that project.
        self.plan['scopes'] = {k: v for k, v in self.plan['scopes'].items() if v['project'] != project}
        return self.stage('trackers', trackers)

    def map_tracker(self, project, type_id, tracker=None, name=None):
        if self.config['jira'].get('single_issue_key'):
            allowed = next(iter(self.plan['scopes'].values()))
            if project != allowed['project'] or str(type_id) != str(allowed['type']['id']) or tracker != allowed['tracker']:
                raise ApiError('Single-Issue test scope is fixed; start a separate test session to change its Tracker')
        selection = self.plan['projects'][project]
        kind = next((t for t in selection['types'] if str(t['id']) == str(type_id)), None)
        if kind is None:
            raise ApiError('프로젝트 미적용 Issue Type은 Tracker에 매핑할 수 없습니다: ' + str(type_id))
        policy = self.config['migration'].get('full_xray', {})
        execution_type = policy.get('issue_types', {}).get('execution', 'Test Execution')
        if tracker is None and not policy.get('create_execution_items', False) and str(execution_type) in (str(kind['id']), kind['name']):
            raise ApiError('Test Execution is source context only; automatic Tracker creation is disabled. Use full-xray source metadata policy.')
        if tracker is not None and tracker not in [t['id'] for t in selection['trackers']]:
            raise ApiError('Tracker must belong to the selected existing project')
        if tracker is None and not (name and name.strip()):
            raise ApiError('New Tracker name is required')
        self.plan['scopes'][project + ':' + str(type_id)] = {
            'project': project, 'type': deepcopy(kind), 'project_id': selection['target'],
            'tracker': tracker, 'new_tracker': name.strip() if tracker is None else None}
        self.state.save('wizard', 'plan', self.plan)

    def keep_only_scope(self, scope):
        """Remove stale GUI mapping selections from the active Import plan.

        This does not delete Jira issues, Codebeamer trackers, or saved source
        history. It only narrows the current session's selected Issue Types.
        """
        if scope not in self.plan['scopes']:
            raise ApiError('선택한 Issue Type의 Tracker 매핑이 없습니다: '+scope)
        selected = self.plan['scopes'][scope]
        project = selected['project']
        if project not in self.plan['projects']:
            raise ApiError('선택한 프로젝트를 찾을 수 없습니다.')
        self.plan['scopes'] = {scope:selected}
        self.plan['projects'] = {project:self.plan['projects'][project]}
        self.plan['projects'][project]['types'] = [deepcopy(selected['type'])]
        self.state.save('wizard','plan',self.plan)
        return scope

    def map_execution_context(self, project, type_id):
        """Select an actual project Execution type without assigning a target Tracker."""
        if self.config['jira'].get('single_issue_key'):
            raise ApiError('Execution context requires the full Xray dependency scope')
        from migration.xray_review import issue_kind
        selected = self.plan['projects'][project]
        kind = next((t for t in selected['types'] if str(t['id']) == str(type_id)), None)
        if not kind or issue_kind(kind, self.config['migration']) != 'execution':
            raise ApiError('Only a project-applied Test Execution may be selected as source context')
        if self.config['migration'].get('full_xray', {}).get('create_execution_items', False):
            raise ApiError('Disable legacy create_execution_items before selecting source-only context')
        self.plan['scopes'][project + ':' + str(type_id)] = {
            'project':project, 'type':deepcopy(kind), 'project_id':selected['target'],
            'tracker':None, 'new_tracker':None, 'source_only':True}
        self.state.save('wizard', 'plan', self.plan)

    def analyze(self, scope):
        self.cb.write = False
        entry = self.plan['scopes'][scope]
        analysis = field_analysis(self.jira, entry['project'], entry['type'])
        if entry.get('source_only'):
            entry.update(analysis=analysis, target_fields=[], rows={}, target_risks=[])
            self.state.save('wizard', 'plan', self.plan)
            return entry
        tracker = entry['tracker']
        if tracker is None:
            tracker = self.config['migration'].get('wizard', {}).get('tracker_template_id')
            if tracker not in [t['id'] for t in self.plan['projects'][entry['project']]['trackers']]:
                raise ApiError('Configure wizard.tracker_template_id using a template Tracker inside the selected project')
            entry['template_tracker'] = tracker
        fields = target_fields(self.cb, tracker)
        from clients.tracker_fields import binding
        for field in fields:
            derived = binding(getattr(self.cb, 'spec', None), field)
            if derived:
                key = 'cb_field_' + str(field_identifier(field))
                self.config['codebeamer'].setdefault('field_bindings', {}).setdefault(key, derived)
        existing = dict(self.config['migration'].get('field_mapping', {}).get(scope, {}))
        old_rows = deepcopy(entry.get('rows', {}))
        for k,r in old_rows.items():
            if r.get('creation_saved') and not r.get('spec'):existing.pop(k,None)
        existing.update({k:r['spec'] for k,r in old_rows.items() if r.get('spec')})
        entry.update(analysis=analysis, target_fields=fields,
                     rows=proposals(analysis, fields, self.config, existing))
        for source, row in old_rows.items():
            phase=self.state.get('field-phase:'+scope+':'+source)
            receipt=self.state.get(field_identity(tracker,source,row))
            if row.get('creation') and receipt and phase and phase[0]!='COMPLETED' and source in entry['rows']:
                entry['rows'][source]=deepcopy(row)
                entry['rows'][source].update(status='create',spec=None)
                continue
            if row.get('creation_saved') and not row.get('spec') and source in entry['rows']:
                entry['rows'][source].update(status='create',spec=None,creation=deepcopy(row['creation']), creation_saved=True, creation_profile=deepcopy(row.get('creation_profile')),creation_token=row.get('creation_token'))
        for source,row in entry['rows'].items():
            row['selected']=old_rows.get(source,{}).get('selected', bool(existing.get(source)) or analysis['fields'][source].get('default_selected',True))
            if row.get('creation'):row['creation_saved']=True
        if getattr(self.cb, 'spec', None) is not None and any(r.get('selected') and r.get('creation') and not r.get('creation_profile') for r in entry['rows'].values()):
            try:
                profiles = self.creation_profiles(scope)
                for row in entry['rows'].values():
                    if row.get('creation') and not row.get('creation_profile'):
                        profile = profiles.get(row['creation']['semantic_type'])
                        if profile:
                            row['creation_profile'] = deepcopy(profile)
            except ApiError as exc:
                entry['creation_diagnostics'] = [str(exc)]
        entry['target_risks'] = [{'field': source, 'reason': 'hidden-target-with-source-values'}
            for source, definition in analysis['fields'].items()
            if any(has_value(v) for v in analysis['values'].get(source, []))
            and any(hidden(f) and label_tokens(source, definition) & label_tokens('', f) for f in fields)]
        self.stage('fields:' + scope, {'source': len(analysis['fields']), 'target': len(fields),
                                      'issues': analysis['issue_count'], 'risks': analysis['risks'] + entry['target_risks']})
        return entry

    def select_field(self, scope, source, selected):
        if type(selected) is not bool:raise ApiError('Field selection must be boolean')
        self.plan['scopes'][scope]['rows'][source]['selected']=selected
        self.state.save('wizard','plan',self.plan)

    def creation_review(self):
        return [{'scope':scope,'source':source,'selected':row.get('selected',True),
                 'creation':deepcopy(row.get('creation')),'target':deepcopy(row.get('spec')),
                 'reason':row.get('reason')} for scope,entry in self.plan['scopes'].items()
                for source,row in entry.get('rows',{}).items()]

    def relation_catalog(self):
        from migration.relation_mapping import catalogs
        catalog = catalogs(self.jira,self.cb)
        mappings = self.config['migration'].setdefault('wizard',{}).setdefault('association_types',{})
        self.state.save('wizard','relation-catalog',catalog)
        self.state.save('wizard','relation-mapping',mappings)
        return catalog

    def save_relation_mapping(self, source_id, mapping):
        from migration.relation_mapping import normalize
        catalog=self.state.load('wizard','relation-catalog')
        if not catalog:raise ApiError('관계 유형을 먼저 조회하세요.')
        source=next((t for t in catalog['jira'] if str(t['id'])==str(source_id)),None)
        if source is None:raise ApiError('Unknown Jira Link Type')
        if mapping is None:
            mappings=self.config['migration'].setdefault('wizard',{}).setdefault('association_types',{})
            mappings.pop(str(source_id),None)
            self.state.save('wizard','relation-mapping',mappings)
            return None
        selected=normalize(mapping)
        if selected['mode']=='association' and selected['type_id'] not in [t['id'] for t in catalog['codebeamer']]:
            raise ApiError('Unknown CB Association Type')
        if selected['mode']=='reference':
            from migration.relation_mapping import verify_reference
            verify_reference(self.cb,selected)
        selected['source_type']=deepcopy(source)
        mappings=self.config['migration'].setdefault('wizard',{}).setdefault('association_types',{})
        mappings[str(source_id)]=selected
        self.state.save('wizard','relation-mapping',mappings)
        return selected

    def save_relation_reference_creation(self, source_id, tracker_id, label, target_tracker_id, multiple, reverse):
        from clients.codebeamer_client import render
        from clients.codebeamer_3007 import configuration_field_contract
        from migration.relation_mapping import normalize
        catalog=self.state.load('wizard','relation-catalog') or {}
        source=next((t for t in catalog.get('jira',[]) if str(t['id'])==str(source_id)),None)
        if source is None:raise ApiError('관계 유형을 먼저 조회하세요.')
        allowed={t['id'] for project in self.plan['projects'].values() for t in project['trackers']}
        if tracker_id not in allowed or target_tracker_id not in allowed:
            raise ApiError('선택한 프로젝트의 실제 Tracker를 선택하세요.')
        if not isinstance(label,str) or not label.strip():raise ApiError('신규 Reference 필드 이름이 필요합니다.')
        if type(multiple) is not bool or type(reverse) is not bool:raise ApiError('단일/다중 및 방향 설정이 필요합니다.')
        profile=deepcopy(self.config['migration'].get('wizard',{}).get('field_types',{}).get('reference',{}))
        if not isinstance(profile.get('reference_template'),dict) or not profile.get('definition'):
            raise ApiError('신규 Reference 생성에는 검증된 field_types.reference.definition/reference_template이 필요합니다. 서버 참조 설정을 추측하지 않습니다.')
        definition=deepcopy(profile['definition'])
        if any(key in definition for key in ('id','fieldId','referenceId')):raise ApiError('새 Reference Field ID는 서버가 부여해야 합니다.')
        definition.update(label=label.strip(),multipleSelection=multiple,
            reference=render(profile['reference_template'],{'targetTrackerId':target_tracker_id}))
        if self.cb.spec is None:self.cb.discover()
        configuration_field_contract(self.cb,definition,tracker_id)
        mapping={'mode':'reference_create','tracker_id':tracker_id,'target_tracker_id':target_tracker_id,
                 'reverse':reverse,'source_type':deepcopy(source),
                 'creation':{'label':label.strip(),'multipleSelection':multiple,'definition':definition}}
        normalize(mapping)
        self.config['migration'].setdefault('wizard',{}).setdefault('association_types',{})[str(source_id)]=mapping
        self.state.save('wizard','relation-mapping',self.config['migration']['wizard']['association_types'])
        return mapping

    def mapping_candidates(self, scope, source):
        entry = self.plan['scopes'][scope]
        kind, multiple = semantic_type(entry['analysis']['fields'][source], True)
        candidates = []
        for target in entry['target_fields']:
            target_kind, target_multiple = semantic_type(target)
            reason = ''
            if hidden(target): reason = 'Hidden 필드'
            elif not kind or not target_kind or type(target_multiple) is not bool:
                reason = '타입/다중 선택 미확인: type='+str(target.get('type', target.get('typeName')))+', valueModel='+str(target.get('valueModel'))
            elif not compatible(kind, multiple, target):
                reason = '타입 불일치: Jira '+str((kind,multiple))+' → CB '+str((target_kind,target_multiple))
            elif any(other != source and row.get('spec', {}).get('field_id') == field_identifier(target)
                     for other,row in entry['rows'].items() if row.get('spec')):
                reason = '다른 Jira 필드에 이미 연결됨'
            candidates.append({'field':target, 'reason':reason})
        return candidates

    def manual_mapping(self, scope, source, target_id, target_key, values):
        entry = self.plan['scopes'][scope]
        candidate = next((c for c in self.mapping_candidates(scope,source) if field_identifier(c['field']) == target_id), None)
        if candidate is None: raise ApiError('선택한 Tracker에 Field ID가 없습니다: '+str(target_id))
        if candidate['reason'] and '미확인' not in candidate['reason']: raise ApiError(candidate['reason'])
        target = candidate['field']
        from clients.codebeamer_client import CodebeamerClient
        detail_error=None
        if isinstance(self.cb,CodebeamerClient):
            try:
                refreshed=target_fields(self.cb,entry['tracker'])
                target=next((f for f in refreshed if field_identifier(f)==target_id),target)
                entry['target_fields']=refreshed
            except ApiError as exc:detail_error=str(exc)
        kind, multiple = semantic_type(entry['analysis']['fields'][source], True)
        target_kind,target_multiple=semantic_type(target)
        if hidden(target):raise ApiError('선택한 필드는 Hidden입니다.')
        if kind and target_kind and type(target_multiple) is bool and not compatible(kind,multiple,target):
            raise ApiError('Incompatible source/target types; no implicit conversion')
        for other, row in entry['rows'].items():
            if other != source and row.get('spec') and (row['spec']['field_id'] == target_id or row['spec']['target'] == target_key):
                raise ApiError('Destination already mapped to another Jira field')
        spec = build_spec(source, target, text_conversion(kind,multiple,target) or 'raw', target_key, values, existing=True)
        if not compatible(kind,multiple,target):spec['selection_unverified']='타입/다중 선택 미확인'
        if detail_error:spec['selection_unverified']='상세 조회 실패: '+detail_error
        if kind=='reference':
            spec['deferred_reference']=True
            spec.pop('missing_definition',None)
        if source == 'components': spec['source_identity'] = 'id'
        if kind!='reference' and not spec.get('selection_unverified'):
            for value in entry['analysis']['values'].get(source, []):
                checked_value(value, spec['type'], values, target, 'id' if source == 'components' else None)
        entry['rows'][source].pop('creation_saved',None)
        entry['rows'][source].pop('creation',None)
        entry['rows'][source].pop('creation_profile',None)
        entry['rows'][source].update(status='mapped', mapping_mode='manual', target=target,
                                     spec=spec, reason='선택 저장됨; Import 전 상세 정의 확인 필요' if spec.get('missing_definition') or spec.get('selection_unverified') else '수동 연결')
        binding = self.config['codebeamer'].get('field_bindings', {}).get(target_key)
        if binding: spec['binding'] = deepcopy(binding)
        self.state.save('wizard', 'plan', self.plan)

    def creation_profiles(self, scope):
        """Read-only schema validation of templates observed in this Tracker."""
        from mapping_helper import creation_templates
        from clients.tracker_fields import binding
        from clients.codebeamer_3007 import configuration_field_contract
        entry = self.plan['scopes'][scope]
        tracker = entry['tracker'] or entry.get('template_tracker')
        profiles = {kind:deepcopy(profile) for kind,profile in self.config['migration'].get('wizard', {}).get('field_types', {}).items()
                    if profile.get('source_tracker_id', tracker) == tracker}
        if getattr(self.cb, 'spec', None) is None:
            self.cb.discover()
        fields = target_fields(self.cb, tracker)
        configuration = self.cb.configuration(tracker)
        reasons = []
        template_configuration=deepcopy(configuration)
        if 'field_type_catalog' in self.cb.config.get('operations',{}):
            operation=self.cb.operation('field_type_catalog')
            if operation['method'].upper()!='GET':raise ApiError('Field type catalog must be GET')
            catalog=self.cb.call('field_type_catalog')
            if not isinstance(catalog,list) or any(not isinstance(f,dict) or type(f.get('typeId')) is not int or not f.get('typeName') for f in catalog):
                raise ApiError('Verified field type catalog requires typeId/typeName definitions')
            template_configuration['fields'].extend(catalog)
            fields=fields+catalog
            self.cb.config['verified_creation_type_ids']=[f['typeId'] for f in catalog]
            self.state.save('wizard','field-type-catalog',catalog)
        for template in creation_templates(template_configuration, fields):
            definition = template['definition']
            from mapping_helper import field_type_label
            representative = next((f for f in fields if f.get('typeId') == definition['typeId']
                                   and field_type_label(f) == template['name']), None)
            if not representative: continue
            kind, _ = semantic_type(representative)
            if not kind or not template['type_verified']: continue
            candidate_binding = binding(getattr(self.cb, 'spec', None), representative)
            if not candidate_binding:
                reasons.append(template['name'] + ': writable FieldValue schema 미확인')
            probe = dict(definition, label='Migration field contract check')
            method = 'configuration'
            try:
                configuration_field_contract(self.cb, probe, tracker)
            except ApiError as config_error:
                from mapping_helper import dedicated_field_contract
                try:
                    dedicated_field_contract(self.cb, probe, tracker)
                    method = 'dedicated'
                except ApiError as exc:
                    reasons.append(template['name'] + ': ' + str(config_error) + '; ' + str(exc))
                    continue
            if candidate_binding: candidate_binding.pop('fieldId', None)
            profile_key = creation_kind(representative) or kind
            profiles.setdefault(profile_key, {'type_name':representative.get('typeName'),
                                      'definition':definition, 'binding':candidate_binding,
                                      'creation_method':method, 'source_tracker_id':tracker,
                                      'definition_contract':'verified',
                                      'value_contract':'verified' if candidate_binding else 'unverified'})
        self.config['migration'].setdefault('wizard', {})['field_types'] = profiles
        entry['creation_profiles'] = deepcopy(profiles)
        entry['creation_diagnostics'] = reasons
        self.state.save('wizard', 'plan', self.plan)
        return profiles

    def configure_creation(self, scope, source, label, semantic, multiple, choices, reference_tracker=None):
        """Save a field proposal only. The actual server write remains in execute()."""
        entry = self.plan['scopes'][scope]
        row = entry['rows'][source]
        receipt=self.state.get(field_identity(entry.get('tracker'),source,row))
        if receipt and receipt[0]!='done':
            raise ApiError('이 필드의 이전 생성 요청이 불확실합니다. 서버 대조 후 생성 설정을 변경하세요.')
        label = label.strip()
        if not label:
            raise ApiError('신규 필드 이름을 입력하세요.')
        source_kind, source_multiple = semantic_type(entry['analysis']['fields'][source], True)
        profiles = entry.get('creation_profiles', self.config['migration'].get('wizard', {}).get('field_types', {}))
        if semantic not in profiles:
            raise ApiError('선택한 타입의 검증된 Codebeamer 생성 정의가 없습니다: ' + semantic)
        selected_profile=deepcopy(profiles[semantic])
        target_kind = {'wikitext':'text', 'integer':'number', 'decimal':'number',
                       'bool':'boolean', 'wikilink':'text'}.get(semantic, semantic)
        target_cardinality = bool(multiple)
        conversion = selected_conversion(source_kind, source_multiple, semantic, target_cardinality)
        if conversion is None and source_kind is None and semantic in ('option', 'status'):
            # Jira Security Level and some add-on fields can omit schema.type.
            # Permit an EXPLICIT Choice decision only when each observed
            # identity is covered by the user's option mapping; do not
            # silently assume that an unknown Jira field is a Choice.
            observed = source_choices(
                entry['analysis'].get('values', {}).get(source, []) +
                entry['analysis']['fields'][source].get('allowedValues', []))
            if observed and isinstance(choices, dict) and all(key in choices for key in observed):
                conversion = 'option'
        if conversion is None:
            jira_schema = entry['analysis']['fields'][source].get('schema') or {}
            raise ApiError('지원하지 않는 값 변환: Jira '+str(source_kind)+' → Codebeamer '+semantic+
                           '; Jira schema.type='+str(jira_schema.get('type'))+
                           ', schema.items='+str(jira_schema.get('items'))+
                           '. Jira Choice 메타데이터를 확인하거나 원본 값과 선택 옵션을 명시적으로 매핑하세요.')
        if source_multiple and not multiple and target_kind not in ('text',):
            raise ApiError('원본 다중 값을 단일 필드로 변환하면 데이터가 손실됩니다.')
        actual_kind = semantic_type(dict(selected_profile['definition'], typeName=selected_profile['type_name']))[0]
        if actual_kind != target_kind:
            raise ApiError('선택한 필드 타입과 서버 생성 템플릿이 일치하지 않습니다: '+semantic)
        if semantic=='reference':
            allowed=[t['id'] for t in self.plan['projects'][entry['project']]['trackers']]
            if reference_tracker not in allowed:raise ApiError('선택한 프로젝트의 실제 참조 대상 Tracker를 선택하세요.')
            template=selected_profile.get('reference_template')
            if not isinstance(template,dict):raise ApiError('Reference 생성 요청의 reference_template 계약이 없습니다. 기존 Reference 필드 연결은 가능합니다.')
            from clients.codebeamer_client import render
            selected_profile['definition']['reference']=render(template,{'targetTrackerId':reference_tracker})
            selected_profile['creation_method']='configuration'
        options = {}
        if semantic in ('option', 'status'):
            if not isinstance(choices, dict) or not choices or any(not str(v).strip() for v in choices.values()):
                raise ApiError('Choice 필드는 원본 값에 대응하는 선택 옵션이 필요합니다.')
            options = {str(k):str(v).strip() for k,v in choices.items()}
        elif choices:
            raise ApiError('Choice가 아닌 필드에는 선택 옵션을 설정할 수 없습니다.')
        if receipt and receipt[0]=='done':
            row['creation_token']=hashlib.sha256(json.dumps([label,semantic,multiple,options],sort_keys=True).encode()).hexdigest()[:16]
        row.pop('target',None)
        row.update(status='create',spec=None,mapping_mode='create', creation_saved=True, creation_profile=selected_profile, reason='Import 승인 후 생성', creation={
            'label':label, 'semantic_type':target_kind, 'profile_key':semantic,
            'conversion':conversion, 'multipleSelection':bool(multiple),
            'choices':options, **({'reference_tracker_id':reference_tracker} if semantic=='reference' else {})})
        self.state.save('wizard', 'plan', self.plan)
        row['creation_stage']='PLANNED'
        self.state.field_phase(scope+':'+source,'PLANNED',field_id=None,plan=deepcopy(row['creation']))
        return deepcopy(row['creation'])

    def errors(self):
        errors = []
        if not self.plan['projects']:
            errors.append('프로젝트를 선택하세요.')
        if not self.plan['scopes']:
            errors.append('이관할 Jira Issue Type과 Tracker를 선택하세요.')
        if self.plan['scopes'] and not any(e.get('analysis', {}).get('issue_count', 0) for e in self.plan['scopes'].values()):
            errors.append('이관할 실제 Issue가 없습니다.')
        # Only explicitly mapped Tracker scopes are Import candidates.
        # Jira project issue_types() is a catalog, not a user selection.
        for scope, entry in self.plan['scopes'].items():
            if 'analysis' not in entry:
                errors.append(scope + ': 분석 필요')
                continue
            if entry.get('source_only'):
                continue
            if entry['analysis']['risks'] or entry.get('target_risks'):
                errors.append(scope + ': Hidden 필드에 실제 값이 있어 누락 위험 해결 필요')
            for source, row in entry['rows'].items():
                if not row.get('selected', True):
                    continue
                spec = row.get('spec') or {}
                if spec.get('missing_definition') or spec.get('selection_unverified'):
                    errors.append(scope+':'+source+': 선택한 Field ID '+str(spec.get('field_id'))+
                                  ' 상세 정의 미확인: '+str(spec.get('missing_definition',spec.get('selection_unverified'))))
                if row.get('target', {}).get('visibility_unknown'):
                    errors.append(scope + ':' + source + ': CB 대상 Hidden 여부 미확인: Field Detail 권한/응답 확인 필요')
                if row['status'] not in ('mapped', 'create'):
                    errors.append(scope + ':' + source + ': ' + row['reason'])
            keys = [row['spec']['target'] for row in entry['rows'].values()
                    if row.get('spec') and row.get('selected', True)]
            if len(keys) != len(set(keys)):
                errors.append(scope + ': 중복 대상 payload 키')
        return errors

    def create_field(self, scope, source, row, tracker):
        phase_key=scope+':'+source
        try:
            return self._create_field(scope,source,row,tracker)
        except (ApiError,ValueError,KeyError,TypeError) as exc:
            previous=self.state.get('field-phase:'+phase_key)
            phase=previous[0] if previous else 'PLANNED'
            receipt=self.state.get(field_identity(tracker,source,row))
            if receipt and receipt[0]=='pending':phase='RECONCILIATION_REQUIRED'
            status=('RECONCILIATION_REQUIRED' if phase in ('CREATE_PENDING','RECONCILIATION_REQUIRED')
                    else 'VALUE_MAPPING_PENDING' if phase in ('CREATED','VALUE_MAPPING_PENDING') else 'FAILED')
            row['creation_stage']=status
            self.state.field_phase(phase_key,status,error=str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
            self.state.save('wizard','plan',self.plan)
            raise

    def _create_field(self, scope, source, row, tracker):
        from mapping_helper import create_dedicated_field, dedicated_field_contract, create_confirmed_field
        proposal = row['creation']
        profile = row.get('creation_profile') or self.config['migration'].get('wizard', {}).get('field_types', {}).get(proposal.get('profile_key', proposal['semantic_type']))
        if not profile:
            raise ApiError('Configure a verified wizard.field_types template for ' + proposal['semantic_type'])
        entry = self.plan['scopes'][scope]
        template_tracker = entry.get('template_tracker') if entry.get('tracker') is None and entry.get('new_tracker') else tracker
        if profile.get('source_tracker_id', tracker) not in (tracker, template_tracker):
            raise ApiError('Field creation template belongs to another Tracker; analyze the selected Tracker again')
        definition = deepcopy(profile['definition'])
        definition.update(label=proposal['label'], multipleSelection=proposal['multipleSelection'])
        if proposal['semantic_type'] in ('option', 'status'):
            definition['options'] = [{'name': label} for label in dict.fromkeys(proposal['choices'].values())]
        if not compatible(proposal['semantic_type'], proposal['multipleSelection'], dict(definition, typeName=profile['type_name'])):
            raise ApiError('Creation template changes source type')
        identity = field_identity(tracker,source,row)
        known = self.state.get(identity)
        fields = self.cb.fields(tracker)
        matches = [f for f in fields if f.get('label', f.get('name')) == proposal['label']]
        if known:
            if len(matches) != 1:
                raise ApiError('Uncertain field creation: reconcile server state')
            created = matches[0]
            if known[0] != 'done':
                journal_path=self.state.root/('field-'+hashlib.sha256(identity.encode()).hexdigest()+'.json')
                journal=json.loads(journal_path.read_text()) if journal_path.exists() else {}
                receipt=next((v for v in journal.values() if v.get('tracker')==tracker),None)
                if not receipt:
                    raise ApiError('Pending request has no POST evidence; reconciliation required, no repost')
                request_body=receipt.get('request',{}).get('body')
                baseline=request_body['fields'][:-1] if request_body else receipt.get('before_fields')
                current=self.cb.configuration(tracker) if request_body else {'fields':fields}
                if baseline is None:raise ApiError('Pending request baseline unavailable; no repost')
                for old in baseline:
                    same=next((f for f in current['fields'] if field_identifier(f)==field_identifier(old)),None)
                    if same is None or any(same.get(k)!=v for k,v in old.items()):
                        raise ApiError('Pending field reconciliation: existing configuration changed')
                if request_body and any(current.get(k)!=v for k,v in request_body.items() if k!='fields'):
                    raise ApiError('Pending field reconciliation: Tracker settings changed')
                for key in ('typeId','mandatory','multipleSelection','permission','reference'):
                    if key in definition and created.get(key)!=definition[key]:
                        raise ApiError('Pending field definition mismatch: '+key)
                if definition.get('options') is not None:
                    options=created.get('options')
                    if not isinstance(options,list) or len(options)!=len(definition['options']) or any(
                        any(actual_option.get(k)!=v for k,v in planned.items())
                        for planned,actual_option in zip(definition['options'],options)):
                        raise ApiError('Pending field Choice definition mismatch')
        else:
            if matches:
                raise ApiError('Field appeared after analysis; analyze again before reusing it')
            journal = self.state.root / ('field-' + hashlib.sha256(identity.encode()).hexdigest() + '.json')
            try:
                if profile.get('creation_method') == 'configuration':
                    raise ApiError('Use verified Configuration creation contract')
                dedicated_field_contract(self.cb,definition,tracker)
                direct = True
            except ApiError:
                from clients.codebeamer_3007 import configuration_field_contract
                expected = configuration_field_contract(self.cb,definition,tracker)
                direct = False
            self.state.field_phase(scope+':'+source,'VALIDATED',tracker_id=tracker,definition=definition)
            row['creation_stage']='VALIDATED'
            def before_post():
                self.state.mark(identity,'pending',None)
                self.state.field_phase(scope+':'+source,'CREATE_PENDING',tracker_id=tracker)
                row['creation_stage']='CREATE_PENDING'
            self.state.save('field-creation', identity+'-request', {'tracker_id':tracker,
                'definition':definition, 'method':'POST',
                'path':'dedicated-field-contract' if direct else '/v3/tracker/configuration'})
            try:
                if direct:
                    created, response = create_dedicated_field(self.cb,tracker,definition,journal,before_post=before_post)
                else:
                    created, response = create_confirmed_field(self.cb,tracker,definition,expected,journal,approved=True,before_post=before_post,allow_reference=proposal['semantic_type']=='reference')
                self.state.save('field-creation', identity+'-readback', {'field':created,'configuration':response})
            except ApiError as exc:
                self.state.save('field-creation', identity+'-failure', {'tracker_id':tracker,
                    'stage':'definition-create-or-readback', 'error':str(exc), 'retry':'reconcile-before-repost'})
                raise

        # Re-read the actual server-assigned ID and full metadata, never trust a proposed ID.
        actual = next((f for f in target_fields(self.cb, tracker) if field_identifier(f) == field_identifier(created)), None)
        if not actual or not compatible(proposal['semantic_type'], proposal['multipleSelection'], actual):
            raise ApiError('Created field readback type/multiplicity mismatch')
        if known and known[0] == 'done' and known[1]['id'] != field_identifier(actual):
            raise ApiError('Created field ID changed')
        self.state.mark(identity,'done',{'id':field_identifier(actual)})
        self.state.field_phase(scope+':'+source,'CREATED',field_id=field_identifier(actual),tracker_id=tracker,field=actual)
        row['creation_stage']='CREATED'
        self.state.field_phase(scope+':'+source,'VALUE_MAPPING_PENDING')
        mapping, unmatched = suggest_values(entry_values(self.plan['scopes'][scope], source), actual.get('options'), 'id' if source == 'components' else None)
        if proposal['semantic_type'] in ('option', 'status') and unmatched:
            raise ApiError('Created Choice values do not cover the source')
        if proposal['semantic_type'] == 'user':
            mapping = deepcopy(self.config['migration'].get('user_mapping', {}))
        key = 'jira_' + source
        source_kind, source_multiple = semantic_type(self.plan['scopes'][scope]['analysis']['fields'][source], True)
        conversion = proposal.get('conversion') or text_conversion(source_kind, source_multiple, actual)
        spec = build_spec(source, actual, conversion, key, mapping,existing=True)
        if proposal['semantic_type']=='reference':
            spec['deferred_reference']=True
            spec.pop('missing_definition',None)
        if source == 'components':
            spec['source_identity'] = 'id'
            spec['source_components'] = source_choices(entry_values(self.plan['scopes'][scope], source), 'id')
            self.state.save('component-mapping', scope, {'tracker_id':tracker, 'field_id':field_identifier(actual),
                'components':spec['source_components'], 'option_mapping':mapping, 'multiple':proposal['multipleSelection']})
        # The discriminator/value member come from a deployment-verified template.
        binding = deepcopy(profile.get('binding'))
        if binding:
            binding['fieldId'] = field_identifier(actual)
            self.cb.config.setdefault('field_bindings', {})[key] = binding
            spec['binding'] = binding
        elif not spec.get('deferred_reference'):
            spec['value_contract_unverified'] = True
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
            from clients.tracker_fields import binding
            from clients.codebeamer_payloads import native_field_property
            for entry in self.plan['scopes'].values():
                for source, row in entry.get('rows', {}).items():
                    if not row.get('selected', True):
                        continue
                    selected = row.get('spec')
                    if not selected or selected.get('deferred_reference'):
                        continue
                    if not (selected['target'].startswith('cb_field_') or selected.get('value_contract_unverified')):
                        continue
                    target = row.get('target') or {}
                    # A built-in Jira/CB field should use the native Item
                    # property ONLY when the current create/update Swagger
                    # contract explicitly exposes that property. Field ID
                    # alone is never sufficient to infer a writable model.
                    try:
                        update_op=self.cb.operation('item_update')
                        update_api=self.cb.spec['paths'][update_op['path']][update_op['method'].lower()]
                    except (ApiError, KeyError, TypeError):
                        update_api=None
                    native = native_field_property(self.cb.spec, api, source, target, update_api)
                    if native:
                        selected['target'] = native
                        selected.pop('binding', None)
                        selected.pop('value_contract_unverified', None)
                        self.state.save('wizard-native-binding', entry['project']+':'+str(entry['type']['id'])+':'+source,
                                        {'field_id':selected['field_id'],'property':native})
                        continue
                    confirmed = selected.get('binding') or binding(self.cb.spec, target)
                    if not confirmed:
                        name = target.get('label',target.get('name',source))
                        model = target.get('valueModel','not provided by field detail')
                        raise ApiError('Codebeamer Field '+str(selected['field_id'])+' ('+str(name)+
                                       '): native Item property not found and writable FieldValue schema unverified. '+
                                       'valueModel='+str(model)+'. Check actual Item POST and Field Detail GET; '+
                                       'no target write attempted.')
                    selected['binding'] = confirmed
                    selected.pop('value_contract_unverified', None)
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

    def visibility_review(self):
        return {scope: entry['analysis'] for scope, entry in self.plan['scopes'].items()
                if entry.get('analysis', {}).get('visibility_unknown') or
                entry.get('analysis', {}).get('configuration_status') == 'unavailable'}

    def execute(self, approved=False, visibility_review=None):
        self.cb.write = False
        if approved is not True:
            raise ApiError('Explicit Import approval is required')
        if self.visibility_review() and visibility_review != self.visibility_review():
            raise ApiError('표시 상태 미확인: 현재 분석 범위와 누락 가능성을 검토하고 명시적으로 확인하세요.')
        policy = self.config['migration'].get('full_xray', {})
        execution_type = policy.get('issue_types', {}).get('execution', 'Test Execution')
        context_scopes = {scope for scope, entry in self.plan['scopes'].items()
                          if not policy.get('create_execution_items', False)
                          and str(execution_type) in (str(entry['type']['id']), entry['type']['name'])}
        if context_scopes and self.config['migration'].get('mode') != 'full-xray':
            raise ApiError('Source-only Test Execution requires full-xray mode and verified Run context mapping; no target write attempted')
        from migration.system_fields import verify_reference_values
        for entry in self.plan['scopes'].values():
            for row in entry.get('rows', {}).values():
                spec = row.get('spec') or {}
                if row.get('selected', True) and spec.get('reference_tracker_id'):
                    verify_reference_values(self.cb, spec)
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
        # The UI is opt-in: only explicitly checked fields may be written.
        # An unselected Jira field is intentionally outside import scope.
        settings['wizard']['selected_fields_only'] = True
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
            if checked_tracker:
                actual_fields=target_fields(self.cb,checked_tracker)
                prior_ids={field_identifier(f) for f in entry['target_fields']}
                resumable_names={r['creation']['label'] for source,r in entry.get('rows',{}).items()
                    if r.get('creation') and self.state.get(field_identity(checked_tracker,source,r))}
                baseline=[f for f in actual_fields if field_identifier(f) in prior_ids or f.get('label',f.get('name')) not in resumable_names]
                if baseline != entry['target_fields']:
                    raise ApiError('Target configuration changed; analyze again: ' + scope)

        from migration.preservation import preflight
        selected_plan=deepcopy(self.plan)
        settings['wizard']['excluded_fields']={}
        for scope,entry in selected_plan['scopes'].items():
            settings['wizard']['excluded_fields'][scope]=[k for k,r in entry['rows'].items() if not r.get('selected',True)]
            entry['rows']={k:r for k,r in entry['rows'].items() if r.get('selected',True)}
            if 'summary' in settings['wizard']['excluded_fields'][scope]:
                raise ApiError(scope+': Summary는 Item 이름에 필요한 필수 필드입니다.')
        preflight(self.cb, settings, selected_plan)
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
                scope = project + ':' + str(issue['fields']['issuetype']['id'])
                entry = self.plan['scopes'].get(scope)
                if entry is None:
                    # Jira returns every Issue Type in the project. Unselected
                    # types must not become Import errors or target objects.
                    continue
                from migration.preservation import collect_r4j
                collect_r4j(jira, issue, settings, self.state)
                # A type-scoped user selection is authoritative: fields not in
                # the selected mapping (including excluded/unconfigured fields)
                # must not block Item Import or become new target fields.
                # Preserve the source bundle for audit without writing them.
                ignored = {f for f, v in issue['fields'].items()
                           if f not in entry['rows'] or not entry['rows'][f].get('selected', True)}
                selected_fields = {f for f,r in entry['rows'].items() if r.get('selected', True)}
                required_missing = {f for f in selected_fields if f not in issue['fields']
                                    and not entry.get('source_only')}
                if required_missing:
                    raise ApiError('Selected source fields absent from Issue '+issue['key']+': '+
                                   ', '.join(sorted(required_missing)))
                if ignored:
                    self.state.save('wizard-excluded-fields', issue['key'],
                                    {'scope':scope,'fields':sorted(ignored)})
                # The original bundle remains intact for History/attachments/Xray
                # source preservation. The migration field_mapping below is
                # restricted to explicitly selected rows.
                from migration.preservation import rewrite_description
                files = issue['fields'].get('attachment', [])
                rewrite_description(issue['fields'].get('description'), files,
                                    {str(a['id']): 'https://target.invalid/' + str(a['id']) for a in files})
                from migration.xray_review import issue_kind
                collect_runs = settings.get('mode') != 'full-xray' or issue_kind(issue['fields']['issuetype'], settings) == 'execution'
                runs[issue['key']] = xray.runs(issue, run_field) if collect_runs else []
                if config['jira'].get('single_issue_key') and runs[issue['key']]:
                    raise ApiError('Single-Issue live test cannot write Xray Runs into a separately configured Tracker; dedicated Run/dependency scope is required')
                bundles[issue['key']] = issue
        from migration.xray_run_collections import prepare
        for source_runs in runs.values():
            for source_run in source_runs:
                prepare(self.cb, settings, source_run)
        from migration.relation_mapping import issue_references, verify_reference
        identities={identity:issue for issue in bundles.values() for identity in (str(issue['id']),issue['key'])}
        for issue in bundles.values():
            scope=issue['fields']['project']['key']+':'+str(issue['fields']['issuetype']['id'])
            for source,row in self.plan['scopes'][scope]['rows'].items():
                spec=row.get('spec') or {}
                if not spec.get('deferred_reference'):continue
                for identity in issue_references(issue['fields'].get(source)):
                    other=identities.get(identity)
                    if other is None:raise ApiError('Issue Picker target outside selected scope: '+identity)
                    target_scope=other['fields']['project']['key']+':'+str(other['fields']['issuetype']['id'])
                    metadata=verify_reference(self.cb,{'tracker_id':self.plan['scopes'][scope]['tracker'],
                        'field_id':spec['field_id'],'target_tracker_id':self.plan['scopes'][target_scope]['tracker']})
                    if metadata['multiple'] != spec.get('expected_multiple'):
                        raise ApiError('Reference cardinality differs from selected Field Detail')
        original_bundle = jira.bundle
        jira.bundle = lambda key: deepcopy(bundles[key]) if key in bundles else original_bundle(key)
        if visibility_review:
            self.state.save('wizard', 'visibility-review', visibility_review)
        self.cb.write = True
        try:
            for scope, entry in self.plan['scopes'].items():
                if scope in context_scopes:
                    continue  # Execution fields/Tracker are not destination objects.
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
                    if not row.get('selected',True):continue
                    spec=row.get('spec') or {}
                    if spec.get('missing_definition') or spec.get('selection_unverified'):
                        errors.append(scope+':'+source+': 선택한 Field ID '+str(spec.get('field_id'))+' 상세 정의 미확인: '+str(spec.get('missing_definition',spec.get('selection_unverified'))))
                    if row.get('target', {}).get('visibility_unknown'):
                        errors.append(scope + ':' + source + ': CB 대상 Hidden 여부 미확인: Field Detail 권한/응답 확인 필요')
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
                if scope in context_scopes:
                    continue
                entry['tracker'] = settings['tracker_mapping'][scope]
                entry['new_tracker'] = None
                entry['target_fields'] = target_fields(self.cb, entry['tracker'])
                for source, row in entry['rows'].items():
                    if not row.get('selected',True):continue
                    spec = settings['field_mapping'][scope][source]
                    actual = next(f for f in entry['target_fields'] if field_identifier(f) == spec['field_id'])
                    row.update(status='mapped', spec=deepcopy(spec), target=actual)
            self.state.save('wizard', 'plan', self.plan)
            unresolved = [scope+':'+source for scope,mapping in settings['field_mapping'].items()
                          for source,spec in mapping.items() if spec.get('value_contract_unverified')]
            if unresolved:
                report = {'complete':False, 'mode':'apply', 'issues':[],
                          'errors':[{'source':name, 'error':'필드 정의 생성·ID 재조회 완료; FieldValue 쓰기 계약 미확인으로 Item 값 이관 차단'} for name in unresolved]}
                self.stage('import',report)
                self.state.save('reports','latest',report)
                (self.state.root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                return report
            xray.runs = lambda issue, field: deepcopy(runs[issue['key']])
            report = Engine(jira, xray, self.cb,
                            self.state, settings, True).migrate()
            self.config['migration']['wizard']['association_types']=deepcopy(settings['wizard'].get('association_types',{}))
            for scope,entry in self.plan['scopes'].items():
                for source,row in entry['rows'].items():
                    if row.get('creation_stage') and report.get('complete'):
                        row['creation_stage']='COMPLETED'
                        self.state.field_phase(scope+':'+source,'COMPLETED',item_ids=[i.get('target_id') for i in report['issues'] if i.get('target_id')])
            self.state.save('wizard','plan',self.plan)
            self.stage('import', report)
            return report
        finally:
            self.cb.write = False


def entry_values(entry, source):
    return list(entry['analysis']['values'].get(source, [])) + list(entry['analysis']['fields'][source].get('allowedValues', []))
