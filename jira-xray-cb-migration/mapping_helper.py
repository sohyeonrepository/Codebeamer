"""Local desktop mapping editor. API calls are GET-only; credentials stay in memory."""
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


class Diagnostics:
    """Bounded shareable summary; no exception messages, URLs, values or locals."""
    def __init__(self, directory):
        self.root = Path(directory) / ('mapping-helper-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.failures = []
        self.latest = '시작'
        self.count = 0
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
            lines.extend(self.failures[-6:])
            if not self.failures:
                lines.append('Recorded errors: 0')
            (self.root / 'summary.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


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
        if run_samples:
            types['__run__'] = {'id': '__run__', 'name': '개별 Xray Run (내부 메타 필드 포함)', 'fields': run_samples}
        projects.append({'key': project['key'], 'types': list(types.values()), 'sample_count': len(samples)})
    targets = []
    warnings = []
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
        configured = {str(f.get('id', f.get('referenceId'))): f for f in configuration['fields']}
        merged = [dict(configured.get(str(f.get('id', f.get('referenceId'))), {}), **f) for f in fields]
        targets.append({'id': tracker, 'name': configuration.get('name', configuration.get('basicInformation', {}).get('name', '')), 'fields': merged})
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
    field_id = target.get('id', target.get('referenceId'))
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
                if field.get('mandatory') and str(field.get('id', field.get('referenceId'))) not in mapped_ids:
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
    fid = field.get('id', field.get('referenceId'))
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
    proposals = {source: suggest_mapping(source, definitions.get(source, {}), samples, fields, settings)
                 for source, samples in kind['fields'].items() if source not in existing}
    claimed = {str(spec.get('field_id')) for spec in existing.values()}
    identities = {}
    for source, proposal in proposals.items():
        field = proposal.get('field')
        if field:
            key = str(field.get('id', field.get('referenceId')))
            identities.setdefault(key, []).append(source)
    for key, sources in identities.items():
        if key in claimed or len(sources) > 1:
            for source in sources:
                proposals[source]['ready'] = False
                proposals[source]['reason'] += ' · 대상 중복: 직접 확인'
    return proposals


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

def main():
    parser = argparse.ArgumentParser(description='고객 PC 내부 필드 매핑 도우미 (서버 쓰기 없음)')
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
    def callback_error(error_type, error, tb):
        _diagnostics.record('GUI 콜백', 'error', error=error)
        messagebox.showerror('화면 처리 실패', '진단 요약 파일을 확인하세요:\n' + str(_diagnostics.root / 'summary.txt'))
    root.report_callback_exception = callback_error
    _diagnostics.record('GUI 창 생성', 'ok')
    root.title('Jira → Codebeamer 로컬 매핑 도우미')
    root.geometry('1180x900')
    root.minsize(950, 720)
    status = tk.StringVar(value='필드 조회 중… 서버 쓰기는 하지 않습니다.')
    ttk.Label(root, textvariable=status).pack(fill='x')
    ttk.Label(root, text='진단 요약: ' + str((_diagnostics.root / 'summary.txt').resolve())).pack(fill='x')
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
        style.configure('TFrame', background='#f3f5f9')
        style.configure('TLabel', background='#f3f5f9', foreground='#25314b', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
        style.configure('Note.TLabel', foreground='#65738a', font=('Segoe UI', 9))
        style.configure('Treeview', rowheight=31, font=('Segoe UI', 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), padding=7)
        style.configure('TButton', padding=(12, 7))
        body = ttk.Frame(root, padding=16); body.pack(fill='both', expand=True)
        ttk.Label(body, text='필드 동기화 설정', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='기존 매핑 유지  →  신규 필드 추천 검토  →  확정한 매핑 저장', style='Note.TLabel').pack(anchor='w', pady=(4,12))
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
        search = tk.StringVar()
        ttk.Entry(bar,textvariable=search,width=20).pack(side='right')
        ttk.Label(bar,text='검색  ').pack(side='right')
        frame = ttk.Frame(body); frame.pack(fill='both',expand=True)
        table = ttk.Treeview(frame, columns=('source','target','conversion','state','reason'),show='headings',selectmode='browse',height=9)
        for col,title,width in [('source','Jira 필드',190),('target','Codebeamer 필드',180),('conversion','변환',80),('state','상태',95),('reason','추천 근거 / 확인할 사항',320)]:
            table.heading(col,text=title); table.column(col,width=width,minwidth=60)
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
        table.tag_configure('kept',foreground='#65738a')
        table.tag_configure('ready',foreground='#176c51')
        table.tag_configure('review',foreground='#9a6611')
        editor = ttk.LabelFrame(body,text='선택한 필드 검토',padding=10);editor.pack(fill='x',pady=10)
        selected_text = tk.StringVar(value='목록에서 필드를 선택하세요.')
        ttk.Label(editor,textvariable=selected_text).pack(anchor='w')
        row = ttk.Frame(editor);row.pack(fill='x',pady=5)
        field_box=ttk.Combobox(row,state='readonly',width=37);field_box.pack(side='left',fill='x',expand=True)
        conversion=ttk.Combobox(row,state='readonly',width=12,values=['text','json','raw','number','boolean','date','datetime','user','status','option','reference']);conversion.pack(side='left',padx=8)
        target_key=ttk.Entry(row,width=22);target_key.pack(side='left')
        ttk.Label(editor,text='대상 필드 / 변환 / API 키 — 기존 설정에서 확인된 키는 자동 채웁니다.',style='Note.TLabel').pack(anchor='w')
        tabs=ttk.Notebook(editor);tabs.pack(fill='x',pady=5)
        samples=tk.Text(tabs,height=4,wrap='word',font=('Segoe UI',10),relief='flat')
        values=tk.Text(tabs,height=4,wrap='word',font=('Consolas',10),relief='flat')
        tabs.add(samples,text='원본 표본 · 대상 선택지');tabs.add(values,text='값 매핑 검토 (JSON)')
        foot=ttk.Frame(body);foot.pack(fill='x')
        notice=tk.StringVar(value='추천은 검토용입니다. 확정 전에는 저장 파일에 포함되지 않습니다.')
        ttk.Label(body,textvariable=notice,style='Note.TLabel',wraplength=1050).pack(anchor='w',pady=(8,0))
        definitions={f['id']:f for f in catalog['definitions']}
        rows={}
        fields=[]
        confirmed=set()
        def current():
            if scope_box.current()<0 or target_box.current()<0:
                raise ValueError('원본 유형과 대상 트래커를 먼저 선택하세요.')
            return (*scopes[scope_box.current()],catalog['targets'][target_box.current()])
        def refresh(*args):
            rows.clear(); table.delete(*table.get_children())
            try:p,t,tracker=current()
            except ValueError:
                counts.set('대상 트래커를 선택하면 자동 추천을 시작합니다.');return
            fields[:]=tracker['fields']
            field_box.configure(values=[str(f.get('label',f.get('name',''))) + '  ·  ID ' + str(f.get('id',f.get('referenceId'))) for f in fields])
            existing=scope_mappings(overlay,p,t)
            proposals=suggest_scope(t,definitions,fields,config,existing)
            kept=ready=pending=0
            for source,observed in t['fields'].items():
                definition=definitions.get(source,{})
                display=definition.get('name',source)
                spec=existing.get(source)
                if spec:
                    field=next((f for f in fields if str(f.get('id',f.get('referenceId'))) == str(spec.get('field_id'))),None)
                    proposal={'field':field,'spec':spec,'conversion':spec.get('type','raw'),'target_key':spec.get('target',''),'values':spec.get('value_mapping',{}),'reason':'기존 매핑 유지' if field else '기존 대상 필드가 조회되지 않음 · 설정 유지','ready':False}
                    state='확정 완료' if (p['key'],t['id'],source) in confirmed else '기존 유지';tag='kept';kept+=1
                else:
                    proposal=proposals[source]
                    state='추천 · 확인 대기' if proposal['ready'] else '검토 필요'
                    tag='ready' if proposal['ready'] else 'review'
                    ready+=int(proposal['ready']);pending+=int(not proposal['ready'])
                if new_only.get() and spec:continue
                if search.get().casefold() not in (source+' '+display).casefold():continue
                iid=str(len(rows));rows[iid]=(source,proposal)
                field=proposal.get('field')
                table.insert('', 'end',iid=iid,values=(display,field.get('label',field.get('name','')) if field else '— 직접 선택 —',proposal.get('conversion',''),state,proposal['reason']),tags=(tag,))
            counts.set(f'기존 유지 {kept}   |   신규 추천 {ready}   |   검토 필요 {pending}')
            children=table.get_children()
            if children:
                table.selection_set(children[0]);inspect()
        def inspect(event=None):
            if not table.selection():return
            p,t,tracker=current();source,proposal=rows[table.selection()[0]]
            selected_text.set(definitions.get(source,{}).get('name',source)+'  ·  '+source+'  /  '+proposal['reason'])
            field=proposal.get('field');field_box.set('')
            if field in fields:field_box.current(fields.index(field))
            conversion.set(proposal.get('conversion','raw'));target_key.delete(0,'end');target_key.insert(0,proposal.get('target_key',''))
            values.delete('1.0','end');values.insert('1.0',json.dumps(proposal.get('values',{}),ensure_ascii=False,indent=2))
            samples.configure(state='normal');samples.delete('1.0','end')
            samples.insert('end','원본 표본: '+json.dumps(t['fields'][source],ensure_ascii=False)[:1500])
            samples.insert('end','\n대상 선택지: '+json.dumps((field or {}).get('options'),ensure_ascii=False)[:1500]);samples.configure(state='disabled')
        def change_field(event=None):
            if not table.selection() or field_box.current()<0:return
            p,t,tracker=current();source,_=rows[table.selection()[0]]
            proposal=suggest_mapping(source,definitions.get(source,{}),t['fields'][source],[fields[field_box.current()]],config,manual=True)
            # Manual choice permits a different label, but never invents API bindings.
            if proposal.get('field'):
                conversion.set(proposal['conversion']);target_key.delete(0,'end');target_key.insert(0,proposal['target_key'])
                values.delete('1.0','end');values.insert('1.0',json.dumps(proposal['values'],ensure_ascii=False,indent=2))
        def confirm_selected():
            try:
                if not table.selection() or field_box.current()<0:raise ValueError('필드와 대상을 선택하세요.')
                p,t,tracker=current();source,_=rows[table.selection()[0]]
                spec=build_spec(source,fields[field_box.current()],conversion.get(),target_key.get(),json.loads(values.get('1.0','end')))
                for sample in t['fields'][source]:convert(sample,spec['type'],spec.get('value_mapping',{}))
                confirm_row(overlay,p,t,source,tracker['id'],spec)
                confirmed.add((p['key'],t['id'],source))
                notice.set('선택한 매핑 확정. 서버 변경 없이 메모리에 반영했습니다.');refresh()
            except (ValueError,ApiError,TypeError) as exc:messagebox.showerror('확인 필요',str(exc),parent=root)
        def confirm_suggestions():
            candidates=[(source,p) for source,p in rows.values() if p.get('ready') and p.get('spec')]
            if not candidates:notice.set('확정할 신규 추천이 없습니다. 검토 필요 항목을 선택하세요.');return
            if not messagebox.askyesno('신규 추천 확정',str(len(candidates))+'개 신규 추천을 확정할까요?\n기존 매핑은 그대로 유지됩니다.\n값 매핑과 대상 API 형식을 확인했는지 검토하세요.',parent=root):return
            try:
                p,t,tracker=current()
                for source,proposal in candidates:
                    confirm_row(overlay,p,t,source,tracker['id'],proposal['spec'])
                    confirmed.add((p['key'],t['id'],source))
                notice.set(str(len(candidates))+'개 추천을 확정했습니다. 저장 전 검사를 실행하세요.');refresh()
            except ValueError as exc:messagebox.showerror('확인 필요',str(exc),parent=root)
        def check():
            errors=validate_overlay(overlay,catalog)
            notice.set('검사: '+str(len(errors))+'개 미해결. '+(' / '.join(errors[:3]) if errors else '표본 검사 통과. 관계·API·실제 쓰기는 별도 검증이 필요합니다.'))
            return errors
        def save():
            errors=check()
            if errors and not messagebox.askyesno('미완성 매핑 저장',str(len(errors))+'개 미해결 항목이 있습니다.\n기존/확정된 매핑만 작업 중 파일로 저장할까요?\n확정하지 않은 추천은 저장하지 않습니다.',parent=root):return
            if output.exists() and not messagebox.askyesno('매핑 저장','기존 파일을 갱신할까요? 기존 매핑과 확정된 항목만 저장됩니다.',parent=root):return
            temporary=output.with_suffix(output.suffix+'.tmp')
            temporary.write_text(json.dumps(overlay,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(output)
            notice.set(str(output)+' 저장 완료 · 미확정 추천 제외 · 서버 쓰기 없음')
        ttk.Checkbutton(bar,text='신규 / 검토 필요만',variable=new_only,command=refresh).pack(side='right',padx=12)
        ttk.Button(foot,text='선택 매핑 확정',command=confirm_selected).pack(side='left')
        ttk.Button(foot,text='표시된 신규 추천 확정',command=confirm_suggestions).pack(side='left',padx=8)
        ttk.Button(foot,text='누락 검사',command=check).pack(side='right',padx=8)
        ttk.Button(foot,text='확정 매핑 저장',command=save).pack(side='right')
        table.bind('<<TreeviewSelect>>',inspect);field_box.bind('<<ComboboxSelected>>',change_field)
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
        status.set('조회 완료 · 기존 설정 불러옴 · 추천은 확정 후 저장')
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
