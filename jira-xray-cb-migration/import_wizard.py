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
from migration.system_fields import choice_input
from migration.xray_review import field_group, issue_kind, TITLES, collect_review


def issue_type_display(kind):
    subtask = kind.get('subtask')
    return (str(kind['id']), 'Sub-task' if subtask is True else 'Regular' if subtask is False else '미확인',
            '프로젝트 사용 가능' if kind.get('project_available') is True else '프로젝트 미적용',
            '미선택' if kind.get('project_available') is True else '매핑 불가')


def relation_tracker_rows(plan):
    """Keep actual selected-project catalog entries; never infer Tracker IDs."""
    result={}
    for project in plan.get('projects',{}).values():
        for tracker in project.get('trackers',[]):
            result.setdefault(tracker['id'],tracker)
    return list(result.values())


def reference_field_rows(fields):
    """Retain actual Reference definitions, including hidden/unverified diagnostics."""
    return [field for field in fields if semantic_type(field)[0]=='reference' or field.get('reference') is not None]


def relation_display(saved, catalog, references):
    if not saved:return '이관하지 않음', '무시', '—'
    from migration.relation_mapping import normalize
    value=normalize(saved)
    mode=value['mode']
    if mode=='association':
        label=next((t['name']+' · ID '+str(t['id']) for t in catalog['codebeamer'] if t['id']==value['type_id']),
                   'Association ID '+str(value['type_id'])+' (목록 미확인)')
    elif mode=='reference_create':label=value['creation']['label']+' (신규 생성 예정)'
    else:
        field=next((f for f in references.get(value['tracker_id'],[]) if field_identifier(f)==value['field_id']),{})
        label=field.get('label',field.get('name','Reference'))+' · ID '+str(value['field_id'])
    return label, 'Reference' if mode.startswith('reference') else 'Association', '반전' if value['reverse'] else '유지'


def launch(wizard, initial_projects=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title('Jira → Codebeamer Import Wizard')
    root.geometry('1120x800')
    status = tk.StringVar(value='프로젝트 조회를 시작하세요. 분석은 읽기 전용입니다.')
    tabs = ttk.Notebook(root)
    tabs.pack(fill='both', expand=True, padx=12, pady=12)
    step2, step3, step4 = (ttk.Frame(tabs, padding=12) for _ in range(3))
    for frame, title in zip((step2, step3, step4), ('STEP 2 · 프로젝트 / Tracker', 'STEP 3 · Jira 필드 검수', 'Import 결과')):
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
    ttk.Label(step2, text='프로젝트 연결', style='WizardTitle.TLabel').pack(anchor='w',pady=(0,8))
    ttk.Label(step2, text='Jira 프로젝트와 기존 Codebeamer 프로젝트를 선택해 먼저 연결합니다.',style='WizardSub.TLabel').pack(anchor='w',pady=(0,8))
    top = ttk.Frame(step2); top.pack(fill='x')
    jira_box = ttk.Combobox(top, state='readonly', width=35)
    cb_box = ttk.Combobox(top, state='readonly', width=45)
    ttk.Label(top, text='Jira 프로젝트').grid(row=0, column=0, sticky='w')
    ttk.Label(top, text='기존 Codebeamer 프로젝트').grid(row=0, column=1, sticky='w')
    jira_box.grid(row=1, column=0, padx=(0,12)); cb_box.grid(row=1, column=1)
    ttk.Label(step2, text='Tracker 연결',style='WizardTitle.TLabel').pack(anchor='w',pady=(14,0))
    ttk.Label(step2, text='이관할 Jira Issue Type만 Tracker에 연결하세요. 연결하지 않은 타입은 Import하지 않습니다.',style='WizardSub.TLabel').pack(anchor='w',pady=(2,4))
    types = ttk.Treeview(step2, columns=('id','kind','availability','tracker'), show='tree headings', selectmode='browse')
    types.heading('#0', text='Jira Issue Type 전체 목록')
    for column, label in [('id','Issue Type ID'),('kind','Regular / Sub-task'),('availability','프로젝트 적용')]:
        types.heading(column, text=label)
        types.column(column, width=130)
    types.heading('tracker', text='Codebeamer Tracker')
    types.tag_configure('unavailable', foreground='#777777')
    types.pack(fill='both', expand=True, pady=12)
    tracker_box = ttk.Combobox(step2, state='readonly', width=65)
    tracker_box.pack(anchor='w')
    new_name = tk.StringVar()
    ttk.Label(step2, text='신규 Tracker 이름 (선택한 기존 프로젝트 내부에 생성)').pack(anchor='w', pady=(8,0))
    ttk.Entry(step2, textvariable=new_name, width=55).pack(anchor='w')
    def apply_projects(result):
        if result is not None:
            projects.update(result)
            jira_box.configure(values=[p['key'] for p in projects['jira']])
            cb_box.configure(values=[str(p['id']) + ' / ' + p.get('name','') for p in projects['codebeamer']])
    def discover():
        result = run('프로젝트 조회', wizard.discover)
        apply_projects(result)
    ttk.Button(top, text='프로젝트 조회', command=discover).grid(row=1, column=2, padx=8)
    if initial_projects is not None:
        apply_projects(initial_projects)
        status.set('서버 연결과 프로젝트 조회 완료. Jira/Codebeamer 프로젝트를 선택하세요.')
    def select_project():
        if jira_box.current() < 0 or cb_box.current() < 0:
            raise ValueError('두 프로젝트를 선택하세요.')
        project = projects['jira'][jira_box.current()]['key']
        target = projects['codebeamer'][cb_box.current()]['id']
        trackers = wizard.select_project(project, target)
        type_rows.clear(); types.delete(*types.get_children())
        for index, kind in enumerate(wizard.plan['projects'][project]['display_types']):
            iid = 'type-' + str(index); type_rows[iid] = (project, kind)
            types.insert('', 'end', iid=iid, text=kind['name'], values=issue_type_display(kind), tags=(() if kind['project_available'] else ('unavailable',)))
        tracker_box.configure(values=[str(t['id']) + ' / ' + t.get('name','') for t in trackers] + ['＋ 새 Tracker 생성'])
        tracker_box.set('')
        catalog_notice.set(' / '.join(wizard.plan['projects'][project]['catalog_warnings']))
        scopes.configure(values=[]); scopes.set(''); table.delete(*table.get_children())
    catalog_notice = tk.StringVar()
    ttk.Label(step2, textvariable=catalog_notice, foreground='#aa5700').pack(anchor='w')
    ttk.Button(top, text='프로젝트 연결', command=lambda: run('프로젝트 연결', select_project)).grid(row=2, column=0, pady=8)
    def show_global_definitions():
        window = tk.Toplevel(root)
        window.title('Jira 전체 정의 — 프로젝트별 사용 가능 여부와 별개')
        output = tk.Text(window, width=90, height=25)
        output.pack(fill='both', expand=True)
        for category in ('priorities', 'statuses', 'resolutions'):
            try:
                values = wizard.jira.global_catalog(categories=[category])[category]
                output.insert('end', category + '\n' + json.dumps(values, ensure_ascii=False, indent=2) + '\n')
            except ApiError as exc:
                output.insert('end', category + ': 조회 실패: ' + str(exc) + '\n')
        output.configure(state='disabled')
    ttk.Button(top, text='Priority / Status / Resolution 정의',
               command=lambda: run('전체 정의 조회', show_global_definitions)).grid(row=2, column=1, pady=8)
    def map_tracker():
        if not types.selection():
            raise ValueError('Issue Type을 선택하세요.')
        iid = types.selection()[0]
        project, kind = type_rows[iid]
        if issue_kind(kind, wizard.config['migration']) == 'execution' and not wizard.config['migration'].get('full_xray', {}).get('create_execution_items', False):
            wizard.map_execution_context(project, kind['id'])
            types.set(iid, 'tracker', '원본 맥락 보존 · 대상 Tracker 없음')
            scopes.configure(values=list(wizard.plan['scopes']))
            scopes.set(project + ':' + str(kind['id']))
            return
        if tracker_box.current() < 0:
            raise ValueError('대상 Tracker를 선택하세요.')
        trackers = wizard.plan['projects'][project]['trackers']
        index = tracker_box.current()
        tracker = trackers[index]['id'] if index < len(trackers) else None
        wizard.map_tracker(project, kind['id'], tracker, new_name.get())
        types.set(iid, 'tracker', tracker_box.get() if tracker else '신규: ' + new_name.get())
        scopes.configure(values=list(wizard.plan['scopes']))
        scopes.set(project + ':' + str(kind['id']))
        selection_info.set('현재 이관 대상: '+', '.join(
            wizard.plan['scopes'][k]['type']['name'] for k in wizard.plan['scopes']))
    selection_info=tk.StringVar(value='Tracker에 연결한 Issue Type만 이관합니다.')
    ttk.Label(step2,textvariable=selection_info,wraplength=1000,
              style='WizardSub.TLabel').pack(anchor='w',pady=(4,4))
    map_button = ttk.Button(step2, text='Tracker 연결',
                            command=lambda: run('Tracker 매핑 저장', map_tracker), state='disabled')
    map_button.pack(anchor='w', pady=8)
    def unmap_tracker():
        if not types.selection():
            raise ValueError('연결을 해제할 Issue Type을 선택하세요.')
        project, kind = type_rows[types.selection()[0]]
        scope=project+':'+str(kind['id'])
        if scope not in wizard.plan['scopes']:
            return
        wizard.plan['scopes'].pop(scope)
        wizard.state.save('wizard','plan',wizard.plan)
        types.set(types.selection()[0],'tracker','미선택')
        scopes.configure(values=list(wizard.plan['scopes']))
        if scopes.get()==scope:scopes.set('')
        selection_info.set('현재 이관 대상: '+(', '.join(e['type']['name'] for e in wizard.plan['scopes'].values()) or '없음'))
        table.delete(*table.get_children())
    ttk.Button(step2,text='선택한 Issue Type 연결 해제 (Import 제외)',
               command=lambda:run('Issue Type 이관 제외',unmap_tracker)).pack(anchor='w',pady=(0,8))
    def selected_type_changed(event=None):
        selected = types.selection()
        allowed = bool(selected and type_rows[selected[0]][1].get('project_available'))
        context = bool(allowed and issue_kind(type_rows[selected[0]][1],wizard.config['migration'])=='execution'
                       and not wizard.config['migration'].get('full_xray',{}).get('create_execution_items',False))
        map_button.configure(state='normal' if allowed else 'disabled',
            text='원본 실행 맥락 선택 (Tracker 생성 없음)' if context else '이 Issue Type의 Tracker 매핑 저장')
        tracker_box.configure(state='disabled' if context else 'readonly')
    types.bind('<<TreeviewSelect>>', selected_type_changed)

    style=ttk.Style(root)
    style.configure('WizardTitle.TLabel',font=('',17,'bold'))
    style.configure('WizardSub.TLabel',foreground='#485568')
    style.configure('Auto.TLabel',foreground='#16743b')
    style.configure('Manual.TLabel',foreground='#075fc9')
    style.configure('Create.TLabel',foreground='#aa5700')
    step3_container=step3
    mapping_tabs=ttk.Notebook(step3_container);mapping_tabs.pack(fill='both',expand=True)
    step3=ttk.Frame(mapping_tabs,padding=4)
    association_tab=ttk.Frame(mapping_tabs,padding=8)
    mapping_tabs.add(step3,text='필드 매핑')
    xray_tab=ttk.Frame(mapping_tabs,padding=8)
    mapping_tabs.add(xray_tab,text='Xray 관계 / 실행 결과 검수')
    ttk.Label(xray_tab,text='Issue는 Key로, Test Run은 별도 숫자 Run ID로 조회합니다. 집계 PASS/FAIL은 개별 Run이 아닙니다.',wraplength=950).pack(anchor='w')
    # Xray memberships are not ordinary Jira fields and cannot be inferred
    # from Jira Issue Link Type names or automatically converted to a text field.
    xray_relationships={
        'test': [('Test Sets','test_sets_for_test','Test Set 구성원 관계'),
                 ('Test Plans','test_plans_for_test','Test Plan 대상 Test 관계')],
        'set': [('Tests','test_set_tests','Test Set → Test Case Reference')],
        'plan': [('Tests','test_plan_tests','Test Plan → Test Case Reference'),
                 ('Test Executions','test_plan_executions','계획 → 실행 맥락')],
        'execution': [('Tests','test_execution_tests','실행 → Test Case'),
                      ('Runs','runs','개별 Test Run ID 필요')],
        'run': [('Test Case / Execution','run','Test/Execution Key가 포함된 Run 상세'),
                ('Steps / Evidence','test_run_steps','Run 결과 및 증빙')]
    }
    relation_guide=ttk.Treeview(xray_tab,columns=('source','operation','target'),show='headings',height=4)
    for column,label,width in (('source','Xray 관계',180),('operation','원본 API 조회 항목',220),('target','Codebeamer 관계 처리',330)):
        relation_guide.heading(column,text=label);relation_guide.column(column,width=width)
    relation_guide.pack(fill='x',pady=(8,6))
    ttk.Label(xray_tab,text='Xray 관계는 원본 API로 조회한 ID를 사용합니다. Tracker/Reference ID가 확인되기 전에는 관계 생성으로 처리하지 않습니다.',
              wraplength=950).pack(anchor='w')
    xray_kind=ttk.Combobox(xray_tab,state='readonly',values=list(TITLES.values()),width=42)
    xray_kind.pack(anchor='w');xray_kind.set(TITLES['test'])
    def show_xray_relations(event=None):
        relation_guide.delete(*relation_guide.get_children())
        selected=next((k for k,v in TITLES.items() if v==xray_kind.get()),'test')
        for source,operation,target in xray_relationships.get(selected,[]):
            relation_guide.insert('','end',values=(source,operation,target))
    xray_kind.bind('<<ComboboxSelected>>',show_xray_relations)
    show_xray_relations()
    ttk.Label(xray_tab,text='원본 Issue Key 또는 Run ID').pack(anchor='w')
    xray_identifier=ttk.Entry(xray_tab,width=40);xray_identifier.pack(anchor='w')
    xray_output=tk.Text(xray_tab,width=100,height=12);xray_output.pack(fill='both',expand=True)
    xray_mapping_rows={}
    xray_map=ttk.Treeview(xray_tab,columns=('category','source','target','conversion'),show='headings',height=6)
    for column,label in [('category','양식'),('source','원본 필드'),('target','검증된 대상 속성'),('conversion','값 변환')]:
        xray_map.heading(column,text=label)
    xray_map.pack(fill='x')
    def refresh_xray_mapping(report):
        from migration.xray_mapping import mapping_rows
        xray_map.delete(*xray_map.get_children());xray_mapping_rows.clear()
        for index,row in enumerate(mapping_rows(report,wizard.config['migration'])):
            key='xray-map-'+str(index);xray_mapping_rows[key]=row
            xray_map.insert('','end',iid=key,values=(row['category'],row['source'],row['spec'].get('target',''),row['spec'].get('type','')))
    def edit_xray_mapping():
        from migration.xray_mapping import writable_properties, save_field, CONVERTERS
        if not xray_map.selection():raise ApiError('조회된 Xray 매핑 행을 선택하세요.')
        key=xray_map.selection()[0];row=xray_mapping_rows[key]
        properties=writable_properties(wizard.cb,row['category'])
        dialog=tk.Toplevel(root);dialog.title(row['category']+' · '+row['source'])
        ttk.Label(dialog,text='생성 요청 스키마로 확인한 대상 속성 · 저장은 계획만 변경합니다.').pack(anchor='w')
        target=ttk.Combobox(dialog,state='readonly',values=list(properties),width=45);target.pack(fill='x')
        target.set(row['spec'].get('target',''))
        conversion=ttk.Combobox(dialog,state='readonly',values=CONVERTERS);conversion.pack(fill='x')
        conversion.set(row['spec'].get('type','raw'))
        ttk.Label(dialog,text='원본 ID → 대상 값 매핑 JSON (User / Choice / Reference)').pack(anchor='w')
        values=tk.Text(dialog,width=65,height=7);values.pack(fill='both',expand=True)
        values.insert('1.0',json.dumps(row['spec'].get('value_mapping',{}),ensure_ascii=False,indent=2))
        def save():
            spec=save_field(wizard,row['category'],row['source'],target.get(),conversion.get(),
                            json.loads(values.get('1.0','end')),row['samples'])
            row['spec']=spec
            xray_map.item(key,values=(row['category'],row['source'],spec['target'],spec['type']))
            dialog.destroy()
        ttk.Button(dialog,text='매핑 계획 저장',command=lambda:run('Xray 매핑 저장',save)).pack(anchor='e')
    ttk.Button(xray_tab,text='선택한 Run / Step 매핑 변경',command=lambda:run('Xray 매핑',edit_xray_mapping)).pack(anchor='w')
    def inspect_xray_source():
        from clients.xray_client import XrayClient
        identifier=xray_identifier.get().strip()
        if not identifier:raise ApiError('원본 Key/Run ID를 입력하세요.')
        report=collect_review(wizard.jira,XrayClient(wizard.jira.http,wizard.jira.config,wizard.state.save),
                              identifier,next(k for k,v in TITLES.items() if v==xray_kind.get()),wizard.config['migration'])
        xray_output.delete('1.0','end')
        xray_output.insert('end',json.dumps(report,ensure_ascii=False,indent=2))
        refresh_xray_mapping(report)
    ttk.Button(xray_tab,text='원본 전용 API 조회 · 쓰기 없음',
               command=lambda:run('Xray 원본 검수',inspect_xray_source)).pack(anchor='w')

    mapping_tabs.add(association_tab,text='관계 매핑')
    title=ttk.Frame(step3);title.pack(fill='x',pady=(0,10))
    ttk.Label(title,text='STEP 3. 필드 매핑',style='WizardTitle.TLabel').pack(anchor='w')
    ttk.Label(title,text='Jira에서 사용 중인 필드를 가져와 기존 Codebeamer 필드와 비교합니다. 대상 셀 클릭: 추천 필드 / 무시 / 신규 생성. Xray 관계는 별도 관계 탭에서 연결합니다.',style='WizardSub.TLabel',wraplength=1050).pack(anchor='w')
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
    ttk.Button(target_bar,text='데이터 가져오기 (GET)',command=lambda:run('Jira 사용 필드 가져오기',analyze)).pack(side='right')
    tools=ttk.Frame(step3);tools.pack(fill='x',pady=(10,4))
    selected_only=tk.BooleanVar(value=False)
    ttk.Checkbutton(tools,text='선택 항목만 보기',variable=selected_only,command=lambda:display_scope()).pack(side='left')
    # Default to fields actually populated in this Jira Issue Type, plus core
    # and required fields. The full metadata catalog is still available.
    field_visibility=tk.StringVar(value='used')
    ttk.Label(tools,text='Jira 필드 범위').pack(side='left',padx=(14,4))
    field_filter=ttk.Combobox(tools,state='readonly',width=20,
                              values=['사용 중 + 기본/필수','프로젝트 설정 필드','전체 조회 필드'])
    field_filter.current(0);field_filter.pack(side='left')
    def set_field_visibility(event=None):
        field_visibility.set(('used','configured','all')[field_filter.current()])
        display_scope()
    field_filter.bind('<<ComboboxSelected>>',set_field_visibility)
    search=tk.StringVar()
    ttk.Label(tools,text='필드 검색').pack(side='right',padx=(8,4))
    search_entry=ttk.Entry(tools,textvariable=search,width=28);search_entry.pack(side='right')
    table_frame=ttk.Frame(step3);table_frame.pack(fill='both',expand=True)
    table = ttk.Treeview(table_frame, columns=('selected','jira','group','kind','cb','options'),show='headings',selectmode='browse')
    for column,text,width in (('selected','선택',55),('jira','Jira 필드',260),('group','처리 구분',120),('kind','Jira 타입',120),('cb','Codebeamer 필드 (추천/무시/생성 ▾)',380),('options','옵션 수',70)):
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
    def select_all(value):
        for source in wizard.plan['scopes'].get(scopes.get(),{}).get('rows',{}):wizard.select_field(scopes.get(),source,value)
        display_scope()
    ttk.Button(action_bar,text='전체 선택',command=lambda:select_all(True)).pack(side='left')
    ttk.Button(action_bar,text='전체 해제',command=lambda:select_all(False)).pack(side='left')
    detail = tk.StringVar(value='Attachment · Comment · Linked Issue · History · R4J는 별도로 자동 이관합니다.')
    ttk.Label(step3,textvariable=detail,wraplength=1050).pack(anchor='w',pady=4)
    warning=tk.StringVar()
    ttk.Label(step3,textvariable=warning,foreground='#ad5b00',wraplength=1050).pack(anchor='w')
    def scope_names(entry):
        if entry.get('source_only'):
            return entry['type']['name'] + ' → 원본 맥락 보존 (CB Tracker 생성 없음)'
        tracker=entry.get('new_tracker')
        if not tracker:
            tracker=next((t.get('name',str(t['id'])) for t in wizard.plan['projects'][entry['project']]['trackers']
                          if t['id']==entry.get('tracker')),'미선택')
        return entry['type'].get('name',str(entry['type']['id']))+'  →  '+tracker
    def display_scope():
        if not scopes.get() or scopes.get() not in wizard.plan['scopes']:
            table.delete(*table.get_children());return
        entry = wizard.plan['scopes'][scopes.get()]
        category=issue_kind(entry['type'],wizard.config['migration'])
        scope_title.set((TITLES.get(category,'Jira Issue')+' · ')+scope_names(entry))
        if category:
            xray_kind.set(TITLES[category])
            show_xray_relations()
        scope_rows.clear(); table.delete(*table.get_children())
        query=search.get().strip().casefold()
        for index,(source,row) in enumerate(entry.get('rows',{}).items()):
            definition=entry['analysis']['fields'][source]
            if field_visibility.get()=='used' and not definition.get('show_by_default',True):continue
            if field_visibility.get()=='configured' and not (definition.get('configured') or definition.get('default_selected')):continue
            if selected_only.get() and not row.get('selected',True):continue
            target=row.get('target') or {}
            left=''
            if row.get('spec'):
                left=target.get('label',target.get('name',''))+' · ID '+str(row['spec']['field_id'])
            else:
                phase=wizard.state.get('field-phase:'+scopes.get()+':'+source)
                if phase and phase[1].get('field_id') and phase[0] in ('CREATED','VALUE_MAPPING_PENDING','COMPLETED'):
                    field=phase[1].get('field',{})
                    left=field.get('label',field.get('name',row.get('creation',{}).get('label','')))+' · ID '+str(phase[1]['field_id'])
                elif row.get('creation'):
                    left=row['creation']['label']+' (신규 생성 예정)'
            right=definition.get('name',source)+(' ('+source+')' if source.startswith('customfield_') else '')
            if definition.get('required') is True:right += ' [필수]'
            if query and query not in (left+' '+right).casefold():continue
            iid='field-'+str(index);scope_rows[iid]=source
            kind,multiple=semantic_type(definition,True)
            table.insert('','end',iid=iid,values=('☑' if row.get('selected',True) else '☐',right,field_group(source,definition),str(kind)+(' / Multi' if multiple else ''),
                (left or row.get('reason','검증 필요')) if row.get('selected',True) else '이관하지 않음 (제외)',
                len(row.get('creation',{}).get('choices',{}))))
        if entry.get('source_only'):
            detail.set('Test Execution의 모든 원본 필드·환경·관계는 Run 맥락으로 보존합니다. 일반 필드 생성/매핑 대상이 아닙니다.')
            warning.set('Xray 관계 / 실행 결과 검수에서 개별 Run ID와 결과를 확인하세요.')
            return
        risks=entry['analysis']['risks']+entry.get('target_risks',[])
        api_warnings=wizard.cb.config.get('tracker_field_evidence',{}).get(str(entry.get('tracker') or entry.get('template_tracker')),{}).get('warnings',[])
        detail.set(str(sum(r.get('selected',True) for r in entry['rows'].values()))+' / '+str(len(entry['rows']))+' 선택 · 조회 Issue '+str(entry['analysis']['issue_count'])+'건 · Comment · Attachment · History는 별도 보존/검증 · Xray 관계/Run은 별도 검수')
        warning.set(('⚠ Hidden 필드에 실제 값이 남아 있어 누락 위험 '+str(len(risks))+'건: '+
                     ', '.join(r['field'] for r in risks)) if risks else '')
        if entry['analysis'].get('visibility_unknown') or entry['analysis'].get('configuration_status') == 'unavailable':
            warning.set(warning.get()+' 표시 상태 미확인: Field Configuration을 확인하지 못한 필드는 수동 검토가 필요합니다.')
        if api_warnings:warning.set(warning.get()+' · '+'; '.join(api_warnings))
        inspect()
    def analyze():
        if not scopes.get(): raise ValueError('Tracker ↔ Issue Type 조합을 선택하세요.')
        result=wizard.analyze(scopes.get());display_scope();return result
    scopes.bind('<<ComboboxSelected>>',lambda event:display_scope())
    search.trace_add('write',lambda *_:display_scope())
    def option_details():
        if not table.selection():return
        source=scope_rows[table.selection()[0]]
        entry=wizard.plan['scopes'][scopes.get()]
        window=tk.Toplevel(root);window.title('원본 옵션 및 생성 계획')
        text=tk.Text(window,width=90,height=25);text.pack(fill='both',expand=True)
        text.insert('1.0',json.dumps({'source':entry['analysis']['fields'][source], 'plan':entry['rows'][source]},ensure_ascii=False,indent=2));text.configure(state='disabled')
    ttk.Button(action_bar,text='옵션 및 계획 상세',command=option_details).pack(side='left')
    def inspect(event=None):
        if table.selection():
            source=scope_rows[table.selection()[0]]
            if source is None:
                detail.set('Jira Hidden 필드는 기본 매핑에서 제외됩니다.')
                create_button.pack_forget();manual_button.configure(state='disabled')
                return
            row = wizard.plan['scopes'][scopes.get()]['rows'][source]
            phase=wizard.state.get('field-phase:'+scopes.get()+':'+source)
            detail.set(row.get('reason','') + ((' · '+phase[0]+' · Field ID '+str(phase[1].get('field_id','미생성'))+' · '+phase[1].get('error','')) if phase else ''))
            if not manual_button.winfo_manager():manual_button.pack(side='left')
            if not create_button.winfo_manager():create_button.pack(side='left')
            manual_button.configure(state='normal');create_button.configure(state='normal')
        else:
            create_button.pack_forget()
            if not manual_button.winfo_manager():manual_button.pack(side='left')
            manual_button.configure(state='disabled')
    table.bind('<<TreeviewSelect>>', inspect)
    def edit(event=None, selected_target=None):
        if not table.selection(): return
        scope = scopes.get(); entry = wizard.plan['scopes'][scope]
        source = scope_rows[table.selection()[0]]
        if source is None:return
        source_kind,source_multiple=semantic_type(entry['analysis']['fields'][source],True)
        candidates = wizard.mapping_candidates(scope,source)
        fields = [c['field'] for c in candidates]
        if not fields:raise ApiError('선택 Tracker의 Field API가 빈 목록을 반환했습니다.')
        dialog = tk.Toplevel(root); dialog.title('필드 수동 연결')
        dialog.transient(root);dialog.grab_set()
        ttk.Label(dialog,text='Jira 필드: '+entry['analysis']['fields'][source].get('name',source),style='WizardTitle.TLabel').pack(padx=18,pady=12)
        ttk.Label(dialog,text='호환되는 기존 Codebeamer 필드').pack(anchor='w',padx=18)
        box = ttk.Combobox(dialog, state='readonly', width=55, values=[c['field'].get('label',c['field'].get('name',''))+' · ID '+str(field_identifier(c['field']))+' · '+str(c['field'].get('typeName',c['field'].get('type','타입 미확인')))+(' — '+c['reason'] if c['reason'] else '') for c in candidates])
        box.pack(padx=18,pady=6)
        value_frame = ttk.Frame(dialog); value_frame.pack(fill='both', padx=12, pady=12)
        choices = []
        def fill(event=None):
            for child in value_frame.winfo_children(): child.destroy()
            choices.clear()
            if box.current() < 0: return
            target = fields[box.current()]
            kind, _ = semantic_type(entry['analysis']['fields'][source], True)
            if kind in ('option','status','user'):
                options = target.get('options') or []
                values = source_choices(entry['analysis']['values'].get(source, []), 'id' if source == 'components' else None)
                for identity, label in values.items():
                    ttk.Label(value_frame, text=label).pack(anchor='w')
                    if kind == 'user' and not options:
                        ttk.Label(value_frame,text='대상 CB 사용자 ID (사용자 목록 미조회: 직접 확인한 ID)').pack(anchor='w')
                        choice = ttk.Entry(value_frame)
                        saved = (entry['rows'][source].get('spec') or {}).get('value_mapping', {})
                        previous = saved.get(identity,wizard.config['migration'].get('user_mapping',{}).get(identity,{}))
                        if isinstance(previous,dict) and previous.get('id') is not None:choice.insert(0,str(previous['id']))
                        choice.pack(fill='x');choices.append((identity,choice,None))
                        continue
                    choice = ttk.Combobox(value_frame, state='readonly', values=[str(o.get('name',o.get('label',''))) for o in options])
                    choice.pack(fill='x'); choices.append((identity,choice,options))
                    saved = (entry['rows'][source].get('spec') or {})
                    if saved.get('field_id') == field_identifier(target):
                        value = saved.get('value_mapping',{}).get(identity)
                        value_id = value.get('id') if isinstance(value,dict) else value
                        index = next((i for i,o in enumerate(options) if o.get('id') == value_id), None)
                        if index is not None:choice.current(index)
        box.bind('<<ComboboxSelected>>', fill)
        chosen = selected_target or entry['rows'][source].get('target')
        if chosen:
            index = next((i for i,f in enumerate(fields) if field_identifier(f)==field_identifier(chosen)), None)
            if index is not None: box.current(index); fill()
        def accept():
            if box.current() < 0: raise ValueError('CB 필드를 선택하세요.')
            if candidates[box.current()]['reason'] and '미확인' not in candidates[box.current()]['reason']:raise ApiError(candidates[box.current()]['reason'])
            target = fields[box.current()]; fid = field_identifier(target)
            bindings = wizard.config['codebeamer'].get('field_bindings', {})
            keys = [k for k,b in bindings.items() if b.get('fieldId') == fid]
            canonical = label_tokens('', target) & {'name','description'}
            key = keys[0] if len(keys) == 1 else (next(iter(canonical)) if canonical else 'cb_field_'+str(fid))
            kind, _ = semantic_type(entry['analysis']['fields'][source], True)
            mapping = dict(wizard.config['migration'].get('user_mapping' if kind == 'user' else 'status_mapping', {}))
            previous = entry['rows'][source].get('spec') or {}
            if previous.get('field_id') == fid: mapping.update(previous.get('value_mapping', {}))
            for identity, choice, options in choices:
                if options is None:
                    value=choice.get().strip()
                    if not value.isdigit() or int(value)<=0:raise ValueError('실제 CB 사용자 ID를 입력하세요.')
                    mapping[identity]={'id':int(value)}
                    continue
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
        definition=entry['analysis']['fields'][source]
        source_kind,source_multiple=semantic_type(definition,True)
        profiles=wizard.creation_profiles(scope)
        if not profiles:
            raise ApiError('검증된 생성 타입이 없습니다. '+ '; '.join(entry.get('creation_diagnostics', [])))
        dialog=tk.Toplevel(root);dialog.title('신규 Codebeamer 필드 생성 설정');dialog.transient(root);dialog.grab_set()
        body=ttk.Frame(dialog,padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='신규 Codebeamer 필드 생성 설정',style='WizardTitle.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,14))
        ttk.Label(body,text='필드 이름 *').grid(row=1,column=0,sticky='w',pady=5)
        name=tk.StringVar(value=row.get('creation',{}).get('label',definition.get('name',source)))
        ttk.Entry(body,textvariable=name,width=38).grid(row=1,column=1,sticky='ew',pady=5)
        ttk.Label(body,text='필드 타입 *').grid(row=2,column=0,sticky='w',pady=5)
        type_names=list(profiles)
        type_box=ttk.Combobox(body,state='readonly',values=type_names,width=35);type_box.grid(row=2,column=1,sticky='ew',pady=5)
        recommended = {'number': 'integer', 'boolean': 'boolean',
                       'text': 'text', 'option': 'option', 'status': 'option'}.get(source_kind,source_kind)
        if recommended in type_names:
            type_box.set(recommended)
        elif source_kind in type_names:
            type_box.set(source_kind)
        elif type_names:
            type_box.set(type_names[0])
        multiple=tk.BooleanVar(value=bool(source_multiple))
        multi_check=ttk.Checkbutton(body,text='다중 값',variable=multiple)
        multi_check.grid(row=3,column=1,sticky='w')
        # Show Choice options ONLY for selectable source types. Standard
        # Summary/Description/Assignee/Date do not require choice options.
        option_label=ttk.Label(body,text='선택 옵션 (Choice/Status 전용, 한 줄에 하나)')
        options=tk.Text(body,width=48,height=7)
        initial=list(dict.fromkeys(row.get('creation',{}).get('choices',{}).values()))
        options.insert('1.0','\n'.join(initial))
        conversion_notice=ttk.Label(body,text='',wraplength=430)
        conversion_notice.grid(row=4,column=0,columnspan=2,sticky='w',pady=(10,4))
        body.columnconfigure(1,weight=1);body.rowconfigure(5,weight=1)
        if any(not profile.get('binding') for profile in profiles.values()):
            ttk.Label(body,text='필드 생성 계약과 값 쓰기 계약은 별도입니다. 값 모델 미확인 시 필드 생성 후 Item 이관은 미완료로 표시됩니다.',wraplength=420).grid(row=9,column=0,columnspan=2,sticky='w')
        reference_box=None
        reference_trackers=wizard.plan['projects'][entry['project']]['trackers']
        reference_label=ttk.Label(body,text='참조 대상 Tracker')
        reference_box=ttk.Combobox(body,state='readonly',values=[
            t.get('name',str(t['id']))+' · ID '+str(t['id']) for t in reference_trackers])
        saved=row.get('creation',{}).get('reference_tracker_id')
        index=next((i for i,t in enumerate(reference_trackers) if t['id']==saved),None)
        if index is not None:reference_box.current(index)
        def selected_type_changed(event=None):
            chosen=type_box.get()
            from migration.wizard import selected_conversion
            conversion=selected_conversion(source_kind,bool(source_multiple),chosen,multiple.get())
            conversion_notice.configure(text='Jira '+str(source_kind)+' → Codebeamer '+chosen+
                                        ' · '+('변환: '+conversion if conversion else '이 조합은 값 변환을 지원하지 않음'))
            option_label.grid_remove();options.grid_remove()
            reference_label.grid_remove();reference_box.grid_remove()
            if chosen in ('option','status'):
                option_label.grid(row=5,column=0,columnspan=2,sticky='w',pady=(8,4))
                options.grid(row=6,column=0,columnspan=2,sticky='nsew')
            if chosen=='reference':
                reference_label.grid(row=8,column=0,sticky='w')
                reference_box.grid(row=8,column=1,sticky='ew')
            multi_check.configure(state='normal' if chosen in ('option','status','user','reference') else 'disabled')
            if chosen not in ('option','status','user','reference'):
                multiple.set(False)
        type_box.bind('<<ComboboxSelected>>',selected_type_changed)
        selected_type_changed()
        def save_creation():
            if not type_box.get():raise ValueError('필드 타입을 선택하세요.')
            originals=source_choices(entry['analysis']['values'].get(source,[])+definition.get('allowedValues',[]), 'id' if source == 'components' else None)
            lines=[line.strip() for line in options.get('1.0','end').splitlines() if line.strip()]
            if type_box.get() in ('option','status') and not lines:
                lines=list(originals.values())
                options.delete('1.0','end')
                options.insert('1.0','\n'.join(lines))
            choices={}
            if type_box.get() in ('option','status'):
                if len(lines)<len(originals):raise ValueError('원본 Choice 값 수만큼 옵션을 입력하세요.')
                choices={identity:lines[index] for index,identity in enumerate(originals)}
                choices.update({'manual:'+str(index):value for index,value in enumerate(lines[len(originals):],start=len(originals))})
            wizard.configure_creation(scope,source,name.get(),type_box.get(),multiple.get(),choices,
                reference_tracker=reference_trackers[reference_box.current()]['id'] if reference_box is not None and reference_box.current()>=0 else None)
            display_scope();dialog.destroy()
        footer=ttk.Frame(body);footer.grid(row=10,column=0,columnspan=2,sticky='e',pady=(14,0))
        ttk.Button(footer,text='취소',command=dialog.destroy).pack(side='left',padx=4)
        ttk.Button(footer,text='생성 설정 저장',command=lambda:run('신규 필드 설정 저장',save_creation)).pack(side='left')
    manual_button.configure(command=lambda:run('수동 매핑',edit))
    create_button.configure(command=lambda:run('신규 필드 설정',create_field_dialog))
    def row_action(event):
        if table.identify_column(event.x)=='#1':
            iid=table.identify_row(event.y)
            if iid in scope_rows:
                source=scope_rows[iid];row=wizard.plan['scopes'][scopes.get()]['rows'][source]
                wizard.select_field(scopes.get(),source,not row.get('selected',True));display_scope()
            return
        # Treeview column indices: #1 selected, #2 Jira, #3 group, #4 type,
        # #5 Codebeamer mapping. An earlier #4 check made the dropdown unusable.
        if table.identify_region(event.x,event.y) != 'cell' or table.identify_column(event.x) != '#5':return
        iid = table.identify_row(event.y)
        if not iid:return
        table.selection_set(iid)
        source = scope_rows.get(iid)
        if source is None:return
        entry = wizard.plan['scopes'][scopes.get()]
        kind, multiple = semantic_type(entry['analysis']['fields'][source], True)
        candidates = wizard.mapping_candidates(scopes.get(),source)
        from mapping_helper import normalized_label
        jira_name=entry['analysis']['fields'][source].get('name',source)
        # Put compatible exact-name or alias matches first. Unverified and
        # incompatible choices stay visible with explicit warnings.
        candidates.sort(key=lambda c: (
            bool(c['reason']),
            normalized_label(c['field'].get('label') or c['field'].get('name')) not in
                {normalized_label(jira_name),normalized_label(source)},
            str(c['field'].get('label',c['field'].get('name','')))))
        fields = [c['field'] for c in candidates]
        labels = [f.get('label',f.get('name',''))+' ('+str(f.get('typeName',f.get('type','?')))+') · ID '+str(field_identifier(f)) for f in fields]
        labels = [('추천 필드 · ' if not c['reason'] else '확인 필요 · ')+label+
                  (' — '+c['reason'] if c['reason'] else '')
                  for label,c in zip(labels,candidates)]
        labels.extend(['⊘ 필드 무시 (Import 제외)','＋ 신규 필드 생성'])
        if not labels:
            detail.set('호환 후보 없음: Jira '+str(kind)+' / 다중 선택 '+str(multiple)+
                       '; CB 타입·valueModel·다중 선택 정보 또는 Hidden 여부를 확인하세요.')
            return
        bounds = table.bbox(iid, 'cb')
        if not bounds:return
        editor = ttk.Combobox(table,state='readonly',values=labels)
        editor.place(x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3])
        current = entry['rows'][source].get('spec') or {}
        index = next((i for i,f in enumerate(fields) if field_identifier(f)==current.get('field_id')),None)
        if index is not None:editor.current(index)
        editor.focus_set()
        def selected(event=None):
            index=editor.current();editor.destroy()
            if index < 0:return
            if index == len(fields):
                wizard.select_field(scopes.get(),source,False)
                display_scope()
                detail.set('Jira 필드 '+source+'는 Import 대상에서 제외했습니다.')
                return
            if index == len(fields)+1:run('신규 필드 설정',create_field_dialog);return
            if candidates[index]['reason'] and '미확인' not in candidates[index]['reason']:
                detail.set(candidates[index]['reason']);return
            target=fields[index]
            if kind in ('option','status','user'):
                run('값 매핑',lambda:edit(selected_target=target));return
            bindings=wizard.config['codebeamer'].get('field_bindings',{})
            keys=[k for k,v in bindings.items() if v.get('fieldId')==field_identifier(target)]
            canonical=label_tokens('',target)&{'name','description'}
            key=keys[0] if len(keys)==1 else (next(iter(canonical)) if canonical else 'cb_field_'+str(field_identifier(target)))
            def save():
                wizard.manual_mapping(scopes.get(),source,field_identifier(target),key,{})
                display_scope()
            run('매핑 저장',save)
        editor.bind('<<ComboboxSelected>>',selected)
        editor.bind('<Escape>',lambda event:editor.destroy())
        # Tk's native dropdown takes focus before it emits ComboboxSelected.
        # Destroying here used to discard the user's choice before save.
    table.bind('<ButtonRelease-1>',row_action)
    def import_now():
        if not scopes.get() or scopes.get() not in wizard.plan['scopes']:
            raise ApiError('이관할 Jira Issue Type과 Codebeamer Tracker를 먼저 선택하세요.')
        active_scope=scopes.get()
        if current_only.get():
            if wizard.config['migration'].get('mode') == 'full-xray':
                raise ApiError('Full Xray는 Test/Plan/Set/Execution 종속성이 필요합니다. 단일 Issue Type 모드를 해제하고 관련 조합을 분석하세요.')
            wizard.keep_only_scope(active_scope)
            scopes.configure(values=[active_scope])
            scopes.set(active_scope)
        planned=[r for r in wizard.creation_review() if r['selected']]
        preview=tk.Toplevel(root);preview.title('생성 예정 내역 확인')
        text=tk.Text(preview,width=100,height=25);text.pack(fill='both',expand=True)
        lines=[]
        for item in planned:
            creation=item.get('creation') or {};target=item.get('target') or {}
            lines.append(item['scope']+' / '+item['source']+' → '+(creation.get('label','')+' / '+str(creation.get('semantic_type'))+(' / 다중' if creation.get('multipleSelection') else ' / 단일')+' / 옵션 '+str(len(creation.get('choices',{})))+'개 / 신규 생성' if not target else str(target.get('target'))+' / Field ID '+str(target.get('field_id'))+' / 기존 필드 활용'))
        text.insert('1.0','\n'.join(lines));text.configure(state='disabled')
        confirmed=[False]
        def confirm():confirmed[0]=True;preview.destroy()
        ttk.Button(preview,text='내역 확인 후 계속',command=confirm).pack()
        preview.grab_set();root.wait_window(preview)
        if not confirmed[0]:return
        errors = wizard.errors()
        if errors: raise ApiError('\n'.join(errors))
        review = wizard.visibility_review()
        if review and not messagebox.askyesno('표시 상태 미확인 · 범위 검토',
                '다음 조합의 Hidden 여부 또는 전체 필드 범위를 확인하지 못했습니다: '+', '.join(review)+
                '\n현재 조회된 필드와 값을 검토했으며 조회되지 않은 필드의 누락 가능성을 확인하고 계속하시겠습니까?'):
            return
        wizard.ensure_write_contracts()
        if not messagebox.askyesno('Import 승인', '선택한 기존 프로젝트에 Tracker, Field, Item 및 관련 데이터를 실제로 생성합니다. Import를 승인하시겠습니까?'):
            return
        report = wizard.execute(approved=True, visibility_review=review)
        show_evidence(report); tabs.select(step4)
        if not report['complete']: status.set('Import 미완료: 오류·누락·보존 결과를 확인한 후 재시도하세요.')
    footer=ttk.Frame(step3);footer.pack(fill='x',pady=(8,0))
    current_only=tk.BooleanVar(value=True)
    ttk.Checkbutton(footer,text='현재 선택한 Jira Issue Type만 Import (이전 매핑 조합 제외)',
                    variable=current_only).pack(side='left')
    ttk.Label(footer,text='신규 필드는 Import 승인 후 생성',style='WizardSub.TLabel').pack(side='left',padx=14)
    ttk.Button(footer,text='다음 (Import 승인)  ›',command=lambda:run('Import',import_now)).pack(side='right')
    relation_rows={}
    relation_catalog=[None]
    reference_catalog={}
    relation_trackers=[]
    reference_fields=[]
    relation_list=ttk.Frame(association_tab);relation_list.pack(fill='both',expand=True,pady=8)
    relation_table=ttk.Treeview(relation_list,columns=('source','direction','mode','target','reverse'),show='headings',selectmode='browse')
    for column,label,width in (('source','Jira Link Type',170),('direction','Outward / Inward',250),
                               ('mode','CB 매핑 방식',140),('target','CB 대상',300),('reverse','방향',80)):
        relation_table.heading(column,text=label);relation_table.column(column,width=width,anchor='w')
    relation_scroll=ttk.Scrollbar(relation_list,orient='vertical',command=relation_table.yview)
    relation_table.configure(yscrollcommand=relation_scroll.set)
    relation_scroll.pack(side='right',fill='y');relation_table.pack(side='left',fill='both',expand=True)
    relation_mode=tk.StringVar(value='')
    relation_reverse=tk.BooleanVar()
    selectors=ttk.Frame(association_tab);selectors.pack(fill='x',pady=4)
    ttk.Label(selectors,text='CB 매핑 방식').grid(row=0,column=0,sticky='w')
    mode_box=ttk.Combobox(selectors,state='readonly',textvariable=relation_mode,values=['association','reference'],width=20)
    mode_box.grid(row=0,column=1,sticky='w',padx=6)
    ttk.Label(selectors,text='Association Type').grid(row=0,column=2,sticky='w')
    relation_target=ttk.Combobox(selectors,state='readonly',width=45)
    relation_target.grid(row=0,column=3,sticky='ew',padx=6)
    ttk.Label(selectors,text='Reference 소유 Tracker').grid(row=1,column=0,sticky='w')
    reference_owner=ttk.Combobox(selectors,state='readonly',width=35)
    reference_owner.grid(row=1,column=1,sticky='ew',padx=6,pady=4)
    ttk.Label(selectors,text='참조 대상 Tracker').grid(row=1,column=2,sticky='w')
    reference_destination=ttk.Combobox(selectors,state='readonly',width=45)
    reference_destination.grid(row=1,column=3,sticky='ew',padx=6,pady=4)
    ttk.Label(selectors,text='Reference Field').grid(row=2,column=0,sticky='w')
    reference_target=ttk.Combobox(selectors,state='readonly',width=80)
    reference_target.grid(row=2,column=1,columnspan=3,sticky='ew',padx=6,pady=4)
    selectors.columnconfigure(3,weight=1)
    ttk.Checkbutton(association_tab,text='방향 반전: 정규화된 Jira outward → inward를 반대로 적용',variable=relation_reverse).pack(anchor='w')
    reference_info=tk.StringVar(value='기본값은 이관하지 않음입니다. 원하는 Jira Link Type만 Association 또는 Reference에 매핑하세요. 미선택 관계는 CB에 생성되지 않습니다.')
    ttk.Label(association_tab,textvariable=reference_info,wraplength=1040).pack(anchor='w',pady=4)
    detail_panel=ttk.Frame(association_tab);detail_panel.pack(fill='x')
    reference_detail=tk.Text(detail_panel,height=4,wrap='word',state='disabled')
    detail_scroll=ttk.Scrollbar(detail_panel,orient='vertical',command=reference_detail.yview)
    reference_detail.configure(yscrollcommand=detail_scroll.set)
    detail_scroll.pack(side='right',fill='y');reference_detail.pack(side='left',fill='x',expand=True)
    def set_reference_detail(value):
        reference_detail.configure(state='normal');reference_detail.delete('1.0','end')
        reference_detail.insert('end',json.dumps(value,ensure_ascii=False,indent=2));reference_detail.configure(state='disabled')
    def refresh_relation_trackers():
        selected_owner=relation_trackers[reference_owner.current()]['id'] if 0<=reference_owner.current()<len(relation_trackers) else None
        selected_destination=relation_trackers[reference_destination.current()]['id'] if 0<=reference_destination.current()<len(relation_trackers) else None
        relation_trackers[:]=relation_tracker_rows(wizard.plan)
        labels=[tracker.get('name',str(tracker['id']))+' · ID '+str(tracker['id']) for tracker in relation_trackers]
        for box,selected in ((reference_owner,selected_owner),(reference_destination,selected_destination)):
            box.configure(values=labels);box.set('')
            index=next((i for i,t in enumerate(relation_trackers) if t['id']==selected),None)
            if index is not None:box.current(index)
    def selected_relation_id():
        selection=relation_table.selection()
        if not selection or selection[0] not in relation_rows:raise ApiError('Jira Link Type을 선택하세요.')
        return relation_rows[selection[0]]
    def display_relations(select_id=None):
        catalog=relation_catalog[0]
        if not catalog:return
        relation_table.delete(*relation_table.get_children());relation_rows.clear()
        mappings=wizard.config['migration'].get('wizard',{}).get('association_types',{})
        for index,link in enumerate(catalog['jira']):
            key=str(link['id']);saved=mappings.get(key)
            label,mode,direction=relation_display(saved,catalog,reference_catalog)
            iid=str(index);relation_rows[iid]=key
            relation_table.insert('','end',iid=iid,values=(link['name']+' · ID '+key,
                link['outward']+' / '+link['inward'],mode,label,direction))
            if key==select_id:relation_table.selection_set(iid)
    def load_reference_fields(selected_id=None,refresh=False):
        if reference_owner.current()<0:raise ApiError('Reference 필드가 속한 실제 Tracker를 선택하세요.')
        tracker=relation_trackers[reference_owner.current()]['id']
        reference_fields.clear();reference_target.configure(values=[]);reference_target.set('');set_reference_detail({})
        reference_info.set('Tracker '+str(tracker)+'의 Reference 필드를 조회합니다.')
        if refresh or tracker not in reference_catalog:
            from migration.wizard import target_fields
            reference_catalog[tracker]=reference_field_rows(target_fields(wizard.cb,tracker))
        reference_fields[:]=reference_catalog[tracker]
        labels=[field.get('label',field.get('name',str(field_identifier(field))))+' · ID '+str(field_identifier(field))+
                ' · '+str(field.get('typeName',field.get('type','타입 미확인')))+
                (' · Hidden' if hidden(field) else '') for field in reference_fields]
        reference_target.configure(values=labels);reference_target.set('')
        index=next((i for i,f in enumerate(reference_fields) if field_identifier(f)==selected_id),None)
        if index is not None:reference_target.current(index)
        warnings=wizard.cb.config.get('tracker_field_evidence',{}).get(str(tracker),{}).get('warnings',[])
        reference_info.set('Tracker '+str(tracker)+'의 실제 Reference 후보 '+str(len(reference_fields))+'개'+
            (' · '+ '; '.join(warnings) if warnings else '')+
            (' · Reference 타입/참조 정의를 가진 필드가 없습니다. 새 필드 설정 또는 상세 API 응답을 확인하세요.' if not reference_fields else ''))
        mappings=wizard.config['migration'].get('wizard',{}).get('association_types',{})
        for iid,key in relation_rows.items():
            label,_,_=relation_display(mappings.get(key),relation_catalog[0],reference_catalog)
            relation_table.set(iid,'target',label)
        inspect_reference()
    def inspect_reference(event=None):
        index=reference_target.current()
        set_reference_detail(reference_fields[index] if 0<=index<len(reference_fields) else {})
    reference_target.bind('<<ComboboxSelected>>',inspect_reference)
    reference_owner.bind('<<ComboboxSelected>>',lambda event:run('Reference Field 조회',lambda:load_reference_fields(refresh=True)))
    def load_relations():
        selected=relation_rows.get(relation_table.selection()[0]) if relation_table.selection() else None
        relation_catalog[0]=wizard.relation_catalog()
        relation_target.configure(values=[t['name']+' · ID '+str(t['id']) for t in relation_catalog[0]['codebeamer']])
        refresh_relation_trackers();display_relations(selected)
    def select_relation(event=None):
        if not relation_table.selection() or relation_table.selection()[0] not in relation_rows:return
        from migration.relation_mapping import normalize
        saved=wizard.config['migration'].get('wizard',{}).get('association_types',{}).get(selected_relation_id())
        relation_target.set('');relation_reverse.set(False);relation_mode.set('')
        refresh_relation_trackers();reference_owner.set('');reference_destination.set('');reference_target.set('');reference_target.configure(values=[])
        reference_fields.clear();set_reference_detail({})
        if not saved:return
        value=normalize(saved);relation_mode.set('reference' if value['mode']=='reference_create' else value['mode']);relation_reverse.set(value['reverse'])
        for box,key in ((reference_owner,'tracker_id'),(reference_destination,'target_tracker_id')):
            index=next((i for i,t in enumerate(relation_trackers) if t['id']==value.get(key)),None)
            if index is not None:box.current(index)
        if value['mode']=='association':
            index=next((i for i,t in enumerate(relation_catalog[0]['codebeamer']) if t['id']==value.get('type_id')),None)
            if index is not None:relation_target.current(index)
        elif value['mode']=='reference' and reference_owner.current()>=0:
            run('Reference Field 조회',lambda:load_reference_fields(value.get('field_id')))
        elif value['mode']=='reference_create':
            reference_info.set(value['creation']['label']+' (신규 Reference 생성 예정) · Import 승인 후 생성 계약을 검증합니다.')
            set_reference_detail(value)
    relation_table.bind('<<TreeviewSelect>>',select_relation)
    def save_relation():
        source_id=selected_relation_id()
        value={'mode':relation_mode.get(),'reverse':relation_reverse.get()}
        if value['mode']=='association':
            if relation_target.current()<0:raise ApiError('CB Association Type을 선택하세요.')
            value['type_id']=relation_catalog[0]['codebeamer'][relation_target.current()]['id']
        elif value['mode']=='reference':
            if reference_owner.current()<0 or reference_destination.current()<0 or reference_target.current()<0:
                raise ApiError('Reference 소유 Tracker·실제 필드·참조 대상 Tracker를 선택하세요.')
            value.update(tracker_id=relation_trackers[reference_owner.current()]['id'],
                         field_id=field_identifier(reference_fields[reference_target.current()]),
                         target_tracker_id=relation_trackers[reference_destination.current()]['id'])
        else:raise ApiError('Association 또는 Reference 방식을 직접 선택하세요.')
        wizard.save_relation_mapping(source_id,value);display_relations(source_id)
    def clear_relation():
        source_id=selected_relation_id();wizard.save_relation_mapping(source_id,None);display_relations(source_id)
        select_relation()
    def create_relation_reference():
        source_id=selected_relation_id()
        if reference_owner.current()<0 or reference_destination.current()<0:
            raise ApiError('Reference 소유 Tracker와 참조 대상 Tracker를 먼저 선택하세요.')
        owner=relation_trackers[reference_owner.current()]['id'];destination=relation_trackers[reference_destination.current()]['id']
        saved=wizard.config['migration'].get('wizard',{}).get('association_types',{}).get(source_id,{})
        saved_creation=saved.get('creation',{}) if isinstance(saved,dict) else {}
        dialog=tk.Toplevel(root);dialog.title('신규 Reference 필드 생성 설정');dialog.transient(root);dialog.grab_set()
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='필드 소유 Tracker '+str(owner)+' → 참조 대상 Tracker '+str(destination)).pack(anchor='w')
        name=tk.StringVar(value=saved_creation.get('label',''))
        ttk.Label(panel,text='신규 필드 이름').pack(anchor='w',pady=(10,2))
        ttk.Entry(panel,textvariable=name,width=48).pack(fill='x')
        multiple=tk.BooleanVar(value=saved_creation.get('multipleSelection',False))
        ttk.Checkbutton(panel,text='다중 Reference',variable=multiple).pack(anchor='w',pady=8)
        ttk.Label(panel,text='설정만 저장합니다. 실제 생성은 Import 승인 후 실행합니다.\n검증된 Reference 생성 템플릿·서버 계약이 필요하며 기존 설정/권한을 보존합니다.',wraplength=420).pack(anchor='w')
        dialog_error=tk.StringVar()
        ttk.Label(panel,textvariable=dialog_error,foreground='#ad2222',wraplength=420).pack(fill='x',pady=6)
        def save():
            try:
                wizard.save_relation_reference_creation(source_id,owner,name.get().strip(),destination,multiple.get(),relation_reverse.get())
            except Exception as exc:
                dialog_error.set(str(exc) if isinstance(exc,(ApiError,ValueError)) else type(exc).__name__)
                raise
            display_relations(source_id);dialog.destroy()
        ttk.Button(panel,text='오류 복사',command=lambda:(dialog.clipboard_clear(),dialog.clipboard_append(dialog_error.get()))).pack(side='left',pady=(12,0))
        ttk.Button(panel,text='생성 설정 저장',command=lambda:run('Reference 생성 설정',save)).pack(side='right',pady=(12,0))
        ttk.Button(panel,text='취소',command=dialog.destroy).pack(side='right',padx=6,pady=(12,0))
    controls=ttk.Frame(association_tab);controls.pack(fill='x',pady=8)
    ttk.Button(controls,text='관계 유형 조회 (GET)',command=lambda:run('관계 유형 조회',load_relations)).pack(side='left')
    ttk.Button(controls,text='Reference 필드 새로 조회',command=lambda:run('Reference Field 조회',lambda:load_reference_fields(refresh=True))).pack(side='left',padx=4)
    ttk.Button(controls,text='＋ 신규 Reference 필드',command=lambda:run('Reference 생성 설정',create_relation_reference)).pack(side='left',padx=4)
    ttk.Button(controls,text='관계 매핑 저장',command=lambda:run('관계 매핑 저장',save_relation)).pack(side='left',padx=4)
    ttk.Button(controls,text='무시 (이관 제외)',command=lambda:run('관계 매핑 제외',clear_relation)).pack(side='left',padx=4)
    ttk.Button(controls,text='오류 복사',command=lambda:(root.clipboard_clear(),root.clipboard_append(status.get()))).pack(side='right')
    saved_catalog=wizard.state.load('wizard','relation-catalog')
    if saved_catalog:
        relation_catalog[0]=saved_catalog
        relation_target.configure(values=[t['name']+' · ID '+str(t['id']) for t in saved_catalog['codebeamer']])
        refresh_relation_trackers();display_relations()
    relation_loaded=[False]
    def relation_tab_opened(event=None):
        if mapping_tabs.select()!=str(association_tab):return
        refresh_relation_trackers()
        if not relation_loaded[0]:
            relation_loaded[0]=True
            run('관계 유형 조회',load_relations)
    mapping_tabs.bind('<<NotebookTabChanged>>',relation_tab_opened)
    root.mainloop()


def launch_connected(config, state):
    """Collect secrets in memory, perform cancellable read-only connection, then open the Wizard."""
    import tkinter as tk
    from tkinter import ttk
    from copy import deepcopy
    from connection_job import ConnectionJob
    from clients.codebeamer_verified_reads import projects as read_cb_projects,load_successful_openapi
    import time
    root=tk.Tk();root.title('Jira → Codebeamer Import Wizard · 1. 서버 연결');root.geometry('760x610')
    style=ttk.Style(root);style.configure('ConnectTitle.TLabel',font=('',17,'bold'))
    outer=ttk.Frame(root,padding=20);outer.pack(fill='both',expand=True)
    ttk.Label(outer,text='1. 서버 연결',style='ConnectTitle.TLabel').pack(anchor='w')
    ttk.Label(outer,text='인증정보는 메모리에서만 사용하며 config 파일이나 상태 보고서에 저장하지 않습니다.').pack(anchor='w',pady=(2,14))
    form=ttk.Frame(outer);form.pack(fill='x')
    values={}
    rows=(
        ('Jira 서버 URL','jira_url',config.get('jira',{}).get('url',''),False),
        ('Jira API 토큰','jira_token','',True),
        ('Codebeamer 서버 URL','cb_url',config.get('codebeamer',{}).get('url',''),False),
        ('Codebeamer 아이디','cb_user','',False),
        ('Codebeamer 비밀번호','cb_password','',True),
        ('Swagger URL (선택 사항, 비우면 자동 탐색)','swagger',config.get('codebeamer',{}).get('swagger_url',''),False))
    for index,(label,key,initial,secret) in enumerate(rows):
        ttk.Label(form,text=label).grid(row=index,column=0,sticky='w',pady=7,padx=(0,12))
        values[key]=tk.StringVar(value=initial)
        ttk.Entry(form,textvariable=values[key],show='*' if secret else '',width=62).grid(row=index,column=1,sticky='ew',pady=7)
    form.columnconfigure(1,weight=1)
    status=tk.StringVar(value='연결을 누르면 Jira 및 Codebeamer 프로젝트를 GET으로 조회합니다. Swagger는 쓰기 계약 확인에만 사용합니다.')
    status_entry=ttk.Entry(outer,textvariable=status,state='readonly');status_entry.pack(fill='x',pady=(18,5))
    status_entry.bind('<Control-a>',lambda event:(status_entry.selection_range(0,tk.END),'break')[-1])
    controls=ttk.Frame(outer);controls.pack(fill='x',pady=8)
    active=[None];result=[None]
    def copy_error():
        root.clipboard_clear();root.clipboard_append(status.get())
    ttk.Button(controls,text='오류 복사',command=copy_error).pack(side='left')
    def required():
        missing=[label for label,key,_,_ in rows[:5] if not values[key].get().strip()]
        if missing:raise ApiError('입력 필요: '+', '.join(missing))
        import urllib.parse
        for key,label in (('jira_url','Jira'),('cb_url','Codebeamer')):
            parsed=urllib.parse.urlsplit(values[key].get().strip())
            if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
                raise ApiError(label+' 서버 URL은 인증정보가 없는 http(s) 주소여야 합니다.')
    def set_busy(busy):
        connect_button.configure(state='disabled' if busy else 'normal')
        cancel_button.configure(state='normal' if busy else 'disabled')
    def connect():
        if active[0] is not None:return
        try:required()
        except ApiError as exc:status.set(str(exc));return
        current=deepcopy(config)
        current.setdefault('jira',{}).update(url=values['jira_url'].get().strip(),auth='bearer')
        current['jira'].pop('username_env',None);current['jira'].pop('password_env',None)
        cb_settings=current.setdefault('codebeamer',{})
        cb_settings.update(url=values['cb_url'].get().strip(),auth='basic',profile='3.0.0.7',
                           swagger_url=values['swagger'].get().strip())
        def jira_credential(label,secret):return values['jira_token'].get()
        def cb_credential(label,secret):return values['cb_password'].get() if secret else values['cb_user'].get()
        jira_http=Http(current['jira']['url'],None,state.root/'requests.jsonl',timeout=10,auth='bearer',
                       allow_http=current['jira'].get('allow_http',False),prompt_auth=True,credential_prompt=jira_credential)
        cb_http=Http(cb_settings['url'],None,state.root/'requests.jsonl',timeout=10,auth='basic',
                     allow_http=cb_settings.get('allow_http',False),prompt_auth=True,credential_prompt=cb_credential)
        jira=JiraClient(jira_http,current['jira'],state.save)
        cb=CodebeamerClient(cb_http,cb_settings,False)
        def fetch():
            saved=load_successful_openapi(state.root,current['jira']['url'],cb_settings['url'])
            if saved:
                cb.spec,cb.config['reused_successful_openapi']=saved
                from clients.codebeamer_3007 import bind
                bind(cb)
            try:jira_projects=jira.projects()
            except ApiError as exc:raise ApiError('Jira 프로젝트 GET: '+str(exc)) from None
            try:cb_projects=read_cb_projects(cb)
            except ApiError as exc:raise ApiError('Codebeamer 프로젝트 GET: '+str(exc)) from None
            if not isinstance(jira_projects,list) or not isinstance(cb_projects,list):
                raise ApiError('프로젝트 조회 응답은 전체 배열이어야 합니다.')
            return current,jira,cb,{'jira':jira_projects,'codebeamer':cb_projects,
                'swagger_warning':None if saved else '이전 성공 OpenAPI 없음: 조회는 가능하지만 실제 쓰기 계약은 아직 미확인'}
        set_busy(True);started=time.monotonic()
        job=ConnectionJob(fetch,timeout=30,clients=(jira_http,cb_http));active[0]=job
        def poll():
            outcome,value=job.poll()
            if outcome=='running':
                status.set('실제 서버 GET 조회 중 · '+str(int(time.monotonic()-started))+'초 / 최대 30초 · 취소 가능')
                root.after(100,poll);return
            active[0]=None;set_busy(False)
            if outcome=='success':
                for client in (jira_http,cb_http):client.read_deadline=None;client.cancel_event=None
                result[0]=value;status.set('연결 및 프로젝트 조회 완료');root.after(50,root.destroy)
            elif outcome=='timeout':status.set('연결 시간 초과(30초). 서버 URL·VPN·인증을 확인하세요.')
            elif outcome=='cancelled':status.set('연결 취소됨. 늦게 도착한 결과는 사용하지 않습니다.')
            else:status.set('연결 실패: '+(str(value) if isinstance(value,ApiError) else type(value).__name__))
        poll()
    connect_button=ttk.Button(controls,text='연결 / 실제 프로젝트 조회 (GET)',command=connect)
    connect_button.pack(side='right')
    cancel_button=ttk.Button(controls,text='취소',state='disabled',command=lambda:active[0].cancel() if active[0] else None)
    cancel_button.pack(side='right',padx=6)
    def close():
        if active[0]:active[0].cancel()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.mainloop()
    if result[0] is None:return
    current,jira,cb,projects=result[0]
    wizard=ImportWizard(jira,cb,state,current)
    launch(wizard,projects)


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
        launch_connected(config,state)
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
