"""Step 2 project/Tracker selection, Step 3 scoped mapping, approved Import.

The older mapping_helper.py remains available for existing Run-only workflows.
"""
import argparse
import json
from pathlib import Path
from clients.http import Http, ApiError
from clients.jira_client import JiraClient
from clients.codebeamer_client import CodebeamerClient
from migration.state import State
from migration.wizard import ImportWizard, field_identifier, semantic_type, hidden
from mapping_helper import label_tokens, source_choices


def launch(wizard):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title('Jira → Codebeamer Import Wizard')
    root.geometry('1120x800')
    status = tk.StringVar(value='프로젝트 조회를 시작하세요. 분석은 읽기 전용입니다.')
    tabs = ttk.Notebook(root)
    tabs.pack(fill='both', expand=True, padx=12, pady=12)
    step2, step3, step4 = (ttk.Frame(tabs, padding=12) for _ in range(3))
    for frame, title in zip((step2, step3, step4), ('STEP 2 · 프로젝트 / Tracker', 'STEP 3 · 필드 매핑', 'Import 결과')):
        tabs.add(frame, text=title)
    ttk.Label(root, textvariable=status, wraplength=1080).pack(fill='x', padx=12, pady=8)
    evidence = tk.Text(step4, wrap='word')
    evidence.pack(fill='both', expand=True)
    projects = {}
    type_rows = {}
    scope_rows = {}
    def run(label, action):
        status.set(label + '…'); root.update_idletasks()
        try:
            value = action()
            if not (isinstance(value, dict) and value.get('complete') is False):
                status.set(label + ' 완료')
            show_evidence(value if isinstance(value, dict) and 'complete' in value else None)
            return value
        except Exception as exc:
            status.set(label + ' 실패: ' + (str(exc) if isinstance(exc, (ApiError, ValueError)) else type(exc).__name__))
            return None
    def show_evidence(report=None):
        evidence.delete('1.0', 'end')
        evidence.insert('end', json.dumps(report or wizard.plan['stages'], ensure_ascii=False, indent=2))
    top = ttk.Frame(step2); top.pack(fill='x')
    jira_box = ttk.Combobox(top, state='readonly', width=35)
    cb_box = ttk.Combobox(top, state='readonly', width=45)
    ttk.Label(top, text='Jira 프로젝트').grid(row=0, column=0, sticky='w')
    ttk.Label(top, text='기존 Codebeamer 프로젝트').grid(row=0, column=1, sticky='w')
    jira_box.grid(row=1, column=0, padx=(0,12)); cb_box.grid(row=1, column=1)
    types = ttk.Treeview(step2, columns=('tracker',), show='tree headings', selectmode='browse')
    types.heading('#0', text='Jira Issue Type 전체 목록')
    types.heading('tracker', text='Codebeamer Tracker')
    types.pack(fill='both', expand=True, pady=12)
    tracker_box = ttk.Combobox(step2, state='readonly', width=65)
    tracker_box.pack(anchor='w')
    new_name = tk.StringVar()
    ttk.Label(step2, text='신규 Tracker 이름 (선택한 기존 프로젝트 내부에 생성)').pack(anchor='w', pady=(8,0))
    ttk.Entry(step2, textvariable=new_name, width=55).pack(anchor='w')
    def discover():
        result = run('프로젝트 조회', wizard.discover)
        if result is not None:
            projects.update(result)
            jira_box.configure(values=[p['key'] for p in projects['jira']])
            cb_box.configure(values=[str(p['id']) + ' / ' + p.get('name','') for p in projects['codebeamer']])
    ttk.Button(top, text='프로젝트 조회', command=discover).grid(row=1, column=2, padx=8)
    def select_project():
        if jira_box.current() < 0 or cb_box.current() < 0:
            raise ValueError('두 프로젝트를 선택하세요.')
        project = projects['jira'][jira_box.current()]['key']
        target = projects['codebeamer'][cb_box.current()]['id']
        trackers = wizard.select_project(project, target)
        type_rows.clear(); types.delete(*types.get_children())
        for index, kind in enumerate(wizard.plan['projects'][project]['types']):
            iid = 'type-' + str(index); type_rows[iid] = (project, kind)
            types.insert('', 'end', iid=iid, text=kind['name'], values=('미선택',))
        tracker_box.configure(values=[str(t['id']) + ' / ' + t.get('name','') for t in trackers] + ['＋ 새 Tracker 생성'])
        tracker_box.set('')
        scopes.configure(values=[]); scopes.set(''); table.delete(*table.get_children())
    ttk.Button(top, text='Issue Type / Tracker 조회', command=lambda: run('유형·Tracker 조회', select_project)).grid(row=2, column=0, pady=8)
    def map_tracker():
        if not types.selection() or tracker_box.current() < 0:
            raise ValueError('Issue Type과 Tracker를 선택하세요.')
        iid = types.selection()[0]
        project, kind = type_rows[iid]
        trackers = wizard.plan['projects'][project]['trackers']
        index = tracker_box.current()
        tracker = trackers[index]['id'] if index < len(trackers) else None
        wizard.map_tracker(project, kind['id'], tracker, new_name.get())
        types.set(iid, 'tracker', tracker_box.get() if tracker else '신규: ' + new_name.get())
        scopes.configure(values=list(wizard.plan['scopes']))
    ttk.Button(step2, text='이 Issue Type의 Tracker 매핑 저장', command=lambda: run('Tracker 매핑 저장', map_tracker)).pack(anchor='w', pady=8)
    ttk.Label(step3, text='왼쪽은 선택한 CB Tracker, 오른쪽은 선택한 Jira Issue Type입니다. 행을 두 번 클릭하면 수동 연결합니다.').pack(anchor='w')
    scopes = ttk.Combobox(step3, state='readonly', width=55)
    scopes.pack(anchor='w', pady=8)
    single_key = tk.StringVar()
    ttk.Label(step3,text='실서버 테스트 범위: Jira Issue 키 1개 (기존 테스트 Tracker에 먼저 매핑)').pack(anchor='w')
    ttk.Entry(step3,textvariable=single_key,width=35).pack(anchor='w')
    def bound_issue():
        scope = wizard.limit_to_issue(single_key.get().strip())
        scopes.configure(values=[scope]);scopes.set(scope)
        table.delete(*table.get_children())
    ttk.Button(step3,text='이 Issue 1건으로 전체 Import 범위 제한',command=lambda:run('1건 범위 설정',bound_issue)).pack(anchor='w',pady=6)
    table = ttk.Treeview(step3, columns=('cb','jira'), displaycolumns=('cb','jira'), show='headings', selectmode='browse')
    table.heading('cb', text='Codebeamer Tracker 필드')
    table.heading('jira', text='Jira Issue Type 필드')
    table.column('cb', width=470); table.column('jira', width=470)
    table.pack(fill='both', expand=True)
    detail = tk.StringVar(value='Attachment · Comment · Linked Issue · History · R4J는 별도로 자동 이관합니다.')
    ttk.Label(step3, textvariable=detail, wraplength=1050).pack(anchor='w', pady=8)
    def display_scope():
        entry = wizard.plan['scopes'][scopes.get()]
        scope_rows.clear(); table.delete(*table.get_children())
        for index, (source, row) in enumerate(entry.get('rows', {}).items()):
            iid = 'field-' + str(index); scope_rows[iid] = source
            target = row.get('target', {})
            left = target.get('label', target.get('name', ''))
            if row['status'] == 'create': left = '＋ 신규 필드 생성안: ' + row['creation']['label']
            right = entry['analysis']['fields'][source].get('name', source)
            table.insert('', 'end', iid=iid, values=(left or '수동 연결 필요', right), tags=(row['status'],))
        table.tag_configure('mapped', foreground='#17734b')
        table.tag_configure('error', foreground='#ad2222')
        detail.set('조회 Issue ' + str(entry['analysis']['issue_count']) + '건 · Hidden 값 누락 위험 ' + str(len(entry['analysis']['risks']) + len(entry.get('target_risks', []))) + '건')
    def analyze():
        if not scopes.get(): raise ValueError('Tracker ↔ Issue Type 조합을 선택하세요.')
        wizard.analyze(scopes.get()); display_scope()
    ttk.Button(step3, text='선택 조합 분석', command=lambda: run('필드 분석', analyze)).pack(anchor='w')
    scopes.bind('<<ComboboxSelected>>', lambda event: table.delete(*table.get_children()))
    def inspect(event=None):
        if table.selection():
            row = wizard.plan['scopes'][scopes.get()]['rows'][scope_rows[table.selection()[0]]]
            detail.set(row['status'] + ' · ' + row['reason'])
    table.bind('<<TreeviewSelect>>', inspect)
    def edit(event=None):
        if not table.selection(): return
        scope = scopes.get(); entry = wizard.plan['scopes'][scope]
        source = scope_rows[table.selection()[0]]
        fields = [f for f in entry['target_fields'] if not hidden(f)]
        dialog = tk.Toplevel(root); dialog.title('필드 수동 연결')
        ttk.Label(dialog, text=entry['analysis']['fields'][source].get('name',source)).pack(padx=12, pady=8)
        box = ttk.Combobox(dialog, state='readonly', width=55, values=[f.get('label',f.get('name','')) for f in fields])
        box.pack(padx=12)
        value_frame = ttk.Frame(dialog); value_frame.pack(fill='both', padx=12, pady=12)
        choices = []
        def fill(event=None):
            for child in value_frame.winfo_children(): child.destroy()
            choices.clear()
            if box.current() < 0: return
            target = fields[box.current()]
            kind, _ = semantic_type(entry['analysis']['fields'][source], True)
            if kind in ('option','status'):
                options = target.get('options') or []
                values = source_choices(entry['analysis']['values'].get(source, []) + entry['analysis']['fields'][source].get('allowedValues', []))
                for identity, label in values.items():
                    ttk.Label(value_frame, text=label).pack(anchor='w')
                    choice = ttk.Combobox(value_frame, state='readonly', values=[str(o.get('name',o.get('label',''))) for o in options])
                    choice.pack(fill='x'); choices.append((identity,choice,options))
        box.bind('<<ComboboxSelected>>', fill)
        def accept():
            if box.current() < 0: raise ValueError('CB 필드를 선택하세요.')
            target = fields[box.current()]; fid = field_identifier(target)
            bindings = wizard.config['codebeamer'].get('field_bindings', {})
            keys = [k for k,b in bindings.items() if b.get('fieldId') == fid]
            canonical = label_tokens('', target) & {'name','description'}
            key = keys[0] if len(keys) == 1 else (next(iter(canonical)) if canonical else '')
            kind, _ = semantic_type(entry['analysis']['fields'][source], True)
            mapping = dict(wizard.config['migration'].get('user_mapping' if kind == 'user' else 'status_mapping', {}))
            for identity, choice, options in choices:
                if choice.current() < 0: raise ValueError('모든 원본 선택지의 대상을 선택하세요.')
                mapping[identity] = {'id': options[choice.current()]['id']}
            wizard.manual_mapping(scope,source,fid,key,mapping)
            display_scope(); dialog.destroy()
        ttk.Button(dialog, text='연결 저장', command=lambda: run('수동 연결', accept)).pack(pady=12)
    table.bind('<Double-1>', edit)
    def import_now():
        errors = wizard.errors()
        if errors: raise ApiError('\n'.join(errors))
        if not messagebox.askyesno('Import 승인', '선택한 기존 프로젝트에 Tracker, Field, Item 및 관련 데이터를 실제로 생성합니다. Import를 승인하시겠습니까?'):
            return
        report = wizard.execute(approved=True)
        show_evidence(report); tabs.select(step4)
        if not report['complete']: status.set('Import 미완료: 오류·누락·보존 결과를 확인한 후 재시도하세요.')
    ttk.Button(step3, text='Import…', command=lambda: run('Import', import_now)).pack(anchor='e', pady=8)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Jira → Codebeamer Import Wizard')
    parser.add_argument('--config', default='config.local.json')
    parser.add_argument('--prompt-auth', action='store_true')
    parser.add_argument('--single-issue', help='Exact Jira key for a bounded live creation check')
    parser.add_argument('--live-test',action='store_true',help='Enter URLs and choose the real test project/Tracker and one Jira Issue in the UI')
    parser.add_argument('--test-project-id', type=int)
    parser.add_argument('--test-tracker-id', type=int)
    parser.add_argument('--test-state-dir', default='.migration/single-import')
    parser.add_argument('--preflight-only', action='store_true', help='Read real APIs and save requests/blockers without any writes')
    args = parser.parse_args(argv)
    if args.single_issue and (not args.test_project_id or not args.test_tracker_id):
        parser.error('--single-issue requires --test-project-id and --test-tracker-id')
    if not args.single_issue and (args.preflight_only or args.test_project_id or args.test_tracker_id):
        parser.error('Test flags require --single-issue')
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    if args.live_test:
        if args.single_issue or args.preflight_only:
            parser.error('--live-test is the interactive alternative to --single-issue/--preflight-only')
        from single_import import launch_live_setup
        return launch_live_setup(config,args.test_state_dir,args.prompt_auth)
    state = State(args.test_state_dir if args.single_issue else config['migration'].get('state_dir', '.migration/import-wizard'))
    def http(settings):
        return Http(settings['url'], settings.get('token_env'), state.root / 'requests.jsonl',
                    auth=settings.get('auth', 'bearer'), username_env=settings.get('username_env'),
                    password_env=settings.get('password_env'), allow_http=settings.get('allow_http', False),
                    prompt_auth=args.prompt_auth)
    try:
        if args.single_issue:
            import os
            import urllib.parse
            blockers = []
            for service in ('jira','codebeamer'):
                settings = config.get(service,{})
                url = urllib.parse.urlsplit(settings.get('url',''))
                if url.scheme not in ('http','https') or not url.netloc:
                    blockers.append({'stage':service + '-url','reason':'A real HTTP(S) base URL is required'})
                required = ([settings.get('username_env'),settings.get('password_env')] if settings.get('auth') == 'basic'
                            else [settings.get('token_env')] if settings.get('auth','bearer') == 'bearer' else [])
                if not args.prompt_auth and any(not key or not os.environ.get(key) for key in required):
                    blockers.append({'stage':service + '-authentication','reason':'Configured authentication variables are missing; use secure environment variables or --prompt-auth'})
            if blockers:
                report = {'complete':False,'full_migration_complete':False,'blockers':blockers}
                (state.root / 'single-import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False,indent=2))
                return 2
            from single_import import SingleImport, launch_single
            test = SingleImport(JiraClient(http(config['jira']), config['jira'], state.save),
                CodebeamerClient(http(config['codebeamer']), dict(config['codebeamer']), False),
                state, config, args.single_issue, args.test_project_id, args.test_tracker_id)
            if args.preflight_only:
                report = test.prepare()
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2 if report['blockers'] else 0
            else:
                launch_single(test)
            return 0
        wizard = ImportWizard(JiraClient(http(config['jira']), config['jira'], state.save),
                              CodebeamerClient(http(config['codebeamer']), config['codebeamer'], False), state, config)
        launch(wizard)
    except (ApiError, OSError, ValueError, KeyError) as exc:
        if not args.single_issue:
            raise
        report = {'complete': False, 'full_migration_complete': False,
                  'blockers': [{'stage':'startup-or-authentication', 'reason':str(exc) if isinstance(exc,ApiError) else type(exc).__name__}]}
        (state.root / 'single-import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 2
    finally:
        state.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
