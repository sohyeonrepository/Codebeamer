"""Tk import wizard; credentials remain in memory and all analysis is GET-only."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

from clients.http import Http, ApiError
from clients.jira_client import JiraClient
from clients.codebeamer_client import CodebeamerClient
from migration.wizard import (analyze, atomic_json, public_profile, project_lists,
                              tracker_list, assert_importable, fingerprint)
import mapping_helper


class Wizard:
    def __init__(self, root, config_path='config.local.json', workspace='.migration/wizard'):
        self.root, self.config_path = root, Path(config_path)
        self.workspace = Path(workspace)
        template = self.config_path if self.config_path.exists() else Path(__file__).with_name('config.json')
        self.config = json.loads(template.read_text(encoding='utf-8-sig'))
        self.analysis_dir = self.workspace
        profile = self.workspace / 'connection.local.json'
        if profile.exists():
            stored = public_profile(json.loads(profile.read_text(encoding='utf-8-sig')))
            for service in ('jira', 'codebeamer'):
                self.config[service].update(stored[service])
        # Inline secrets in old hand-written configs are never persisted by this UI.
        for service in ('jira', 'codebeamer'):
            for key in ('password', 'token', 'authorization', 'api_key', 'secret'):
                self.config[service].pop(key, None)
        self.plan = self.catalog = self.clients = None
        self.jira_projects, self.cb_projects, self.trackers = [], [], []
        self.events, self.busy = queue.Queue(), False
        self.connection_vars = {}
        import tkinter.font as tkfont
        families = set(tkfont.families(root))
        font = next((name for name in ('Malgun Gothic', '맑은 고딕', 'Noto Sans CJK KR', 'unifont') if name in families), 'Segoe UI')
        for name in ('TkDefaultFont', 'TkTextFont', 'TkHeadingFont'):
            tkfont.nametofont(name).configure(family=font, size=10)
        root.title('Jira → Codebeamer Import Wizard')
        root.geometry('1120x800'); root.minsize(900, 650)
        self.tabs = ttk.Notebook(root); self.tabs.pack(fill='both', expand=True, padx=16, pady=12)
        self.pages = [ttk.Frame(self.tabs, padding=18) for _ in range(4)]
        for page, label in zip(self.pages, ['1 연결 설정', '2 프로젝트 선택', '3 자동 매핑', '4 Import · 결과']):
            self.tabs.add(page, text=label)
        self.status = tk.StringVar(value='연결 정보를 입력하고 연결 테스트를 실행하세요.')
        ttk.Label(root, textvariable=self.status, wraplength=1060).pack(fill='x', padx=20, pady=8)
        self.build_connection(); self.build_projects(); self.build_mapping(); self.build_import()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_handle = root.after(100, self.poll)
        root.bind('<Destroy>', self.cleanup, add='+')

    def cleanup(self, event):
        if event.widget is self.root:
            try: self.root.after_cancel(self.poll_handle)
            except tk.TclError: pass

    def close(self):
        if self.busy:
            self.status.set('처리 중에는 종료할 수 없습니다. 완료 후 닫아주세요.'); return
        for values in self.connection_vars.values():
            for key in ('password', 'token'): values[key].set('')
        self.clients = None
        self.root.destroy()

    def invalidate(self, *args):
        self.plan = self.catalog = None
        if hasattr(self, 'import_button'): self.import_button.configure(state='disabled')

    def build_connection(self):
        page = self.pages[0]
        ttk.Label(page, text='연결 정보', font=('', 17)).pack(anchor='w', pady=(0, 12))
        ttk.Label(page, text='비밀번호·토큰은 메모리에서만 사용합니다. 재실행 시 환경변수 또는 재입력으로 인증합니다.').pack(anchor='w')
        for service, title, default in [('jira', 'Jira Data Center', 'bearer'), ('codebeamer', 'Codebeamer 3.0', 'basic')]:
            box = ttk.LabelFrame(page, text=title, padding=12); box.pack(fill='x', pady=10)
            settings = self.config[service]
            values = self.connection_vars[service] = {}
            for index, (key, label) in enumerate([('url', '서버 URL'), ('auth', '인증 방식'), ('username', '사용자 이름'), ('password', '비밀번호'), ('token', 'API 토큰')]):
                env_key = {'username': 'username_env', 'password': 'password_env', 'token': 'token_env'}.get(key)
                value = os.environ.get(settings.get(env_key, ''), '') if env_key else settings.get(key, default if key == 'auth' else '')
                values[key] = tk.StringVar(value=value)
                ttk.Label(box, text=label).grid(row=index, column=0, sticky='w', padx=(0, 16), pady=3)
                if key == 'auth':
                    widget = ttk.Combobox(box, textvariable=values[key], state='readonly', values=['bearer', 'basic'])
                else:
                    widget = ttk.Entry(box, textvariable=values[key], show='●' if key in ('password', 'token') else '')
                widget.grid(row=index, column=1, sticky='ew', pady=3)
                values[key].trace_add('write', self.connection_changed)
            box.columnconfigure(1, weight=1)
        ttk.Button(page, text='연결 테스트 · 프로젝트 조회', command=self.connect).pack(anchor='e', pady=10)

    def connection_changed(self, *args):
        self.clients = None
        self.invalidate()

    def current_connections(self):
        import base64
        result = []
        updated = deepcopy(self.config)
        for service in ('jira', 'codebeamer'):
            values = {key: variable.get().strip() if key not in ('password', 'token') else variable.get() for key, variable in self.connection_vars[service].items()}
            settings = updated[service]
            settings.update(url=values['url'], auth=values['auth'])
            public_profile(updated)  # Reject URL credentials before creating any request.
            http = Http(values['url'], auth='none', log=self.workspace / 'requests.jsonl', allow_http=settings.get('allow_http', False))
            http.url('/')
            if values['auth'] == 'basic':
                if not values['username'] or not values['password'] or ':' in values['username']:
                    raise ApiError('사용자 이름과 비밀번호를 입력하세요.')
                http.authorization = 'Basic ' + base64.b64encode((values['username'] + ':' + values['password']).encode()).decode('ascii')
            else:
                if not values['token']: raise ApiError('API 토큰을 입력하세요.')
                http.authorization = 'Bearer ' + values['token']
            result.append(http)
        return updated, JiraClient(result[0], updated['jira']), CodebeamerClient(result[1], updated['codebeamer'], False)

    def connect(self):
        try: config, jira, cb = self.current_connections()
        except (ApiError, ValueError) as exc: self.status.set(str(exc)); return
        def work():
            lists = project_lists(jira, cb)
            return config, jira, cb, lists
        self.run('connect', work)

    def build_projects(self):
        page = self.pages[1]
        ttk.Label(page, text='기존 프로젝트 선택', font=('', 17)).pack(anchor='w', pady=10)
        ttk.Label(page, text='Codebeamer 프로젝트는 조회한 기존 프로젝트만 선택할 수 있습니다. 신규 프로젝트를 만들지 않습니다.').pack(anchor='w')
        self.project_boxes = []
        for title in ('원본 Jira 프로젝트', '대상 Codebeamer 프로젝트'):
            ttk.Label(page, text=title).pack(anchor='w', pady=(20, 5))
            box = ttk.Combobox(page, state='readonly'); box.pack(fill='x')
            box.bind('<<ComboboxSelected>>', self.selection_changed)
            self.project_boxes.append(box)
        ttk.Button(page, text='전체 데이터 사전 분석 (서버 쓰기 없음)', command=self.start_analysis).pack(anchor='e', pady=24)

    def selection_changed(self, event=None):
        self.invalidate()

    def build_mapping(self):
        page = self.pages[2]
        self.counts = tk.StringVar(value='아직 분석하지 않았습니다.')
        ttk.Label(page, textvariable=self.counts, font=('', 13)).pack(anchor='w', pady=10)
        self.details = tk.BooleanVar(value=False)
        ttk.Checkbutton(page, text='전체 매핑 상세 보기', variable=self.details, command=self.render_plan).pack(anchor='w')
        self.table = ttk.Treeview(page, columns=('scope', 'target', 'state', 'reason'), show='tree headings', height=12)
        for key, text, width in [('#0', '원본 필드 / 유형', 200), ('scope', '범위', 120), ('target', '대상', 110), ('state', '상태', 100), ('reason', '확인 사항', 350)]:
            self.table.heading(key, text=text); self.table.column(key, width=width, minwidth=80)
        self.table.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(page, orient='horizontal', command=self.table.xview); scroll.pack(fill='x')
        self.table.configure(xscrollcommand=scroll.set)
        ttk.Button(page, text='예외 매핑 수정', command=self.edit_exceptions).pack(anchor='e', pady=10)
        ttk.Button(page, text='저장한 매핑으로 전체 재분석', command=self.start_analysis).pack(anchor='e')
        ttk.Label(page, text='생성안은 서버 변경이 아닙니다. 타입·API 계약을 확인하지 못한 생성안과 미지원 기능은 Import를 차단합니다.', wraplength=1000).pack(anchor='w', pady=10)

    def build_import(self):
        page = self.pages[3]
        self.progress = ttk.Progressbar(page, mode='indeterminate'); self.progress.pack(fill='x', pady=10)
        self.metrics = tk.StringVar(value='성공 0 · 실패 0 · 아직 실행하지 않음')
        ttk.Label(page, textvariable=self.metrics).pack(anchor='w')
        self.stage_table = ttk.Treeview(page, columns=('status',), show='tree headings', height=10)
        self.stage_table.heading('#0', text='처리 단계'); self.stage_table.column('#0', width=720)
        self.stage_table.heading('status', text='구현 / 실행 상태'); self.stage_table.pack(fill='x', pady=12)
        self.blockers = tk.Text(page, height=10, wrap='word', state='disabled'); self.blockers.pack(fill='both', expand=True)
        self.import_button = ttk.Button(page, text='Import', command=self.import_data, state='disabled'); self.import_button.pack(anchor='e', pady=10)
        ttk.Label(page, text='사전 분석과 Import는 별개입니다. 미해결 항목이 하나라도 있으면 실제 실행할 수 없습니다.').pack(anchor='w')

    def run(self, kind, work):
        if self.busy: self.status.set('현재 작업이 끝난 뒤 진행하세요.'); return
        self.busy = True; self.progress.start(); self.status.set('처리 중: ' + kind)
        # Disable form edits during a worker so an old result cannot activate a new selection.
        self.locked_widgets = []
        def lock(widget):
            if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Checkbutton)):
                self.locked_widgets.append((widget, widget.state()))
                widget.state(['disabled'])
            for child in widget.winfo_children(): lock(child)
        lock(self.tabs)
        def worker():
            try: self.events.put((kind, work(), None))
            except Exception as exc:
                self.events.put((kind, None, str(exc) if isinstance(exc, ApiError) else type(exc).__name__))
        threading.Thread(target=worker, daemon=True).start()

    def start_analysis(self):
        if self.busy: return
        if not self.clients: self.status.set('먼저 연결 테스트를 실행하세요.'); return
        a, b = (box.current() for box in self.project_boxes)
        if a < 0 or b < 0: self.status.set('두 프로젝트를 모두 선택하세요.'); return
        self.invalidate()
        self.config['jira']['projects'] = [self.jira_projects[a]['key']]
        self.config['jira'].pop('execution_issue_types', None)
        jira, cb = self.clients
        jira.config = self.config['jira']
        target = self.cb_projects[b]['id']
        selected_key = fingerprint([self.config['jira']['url'], self.config['codebeamer']['url'], self.jira_projects[a]['key'], target])[:24]
        self.analysis_dir = self.workspace / selected_key
        self.config['migration']['state_dir'] = str(self.analysis_dir / 'import-state')
        self.config['migration'].setdefault('project_mapping', {})[self.jira_projects[a]['key']] = target
        config = deepcopy(self.config)
        output = self.analysis_dir / 'mapping.local.json'
        try:
            saved = json.loads(output.read_text(encoding='utf-8-sig')) if output.exists() else None
        except (OSError, ValueError):
            self.status.set('저장된 매핑 파일을 읽을 수 없습니다. 분석 폴더의 파일 상태를 확인하세요.'); return
        def work():
            trackers = tracker_list(cb, target)
            plan, catalog = analyze(config, jira, cb, target, trackers, saved,
                progress=lambda **event: self.events.put(('progress', event, None)))
            atomic_json(self.analysis_dir / 'analysis-report.json', plan)
            atomic_json(output, plan['overlay'])
            atomic_json(self.analysis_dir / 'config.local.json', config)
            return plan, catalog, trackers
        self.run('analysis', work)

    def render_plan(self):
        if not self.plan: return
        summary = self.plan['summary']
        self.counts.set('필드 전체 {total} · 자동 매핑 {auto} · 신규 생성 예정 {create} · 확인 필요 {review}'.format(**summary)
                        + '  |  Tracker 생성안 {trackers} · Choice 생성안 {choices}'.format(**self.plan.get('additional_creations', {'trackers': 0, 'choices': 0})))
        self.table.delete(*self.table.get_children())
        for row in self.plan['rows']:
            if row['state'] == 'auto' and not self.details.get(): continue
            self.table.insert('', 'end', text=row.get('label', row['source']), values=(row['scope'], row.get('target', ''), {'auto':'자동 매핑', 'create':'생성안', 'review':'확인 필요'}[row['state']], row['reason']))
        self.stage_table.delete(*self.stage_table.get_children())
        for stage in self.plan['stages']:
            self.stage_table.insert('', 'end', text=stage['name'], values=('미구현 · 차단' if stage['status'] == 'blocked' else '서버 어댑터 검증 필요',))
        self.blockers.configure(state='normal'); self.blockers.delete('1.0', 'end')
        self.blockers.insert('end', '\n'.join(self.plan['blockers']) or '미해결 항목 없음')
        self.blockers.configure(state='disabled')
        self.import_button.configure(state='normal' if self.plan['ready'] and not self.busy else 'disabled')

    def edit_exceptions(self):
        if self.busy: return
        if not self.plan or not self.trackers: self.status.set('먼저 분석하고 대상 Tracker를 확인하세요.'); return
        argv = ['--config', str(self.analysis_dir / 'config.local.json'), '--output', str(self.analysis_dir / 'mapping.local.json'), '--analysis-only', '--exceptions-only']
        for tracker in self.trackers: argv.extend(['--tracker-id', str(tracker['id'])])
        catalog = self.catalog
        exceptions = {(row['scope'],row['source']) for row in self.plan['rows'] if row['state'] != 'auto'}
        self.invalidate()
        mapping_helper.main(argv, clients=self.clients, parent=self.root, catalog_override=catalog, exception_sources=exceptions)
        self.status.set('예외 매핑을 저장했다면 전체 재분석을 실행하세요.')

    def import_data(self):
        try: assert_importable(self.plan or {}, self.config)
        except ApiError as exc: self.status.set(str(exc)); return
        # Use the existing CLI entry point and Engine; never implement a second importer.
        import main
        self.run('import', lambda: main.main(['migrate', '--config', str(self.analysis_dir / 'config.local.json'),
            '--mapping', str(self.analysis_dir / 'mapping.local.json'), '--apply',
            '--wizard-plan', str(self.analysis_dir / 'analysis-report.json')],
            clients=self.clients, progress=lambda **event: self.events.put(('progress', event, None))))

    def poll(self):
        try:
            while True:
                kind, result, error = self.events.get_nowait()
                if kind == 'progress':
                    self.metrics.set('처리 {processed}건 · 성공 {success} · 실패 {failed} · 단계 {stage}'.format(processed=result.get('processed', 0), success=result.get('success', 0), failed=result.get('failed', 0), stage=result.get('stage', 'analysis')))
                    continue
                self.busy = False; self.progress.stop()
                for widget, state in self.locked_widgets:
                    widget.state(['!disabled', '!readonly'])
                    widget.state(state)
                if error:
                    self.invalidate(); self.status.set('실패: ' + error); continue
                if kind == 'connect':
                    self.config, jira, cb, lists = result
                    self.clients = jira, cb
                    self.jira_projects, self.cb_projects = lists
                    for box, projects in zip(self.project_boxes, lists):
                        box.configure(values=[p.get('name', '') + ' (' + str(p.get('key', p.get('id'))) + ')' for p in projects]); box.set('')
                    atomic_json(self.workspace / 'connection.local.json', public_profile(self.config))
                    self.tabs.select(self.pages[1]); self.status.set('연결 성공. 프로젝트를 선택하세요.')
                elif kind == 'analysis':
                    self.plan, self.catalog, self.trackers = result
                    self.render_plan(); self.tabs.select(self.pages[2])
                    self.status.set('전체 분석 완료 · 서버 쓰기 0 · Import 차단 사유 ' + str(len(self.plan['blockers'])) + '건')
                else:
                    self.status.set('Import 종료. report.json을 확인하세요.')
        except queue.Empty: pass
        self.poll_handle = self.root.after(100, self.poll)


def main():
    parser = argparse.ArgumentParser(description='Jira → Codebeamer Import Wizard')
    parser.add_argument('--config', default='config.local.json')
    parser.add_argument('--workspace', default='.migration/wizard')
    args = parser.parse_args()
    root = tk.Tk()
    Wizard(root, args.config, args.workspace)
    root.mainloop()


if __name__ == '__main__': main()
