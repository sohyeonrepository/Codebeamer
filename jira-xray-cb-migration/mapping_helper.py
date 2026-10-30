"""Local mapping editor. Reads by default; field creation requires explicit approval."""
import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import sys
import traceback
import uuid
from copy import deepcopy
from itertools import islice
import json
from pathlib import Path
import queue
import threading

from clients.http import Http
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient
from clients.codebeamer_client import CodebeamerClient
from clients.http import ApiError
from migration.fields import convert


_diagnostics = None


def field_identifier(field):
    return next((field[key] for key in ('id','referenceId','fieldId') if field.get(key) is not None), None)


class Diagnostics:
    """Bounded shareable summary; no exception messages, URLs, values or locals."""
    def __init__(self, directory):
        self.root = Path(directory) / ('mapping-helper-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.failures = []
        self.latest = '시작'
        self.count = 0
        self.profiles = []
        self.record('프로그램 시작', 'ok')

    def record(self, stage, outcome, error=None, result=None):
        event = {'time': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'outcome': outcome}
        if result is not None:
            event['shape'] = type(result).__name__
            if isinstance(result, (list, dict)):
                event['count'] = len(result)
        if error is not None:
            event['error_type'] = type(error).__name__
            # HTTP status is the only part taken from exception text.
            match = re.search(r'HTTP (\d{3})\b', str(error))
            if match:
                event['http_status'] = int(match[1])
            event['frames'] = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                               for f in traceback.extract_tb(error.__traceback__)[-8:]]
        with self.lock:
            with (self.root / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.count += 1
            self.latest = stage + ' / ' + outcome
            if error is not None:
                text = stage + ' / ' + type(error).__name__
                if 'http_status' in event:
                    text += ' / HTTP ' + str(event['http_status'])
                self.failures.append(text)
            lines = ['Mapping helper diagnostic summary v1', 'Python: ' + sys.version.split()[0] + ' / ' + sys.platform,
                     'Events: ' + str(self.count), 'Latest: ' + self.latest]
            lines.extend(self.profiles[-2:])
            lines.extend(self.failures[-4:])
            if not self.failures:
                lines.append('Recorded errors: 0')
            (self.root / 'summary.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def tracker_profile(self, tracker):
        fields=tracker.get('fields',[])
        config=tracker.get('configuration',{})
        configured=config.get('fields',[]) if isinstance(config.get('fields'),list) else []
        profile=('Tracker '+str(tracker['id'])+': config_fields='+str(len(configured))+
                 ', api_fields='+str(len(fields))+
                 ', api_types='+str(sum('typeId' in f or 'type' in f for f in fields))+
                 ', typeIds='+str(sum('typeId' in f for f in configured))+
                 ', permissions='+str(sum(isinstance(f.get('permission'),dict) for f in configured))+
                 ', templates='+str(len(creation_templates(config)))+
                 ', trackerId_match='+str(config.get('basicInformation',{}).get('trackerId')==tracker['id']))
        self.profiles=[profile]
        self.record('새 필드 생성 준비 상태','info')


def read_stage(label, action, diagnostics=None):
    diagnostics = diagnostics or _diagnostics
    if diagnostics:
        diagnostics.record(label, 'start')
    try:
        result = action()
        if diagnostics:
            diagnostics.record(label, 'ok', result=result)
        return result
    except Exception as exc:
        if diagnostics:
            diagnostics.record(label, 'error', error=exc)
        if isinstance(exc, ApiError):
            raise ApiError(label + ': ' + str(exc)) from None
        raise


def collect_catalog(jira, cb, trackers, sample_limit=20):
    if sample_limit < 1 or sample_limit > 1000:
        raise ValueError('sample_limit must be between 1 and 1000')
    definitions = read_stage('Jira 필드 목록', jira.fields)
    projects = []
    warnings = []
    for project in read_stage('Jira 프로젝트 목록', jira.projects):
        config = dict(jira.config)
        config.pop('execution_issue_types', None)
        reader = JiraClient(jira.http, config)
        samples = read_stage('Jira 프로젝트 이슈 표본 검색', lambda: list(islice(reader.issues(project['key']), sample_limit)))
        types = {}
        run_samples = {}
        for issue in samples:
            issue_type = issue['fields']['issuetype']
            type_id = str(issue_type['id'])
            entry = types.setdefault(type_id, {'id': type_id, 'name': issue_type['name'], 'fields': {}})
            for key, value in issue['fields'].items():
                values = entry['fields'].setdefault(key, [])
                if value not in values and len(values) < 5:
                    values.append(value)
            relationship = XrayClient.relationship_field(issue, config.get('xray_run_field'))
            if relationship:
                xray = XrayClient(jira.http, dict(config, history_source='mapping-sample', collect_run_events=False))
                for run in read_stage('Xray Run 표본 상세 조회', lambda: xray.runs(issue, relationship)):
                    for key, value in run['data'].items():
                        values = run_samples.setdefault(key, [])
                        if value not in values and len(values) < 5:
                            values.append(value)
                for key in ('_testItem', '_executionItem', '_sourceReferences', '_executionSource', '_executionHistoryState', '_sourceEvent'):
                    run_samples.setdefault(key, [])
        if jira.http is not None:
            for entry in types.values():
                representative = next(issue for issue in samples if str(issue['fields']['issuetype']['id']) == entry['id'])
                try:
                    from clients.jira_client import segment
                    metadata = read_stage('Jira 유형별 편집 화면 필드', lambda: jira.http.request('GET', '/rest/api/2/issue/' + segment(representative['key']) + '/editmeta'))
                    if not isinstance(metadata.get('fields'), dict):
                        raise ValueError('Missing edit metadata fields')
                    entry['screen_fields'] = list(metadata['fields'])
                    entry['definitions'] = metadata['fields']
                    for key in metadata['fields']:
                        entry['fields'].setdefault(key, [])
                except (ApiError, ValueError):
                    warnings.append('Jira 편집 화면 필드 확인 불가: 값이 있는 필드와 기본 필드로 표시합니다.')
        if run_samples:
            types['__run__'] = {'id': '__run__', 'name': '개별 Xray Run (내부 메타 필드 포함)', 'fields': run_samples}
        projects.append({'key': project['key'], 'types': list(types.values()), 'sample_count': len(samples)})
    targets = []
    for tracker in trackers:
        try:
            fields = read_stage('Codebeamer 트래커 ' + str(tracker) + ' 필드 목록', lambda: cb.fields(tracker))
        except ApiError as exc:
            warnings.append(str(exc))
            continue
        try:
            configuration = read_stage('Codebeamer 트래커 ' + str(tracker) + ' 설정', lambda: cb.configuration(tracker))
        except ApiError as exc:
            warnings.append(str(exc))
            configuration = {'fields': []}
        if not isinstance(fields, list) or not isinstance(configuration.get('fields'), list):
            raise ValueError('Expected full Codebeamer fields arrays')
        configured = {str(field_identifier(f)): f for f in configuration['fields']}
        merged = [dict(configured.get(str(field_identifier(f)), {}), **f) for f in fields]
        targets.append({'id': tracker, 'name': configuration.get('name', configuration.get('basicInformation', {}).get('name', '')), 'fields': merged, 'configuration': configuration})
    if not targets:
        raise ApiError('대상 트래커 조회 실패\n' + '\n'.join(warnings))
    return {'warnings': warnings, 'definitions': definitions, 'projects': projects, 'targets': targets,
            'sample_limit': sample_limit, 'values_complete': False}


def apply_overlay(config, overlay):
    """Merge only mapping data; never accept server/auth/runtime changes."""
    if set(overlay) != {'migration'} or not isinstance(overlay['migration'], dict):
        raise ValueError('매핑 파일에는 migration 객체만 허용됩니다.')
    allowed = {'field_mapping', 'tracker_mapping', 'run_adapter', 'xray_run_tracker'}
    migration = overlay['migration']
    if set(migration) - allowed:
        raise ValueError('매핑 파일에 허용되지 않은 설정이 있습니다.')
    result = deepcopy(config)
    for section in ('field_mapping', 'tracker_mapping'):
        if section in migration:
            if not isinstance(migration[section], dict):
                raise ValueError(section + ' must be an object')
            destination = result['migration'].setdefault(section, {})
            if section == 'field_mapping':
                for scope, fields in migration[section].items():
                    if not isinstance(fields, dict):
                        raise ValueError('field_mapping scopes must be objects')
                    destination.setdefault(scope, {}).update(deepcopy(fields))
            else:
                destination.update(deepcopy(migration[section]))
    if 'run_adapter' in migration:
        if set(migration['run_adapter']) != {'fields'} or not isinstance(migration['run_adapter']['fields'], dict):
            raise ValueError('Only run_adapter.fields is allowed')
        result['migration'].setdefault('run_adapter', {}).setdefault('fields', {}).update(deepcopy(migration['run_adapter']['fields']))
    if 'xray_run_tracker' in migration:
        tracker = migration['xray_run_tracker']
        if type(tracker) is not int or tracker <= 0:
            raise ValueError('Invalid Run tracker ID')
        result['migration']['xray_run_tracker'] = tracker
    return result


def build_spec(source, target, conversion, target_key, values):
    if not target_key.strip():
        raise ValueError('대상 payload 키를 입력하세요.')
    if not isinstance(values, dict):
        raise ValueError('값 매핑은 JSON 객체여야 합니다.')
    if conversion not in ('text', 'json', 'raw', 'number', 'boolean', 'date', 'datetime', 'user', 'status', 'option', 'reference'):
        raise ValueError('지원하지 않는 변환입니다.')
    if conversion in ('user', 'status', 'option', 'reference') and not values:
        raise ValueError('선택지/사용자/참조 변환은 값 매핑이 필요합니다.')
    field_id = field_identifier(target)
    if field_id is None:
        raise ValueError('대상 API 필드 ID가 없습니다.')
    required = ('typeId', 'mandatory', 'multipleSelection', 'options', 'reference')
    if any(k not in target for k in required):
        raise ValueError('대상 정의에 타입/필수/다중/선택지/참조 정보가 누락됐습니다. 실제 API 정의를 확인하세요.')
    return {'target': target_key.strip(), 'type': conversion, 'field_id': field_id,
            'expected_type_id': target['typeId'], 'expected_mandatory': target['mandatory'],
            'expected_multiple': target['multipleSelection'], 'expected_options': target['options'],
            'expected_reference': target['reference'], 'value_mapping': deepcopy(values)}


def validate_overlay(overlay, catalog):
    errors = list(catalog.get('warnings', []))
    if not any(p['types'] for p in catalog['projects']):
        errors.append('조회된 원본 유형이 없습니다. 프로젝트/표본 범위를 확인하세요.')
    for project in catalog['projects']:
        for kind in project['types']:
            key = project['key'] + ':' + kind['id']
            is_run = kind['id'] == '__run__'
            mappings = (overlay.get('migration', {}).get('run_adapter', {}).get('fields', {}) if is_run else
                        overlay.get('migration', {}).get('field_mapping', {}).get(key, {}))
            for source in kind['fields']:
                if source.startswith('_'):
                    continue  # Metadata depends on chosen migration/history mode.
                if source not in mappings and source not in ('attachment', 'comment', 'worklog', 'issuelinks'):
                    errors.append(key + ': 원본 필드 미매핑: ' + source)
            for source, spec in mappings.items():
                for sample in kind['fields'].get(source, []):
                    try:
                        convert(sample, spec['type'], spec.get('value_mapping', {}))
                    except (ApiError, ValueError, TypeError):
                        errors.append(key + ': 표본 값 변환/값 매핑 오류: ' + source)
                        break
            targets = [m['target'] for m in mappings.values()]
            if len(targets) != len(set(targets)):
                errors.append(key + ': 대상 payload 키 중복')
            tracker = (overlay.get('migration', {}).get('xray_run_tracker') if is_run else
                       overlay.get('migration', {}).get('tracker_mapping', {}).get(key))
            destination = next((t for t in catalog['targets'] if t['id'] == tracker), None)
            if not destination:
                errors.append(key + ': 대상 트래커 미선택')
                continue
            mapped_ids = {str(m['field_id']) for m in mappings.values()}
            for field in destination['fields']:
                if field.get('mandatory') and str(field_identifier(field)) not in mapped_ids:
                    errors.append(key + ': 필수 대상 필드 미매핑: ' + str(field.get('label', field.get('name'))))
    return errors


def normalized_label(value):
    return re.sub(r'[^a-z0-9가-힣]', '', str(value).casefold())


ALIASES = {
    'summary': 'name', 'title': 'name', '제목': 'name', '이름': 'name',
    '설명': 'description', '우선순위': 'priority', 'namedpriority': 'priority',
    '상태': 'status', '결과': 'result', 'resolutions': 'result',
    'assignee': 'assignedto', '담당자': 'assignedto',
    'reporter': 'submitter', 'submittedby': 'submitter',
    'created': 'submittedat', 'updated': 'modifiedat',
    'executedby': 'runby', 'finishedon': 'closedat', 'completedat': 'closedat',
}


def label_tokens(field_id, definition):
    labels = [field_id, definition.get('name', ''), definition.get('label', '')]
    return {ALIASES.get(normalized_label(x), normalized_label(x)) for x in labels if x}


def existing_overlay(config, saved=None):
    migration = config.get('migration', {})
    result = {'migration': {key: deepcopy(migration.get(key, {})) for key in ('field_mapping', 'tracker_mapping')}}
    if migration.get('run_adapter', {}).get('fields'):
        result['migration']['run_adapter'] = {'fields': deepcopy(migration['run_adapter']['fields'])}
    if migration.get('xray_run_tracker'):
        result['migration']['xray_run_tracker'] = migration['xray_run_tracker']
    if saved:
        merged = apply_overlay({'migration': result['migration']}, saved)
        result = {'migration': merged['migration']}
    groups = list(result['migration']['field_mapping'].values()) + [result['migration'].get('run_adapter', {}).get('fields', {})]
    for group in groups:
        for spec in group.values():
            if not isinstance(spec, dict):
                continue
            kind = spec.get('type')
            if kind in ('user', 'status', 'option', 'reference') and 'value_mapping' not in spec:
                name = {'user': 'user_mapping', 'status': 'status_mapping'}.get(kind, 'value_mapping')
                spec['value_mapping'] = deepcopy(migration.get(name, {}))
    return result


def scope_mappings(overlay, project, kind):
    migration = overlay['migration']
    if kind['id'] == '__run__':
        return migration.get('run_adapter', {}).get('fields', {})
    mappings = migration.get('field_mapping', {})
    combined = {}
    for key in (kind['name'], kind['id'], project['key'] + ':' + kind['id']):
        combined.update(deepcopy(mappings.get(key, {})))
    return combined


def selected_tracker(overlay, project, kind):
    migration = overlay['migration']
    if kind['id'] == '__run__':
        return migration.get('xray_run_tracker')
    trackers = migration.get('tracker_mapping', {})
    return trackers.get(project['key'] + ':' + kind['id'], trackers.get(project['key'] + ':' + kind['name']))


def suggest_values(samples, options):
    """Exact unique display-name matches only; returned values still require review."""
    if not isinstance(options, list):
        return {}, []
    output, unmatched = {}, []
    flattened = [item for value in samples for item in (value if isinstance(value, list) else [value])]
    for source in flattened:
        if source is None:
            continue
        if isinstance(source, dict):
            identity = str(source.get('accountId', source.get('name', source.get('id'))))
            label = source.get('name', source.get('value', source.get('displayName', '')))
        else:
            identity, label = str(source), str(source)
        matches = [option for option in options if isinstance(option, dict) and option.get('id') is not None
                   and normalized_label(option.get('name', option.get('label', option.get('value', '')))) == normalized_label(label)]
        if len(matches) == 1 and label:
            proposed = {'id': matches[0]['id']}
            if identity in output and output[identity] != proposed:
                unmatched.append(identity)
                output.pop(identity)
            elif identity not in unmatched:
                output[identity] = proposed
        else:
            unmatched.append(identity)
    return output, sorted(set(unmatched))


def suggest_mapping(source, definition, samples, target_fields, settings, manual=False):
    tokens = label_tokens(source, definition)
    candidates = list(target_fields) if manual else [field for field in target_fields if tokens & label_tokens('', field)]
    fuzzy = False
    if not candidates and not manual:
        ranked = sorted([(max((SequenceMatcher(None, a, b).ratio() for a in tokens for b in label_tokens('', field)), default=0), index, field) for index, field in enumerate(target_fields)], key=lambda x:x[0], reverse=True)
        if ranked and ranked[0][0] >= 0.82 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
            candidates = [ranked[0][2]]
            fuzzy = True
    if len(candidates) != 1:
        return {'field': None, 'reason': '같은 이름의 대상이 여러 개: 직접 선택' if candidates else '대상 직접 선택 필요', 'ready': False}
    field = candidates[0]
    schema = definition.get('schema', {})
    kind = schema.get('type')
    aliases = tokens
    if aliases & {'submittedat', 'modifiedat', 'closedat', 'runby', 'submitter'}:
        system_note = ' · 시스템 필드 쓰기 권한 확인 필요'
    else:
        system_note = ''
    conversion = {'string': 'text', 'number': 'number', 'integer': 'number', 'boolean': 'boolean',
                  'date': 'date', 'datetime': 'datetime', 'user': 'user', 'option': 'option',
                  'priority': 'option', 'status': 'status'}.get(kind)
    if kind == 'array':
        conversion = {'option': 'option', 'user': 'user', 'version': 'option'}.get(schema.get('items'))
    if conversion is None and samples:
        nonnull = next((s for s in samples if s is not None), None)
        conversion = 'text' if isinstance(nonnull, str) else ('boolean' if isinstance(nonnull, bool) else ('number' if isinstance(nonnull, (int, float)) else None))
    fid = field_identifier(field)
    bindings = settings.get('codebeamer', {}).get('field_bindings', {})
    keys = [key for key, binding in bindings.items() if str(binding.get('fieldId')) == str(fid)]
    target_key = keys[0] if len(keys) == 1 else ''
    canonical = next(iter(tokens & label_tokens('', field) & {'name', 'description'}), None)
    if not target_key and canonical:
        target_key = canonical
    values, unmatched = suggest_values(samples, field.get('options'))
    reason = ('사용자 선택 대상' if manual else ('유사 이름 후보 · 확인 필요' if fuzzy else '이름/알려진 별칭 일치')) + system_note
    if conversion in ('option', 'status', 'user', 'reference') and unmatched:
        reason += ' · 값 확인 ' + str(len(unmatched)) + '개'
    if not target_key:
        reason += ' · API 키 확인 필요'
    ready = False
    spec = None
    try:
        if conversion:
            spec = build_spec(source, field, conversion, target_key, values)
            for sample in samples:
                convert(sample, conversion, values)
            ready = not system_note and not fuzzy
            if conversion in ('text', 'number', 'boolean', 'date', 'datetime') and field.get('options'):
                ready = False
                reason += ' · 대상은 선택형: 타입 검토 필요'
    except (ValueError, ApiError, TypeError):
        reason += ' · 타입/정의/값 확인 필요'
    return {'field': field, 'conversion': conversion or 'raw', 'target_key': target_key,
            'values': values, 'spec': spec, 'reason': reason, 'ready': ready}


def suggest_scope(kind, definitions, fields, settings, existing):
    proposals = {source: suggest_mapping(source, definitions.get(source, {}), source_observations(kind, source), fields, settings)
                 for source, samples in kind['fields'].items() if source not in existing}
    claimed = {str(spec.get('field_id')) for spec in existing.values()}
    identities = {}
    for source, proposal in proposals.items():
        field = proposal.get('field')
        if field:
            key = str(field_identifier(field))
            identities.setdefault(key, []).append(source)
    for key, sources in identities.items():
        if key in claimed or len(sources) > 1:
            for source in sources:
                proposals[source]['ready'] = False
                proposals[source]['reason'] += ' · 대상 중복: 직접 확인'
    return proposals


def auto_connect_scope(overlay, project, kind, fields, definitions, settings):
    existing=scope_mappings(overlay,project,kind)
    proposals=suggest_scope(kind,definitions,fields,settings,existing)
    applied=[]
    for source,proposal in proposals.items():
        if proposal.get('ready') and proposal.get('spec'):
            confirm_row(overlay,project,kind,source,selected_tracker(overlay,project,kind),proposal['spec'])
            applied.append(source)
    return applied


def confirm_row(overlay, project, kind, source, tracker, spec):
    migration = overlay['migration']
    previous = selected_tracker(overlay, project, kind)
    if previous is not None and previous != tracker:
        raise ValueError('이 유형의 기존 대상 트래커와 다릅니다. 기존 매핑을 보존하기 위해 변경을 중단했습니다.')
    for other, current in scope_mappings(overlay, project, kind).items():
        if other != source and (str(current.get('field_id')) == str(spec.get('field_id')) or current.get('target') == spec.get('target')):
            raise ValueError('다른 원본 필드가 같은 대상에 이미 연결되어 있습니다: ' + other)
    if kind['id'] == '__run__':
        migration.setdefault('run_adapter', {}).setdefault('fields', {})[source] = deepcopy(spec)
        migration['xray_run_tracker'] = tracker
    else:
        key = project['key'] + ':' + kind['id']
        current = deepcopy(scope_mappings(overlay, project, kind))
        current[source] = deepcopy(spec)
        migration.setdefault('field_mapping', {})[key] = current
        migration.setdefault('tracker_mapping', {})[key] = tracker

DEFAULT_FIELDS = {'summary', 'description', 'status', 'priority', 'assignee', 'reporter', 'created', 'updated', 'resolution', 'labels', 'components', 'fixVersions'}
TECHNICAL_FIELDS = {'issuetype', 'project', 'attachment', 'comment', 'worklog', 'issuelinks', 'votes', 'watches', 'progress', 'aggregateprogress', 'workratio', 'lastViewed'}


def source_observations(kind, source):
    samples = list(kind['fields'].get(source, []))
    allowed = kind.get('definitions', {}).get(source, {}).get('allowedValues')
    if isinstance(allowed, list):
        for value in allowed:
            if value not in samples:
                samples.append(value)
    return samples


def visible_sources(kind, existing, all_fields=False):
    if all_fields:
        return list(kind['fields'])
    screen = kind.get('screen_fields')
    result = []
    for source, samples in kind['fields'].items():
        if source in existing:
            result.append(source)
        elif source.startswith('_') or source in TECHNICAL_FIELDS:
            continue
        elif screen is not None:
            if source in screen or source in DEFAULT_FIELDS:
                result.append(source)
        elif source in DEFAULT_FIELDS or any(value not in (None, '', [], {}) for value in samples):
            result.append(source)
    return result


def source_choices(samples):
    choices = {}
    for sample in samples:
        for value in sample if isinstance(sample, list) else [sample]:
            if value is None:
                continue
            if isinstance(value, dict):
                identity = value.get('accountId', value.get('name', value.get('id')))
                label = value.get('name', value.get('value', value.get('displayName', identity)))
            else:
                identity, label = value, value
            if identity is not None:
                choices[str(identity)] = str(label)
    return choices


def creation_templates(configuration):
    """Minimal templates from observed field definitions; no copied ID or options."""
    templates = []
    seen = set()
    for field in configuration.get('fields', []):
        if 'typeId' not in field or not isinstance(field.get('permission'),dict):
            continue
        if field.get('reference') is not None:
            continue
        signature = json.dumps(field['typeId'],sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        # These are explicit defaults for the NEW field, not inferred old-field attributes.
        template={'typeId':deepcopy(field['typeId']), 'permission':deepcopy(field['permission']),
                  'mandatory':False,'multipleSelection':False,
                  'options':[] if isinstance(field.get('options'),list) else None,'reference':None}
        templates.append({'name': str(field.get('label', field.get('name', ''))) + '와 같은 API 타입 (' + str(field['typeId']) + ')', 'definition': template})
    return templates


def create_confirmed_field(cb, tracker, definition, expected, journal_path, approved=False):
    """Add one approved definition, preserving existing fields and uncertain writes."""
    if approved is not True:
        raise ApiError('필드 생성 확인이 필요합니다.')
    if not isinstance(definition, dict) or not isinstance(definition.get('label'), str) or not definition['label'].strip():
        raise ApiError('새 필드 이름이 필요합니다.')
    if any(key in definition for key in ('id', 'referenceId', 'fieldId')):
        raise ApiError('새 필드 ID는 서버가 부여해야 합니다.')
    required = ('typeId', 'mandatory', 'multipleSelection', 'options', 'reference', 'permission')
    if any(key not in definition for key in required) or not isinstance(definition['permission'], dict):
        raise ApiError('실제 서버의 필드 타입/권한/선택지/참조 정의가 필요합니다.')
    if type(definition['mandatory']) is not bool or type(definition['multipleSelection']) is not bool:
        raise ApiError('필수 여부와 다중 선택은 true/false여야 합니다.')
    if definition['options'] is not None and not isinstance(definition['options'], list):
        raise ApiError('선택지는 실제 API 형식의 배열이어야 합니다.')
    if definition['reference'] is not None:
        raise ApiError('참조 필드 자동 생성은 별도 검증된 참조 생성 계약이 필요합니다.')
    if not isinstance(expected.get('fields'), list) or expected.get('basicInformation', {}).get('trackerId') != tracker:
        raise ApiError('대상 트래커의 전체 설정을 조회해야 생성할 수 있습니다.')
    if not all(isinstance(f,dict) for f in expected['fields']):
        raise ApiError('기존 필드 설정의 구조가 올바르지 않습니다.')
    identities=[('id',str(field_identifier(f))) if field_identifier(f) is not None else ('label',f.get('label'),f.get('typeId')) for f in expected['fields']]
    if len(set(identities))!=len(identities) or any(key[0]=='label' and not key[1] for key in identities):
        raise ApiError('기존 필드의 고유 ID/이름을 확인할 수 없어 생성을 중단합니다.')
    if definition['typeId'] not in {f.get('typeId') for f in expected['fields']}:
        raise ApiError('서버에서 확인되지 않은 필드 타입 ID입니다.')
    if any(normalized_label(f.get('label','')) == normalized_label(definition['label']) for f in expected['fields']):
        raise ApiError('같은 이름의 필드가 이미 있습니다. 기존 필드에 매핑하세요.')
    journal_path = Path(journal_path)
    journal = json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
    import hashlib
    identity = hashlib.sha256(json.dumps([tracker, definition['label']], ensure_ascii=False).encode()).hexdigest()
    if identity in journal:
        raise ApiError('이 필드의 이전 생성 기록이 있습니다. 서버 상태를 확인한 뒤 기존 필드에 매핑하세요.')
    if cb.spec is None:
        read_stage('Codebeamer 생성 API 정의 조회', cb.discover)
    cb.operation('tracker_configuration_update')
    current = read_stage('Codebeamer 생성 전 설정 재확인', lambda: cb.configuration(tracker))
    if current != expected:
        raise ApiError('트래커 설정이 변경됐습니다. 도우미를 재실행해 다시 조회하세요.')
    body = deepcopy(expected)
    body['fields'].append(deepcopy(definition))
    body=prepare_configuration_permissions(cb,body)
    journal[identity] = {'tracker': tracker, 'status': 'pending'}
    def persist():
        temporary = journal_path.with_suffix(journal_path.suffix + '.tmp')
        temporary.write_text(json.dumps(journal, indent=2), encoding='utf-8')
        temporary.replace(journal_path)
    persist()
    previous = cb.write
    try:
        cb.write = True
        read_stage('Codebeamer 새 필드 생성', lambda: cb.call('tracker_configuration_update', {'trackerId': tracker}, body))
    finally:
        cb.write = previous
    actual = read_stage('Codebeamer 생성 후 설정 확인', lambda: cb.configuration(tracker))
    matches = [field for field in actual.get('fields', []) if field.get('label') == definition['label']]
    if len(matches) != 1 or field_identifier(matches[0]) is None:
        raise ApiError('생성 결과의 실제 필드 ID를 확인할 수 없습니다. 재생성하지 말고 서버에서 확인하세요.')
    created = dict(definition, **matches[0])
    if any(created.get(key) != definition[key] for key in required):
        raise ApiError('생성된 필드 정의가 제안과 다릅니다. 재생성하지 말고 서버에서 확인하세요.')
    for old in expected['fields']:
        field_id = field_identifier(old)
        existing = [field for field in actual.get('fields', []) if (field_identifier(field) == field_id if field_id is not None else (field.get('label')==old.get('label') and field.get('typeId')==old.get('typeId')))]
        if len(existing) != 1 or any(existing[0].get(key) != value for key, value in old.items()):
            raise ApiError('기존 필드의 보존을 확인할 수 없습니다. 서버 설정을 확인하세요.')
    journal[identity] = {'tracker': tracker, 'status': 'done', 'field_id': field_identifier(created)}
    persist()
    return created, actual

def schema_object(spec, schema, seen=()):
    if not isinstance(schema, dict):
        raise ApiError('선택지 생성 스키마가 없습니다.')
    if '$ref' in schema:
        reference=schema['$ref']
        if not reference.startswith('#/') or reference in seen:
            raise ApiError('외부/순환 스키마는 자동 생성에 사용할 수 없습니다.')
        value=spec
        for part in reference[2:].split('/'):
            value=value[part.replace('~1','/').replace('~0','~')]
        return schema_object(spec,value,seen+(reference,))
    if schema.get('oneOf') or schema.get('anyOf'):
        raise ApiError('생성 스키마가 여러 타입으로 나뉩니다. 명시적인 API 설정이 필요합니다.')
    result=deepcopy(schema)
    for base in schema.get('allOf',[]):
        resolved=schema_object(spec,base,seen)
        result.setdefault('properties',{}).update(resolved.get('properties',{}))
        result['required']=list(set(result.get('required',[]))|set(resolved.get('required',[])))
    return result


def request_schema(spec, operation):
    body=operation.get('requestBody')
    if body:
        body=schema_object(spec,body)
        media=body.get('content',{}).get('application/json')
        if media:
            return schema_object(spec,media.get('schema',{}))
    for parameter in operation.get('parameters',[]):
        parameter=schema_object(spec,parameter)
        if parameter.get('in')=='body':
            return schema_object(spec,parameter.get('schema',{}))
    raise ApiError('JSON 생성 요청 스키마가 없습니다.')


def option_payload(spec, schema, label):
    schema=schema_object(spec,schema)
    properties=schema.get('properties',{})
    names=[name for name in ('name','label','value') if name in properties and schema_object(spec,properties[name]).get('type')=='string' and not properties[name].get('readOnly')]
    if not names:
        raise ApiError('선택지 이름을 전달할 속성이 스키마에 없습니다.')
    selected=names[0]
    body={selected:label}
    for name in schema.get('required',[]):
        if name in body:continue
        definition=schema_object(spec,properties.get(name,{}))
        if definition.get('readOnly'):continue
        if name.lower() in ('id','fieldid','trackerid','referenceid'):
            raise ApiError('서버가 선택지 ID를 필수 입력으로 요구합니다. ID를 추측하지 않습니다.')
        if 'const' in definition:body[name]=deepcopy(definition['const'])
        elif len(definition.get('enum',[]))==1:body[name]=definition['enum'][0]
        elif 'default' in definition:body[name]=deepcopy(definition['default'])
        else:raise ApiError('선택지 생성의 추가 필수값을 확인해야 합니다: '+name)
    return body


def prepare_configuration_permissions(cb, body):
    """Preserve optional omissions. Never invent permissions for old fields."""
    fields=body.get('fields',[])
    if all(isinstance(field.get('permission'),dict) for field in fields):
        return body
    op=cb.operation('tracker_configuration_update')
    schema=request_schema(cb.spec,cb.spec['paths'][op['path']][op['method'].lower()])
    array=schema_object(cb.spec,schema.get('properties',{}).get('fields',{}))
    item=schema_object(cb.spec,array.get('items',{}))
    if not item.get('properties'):
        raise ApiError('Swagger에서 기존 필드의 permission 선택 여부를 확인할 수 없습니다.')
    if 'permission' in item.get('required',[]):
        raise ApiError('서버는 모든 필드에 permission을 요구하지만 조회 응답에는 일부가 없습니다. 권한을 임의로 채우지 않습니다.')
    result=deepcopy(body)
    for field in result['fields']:
        if field.get('permission') is None:
            field.pop('permission',None)
        elif not isinstance(field['permission'],dict):
            raise ApiError('일부 기존 permission이 객체형이 아닙니다. 원래 권한을 자동 변환하지 않습니다.')
    return result


def choice_contract(cb, tracker, field_id, label):
    if cb.spec is None:read_stage('Codebeamer 선택지 생성 API 조회',cb.discover)
    candidates=[]
    for path,methods in cb.spec.get('paths',{}).items():
        operation=methods.get('post')
        if not isinstance(operation,dict):continue
        params=set(re.findall(r'\{([^}]+)\}',path))
        if params != {'trackerId','fieldId'}:continue
        if not any(word in (path+' '+operation.get('operationId','')).casefold() for word in ('option','choice')):continue
        try:
            if any(parameter.get('required') and parameter.get('in') not in ('path','body') for parameter in operation.get('parameters',[])):
                continue
            body=option_payload(cb.spec,request_schema(cb.spec,operation),label)
            candidates.append(({'path':path,'method':'POST'},body,'option'))
        except ApiError:continue
    if len(candidates)==1:return candidates[0]
    if len(candidates)>1:raise ApiError('선택지 생성 API가 여러 개입니다. 자동 선택하지 않습니다.')
    # Configuration fallback is allowed only when its actual option-item schema is known.
    operation=cb.operation('tracker_configuration_update')
    definition=cb.spec['paths'][operation['path']][operation['method'].lower()]
    root=request_schema(cb.spec,definition)
    fields=schema_object(cb.spec,root.get('properties',{}).get('fields',{}))
    field=schema_object(cb.spec,fields.get('items',{}))
    options=schema_object(cb.spec,field.get('properties',{}).get('options',{}))
    if options.get('type')!='array':raise ApiError('서버 Swagger에서 선택지 추가 형식을 확인할 수 없습니다.')
    body=option_payload(cb.spec,options.get('items',{}),label)
    return operation,body,'configuration'


def matching_option(field, label):
    matches=[option for option in field.get('options') or [] if isinstance(option,dict) and option.get('id') is not None and normalized_label(option.get('name',option.get('label',option.get('value',''))))==normalized_label(label)]
    if len(matches)>1:raise ApiError('같은 이름의 대상 선택지가 여러 개입니다.')
    return matches[0] if matches else None


def create_missing_option(cb, tracker, field, label, journal_path):
    """Create only an absent named option and map to the ID read back from server."""
    existing=matching_option(field,label)
    if existing:return existing,None
    field_id=field_identifier(field)
    if type(field_id) is not int or field_id<=0:raise ApiError('선택지 대상 필드의 실제 ID가 필요합니다.')
    operation,payload,mode=choice_contract(cb,tracker,field_id,label)
    expected=({'fields':read_stage('Codebeamer 선택지 생성 전 필드 조회',lambda:cb.fields(tracker)), '_fields_only':True} if mode=='option' else read_stage('Codebeamer 선택지 생성 전 설정',lambda:cb.configuration(tracker)))
    fields=expected.get('fields')
    if not isinstance(fields,list):raise ApiError('전체 필드 설정을 확인할 수 없습니다.')
    matches=[item for item in fields if field_identifier(item)==field_id]
    if len(matches)!=1:raise ApiError('선택지 대상 필드를 설정에서 식별할 수 없습니다.')
    current=matches[0]
    if not isinstance(current.get('options'),list):raise ApiError('대상 필드가 선택지 배열을 제공하지 않습니다. 선택형 필드를 선택하세요.')
    found=matching_option(current,label)
    if found:return found,expected
    import hashlib
    journal_path=Path(journal_path)
    journal=json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
    identity=hashlib.sha256(json.dumps(['option',tracker,field_id,normalized_label(label)]).encode()).hexdigest()
    if identity in journal:raise ApiError('이 선택지의 이전 생성 기록이 있습니다. 중복 생성하지 말고 서버를 확인하세요.')
    body=payload
    if mode=='configuration':
        if expected.get('basicInformation',{}).get('trackerId')!=tracker:raise ApiError('대상 트래커 설정 ID를 확인할 수 없습니다.')
        body=deepcopy(expected)
        target=next(item for item in body['fields'] if field_identifier(item)==field_id)
        if not isinstance(target.get('options'),list):raise ApiError('기존 선택지 배열이 없습니다.')
        target['options'].append(payload)
    if mode=='configuration' and read_stage('Codebeamer 선택지 생성 전 변경 검사',lambda:cb.configuration(tracker))!=expected:
        raise ApiError('트래커 설정이 변경됐습니다. 다시 조회하세요.')
    journal[identity]={'status':'pending','tracker':tracker,'field_id':field_id}
    def persist():
        temporary=journal_path.with_suffix(journal_path.suffix+'.tmp')
        temporary.write_text(json.dumps(journal,indent=2),encoding='utf-8');temporary.replace(journal_path)
    persist()
    previous=cb.write
    previous_operation=cb.config.setdefault('operations',{}).get('helper_option_create')
    try:
        cb.write=True
        cb.config['operations']['helper_option_create']=operation
        name='helper_option_create' if mode=='option' else 'tracker_configuration_update'
        read_stage('Codebeamer 없는 선택지 생성',lambda:cb.call(name,{'trackerId':tracker,'fieldId':field_id},body))
    finally:
        cb.write=previous
        if previous_operation is None:cb.config['operations'].pop('helper_option_create',None)
        else:cb.config['operations']['helper_option_create']=previous_operation
    actual=({'fields':read_stage('Codebeamer 선택지 생성 후 필드 확인',lambda:cb.fields(tracker)), '_fields_only':True} if mode=='option' else read_stage('Codebeamer 선택지 생성 후 확인',lambda:cb.configuration(tracker)))
    actual_fields=[item for item in actual.get('fields',[]) if field_identifier(item)==field_id]
    if len(actual_fields)!=1:raise ApiError('생성 후 대상 필드를 확인할 수 없습니다.')
    created=matching_option(actual_fields[0],label)
    if not created:raise ApiError('생성 후 선택지 ID가 확인되지 않습니다. 반복 생성하지 마세요.')
    for old in fields:
        old_id=field_identifier(old)
        new=next((item for item in actual.get('fields',[]) if field_identifier(item)==old_id),None)
        if new is None:raise ApiError('기존 필드 보존을 확인할 수 없습니다.')
        for key,value in old.items():
            if old_id==field_id and key=='options':
                if any(option not in new.get('options',[]) for option in value or []):raise ApiError('기존 선택지 보존을 확인할 수 없습니다.')
            elif new.get(key)!=value:raise ApiError('기존 필드 설정이 변경되었습니다. 서버를 확인하세요.')
    journal[identity]={'status':'done','tracker':tracker,'field_id':field_id,'option_id':created['id']};persist()
    return created,actual

def dedicated_field_contract(cb, definition=None, tracker=None):
    if cb.spec is None:read_stage('Codebeamer 필드 전용 생성 API 조회',cb.discover)
    configured=cb.config.get('operations',{}).get('field_create')
    paths=[]
    if configured:
        op=cb.operation('field_create');paths.append((op['path'],op['method'].lower(),op))
    else:
        for path,methods in cb.spec.get('paths',{}).items():
            if not path.rstrip('/').casefold().endswith('/fields') or 'tracker' not in path.casefold():continue
            if isinstance(methods.get('post'),dict):paths.append((path,'post',{'path':path,'method':'POST'}))
    matches=[]
    for path,method,op in paths:
        parameters=set(re.findall(r'\{([^}]+)\}',path))
        if parameters not in ({'trackerId'},{'id'}):continue
        if method!='post':continue
        api=cb.spec['paths'][path][method]
        try:
            schema=request_schema(cb.spec,api)
            properties=schema.get('properties',{})
            if not any(key in properties for key in ('name','label')):continue
            if definition is None:
                matches.append((op,schema));continue
            label_key='label' if 'label' in properties else 'name'
            body={label_key:definition['label']}
            for name,prop in properties.items():
                prop=schema_object(cb.spec,prop)
                if prop.get('readOnly') or name in ('id','fieldId','referenceId') or name==label_key:continue
                if name in definition:
                    value=deepcopy(definition[name])
                    if prop.get('enum') and value not in prop['enum']:raise ApiError('제안한 필드 타입이 생성 API의 타입 목록과 다릅니다.')
                    body[name]=value
                elif name=='trackerId' and tracker is not None:body[name]=tracker
                elif name in schema.get('required',[]):
                    if 'const' in prop:body[name]=prop['const']
                    elif len(prop.get('enum',[]))==1:body[name]=prop['enum'][0]
                    elif 'default' in prop:body[name]=deepcopy(prop['default'])
                    else:raise ApiError('필드 생성 API의 추가 필수값이 필요합니다: '+name)
            missing=[name for name in schema.get('required',[]) if name not in body and not schema_object(cb.spec,properties.get(name,{})).get('readOnly')]
            if missing:raise ApiError('필드 생성 API에서 확인되지 않은 필수값이 있습니다.')
            matches.append((op,body))
        except ApiError:
            if configured:raise
            continue
    if len(matches)!=1:
        raise ApiError('Swagger에서 사용할 필드 전용 생성 API를 하나로 확인할 수 없습니다. 전체 설정 API가 HTTP 500이면 서버 관리자 확인이 필요합니다.')
    return matches[0]


def partial_field_templates(fields):
    templates=[];seen=set()
    for field in fields:
        if field.get('reference') is not None:continue
        observed={key:deepcopy(field[key]) for key in ('typeId','type') if key in field and isinstance(field[key],(str,int)) and not isinstance(field[key],bool)}
        if not observed:continue
        identity=json.dumps(observed,sort_keys=True)
        if identity in seen:continue
        seen.add(identity)
        draft=dict(observed,mandatory=False,multipleSelection=False,options=None,reference=None)
        if isinstance(field.get('permission'),dict):draft['permission']=deepcopy(field['permission'])
        templates.append({'name':str(field.get('label',field.get('name','필드')))+'와 같은 서버 타입 ('+str(observed.get('typeId',observed.get('type')))+')','definition':draft})
    return templates


def create_dedicated_field(cb, tracker, definition, journal_path):
    if not isinstance(definition.get('label'),str) or not definition['label'].strip():raise ApiError('새 필드 이름이 필요합니다.')
    if any(key in definition for key in ('id','fieldId','referenceId')):raise ApiError('새 필드 ID는 서버가 부여해야 합니다.')
    operation,body=dedicated_field_contract(cb,definition,tracker)
    before=read_stage('Codebeamer 전용 생성 전 필드 목록',lambda:cb.fields(tracker))
    if not isinstance(before,list):raise ApiError('필드 목록 배열을 확인할 수 없습니다.')
    if any(normalized_label(field.get('label',field.get('name','')))==normalized_label(definition['label']) for field in before):raise ApiError('같은 이름의 필드가 이미 있습니다. 기존 필드에 연결하세요.')
    import hashlib
    journal_path=Path(journal_path)
    journal=json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
    identity=hashlib.sha256(json.dumps([tracker,definition['label']],ensure_ascii=False).encode()).hexdigest()
    if identity in journal:raise ApiError('이 필드의 이전 생성 기록이 있습니다. 서버에서 확인한 뒤 기존 필드에 연결하세요.')
    journal[identity]={'status':'pending','tracker':tracker}
    def persist():
        temporary=journal_path.with_suffix(journal_path.suffix+'.tmp')
        temporary.write_text(json.dumps(journal,indent=2),encoding='utf-8');temporary.replace(journal_path)
    persist()
    previous=cb.write
    previous_op=cb.config.setdefault('operations',{}).get('helper_field_create')
    try:
        cb.write=True;cb.config['operations']['helper_field_create']=operation
        read_stage('Codebeamer 전용 API 필드 생성',lambda:cb.call('helper_field_create',{'trackerId':tracker,'id':tracker},body))
    finally:
        cb.write=previous
        if previous_op is None:cb.config['operations'].pop('helper_field_create',None)
        else:cb.config['operations']['helper_field_create']=previous_op
    fields=read_stage('Codebeamer 전용 생성 후 필드 확인',lambda:cb.fields(tracker))
    matches=[field for field in fields if normalized_label(field.get('label',field.get('name','')))==normalized_label(definition['label'])]
    if len(matches)!=1 or field_identifier(matches[0]) is None:raise ApiError('생성한 필드 ID를 확인할 수 없습니다. 반복 생성하지 마세요.')
    created=matches[0]
    for key in ('typeId','type'):
        if key in definition and key in created and created[key]!=definition[key]:raise ApiError('생성된 필드 타입이 요청과 다릅니다. 서버에서 확인하세요.')
    old_ids={str(field_identifier(field)) for field in before}
    new_ids={str(field_identifier(field)) for field in fields}
    if not old_ids<=new_ids:raise ApiError('기존 필드 보존을 확인할 수 없습니다.')
    journal[identity]={'status':'done','tracker':tracker,'field_id':field_identifier(created)};persist()
    return created,{'fields':fields,'_fields_only':True}

def main():
    parser = argparse.ArgumentParser(description='고객 PC 내부 매핑 도우미 (연결 자동 반영 / 없는 값 생성)')
    parser.add_argument('--config', default='config.local.json')
    parser.add_argument('--tracker-id', type=int, action='append', required=True)
    parser.add_argument('--sample-limit', type=int, default=20)
    parser.add_argument('--prompt-auth', action='store_true')
    parser.add_argument('--output', default='mapping.local.json')
    parser.add_argument('--log-dir', default='logs', help='Local diagnostic log directory')
    args = parser.parse_args()
    global _diagnostics
    _diagnostics = Diagnostics(args.log_dir)
    print('진단 요약: ' + str((_diagnostics.root / 'summary.txt').resolve()))
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    def http(settings):
        return Http(settings['url'], settings.get('token_env'), log=_diagnostics.root / 'requests.jsonl', auth=settings.get('auth', 'bearer'),
                    username_env=settings.get('username_env'), password_env=settings.get('password_env'),
                    allow_http=settings.get('allow_http', False), prompt_auth=args.prompt_auth)
    jira = JiraClient(http(config['jira']), config['jira'])
    cb = CodebeamerClient(http(config['codebeamer']), config['codebeamer'], False)
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    import tkinter.font as tkfont
    families=set(tkfont.families(root))
    ui_font=next((name for name in ('Malgun Gothic','맑은 고딕','Noto Sans CJK KR','unifont') if name in families),'Segoe UI')
    for name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont','TkTooltipFont','TkSmallCaptionFont'):
        try:tkfont.nametofont(name).configure(family=ui_font,size=10)
        except tk.TclError:pass
    def callback_error(error_type, error, tb):
        _diagnostics.record('GUI 콜백', 'error', error=error)
        messagebox.showerror('화면 처리 실패', '진단 요약 파일을 확인하세요:\n' + str(_diagnostics.root / 'summary.txt'))
    root.report_callback_exception = callback_error
    _diagnostics.record('GUI 창 생성', 'ok')
    root.title('Jira → Codebeamer 로컬 매핑 도우미')
    root.geometry('1080x820')
    root.minsize(900, 680)
    status = tk.StringVar(value='필드 조회 중… 연결은 자동 반영됩니다.')
    ttk.Label(root, textvariable=status).pack(fill='x')
    ttk.Label(root, text='진단 요약: ' + str(_diagnostics.root / 'summary.txt')).pack(fill='x')
    ttk.Label(root, text='값은 프로젝트별 최대 지정 건수의 표본입니다. 전체 값 목록이나 이관 준비 완료를 뜻하지 않습니다.').pack()
    results = queue.Queue()
    def fetch():
        try:
            catalog = collect_catalog(jira, cb, args.tracker_id, args.sample_limit)
            _diagnostics.record('전체 조회 완료', 'ok')
            results.put((catalog, None))
        except Exception as exc:
            _diagnostics.record('전체 조회', 'error', error=exc)
            results.put((None, type(exc).__name__ + ': ' + str(exc)))
    threading.Thread(target=fetch, daemon=True).start()
    def render(catalog):
        output = Path(args.output)
        saved = json.loads(output.read_text(encoding='utf-8-sig')) if output.exists() else None
        overlay = existing_overlay(config, saved)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TFrame', background='#f5f5f5')
        style.configure('TLabel', background='#f5f5f5', foreground='#25314b', font=(ui_font, 10))
        style.configure('Title.TLabel', font=(ui_font, 12, 'bold'))
        style.configure('Note.TLabel', foreground='#65738a', font=(ui_font, 9))
        style.configure('Treeview', rowheight=25, font=(ui_font, 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=(ui_font, 10, 'bold'), padding=7)
        style.configure('TButton', padding=(12, 7))
        body = ttk.Frame(root, padding=12); body.pack(fill='both', expand=True)
        ttk.Label(body, text='필드 동기화 설정', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='기존 매핑 유지 · 연결은 자동 반영 · 없는 선택지는 생성 후 ID로 연결', style='Note.TLabel').pack(anchor='w', pady=(4,8))
        connection=ttk.LabelFrame(body,text='연결',padding=8);connection.pack(fill='x',pady=(0,8))
        ttk.Label(connection,text='Jira 서버: '+config['jira']['url']).pack(anchor='w')
        ttk.Label(connection,text='Codebeamer 서버: '+config['codebeamer']['url']).pack(anchor='w')
        ttk.Label(connection,text='방향: Jira → Codebeamer 가져오기',style='Note.TLabel').pack(anchor='w')
        scopes = [(p,t) for p in catalog['projects'] for t in p['types']]
        for project, kind in scopes:
            if kind['id'] != '__run__':
                existing = scope_mappings(overlay, project, kind)
                if existing:
                    overlay['migration']['field_mapping'][project['key'] + ':' + kind['id']] = existing
        controls = ttk.Frame(body); controls.pack(fill='x')
        ttk.Label(controls, text='원본 프로젝트 / 유형').grid(row=0, column=0, sticky='w')
        scope_box = ttk.Combobox(controls, state='readonly', width=42,
            values=[p['key'] + ' / ' + t['name'] for p,t in scopes])
        scope_box.grid(row=1,column=0,sticky='ew',padx=(0,16))
        ttk.Label(controls, text='대상 Codebeamer 트래커').grid(row=0,column=1,sticky='w')
        target_box = ttk.Combobox(controls, state='readonly', width=30,
            values=[str(t['id']) + (' / ' + str(t['name']) if t.get('name') else '') for t in catalog['targets']])
        target_box.grid(row=1,column=1,sticky='ew')
        controls.columnconfigure(0,weight=2); controls.columnconfigure(1,weight=1)
        bar = ttk.Frame(body); bar.pack(fill='x',pady=10)
        counts = tk.StringVar()
        ttk.Label(bar,textvariable=counts).pack(side='left')
        new_only = tk.BooleanVar(value=False)
        all_fields = tk.BooleanVar(value=False)
        search = tk.StringVar()
        ttk.Entry(bar,textvariable=search,width=20).pack(side='right')
        ttk.Label(bar,text='검색  ').pack(side='right')
        frame = ttk.Frame(body); frame.pack(fill='both',expand=True)
        table = ttk.Treeview(frame, columns=('target','state'),show='tree headings',selectmode='browse',height=10)
        table.heading('#0',text='Jira 필드 / 선택지');table.column('#0',width=360,minwidth=180)
        for col,title,width in [('target','Codebeamer 대상 필드 / 선택지',360),('state','상태',140)]:
            table.heading(col,text=title);table.column(col,width=width,minwidth=80)
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
        table.tag_configure('kept',foreground='#65738a')
        table.tag_configure('ready',foreground='#176c51')
        table.tag_configure('review',foreground='#9a6611')
        editor=tk.Toplevel(root);editor.title('고급 매핑 설정');editor.withdraw()
        editor.protocol('WM_DELETE_WINDOW',editor.withdraw)
        selected_text = tk.StringVar(value='목록에서 필드를 선택하세요.')
        ttk.Label(editor,textvariable=selected_text).pack(anchor='w')
        row = ttk.Frame(editor);row.pack(fill='x',pady=5)
        field_box=ttk.Combobox(row,state='readonly',width=37);field_box.pack(side='left',fill='x',expand=True)
        advanced=ttk.Frame(editor);advanced.pack(fill='both',expand=True)
        advanced_row=ttk.Frame(advanced);advanced_row.pack(fill='x')
        conversion=ttk.Combobox(advanced_row,state='readonly',width=12,values=['text','json','raw','number','boolean','date','datetime','user','status','option','reference']);conversion.pack(side='left',padx=8)
        target_key=ttk.Entry(advanced_row,width=22);target_key.pack(side='left')
        ttk.Label(advanced_row,text='변환 / API 키',style='Note.TLabel').pack(side='left',padx=8)
        tabs=ttk.Notebook(advanced);tabs.pack(fill='x',pady=5)
        samples=tk.Text(tabs,height=4,wrap='word',font=(ui_font,10),relief='flat')
        values=tk.Text(tabs,height=4,wrap='word',font=('Consolas',10),relief='flat')
        tabs.add(samples,text='원본 표본 · 대상 선택지');tabs.add(values,text='값 매핑 검토 (JSON)')
        foot=ttk.Frame(body);foot.pack(fill='x')
        notice=tk.StringVar(value='필드/선택지 변경은 바로 반영됩니다. 매핑 저장으로 나중에 다시 사용하세요.')
        ttk.Label(body,textvariable=notice,style='Note.TLabel',wraplength=1050).pack(anchor='w',pady=(8,0))
        definitions={f['id']:f for f in catalog['definitions']}
        rows={}
        fields=[]
        confirmed=set()
        value_drafts={}
        field_drafts={}
        creation_busy=tk.BooleanVar(value=False)
        def selected_parent():
            selection=table.selection()
            if not selection:return None
            item=selection[0]
            return item if item in rows else table.parent(item)
        def current():
            if scope_box.current()<0 or target_box.current()<0:
                raise ValueError('원본 유형과 대상 트래커를 먼저 선택하세요.')
            return (*scopes[scope_box.current()],catalog['targets'][target_box.current()])
        def refresh(*args):
            previous_parent=selected_parent()
            previous_source=rows.get(previous_parent,(None,None))[0]
            rows.clear(); table.delete(*table.get_children())
            try:p,t,tracker=current()
            except ValueError:
                counts.set('대상 트래커를 선택하면 자동 추천을 시작합니다.');return
            fields[:]=tracker['fields']
            field_box.configure(values=[str(f.get('label',f.get('name',''))) + '  ·  ID ' + str(field_identifier(f)) for f in fields])
            scoped_definitions=dict(definitions,**t.get('definitions',{}))
            if selected_tracker(overlay,p,t) is None:
                if t['id']=='__run__':overlay['migration']['xray_run_tracker']=tracker['id']
                else:overlay['migration']['tracker_mapping'][p['key']+':'+t['id']]=tracker['id']
            for source in auto_connect_scope(overlay,p,t,fields,scoped_definitions,config):confirmed.add((p['key'],t['id'],source))
            existing=scope_mappings(overlay,p,t)
            proposals=suggest_scope(t,scoped_definitions,fields,config,existing)
            visible=set(visible_sources(t,existing,all_fields.get()))
            kept=ready=pending=0
            for source,observed in t['fields'].items():
                if source not in visible:continue
                definition=scoped_definitions.get(source,{})
                display=definition.get('name',source)
                spec=existing.get(source)
                if spec:
                    field=next((f for f in fields if str(field_identifier(f)) == str(spec.get('field_id'))),None)
                    proposal={'field':field,'spec':spec,'conversion':spec.get('type','raw'),'target_key':spec.get('target',''),'values':spec.get('value_mapping',{}),'reason':'기존 매핑 유지' if field else '기존 대상 필드가 조회되지 않음 · 설정 유지','ready':False}
                    state='자동 반영' if (p['key'],t['id'],source) in confirmed else '기존 유지';tag='kept';kept+=1
                else:
                    proposal=proposals[source]
                    state='추천 · 확인 대기' if proposal['ready'] else '검토 필요'
                    tag='ready' if proposal['ready'] else 'review'
                    ready+=int(proposal['ready']);pending+=int(not proposal['ready'])
                if new_only.get() and spec:continue
                if search.get().casefold() not in (source+' '+display).casefold():continue
                proposal=deepcopy(field_drafts.get((p['key'],t['id'],source,tracker['id']),proposal))
                proposal['values']=deepcopy(value_drafts.get((p['key'],t['id'],source),proposal.get('values',{})))
                if (p['key'],t['id'],source) in value_drafts:
                    proposal['ready']=False
                    proposal['spec']=None
                iid='field-'+str(len(rows));rows[iid]=(source,proposal)
                field=proposal.get('field')
                if field:
                    field=next((item for item in fields if str(field_identifier(item))==str(field_identifier(field))),field)
                    proposal['field']=field
                table.insert('', 'end',iid=iid,text=display+' ('+str(definition.get('schema',{}).get('type',proposal.get('conversion','필드')))+')',values=(field.get('label',field.get('name','')) if field else '— 대상 선택 / 새 필드 생성 —',state),tags=(tag,))
                if proposal.get('conversion') in ('option','status','user','reference') or definition.get('schema',{}).get('type') in ('option','status','priority','user') or definition.get('schema',{}).get('items') in ('option','user','version'):
                    for number,(identity,label) in enumerate(source_choices(source_observations(t,source)).items()):
                        mapped=proposal['values'].get(identity)
                        option=next((o for o in (field or {}).get('options') or [] if isinstance(o,dict) and isinstance(mapped,dict) and o.get('id')==mapped.get('id')),None)
                        table.insert(iid,'end',iid=iid+'-value-'+str(number),text=label,values=(option.get('name',option.get('label',str(mapped))) if option else (str(mapped) if mapped is not None else '— 선택지 연결 —'),'값 확인' if not spec else '기존값'),tags=(tag,))
            counts.set(f'기존 {kept} · 추천 {ready} · 검토 {pending} · 숨김 {len(t["fields"])-len(visible)}')
            children=table.get_children()
            if children:
                chosen=next((iid for iid,(source,_) in rows.items() if source==previous_source),children[0])
                table.selection_set(chosen);inspect()
        def inspect(event=None):
            parent=selected_parent()
            if not parent or parent not in rows:return
            p,t,tracker=current();source,proposal=rows[parent]
            selected_text.set(definitions.get(source,{}).get('name',source)+'  ·  '+source+'  /  '+proposal['reason'])
            field=proposal.get('field');field_box.set('')
            if field in fields:field_box.current(fields.index(field))
            conversion.set(proposal.get('conversion','raw'));target_key.delete(0,'end');target_key.insert(0,proposal.get('target_key',''))
            values.delete('1.0','end');values.insert('1.0',json.dumps(proposal.get('values',{}),ensure_ascii=False,indent=2))
            samples.configure(state='normal');samples.delete('1.0','end')
            samples.insert('end','원본 표본: '+json.dumps(t['fields'][source],ensure_ascii=False)[:1500])
            samples.insert('end','\n대상 선택지: '+json.dumps((field or {}).get('options'),ensure_ascii=False)[:1500]);samples.configure(state='disabled')
        def change_field(event=None):
            parent=selected_parent()
            if not parent or field_box.current()<0:return
            p,t,tracker=current();source,_=rows[parent]
            proposal=suggest_mapping(source,definitions.get(source,{}),t['fields'][source],[fields[field_box.current()]],config,manual=True)
            # Manual choice permits a different label, but never invents API bindings.
            if proposal.get('field'):
                conversion.set(proposal['conversion']);target_key.delete(0,'end');target_key.insert(0,proposal['target_key'])
                values.delete('1.0','end');values.insert('1.0',json.dumps(proposal['values'],ensure_ascii=False,indent=2))
                field_drafts[(p['key'],t['id'],source,tracker['id'])]=deepcopy(proposal)
                value_drafts.pop((p['key'],t['id'],source),None)
                rows[parent]=(source,proposal)
                table.item(parent,values=(proposal['field'].get('label',proposal['field'].get('name','')),'변경 대기'))
                table.delete(*table.get_children(parent))
                if proposal['conversion'] in ('option','status','user','reference'):
                    for number,(identity,label) in enumerate(source_choices(source_observations(t,source)).items()):
                        mapped=proposal['values'].get(identity)
                        table.insert(parent,'end',iid=parent+'-value-'+str(number),text=label,values=(str(mapped) if mapped is not None else '— 선택지 연결 —','값 확인'))
            apply_selected()
        def apply_selected(event=None):
            try:
                parent=selected_parent()
                if not parent or field_box.current()<0:raise ValueError('필드와 대상을 선택하세요.')
                p,t,tracker=current();source,_=rows[parent]
                spec=build_spec(source,fields[field_box.current()],conversion.get(),target_key.get(),json.loads(values.get('1.0','end')))
                for sample in t['fields'][source]:convert(sample,spec['type'],spec.get('value_mapping',{}))
                confirm_row(overlay,p,t,source,tracker['id'],spec)
                confirmed.add((p['key'],t['id'],source))
                field_drafts.pop((p['key'],t['id'],source,tracker['id']),None)
                value_drafts.pop((p['key'],t['id'],source),None)
                notice.set('매핑 자동 반영됨. 저장하면 나중에 다시 사용할 수 있습니다.');refresh()
            except (ValueError,ApiError,TypeError) as exc:notice.set('매핑 설정 필요: '+str(exc))
        def check():
            errors=validate_overlay(overlay,catalog)
            notice.set('검사: '+str(len(errors))+'개 미해결. '+(' / '.join(errors[:3]) if errors else '표본 검사 통과. 관계·API·실제 쓰기는 별도 검증이 필요합니다.'))
            return errors
        def save():
            if creation_busy.get():
                notice.set('필드 생성 결과 확인 후 저장하세요.');return
            apply_selected()
            errors=check()
            temporary=output.with_suffix(output.suffix+'.tmp')
            temporary.write_text(json.dumps(overlay,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(output)
            notice.set(str(output)+' 저장 완료 · 미해결 '+str(len(errors))+'개 · 서버 생성은 별도 동작')
        def remember_values(source, mapping, actual=None):
            p,t,tracker=current()
            value_drafts[(p['key'],t['id'],source)]=mapping
            if actual:
                if not actual.get('_fields_only'):tracker['configuration']=actual
                by_id={str(field_identifier(f)):f for f in actual.get('fields',[])}
                tracker['fields']=[dict(field,**by_id.get(str(field_identifier(field)),{})) for field in tracker['fields']]
            refresh();apply_selected()

        def create_options(source, proposal, selected=None):
            if creation_busy.get():notice.set('서버 생성 처리 중입니다.');return
            p,t,tracker=current()
            field=proposal.get('field')
            if not field:notice.set('대상 필드를 먼저 선택하세요. 필드가 없으면 새 필드 생성을 사용하세요.');return
            if not isinstance(field.get('options'),list):notice.set('대상 선택형 필드를 먼저 선택하세요.');return
            choices=selected or source_choices(source_observations(t,source))
            if not choices:notice.set('원본 선택지/표본 값이 없습니다.');return
            creation_busy.set(True)
            journal=output.with_name(output.stem+'.field-actions.local.json')
            def worker():
                mapping=deepcopy(proposal.get('values',{}));actual=None;error=None
                try:
                    for identity,label in choices.items():
                        option,readback=create_missing_option(cb,tracker['id'],field,label,journal)
                        mapping[identity]={'id':option['id']}
                        if readback:actual=readback
                except Exception as exc:
                    _diagnostics.record('선택지 자동 생성','error',error=exc)
                    error=type(exc).__name__+': '+str(exc)
                results.put(('options-created',p['key'],t['id'],tracker['id'],source,mapping,actual,error))
            threading.Thread(target=worker,daemon=True).start()
            notice.set('대상에 없는 원본 값을 선택지로 생성 중…')

        def propose_creation():
            if creation_busy.get():
                notice.set('이전 필드 생성 결과를 확인 중입니다. 잠시 기다려주세요.');return
            parent=selected_parent()
            if not parent or parent not in rows:
                notice.set('새로 만들 원본 필드를 먼저 선택하세요.');return
            p,t,tracker=current();source,_=rows[parent]
            expected=tracker.get('configuration',{})
            _diagnostics.tracker_profile(tracker)
            templates=creation_templates(expected)
            if templates and expected.get('basicInformation',{}).get('trackerId')==tracker['id']:
                open_creation_dialog(p,t,tracker,source,expected,templates,False);return
            creation_busy.set(True);notice.set('전체 설정 대신 사용할 필드 전용 생성 API를 확인 중…')
            def prepare():
                try:
                    dedicated_field_contract(cb)
                    templates=partial_field_templates(tracker['fields'])
                    if not templates:raise ApiError('필드 목록에도 실제 생성 타입 정보(typeId/type)가 없습니다. 타입을 추측해 생성할 수 없습니다.')
                    results.put(('field-prepared',p['key'],t['id'],tracker['id'],source,templates))
                except Exception as exc:
                    _diagnostics.record('필드 전용 생성 API/타입 확인','error',error=exc)
                    results.put(('creation-error',type(exc).__name__+': '+str(exc)))
            threading.Thread(target=prepare,daemon=True).start()

        def open_creation_dialog(p,t,tracker,source,expected,templates,direct):
            dialog=tk.Toplevel(root);dialog.title('새 필드 생성 제안');dialog.transient(root);dialog.grab_set();dialog.geometry('650x480')
            panel=ttk.Frame(dialog,padding=14);panel.pack(fill='both',expand=True)
            ttk.Label(panel,text='대상 트래커 '+str(tracker['id'])+'에 새 필드를 생성합니다.',style='Title.TLabel').pack(anchor='w')
            ttk.Label(panel,text='기존 필드와 권한은 유지합니다. 이 작업은 실제 서버를 변경합니다.',wraplength=600).pack(anchor='w',pady=6)
            ttk.Label(panel,text='새 필드 이름').pack(anchor='w')
            label=ttk.Entry(panel);label.insert(0,definitions.get(source,{}).get('name',source));label.pack(fill='x')
            ttk.Label(panel,text='서버에서 확인된 타입 (같은 타입의 기존 필드 기준)').pack(anchor='w',pady=(8,0))
            template=ttk.Combobox(panel,state='readonly',values=[item['name'] for item in templates]);template.pack(fill='x')
            proposal_summary=tk.StringVar(value='타입을 선택하면 생성 제안 요약을 표시합니다.')
            ttk.Label(panel,textvariable=proposal_summary,wraplength=600).pack(anchor='w',pady=8)
            definition_frame=ttk.Frame(panel)
            definition_text=tk.Text(definition_frame,height=9,font=('Consolas',10));definition_text.pack(fill='both',expand=True)
            expanded=tk.BooleanVar(value=False)
            ttk.Checkbutton(panel,text='고급 설정 · 생성 API 정의 보기/편집',variable=expanded,command=lambda:definition_frame.pack(fill='both',expand=True,before=create_button) if expanded.get() else definition_frame.pack_forget()).pack(anchor='w')
            def fill(event=None):
                draft=deepcopy(templates[template.current()]['definition']);draft['label']=label.get().strip()
                proposal_summary.set('새 이름: '+draft['label']+' / 타입 ID: '+str(draft.get('typeId',draft.get('type')))+' / 필수: '+str(draft['mandatory'])+' / 선택지: '+str(len(draft.get('options') or []))+'개\n선택지 추가나 배포별 정의 변경이 필요하면 고급 설정에서 검토하세요.')
                definition_text.delete('1.0','end');definition_text.insert('1.0',json.dumps(draft,ensure_ascii=False,indent=2))
            template.bind('<<ComboboxSelected>>',fill)
            def create():
                try:
                    if template.current()<0:raise ValueError('생성할 서버 필드 타입을 선택하세요.')
                    draft=json.loads(definition_text.get('1.0','end'));draft['label']=label.get().strip()
                    create_button.configure(state='disabled');dialog.update_idletasks()
                    creation_busy.set(True)
                    journal=output.with_name(output.stem+'.field-actions.local.json')
                    def worker():
                        try:
                            if direct:created,actual=create_dedicated_field(cb,tracker['id'],draft,journal)
                            else:created,actual=create_confirmed_field(cb,tracker['id'],draft,expected,journal,approved=True)
                            results.put(('created-field',tracker['id'],created,actual,source,p['key'],t['id']))
                        except Exception as exc:
                            _diagnostics.record('필드 생성 처리','error',error=exc)
                            results.put(('creation-error',type(exc).__name__+': '+str(exc)))
                    threading.Thread(target=worker,daemon=True).start()
                    dialog.destroy();notice.set('새 필드 생성 중… 결과를 확인할 때까지 매핑 저장은 기다려주세요.')
                except (ValueError,ApiError,TypeError) as exc:
                    messagebox.showerror('생성 제안 확인',str(exc),parent=dialog)
                    create_button.configure(state='normal')
            create_button=ttk.Button(panel,text='새 필드 생성',command=create);create_button.pack(pady=(10,0))

        def process_actions():
            try:
                item=results.get_nowait()
            except queue.Empty:
                root.after(150,process_actions);return
            creation_busy.set(False)
            if item[0]=='field-prepared':
                _,project_key,kind_id,tracker_id,source,templates=item
                p,t,tracker=current()
                parent=selected_parent()
                if (p['key'],t['id'],tracker['id'])==(project_key,kind_id,tracker_id) and parent in rows and rows[parent][0]==source:
                    open_creation_dialog(p,t,tracker,source,tracker.get('configuration',{}),templates,True)
                else:notice.set('대상 선택이 바뀌었습니다. 새 필드 생성을 다시 선택하세요.')
            elif item[0]=='created-field':
                _,tracker_id,created,actual,source,project_key,kind_id=item
                tracker=next(target for target in catalog['targets'] if target['id']==tracker_id)
                if not actual.get('_fields_only'):tracker['configuration']=actual
                if actual.get('_fields_only'):
                    previous_fields={str(field_identifier(field)):field for field in tracker['fields']}
                    tracker['fields']=[dict(previous_fields.get(str(field_identifier(field)),{}),**field) for field in actual['fields']]
                else:tracker['fields'].append(created)
                refresh()
                notice.set('필드 생성 확인 완료: '+str(created.get('label'))+' · 실제 ID '+str(field_identifier(created))+' · 대상에 선택하면 매핑이 자동 반영됩니다.')
            elif item[0]=='options-created':
                _,project_key,kind_id,tracker_id,source,mapping,actual,error=item
                p,t,tracker=current()
                if (p['key'],t['id'],tracker['id'])==(project_key,kind_id,tracker_id):remember_values(source,mapping,actual)
                else:
                    value_drafts[(project_key,kind_id,source)]=mapping
                    target=next(target for target in catalog['targets'] if target['id']==tracker_id)
                    if actual:
                        if not actual.get('_fields_only'):target['configuration']=actual
                        by_id={str(field_identifier(f)):f for f in actual['fields']}
                        target['fields']=[dict(f,**by_id.get(str(field_identifier(f)),{})) for f in target['fields']]
                if error:notice.set('일부 값 생성 중단: '+error)
                else:notice.set('선택지 생성/연결 완료. 매핑 저장으로 보관하세요.')
            elif item[0]=='creation-error':
                notice.set('필드 생성 확인 실패. 반복 생성하지 말고 서버 필드와 로그를 확인하세요.')
                messagebox.showerror('필드 생성 확인',item[1],parent=root)
            root.after(150,process_actions)

        ttk.Checkbutton(bar,text='전체 API 필드',variable=all_fields,command=refresh).pack(side='right',padx=8)
        ttk.Checkbutton(bar,text='신규만',variable=new_only,command=refresh).pack(side='right',padx=12)
        inline_widget=[None]
        def edit_target(event=None):
            if inline_widget[0] is not None:
                try:inline_widget[0].destroy()
                except tk.TclError:pass
                inline_widget[0]=None
            if event is not None and getattr(event,'num',None)==1:
                item=table.identify_row(event.y)
                if not item or table.identify_column(event.x)!='#1':return
                table.selection_set(item)
            else:
                selection=table.selection()
                if not selection:return
                item=selection[0]
            inspect()
            parent=item if item in rows else table.parent(item)
            if parent not in rows:return
            p,t,tracker=current();source,proposal=rows[parent]
            bbox=table.bbox(item,'target')
            if not bbox:return
            x,y,width,height=bbox
            if item in rows:
                labels=['＋ 새 필드 생성']+[str(field.get('label',field.get('name','')))+' / ID '+str(field_identifier(field)) for field in fields]
                box=ttk.Combobox(table,state='readonly',values=labels)
                old=proposal.get('field')
                box.current(fields.index(old)+1 if old in fields else 0)
                def accept(event=None):
                    index=box.current();box.destroy();inline_widget[0]=None
                    if index==0:propose_creation()
                    elif index>0:field_box.current(index-1);change_field()
            else:
                entries=list(source_choices(source_observations(t,source)).items())
                children=list(table.get_children(parent))
                identity,label=entries[children.index(item)]
                field=proposal.get('field')
                if not field:notice.set('먼저 상위 필드의 대상 칸에서 연결하거나 새 필드를 만드세요.');return
                options=[option for option in field.get('options') or [] if isinstance(option,dict) and option.get('id') is not None]
                box=ttk.Combobox(table,state='readonly',values=['＋ '+label+' 새 선택지 생성']+[str(option.get('name',option.get('label','')))+' / ID '+str(option['id']) for option in options])
                mapped=proposal.get('values',{}).get(identity)
                index=next((number+1 for number,option in enumerate(options) if isinstance(mapped,dict) and mapped.get('id')==option['id']),0)
                box.current(index)
                def accept(event=None):
                    index=box.current();box.destroy();inline_widget[0]=None
                    if index==0:create_options(source,proposal,{identity:label})
                    elif index>0:
                        mapping=deepcopy(proposal.get('values',{}));mapping[identity]={'id':options[index-1]['id']}
                        remember_values(source,mapping)
            inline_widget[0]=box
            box.place(x=x,y=y,width=width,height=height)
            box.bind('<<ComboboxSelected>>',accept)
            box.bind('<Escape>',lambda event:box.destroy())
            box.focus_set()

        def advanced_settings():
            inspect();editor.deiconify();editor.lift()

        menu=tk.Menu(root,tearoff=False)
        menu.add_command(label='대상 필드/선택지 변경',command=edit_target)
        menu.add_command(label='새 필드 생성…',command=propose_creation)
        menu.add_command(label='이 필드의 없는 선택지 모두 생성',command=lambda:create_options(*rows[selected_parent()]) if selected_parent() in rows else None)
        menu.add_separator()
        menu.add_command(label='고급 설정…',command=advanced_settings)
        def open_menu(event):
            item=table.identify_row(event.y)
            if item:table.selection_set(item);inspect()
            menu.tk_popup(event.x_root,event.y_root)

        ttk.Label(foot,text='대상 칸 클릭: 변경 / 새로 생성 · 오른쪽 클릭: 추가 설정',style='Note.TLabel').pack(side='left',pady=8)
        ttk.Button(foot,text='닫기',command=root.destroy).pack(side='right')
        ttk.Button(foot,text='저장',command=save).pack(side='right',padx=8)
        table.bind('<<TreeviewSelect>>',inspect)
        table.bind('<Button-1>',edit_target);table.bind('<Return>',edit_target);table.bind('<Button-3>',open_menu)
        field_box.bind('<<ComboboxSelected>>',change_field)
        conversion.bind('<<ComboboxSelected>>',apply_selected);target_key.bind('<FocusOut>',apply_selected);values.bind('<FocusOut>',apply_selected)
        def scope_changed(event=None):
            if scope_box.current()<0:return
            p,t=scopes[scope_box.current()];tracker=selected_tracker(overlay,p,t)
            index=next((i for i,x in enumerate(catalog['targets']) if x['id']==tracker),None)
            target_box.set('')
            if index is not None:target_box.current(index)
            elif len(catalog['targets'])==1:target_box.current(0)
            refresh()
        scope_box.bind('<<ComboboxSelected>>',scope_changed);target_box.bind('<<ComboboxSelected>>',refresh)
        search.trace_add('write',refresh)
        if scopes:scope_box.current(0);scope_changed()
        if catalog.get('warnings'):notice.set('일부 조회 실패: '+' / '.join(catalog['warnings']))
        root.after(150,process_actions)
        status.set('조회 완료 · 편집 화면/기본 필드 우선 · 전체 필드는 별도 표시')
    def poll():
        try:
            catalog,error = results.get_nowait()
        except queue.Empty:
            root.after(100, poll); return
        if error:
            status.set('조회 실패'); messagebox.showerror('조회 실패', error)
        else:
            try:
                _diagnostics.record('매핑 화면 구성', 'start')
                render(catalog)
                _diagnostics.record('매핑 화면 구성', 'ok')
                root.deiconify()
                root.lift()
            except Exception as exc:
                _diagnostics.record('매핑 화면 구성', 'error', error=exc)
                status.set('화면 구성 실패: summary.txt 확인')
                messagebox.showerror('화면 구성 실패', '오류 종류: ' + type(exc).__name__ + '\n진단 요약 파일을 확인하세요.')
    root.after(100, poll)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if _diagnostics:
            _diagnostics.record('프로그램 실행', 'error', error=exc)
            print('실행 실패: ' + type(exc).__name__ + '\n진단 요약: ' + str((_diagnostics.root / 'summary.txt').resolve()))
        else:
            print('로그 초기화 실패: ' + type(exc).__name__)
        raise SystemExit(2)
