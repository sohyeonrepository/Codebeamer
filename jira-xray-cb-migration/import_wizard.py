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
from migration.wizard import ImportWizard, field_identifier, semantic_type, hidden, compatible
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
    style=ttk.Style(root)
    style.configure('WizardTitle.TLabel',font=('',17,'bold'))
    style.configure('WizardSub.TLabel',foreground='#485568')
    style.configure('Auto.TLabel',foreground='#16743b')
    style.configure('Manual.TLabel',foreground='#075fc9')
    style.configure('Create.TLabel',foreground='#aa5700')
    title=ttk.Frame(step3);title.pack(fill='x',pady=(0,10))
    ttk.Label(title,text='STEP 3. 필드 매핑',style='WizardTitle.TLabel').pack(anchor='w')
    ttk.Label(title,text='선택한 조합의 Jira 필드를 Codebeamer 필드와 매핑합니다.',style='WizardSub.TLabel').pack(anchor='w')
    target_bar=ttk.LabelFrame(step3,text='대상 조합',padding=8);target_bar.pack(fill='x')
    scopes = ttk.Combobox(target_bar, state='readonly', width=42)
    scopes.pack(side='left',padx=(0,8))
    scope_title=tk.StringVar(value='Jira Issue Type → CB Tracker')
    ttk.Label(target_bar,textvariable=scope_title).pack(side='left',padx=8)
    single_key = tk.StringVar()
    ttk.Label(target_bar,text='Jira Issue 1건 테스트').pack(side='left',padx=(20,4))
    ttk.Entry(target_bar,textvariable=single_key,width=18).pack(side='left')
    def bound_issue():
        scope = wizard.limit_to_issue(single_key.get().strip())
        scopes.configure(values=[scope]);scopes.set(scope)
        table.delete(*table.get_children())
    ttk.Button(target_bar,text='1건 범위 설정',command=lambda:run('1건 범위 설정',bound_issue)).pack(side='left',padx=6)
    ttk.Button(target_bar,text='선택 조합 분석',command=lambda:run('필드 분석',analyze)).pack(side='right')
    tools=ttk.Frame(step3);tools.pack(fill='x',pady=(10,4))
    active_filter=tk.StringVar(value='all')
    filter_buttons={}
    search=tk.StringVar()
    ttk.Label(tools,text='필드 검색').pack(side='right',padx=(8,4))
    search_entry=ttk.Entry(tools,textvariable=search,width=28);search_entry.pack(side='right')
    table_frame=ttk.Frame(step3);table_frame.pack(fill='both',expand=True)
    table = ttk.Treeview(table_frame, columns=('cb','cb_type','status','jira','action'),
                         displaycolumns=('cb','cb_type','status','jira','action'),show='headings',selectmode='browse')
    for column,text,width in (('cb','Codebeamer 필드',260),('cb_type','타입',90),('status','매핑 상태',120),
                              ('jira','Jira 필드 (실제 필드명)',330),('action','작업',150)):
        table.heading(column,text=text);table.column(column,width=width,anchor='w')
    scrollbar=ttk.Scrollbar(table_frame,orient='vertical',command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    table.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
    table.tag_configure('auto',foreground='#16743b')
    table.tag_configure('manual',foreground='#075fc9')
    table.tag_configure('create',foreground='#aa5700')
    table.tag_configure('error',foreground='#ad2222')
    action_bar=ttk.Frame(step3);action_bar.pack(fill='x',pady=6)
    manual_button=ttk.Button(action_bar,text='선택 필드 매핑 변경')
    create_button=ttk.Button(action_bar,text='＋ 신규 필드 생성')
    manual_button.pack(side='left')
    detail = tk.StringVar(value='Attachment · Comment · Linked Issue · History · R4J는 별도로 자동 이관합니다.')
    ttk.Label(step3,textvariable=detail,wraplength=1050).pack(anchor='w',pady=4)
    warning=tk.StringVar()
    ttk.Label(step3,textvariable=warning,foreground='#ad5b00',wraplength=1050).pack(anchor='w')
    def mode(row):
        if row.get('status')=='create':return 'create'
        if row.get('status')=='error':return 'error'
        if row.get('status')=='mapped':return row.get('mapping_mode') or ('auto' if row.get('reason')=='이름·타입 일치' else 'manual')
        return 'manual'
    def scope_names(entry):
        tracker=entry.get('new_tracker')
        if not tracker:
            tracker=next((t.get('name',str(t['id'])) for t in wizard.plan['projects'][entry['project']]['trackers']
                          if t['id']==entry.get('tracker')),'미선택')
        return entry['type'].get('name',str(entry['type']['id']))+'  →  '+tracker
    def display_scope():
        if not scopes.get() or scopes.get() not in wizard.plan['scopes']:
            table.delete(*table.get_children());return
        entry = wizard.plan['scopes'][scopes.get()]
        scope_title.set(scope_names(entry))
        scope_rows.clear(); table.delete(*table.get_children())
        counts={'all':0,'auto':0,'manual':0,'create':0,'error':0,'excluded':len(entry['analysis'].get('excluded',[]))}
        query=search.get().strip().casefold()
        for index, (source, row) in enumerate(entry.get('rows', {}).items()):
            row_mode=mode(row);counts['all']+=1;counts[row_mode]=counts.get(row_mode,0)+1
            target = row.get('target', {})
            left = target.get('label', target.get('name', ''))
            action='매핑 변경'
            if row_mode=='create':left='-';action='＋ 신규 필드 생성'
            definition=entry['analysis']['fields'][source]
            actual_name=definition.get('name',source)
            right=actual_name+('  ('+source+')' if source.startswith('customfield_') else '')
            cb_kind,_=semantic_type(target) if target else (None,None)
            if active_filter.get() not in ('all',row_mode):continue
            if query and query not in (left+' '+right+' '+str(cb_kind)).casefold():continue
            iid='field-'+str(index);scope_rows[iid]=source
            status_text={'auto':'✓ 자동 매핑','manual':'✎ 수동 매핑','create':'＋ 신규 생성 필요','error':'! 매핑 오류'}[row_mode]
            table.insert('', 'end',iid=iid,values=(left or '수동 연결 필요',cb_kind or '-',status_text,right,action),tags=(row_mode,))
        if active_filter.get()=='excluded':
            for index,item in enumerate(entry['analysis'].get('excluded',[])):
                right=item['name']+('  ('+item['field']+')' if item['field'].startswith('customfield_') else '')
                if query and query not in right.casefold():continue
                iid='excluded-'+str(index);scope_rows[iid]=None
                table.insert('','end',iid=iid,values=('-', '-', '제외 (Jira Hidden)',right,'-'),tags=('error' if item['has_value'] else 'manual',))
        labels={'all':'전체','auto':'자동 매핑','manual':'수동 매핑','create':'신규 생성','error':'오류','excluded':'제외'}
        for key,button in filter_buttons.items():button.configure(text=labels[key]+' ('+str(counts.get(key,0))+')')
        risks=entry['analysis']['risks']+entry.get('target_risks',[])
        detail.set('조회 Issue '+str(entry['analysis']['issue_count'])+'건 · Attachment · Comment · Association · History는 별도 자동 처리')
        warning.set(('⚠ Hidden 필드에 실제 값이 남아 있어 누락 위험 '+str(len(risks))+'건: '+
                     ', '.join(r['field'] for r in risks)) if risks else '')
        inspect()
    def analyze():
        if not scopes.get(): raise ValueError('Tracker ↔ Issue Type 조합을 선택하세요.')
        result=wizard.analyze(scopes.get());display_scope();return result
    for key in ('all','auto','manual','create','excluded','error'):
        button=ttk.Button(tools,text=key,command=lambda k=key:(active_filter.set(k),display_scope()))
        button.pack(side='left',padx=(0,4));filter_buttons[key]=button
    scopes.bind('<<ComboboxSelected>>',lambda event:display_scope())
    search.trace_add('write',lambda *_:display_scope())
    def inspect(event=None):
        if table.selection():
            source=scope_rows[table.selection()[0]]
            if source is None:
                detail.set('Jira Hidden 필드는 기본 매핑에서 제외됩니다.')
                create_button.pack_forget();manual_button.configure(state='disabled')
                return
            row = wizard.plan['scopes'][scopes.get()]['rows'][source]
            detail.set(row.get('reason',''))
            if row.get('status')=='create':
                manual_button.pack_forget()
                if not create_button.winfo_manager():create_button.pack(side='left')
                create_button.configure(state='normal')
            else:
                create_button.pack_forget()
                if not manual_button.winfo_manager():manual_button.pack(side='left')
                manual_button.configure(state='normal')
        else:
            create_button.pack_forget()
            if not manual_button.winfo_manager():manual_button.pack(side='left')
            manual_button.configure(state='disabled')
    table.bind('<<TreeviewSelect>>', inspect)
    def edit(event=None):
        if not table.selection(): return
        scope = scopes.get(); entry = wizard.plan['scopes'][scope]
        source = scope_rows[table.selection()[0]]
        if source is None:return
        source_kind,source_multiple=semantic_type(entry['analysis']['fields'][source],True)
        fields = [f for f in entry['target_fields'] if not hidden(f) and compatible(source_kind,source_multiple,f)]
        if not fields:raise ValueError('호환되는 기존 Codebeamer 필드가 없습니다. 신규 필드 생성을 사용하세요.')
        dialog = tk.Toplevel(root); dialog.title('필드 수동 연결')
        dialog.transient(root);dialog.grab_set()
        ttk.Label(dialog,text='Jira 필드: '+entry['analysis']['fields'][source].get('name',source),style='WizardTitle.TLabel').pack(padx=18,pady=12)
        ttk.Label(dialog,text='호환되는 기존 Codebeamer 필드').pack(anchor='w',padx=18)
        box = ttk.Combobox(dialog, state='readonly', width=55, values=[f.get('label',f.get('name','')) for f in fields])
        box.pack(padx=18,pady=6)
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
        ttk.Button(dialog,text='매핑 저장',command=lambda:run('수동 연결',accept)).pack(pady=12)
    def create_field_dialog(event=None):
        if not table.selection():return
        scope=scopes.get();entry=wizard.plan['scopes'][scope];source=scope_rows[table.selection()[0]]
        if source is None:return
        row=entry['rows'][source]
        if row.get('status')!='create':return
        definition=entry['analysis']['fields'][source]
        source_kind,source_multiple=semantic_type(definition,True)
        profiles=wizard.config['migration'].get('wizard',{}).get('field_types',{})
        dialog=tk.Toplevel(root);dialog.title('신규 Codebeamer 필드 생성 설정');dialog.transient(root);dialog.grab_set()
        body=ttk.Frame(dialog,padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='신규 Codebeamer 필드 생성 설정',style='WizardTitle.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,14))
        ttk.Label(body,text='필드 이름 *').grid(row=1,column=0,sticky='w',pady=5)
        name=tk.StringVar(value=row['creation'].get('label',definition.get('name',source)))
        ttk.Entry(body,textvariable=name,width=38).grid(row=1,column=1,sticky='ew',pady=5)
        ttk.Label(body,text='필드 타입 *').grid(row=2,column=0,sticky='w',pady=5)
        type_names=list(profiles)
        type_box=ttk.Combobox(body,state='readonly',values=type_names,width=35);type_box.grid(row=2,column=1,sticky='ew',pady=5)
        if source_kind in type_names:type_box.set(source_kind)
        multiple=tk.BooleanVar(value=bool(source_multiple))
        ttk.Checkbutton(body,text='다중 선택',variable=multiple,state='disabled').grid(row=3,column=1,sticky='w')
        ttk.Label(body,text='선택 옵션 (한 줄에 하나)').grid(row=4,column=0,columnspan=2,sticky='w',pady=(10,4))
        options=tk.Text(body,width=48,height=7)
        initial=list(dict.fromkeys(row['creation'].get('choices',{}).values()))
        options.insert('1.0','\n'.join(initial));options.grid(row=5,column=0,columnspan=2,sticky='nsew')
        if source_kind not in ('option','status'):options.configure(state='disabled')
        body.columnconfigure(1,weight=1);body.rowconfigure(5,weight=1)
        def save_creation():
            if not type_box.get():raise ValueError('필드 타입을 선택하세요.')
            originals=source_choices(entry['analysis']['values'].get(source,[])+definition.get('allowedValues',[]))
            lines=[line.strip() for line in options.get('1.0','end').splitlines() if line.strip()]
            choices={}
            if type_box.get() in ('option','status'):
                if len(lines)<len(originals):raise ValueError('원본 Choice 값 수만큼 옵션을 입력하세요.')
                choices={identity:lines[index] for index,identity in enumerate(originals)}
                choices.update({'manual:'+str(index):value for index,value in enumerate(lines[len(originals):],start=len(originals))})
            wizard.configure_creation(scope,source,name.get(),type_box.get(),multiple.get(),choices)
            display_scope();dialog.destroy()
        footer=ttk.Frame(body);footer.grid(row=6,column=0,columnspan=2,sticky='e',pady=(14,0))
        ttk.Button(footer,text='취소',command=dialog.destroy).pack(side='left',padx=4)
        ttk.Button(footer,text='생성 설정 저장',command=lambda:run('신규 필드 설정 저장',save_creation)).pack(side='left')
    manual_button.configure(command=lambda:run('수동 매핑',edit))
    create_button.configure(command=lambda:run('신규 필드 설정',create_field_dialog))
    def row_action(event):
        region=table.identify_region(event.x,event.y);column=table.identify_column(event.x)
        if region=='cell' and column=='#5':
            iid=table.identify_row(event.y)
            if iid:
                table.selection_set(iid)
                source=scope_rows[iid]
                if source is not None:create_field_dialog() if wizard.plan['scopes'][scopes.get()]['rows'][source].get('status')=='create' else edit()
        elif region=='cell':edit()
    table.bind('<Double-1>',row_action)
    def import_now():
        errors = wizard.errors()
        if errors: raise ApiError('\n'.join(errors))
        if not messagebox.askyesno('Import 승인', '선택한 기존 프로젝트에 Tracker, Field, Item 및 관련 데이터를 실제로 생성합니다. Import를 승인하시겠습니까?'):
            return
        report = wizard.execute(approved=True)
        show_evidence(report); tabs.select(step4)
        if not report['complete']: status.set('Import 미완료: 오류·누락·보존 결과를 확인한 후 재시도하세요.')
    footer=ttk.Frame(step3);footer.pack(fill='x',pady=(8,0))
    ttk.Label(footer,text='필드는 Import 승인 후 실제 Codebeamer에 생성됩니다.',style='WizardSub.TLabel').pack(side='left')
    ttk.Button(footer,text='다음 (Import 승인)  ›',command=lambda:run('Import',import_now)).pack(side='right')
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
