"""Local desktop mapping editor. API calls are GET-only; credentials stay in memory."""
import argparse
from datetime import datetime, timezone
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
        targets.append({'id': tracker, 'fields': merged})
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
            result['migration'].setdefault(section, {}).update(deepcopy(migration[section]))
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
    root.geometry('1150x850')
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
        overlay = {'migration': {'field_mapping': {}, 'tracker_mapping': {}}}
        output = Path(args.output)
        if output.exists():
            saved = json.loads(output.read_text(encoding='utf-8'))
            overlay['migration']['field_mapping'] = saved.get('migration', {}).get('field_mapping', {})
            overlay['migration']['tracker_mapping'] = saved.get('migration', {}).get('tracker_mapping', {})
            for section in ('run_adapter', 'xray_run_tracker'):
                if section in saved.get('migration', {}):
                    overlay['migration'][section] = saved['migration'][section]
        sources = [(p, t, f) for p in catalog['projects'] for t in p['types'] for f in t['fields']]
        definitions = {f['id']: f for f in catalog['definitions']}
        source_box = ttk.Combobox(root, state='readonly', width=120,
            values=[p['key'] + ' / ' + t['name'] + ' / ' + f + ' / ' + definitions.get(f, {}).get('name', '') for p,t,f in sources])
        source_box.pack(fill='x')
        target_items = [(t, f) for t in catalog['targets'] for f in t['fields']]
        target_box = ttk.Combobox(root, state='readonly', width=120,
            values=[str(t['id']) + ' / ' + str(f.get('id', f.get('referenceId'))) + ' / ' + str(f.get('label', f.get('name', ''))) for t,f in target_items])
        target_box.pack(fill='x')
        details = tk.Text(root, height=15, wrap='word')
        details.pack(fill='both', expand=True)
        def show(event=None):
            details.delete('1.0', 'end')
            if source_box.current() >= 0:
                p,t,f = sources[source_box.current()]
                key = p['key'] + ':' + t['id']
                mapping = (overlay['migration'].get('run_adapter', {}).get('fields', {}) if t['id'] == '__run__' else overlay['migration']['field_mapping'].get(key, {})).get(f)
                if mapping:
                    conversion.set(mapping['type'])
                    target_key.delete(0, 'end'); target_key.insert(0, mapping['target'])
                    values.delete('1.0', 'end'); values.insert('1.0', json.dumps(mapping.get('value_mapping', {}), ensure_ascii=False, indent=2))
                details.insert('end', 'Jira 표본 값/정의:\n' + json.dumps({'definition': definitions.get(f), 'samples': t['fields'][f]}, ensure_ascii=False, indent=2))
            if target_box.current() >= 0:
                details.insert('end', '\nCodebeamer 필드 정의/선택지:\n' + json.dumps(target_items[target_box.current()][1], ensure_ascii=False, indent=2))
        source_box.bind('<<ComboboxSelected>>', show)
        target_box.bind('<<ComboboxSelected>>', show)
        ttk.Label(root, text='변환 / 대상 payload 키 (실제 API 계약 기준으로 입력)').pack()
        conversion = ttk.Combobox(root, state='readonly', values=['text','json','raw','number','boolean','date','datetime','user','status','option','reference'])
        conversion.set('text'); conversion.pack(fill='x')
        target_key = ttk.Entry(root); target_key.pack(fill='x')
        ttk.Label(root, text='필드별 값 매핑 JSON: {"원본 식별값": 대상값}. ID·객체 형식은 대상 API 계약을 따릅니다.').pack()
        values = tk.Text(root, height=5); values.insert('1.0', '{}'); values.pack(fill='x')
        summary = tk.Text(root, height=8); summary.pack(fill='both', expand=True)
        def add():
            try:
                if source_box.current() < 0 or target_box.current() < 0:
                    raise ValueError('원본과 대상 필드를 선택하세요.')
                p,t,f = sources[source_box.current()]
                tracker, field = target_items[target_box.current()]
                key = p['key'] + ':' + t['id']
                is_run = t['id'] == '__run__'
                previous = (overlay['migration'].get('xray_run_tracker') if is_run else overlay['migration']['tracker_mapping'].get(key))
                if previous is not None and previous != tracker['id']:
                    raise ValueError('같은 원본 유형은 하나의 대상 트래커로 연결하세요.')
                spec = build_spec(f, field, conversion.get(), target_key.get(), json.loads(values.get('1.0', 'end')))
                if is_run:
                    overlay['migration'].setdefault('run_adapter', {}).setdefault('fields', {})[f] = spec
                    overlay['migration']['xray_run_tracker'] = tracker['id']
                else:
                    overlay['migration']['field_mapping'].setdefault(key, {})[f] = spec
                    overlay['migration']['tracker_mapping'][key] = tracker['id']
                summary.insert('end', key + ' / ' + f + ' → ' + spec['target'] + '\n')
            except (ValueError, KeyError) as exc:
                messagebox.showerror('매핑 확인', str(exc))
        def save():
            errors = validate_overlay(overlay, catalog)
            summary.delete('1.0', 'end')
            summary.insert('end', '\n'.join(errors) if errors else '표본 필드 검사를 통과했습니다. API·관계·실제 쓰기는 별도 검증이 필요합니다.')
            if output.exists() and not messagebox.askyesno('저장', '기존 매핑 파일을 갱신할까요?'):
                return
            temporary = output.with_suffix(output.suffix + '.tmp')
            temporary.write_text(json.dumps(overlay, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(output)
            status.set(str(output) + ' 저장됨 / 미해결 항목 ' + str(len(errors)) + '개. 인증정보·표본 값은 저장하지 않았습니다.')
        def remove():
            if source_box.current() < 0:
                return
            p,t,f = sources[source_box.current()]
            mappings = (overlay['migration'].get('run_adapter', {}).get('fields', {}) if t['id'] == '__run__' else overlay['migration']['field_mapping'].get(p['key'] + ':' + t['id'], {}))
            mappings.pop(f, None)
            summary.insert('end', f + ' 매핑 삭제됨\n')
        ttk.Button(root, text='선택한 필드 매핑 추가/갱신', command=add).pack(fill='x')
        ttk.Button(root, text='선택한 원본 필드의 매핑 삭제', command=remove).pack(fill='x')
        ttk.Button(root, text='누락 검사 후 로컬 매핑 저장 (서버 쓰기 없음)', command=save).pack(fill='x')
        if catalog.get('warnings'):
            summary.insert('1.0', '일부 조회 실패 — 조회된 필드만 표시합니다.\n' + '\n'.join(catalog['warnings']) + '\n')
        if sources:
            source_box.current(0)
        if target_items:
            target_box.current(0)
        show()
        status.set('조회 완료. 프로젝트 ' + str(len(catalog['projects'])) + '개 / 트래커 ' + str(len(catalog['targets'])) + '개 / 경고 ' + str(len(catalog.get('warnings', []))) + '건')
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
