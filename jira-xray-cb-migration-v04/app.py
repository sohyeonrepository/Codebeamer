"""5-step Jira DC/Xray -> Codebeamer desktop migration console.

Use Python 3.11+, Tkinter and requests. No credentials are saved to config.
All write actions are dry-run by default and need explicit confirmation.
"""
from __future__ import annotations

import json
import os
import threading
import queue
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

from migrator import Client, jira_issues, jira_details, xray_relations, build_plan, kind, read, save
from writer import process_items, relation_stage, verify_adapter, build_history_bridge
from full_migration import (extract_xray_test_runs, apply_test_runs,
                            apply_related_associations, audit, execute_java_history_bridge)

CATEGORIES = [('test_plan','Test Plan → Release'),('test_case','Test Case → Test Case'),
              ('test_set','Test Set → Test Set'),('test_execution','Test Execution → Tracker')]

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Jira Xray → Codebeamer Migrator · v0.4')
        self.geometry('1050x780')
        self.minsize(890,680)
        self.cfg_path = Path('config.json')
        self.cfg = read(self.cfg_path,{})
        if not isinstance(self.cfg,dict): self.cfg={}
        self.cfg.setdefault('cb_tracker_ids',{})
        self.cfg.setdefault('field_map',{})
        self.cfg.setdefault('relation_field_map',{})
        self.cfg.setdefault('xray_paths',{})
        self.cfg.setdefault('cb_api',{})
        self.output = Path('output')
        self.jira = None; self.cb = None
        self.issues=[]
        self.cb_fields={}; self.jira_fields={}
        self.track_vars={}
        self.link_vars={}; self.field_widgets=[]
        self.jira_url=tk.StringVar(); self.jira_user=tk.StringVar(); self.jira_pw=tk.StringVar()
        self.cb_url=tk.StringVar(); self.cb_user=tk.StringVar(); self.cb_pw=tk.StringVar()
        self.jira_project=tk.StringVar(value=self.cfg.get('jira_project',''))
        self.cb_project=tk.StringVar(value=str(self.cfg.get('cb_project_id','')))
        self.project_text=tk.StringVar(value='연결 후 프로젝트 조회')
        self.enabled_apply=tk.BooleanVar(value=False)
        self.msg=tk.StringVar(value='준비됨 · 기본 모드: 미리보기 (서버 변경 없음)')
        self._ui_queue=queue.Queue()
        self._busy=False
        self.after(25,self._pump)
        self._layout()
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _layout(self):
        main=ttk.Frame(self,padding=15); main.pack(fill='both',expand=True)
        ttk.Label(main,text='Jira / Xray → Codebeamer',font=('Arial',19,'bold')).pack(anchor='w')
        ttk.Label(main,text='1 연결  →  2 프로젝트 선택  →  3 Tracker/Field 매핑  →  4 데이터 이관  →  5 Reference/Association').pack(anchor='w',pady=(2,10))
        self.tabs=ttk.Notebook(main);self.tabs.pack(fill='both',expand=True)
        self.steps=[ttk.Frame(self.tabs,padding=12) for _ in range(5)]
        for f,title in zip(self.steps,['① 서버 연결','② 프로젝트','③ 트래커·필드 매핑','④ 데이터 이관','⑤ 관계 이관']):self.tabs.add(f,text=title)
        self._step1();self._step2();self._step3();self._step4();self._step5()
        ttk.Separator(main).pack(fill='x',pady=(10,4))
        ttk.Label(main,textvariable=self.msg).pack(anchor='w')
        logs=ttk.LabelFrame(main,text='실행 로그')
        logs.pack(fill='both',expand=False,pady=(7,0))
        self.log_text=tk.Text(logs,height=7,wrap='word',state='disabled');self.log_text.pack(fill='both',expand=True,padx=5,pady=5)

    def _field(self,parent,row,label,var,secret=False,width=65):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=6)
        w=ttk.Entry(parent,textvariable=var,width=width,show='●' if secret else '')
        w.grid(row=row,column=1,sticky='we',padx=(12,0),pady=6)
        parent.columnconfigure(1,weight=1)
        return w

    def _step1(self):
        f=self.steps[0]
        ttk.Label(f,text='Jira Data Center',font=('Arial',13,'bold')).pack(anchor='w')
        box=ttk.Frame(f);box.pack(fill='x',pady=(3,13))
        self._field(box,0,'Jira URL',self.jira_url)
        self._field(box,1,'ID',self.jira_user)
        self._field(box,2,'Password / Token',self.jira_pw,True)
        ttk.Label(f,text='Codebeamer',font=('Arial',13,'bold')).pack(anchor='w')
        box=ttk.Frame(f);box.pack(fill='x',pady=(3,13))
        self._field(box,0,'CB URL (origin)',self.cb_url)
        self._field(box,1,'ID',self.cb_user)
        self._field(box,2,'Password / Token',self.cb_pw,True)
        ttk.Button(f,text='서버 연결 확인',command=lambda:self._start(self._connect)).pack(anchor='e')
        ttk.Label(f,text='주의: HTTP 사용 시 인증정보가 암호화되지 않습니다. 가능하면 HTTPS를 사용하세요.',foreground='#9c5c24').pack(anchor='w',pady=12)

    def _step2(self):
        f=self.steps[1]
        self._field(f,0,'Jira 프로젝트 Key',self.jira_project,width=35)
        self._field(f,1,'Codebeamer 프로젝트 ID',self.cb_project,width=35)
        b=ttk.Frame(f);b.grid(row=2,column=1,sticky='w',pady=12)
        ttk.Button(b,text='Jira 프로젝트 목록',command=lambda:self._start(self._list_jira_projects)).pack(side='left',padx=(0,8))
        ttk.Button(b,text='CB 프로젝트 목록',command=lambda:self._start(self._list_cb_projects)).pack(side='left')
        ttk.Label(f,textvariable=self.project_text).grid(row=3,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(f,text='목록 조회가 지원되지 않는 버전에서는 프로젝트 Key/ID를 직접 입력하세요.').grid(row=4,column=0,columnspan=2,sticky='w')
        ttk.Button(f,text='프로젝트 확정 / 저장',command=self._save_project).grid(row=5,column=1,sticky='e',pady=20)

    def _step3(self):
        f=self.steps[2]
        ttk.Label(f,text='대상 Tracker ID 입력 후 각각 필드 불러오기',font=('Arial',12,'bold')).pack(anchor='w')
        outer=ttk.LabelFrame(f,text='대상 Tracker 설정');outer.pack(fill='x',pady=10)
        for n,(typ,label) in enumerate(CATEGORIES):
            ttk.Label(outer,text=label).grid(row=n,column=0,padx=7,pady=5,sticky='w')
            v=tk.StringVar(value=str(self.cfg['cb_tracker_ids'].get(typ,'') or ''))
            self.track_vars[typ]=v
            ttk.Entry(outer,textvariable=v,width=17).grid(row=n,column=1,padx=7)
        ttk.Button(outer,text='Tracker 및 필드 불러오기',command=lambda:self._start(self._load_fields)).grid(row=4,column=0,columnspan=2,padx=7,pady=8,sticky='w')
        ttk.Label(f,text='필드 매핑: 가져올 Jira 필드를 체크하고 Codebeamer 필드 ID를 입력하세요.').pack(anchor='w')
        panel=ttk.Frame(f);panel.pack(fill='both',expand=True,pady=6)
        canvas=tk.Canvas(panel,highlightthickness=0)
        scroll=ttk.Scrollbar(panel,orient='vertical',command=canvas.yview)
        self.field_frame=ttk.Frame(canvas)
        self.field_frame.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0,0),window=self.field_frame,anchor='nw')
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        actions=ttk.Frame(f);actions.pack(anchor='e',pady=6)
        ttk.Button(actions,text='새 필드 생성 도구',command=self._field_dialog).pack(side='left',padx=5)
        ttk.Button(actions,text='체크박스 매핑 저장',command=self._save_mapping).pack(side='left')

    def _step4(self):
        f=self.steps[3]
        ttk.Label(f,text='기본 Item 이관 / Test Run / History',font=('Arial',13,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
        buttons=[('① Jira/Xray 원본 추출',self._export),('② 기본 데이터 미리보기',self._plan_items),
                 ('③ 기본 Item 이관 실행',self._apply_items),('④ Xray Test Run 원본 추출',self._export_runs),
                 ('⑤ Test Run 이관 미리보기',self._plan_runs),('⑥ Test Run 이관 실행',self._apply_runs),
                 ('⑦ History Java 전달 파일 생성',self._history_queue),('⑧ Java History Writer 실행',self._apply_history)]
        for i,(title,fn) in enumerate(buttons):
            ttk.Button(f,text=title,command=lambda cb=fn:self._start(cb),width=46).grid(row=1+i//2,column=i%2,padx=8,pady=8,sticky='w')
        self._apply_controls(f,row=5)
        ttk.Label(f,text='원본 JSON·체크포인트·오류 목록은 output 폴더에 보관됩니다.').grid(row=6,column=0,columnspan=2,sticky='w',pady=8)

    def _step5(self):
        f=self.steps[4]
        ttk.Label(f,text='모든 Item 생성 후 ID 매핑 기준으로 관계 연결',font=('Arial',13,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Label(f,text='Reference 필드 ID를 입력하세요 (키별 매핑 필요).').grid(row=1,column=0,columnspan=2,sticky='w')
        for row,(typ,label) in enumerate(CATEGORIES):
            v=tk.StringVar(value=json.dumps(self.cfg.get('relation_field_map',{}).get(typ,{}),ensure_ascii=False))
            self.link_vars[typ]=v
            ttk.Label(f,text=label).grid(row=row+2,column=0,sticky='w',pady=5)
            ttk.Entry(f,textvariable=v,width=78).grid(row=row+2,column=1,sticky='we',padx=10)
        ttk.Label(f,text='입력 형식: {"tests":{"cb_reference_field_id":12345}}').grid(row=6,column=0,columnspan=2,sticky='w',pady=6)
        buttons=[('Reference 미리보기',self._plan_refs),('Reference 적용',self._apply_refs),
                 ('Linked Issues 미리보기',self._plan_assoc),('Related Association 적용',self._apply_assoc),
                 ('정합성 보고서 생성',self._audit)]
        for n,(title,fn) in enumerate(buttons):
            ttk.Button(f,text=title,command=lambda cb=fn:self._start(cb),width=33).grid(row=7+n//2,column=n%2,padx=7,pady=8,sticky='w')
        f.columnconfigure(1,weight=1)

    def _apply_controls(self,f,row):
        ttk.Checkbutton(f,text='실제 Codebeamer 쓰기 작업 허용 (각 단계 실행 시 추가 확인)',variable=self.enabled_apply).grid(row=row,column=0,columnspan=2,sticky='w',pady=16)

    def _log(self,message):
        def draw():
            self.log_text.configure(state='normal');self.log_text.insert('end',str(message)+'\n')
            self.log_text.see('end');self.log_text.configure(state='disabled')
            self.msg.set(str(message).splitlines()[0][:180])
        self._post(draw)

    def _pump(self):
        try:
            while True:self._ui_queue.get_nowait()()
        except queue.Empty:pass
        self.after(25,self._pump)

    def _post(self,callback):
        self._ui_queue.put(callback)

    def _value(self,variable):
        if threading.current_thread() is threading.main_thread():return variable.get()
        event=threading.Event();result=[]
        def read():
            try:result.append(variable.get())
            finally:event.set()
        self._post(read)
        if not event.wait(60):raise RuntimeError('UI unavailable')
        return result[0]

    def _start(self,func):
        if self._busy:return
        self._busy=True
        def work():
            try:func()
            except Exception as e:
                self._log(f'ERROR {type(e).__name__}: {e}')
            finally:self._post(lambda:setattr(self,'_busy',False))
        threading.Thread(target=work,daemon=True).start()

    def _ensure_connection(self,source):
        cli=self.jira if source=='jira' else self.cb
        if cli is None:raise RuntimeError(f'{source.upper()} 연결 버튼을 먼저 누르세요')
        return cli

    def _connect(self):
        jira=Client(self._value(self.jira_url).strip(),self._value(self.jira_user).strip(),self._value(self.jira_pw))
        cb=Client(self._value(self.cb_url).strip(),self._value(self.cb_user).strip(),self._value(self.cb_pw))
        j=jira.request('GET','/rest/api/2/myself')
        self._log('Jira 인증 성공: '+str(j.get('displayName') or j.get('name')))
        # Codebeamer 3.x endpoint is configurable; GET tracker configuration if ID exists.
        tracker=next((int(self._value(v)) for v in self.track_vars.values() if self._value(v).isdigit() and int(self._value(v))>0),None)
        if tracker:
            self._cb_tracker_config(cb,tracker)
            self._log('Codebeamer 트래커 GET 성공')
        else:self._log('Codebeamer 인증 검증 보류: 3단계에서 대상 Tracker ID 입력 후 필드 불러오기')
        self.jira=jira;self.cb=cb

    def _list_jira_projects(self):
        rows=self._ensure_connection('jira').request('GET','/rest/api/2/project')
        self._show_picker(rows,'Jira 프로젝트',lambda r:self.jira_project.set(r.get('key','')))

    def _list_cb_projects(self):
        # Query path can be overridden in config if target CB uses other context prefix.
        path=self.cfg.get('cb_projects_path','/cb/api/v3/projects')
        rows=self._ensure_connection('cb').request('GET',path)
        if isinstance(rows,dict):rows=rows.get('projects',rows.get('items',[]))
        self._show_picker(rows,'Codebeamer 프로젝트',lambda r:self.cb_project.set(str(r.get('id',''))))

    def _show_picker(self,rows,title,callback):
        if not isinstance(rows,list):raise ValueError('프로젝트 목록 응답 형식 확인 필요')
        def draw():
            win=tk.Toplevel(self);win.title(title);win.geometry('530x430')
            tree=ttk.Treeview(win,columns=('id','key','name'),show='headings')
            for col in ('id','key','name'):tree.heading(col,text=col)
            for i,r in enumerate(rows):tree.insert('', 'end',iid=str(i),values=(r.get('id'),r.get('key',''),r.get('name','')))
            tree.pack(fill='both',expand=True)
            def chosen():
                sel=tree.selection()
                if sel:callback(rows[int(sel[0])]);win.destroy()
            ttk.Button(win,text='선택',command=chosen).pack(pady=8)
        self._post(draw)

    def _save_project(self):
        try:
            n=int(self._value(self.cb_project))
            if n<=0:raise ValueError('양의 프로젝트 ID가 필요합니다')
            if not self._value(self.jira_project).strip():raise ValueError('Jira 프로젝트 Key가 필요합니다')
            self.cfg['jira_project']=self._value(self.jira_project).strip()
            self.cfg['cb_project_id']=n
            self._save_config();self._log('프로젝트 선택 저장 완료')
        except ValueError as exc:messagebox.showerror('프로젝트',str(exc))

    def _cb_tracker_config(self,client,tracker):
        template=self.cfg.get('cb_tracker_config_path','/cb/api/v3/tracker/{tracker_id}/configuration')
        return client.request('GET',template.replace('{tracker_id}',str(tracker)))

    def _load_fields(self):
        cb=self._ensure_connection('cb')
        self._save_trackers()
        if not self.issues:
            jira=self._ensure_connection('jira')
            self.issues=jira_issues(jira,self._value(self.jira_project).strip())
        grouped={t:{} for t,_ in CATEGORIES}
        for issue in self.issues:
            t=kind(issue)
            if t not in grouped:continue
            for field_id,val in issue.get('fields',{}).items():
                if field_id not in ('issuetype',):grouped[t][field_id]=field_id
        self.jira_fields=grouped
        for t,_ in CATEGORIES:
            tracker=self.cfg['cb_tracker_ids'].get(t)
            if not tracker:continue
            payload=self._cb_tracker_config(cb,tracker)
            self.cb_fields[t]=payload.get('fields',[])
            save(self.output/f'tracker_{tracker}_configuration_backup.json',payload)
        self._post(self._render_fields)
        self._log('Jira 필드 + Codebeamer Tracker Configuration 로드 완료; 백업 저장')

    def _render_fields(self):
        for child in self.field_frame.winfo_children():child.destroy()
        self.field_widgets=[]
        row=0
        for typ,title in CATEGORIES:
            ttk.Label(self.field_frame,text=title,font=('Arial',11,'bold')).grid(row=row,column=0,columnspan=3,sticky='w',pady=(10,4))
            row+=1
            for key in sorted(self.jira_fields.get(typ,{})):
                existing=self.cfg.get('field_map',{}).get(typ,{}).get(key)
                enabled=tk.BooleanVar(value=existing is not None)
                dest=tk.StringVar(value=str(existing or ''))
                ttk.Checkbutton(self.field_frame,text=key,variable=enabled).grid(row=row,column=0,sticky='w',padx=12)
                ttk.Entry(self.field_frame,textvariable=dest,width=20).grid(row=row,column=1,padx=5)
                ttk.Label(self.field_frame,text='CB field ID').grid(row=row,column=2)
                self.field_widgets.append((typ,key,enabled,dest))
                row+=1
            cb_fields=self.cb_fields.get(typ,[])
            if cb_fields:
                ttk.Label(self.field_frame,text='CB 후보: '+', '.join(f"{p.get('label')}({p.get('referenceId')})" for p in cb_fields[:15]),wraplength=760,foreground='#5b6470').grid(row=row,column=0,columnspan=3,sticky='w',padx=12,pady=6)
                row+=1

    def _save_trackers(self):
        for typ,v in self.track_vars.items():
            txt=self._value(v).strip()
            self.cfg['cb_tracker_ids'][typ]=int(txt) if txt.isdigit() and int(txt)>0 else 0
        self._save_config()


    def _field_dialog(self):
        win=tk.Toplevel(self);win.title('필드 생성 · 전체 Configuration 보존')
        win.geometry('530x380');win.transient(self)
        typ=tk.StringVar(value='test_case');kind_var=tk.StringVar(value='Text')
        label=tk.StringVar(value='');ref=tk.StringVar(value='')
        options=tk.StringVar(value='');multi=tk.BooleanVar(value=False)
        entries=[('Tracker 종류',typ),('필드 이름',label),('Reference ID (중복되지 않는 ID)',ref),('Choice 옵션 (쉼표 구분)',options)]
        for row,(text_,var) in enumerate(entries):
            ttk.Label(win,text=text_).grid(row=row,column=0,sticky='w',padx=12,pady=9)
            if row==0:ttk.Combobox(win,textvariable=var,values=[k for k,_ in CATEGORIES],state='readonly',width=30).grid(row=row,column=1,pady=9)
            else:ttk.Entry(win,textvariable=var,width=38).grid(row=row,column=1,pady=9)
        ttk.Label(win,text='필드 타입').grid(row=4,column=0,padx=12,pady=8)
        ttk.Combobox(win,textvariable=kind_var,values=['Text','Integer','Date','Choice','User','Reference'],state='readonly',width=30).grid(row=4,column=1)
        ttk.Checkbutton(win,text='복수 선택',variable=multi).grid(row=5,column=1,sticky='w',pady=8)
        ttk.Label(win,text='Reference 타입의 대상 필터는 서버별 확인이 필요합니다.',foreground='#994a35').grid(row=6,column=0,columnspan=2,padx=12,pady=6)
        def process(apply):
            try:
                from field_config import propose_and_apply_field
                tracker=self.cfg['cb_tracker_ids'].get(typ.get())
                if not tracker:raise ValueError('3단계 Tracker ID를 먼저 입력하세요')
                if not ref.get().isdigit():raise ValueError('양의 Reference ID 입력 필요')
                spec={'label':label.get().strip(),'referenceId':int(ref.get()),'type':kind_var.get(),
                      'options':[x.strip() for x in options.get().split(',') if x.strip()],
                      'multipleSelection':multi.get()}
                if apply:
                    if not self._value(self.enabled_apply):raise ValueError('4단계 실제 쓰기 허용을 먼저 체크하세요')
                    if not messagebox.askyesno('설정 변경','기존 전체 Tracker Configuration을 백업한 뒤 신규 필드 1개를 추가합니다. 진행할까요?'):return
                win.destroy()
                def job():
                    result=propose_and_apply_field(self._ensure_connection('cb'),tracker,spec,self.output,apply=apply,
                     get_path=self.cfg.get('cb_tracker_config_path','/cb/api/v3/tracker/{tracker_id}/configuration'),
                     post_path=self.cfg.get('cb_tracker_post_path','/cb/api/v3/tracker/configuration'))
                    self._log('필드 작업: '+str(result))
                self._start(job)
            except Exception as e:messagebox.showerror('필드 생성',str(e))
        ttk.Button(win,text='POST JSON 미리보기 생성',command=lambda:process(False)).grid(row=7,column=0,pady=12)
        ttk.Button(win,text='실제 필드 생성',command=lambda:process(True)).grid(row=7,column=1,pady=12)

    def _save_mapping(self):
        try:
            mappings={t:{} for t,_ in CATEGORIES}
            for typ,key,enabled,dest in self.field_widgets:
                if enabled.get():
                    if not dest.get().isdigit() or int(dest.get())<=0:raise ValueError(f'{typ}:{key} → CB 필드 ID 입력 필요')
                    mappings[typ][key]=int(dest.get())
            self.cfg['field_map']=mappings
            self._save_trackers();self._save_config();self._log('필드 체크박스 매핑 저장 완료')
        except ValueError as exc:messagebox.showerror('매핑',str(exc))

    def _save_config(self):
        save(self.cfg_path,self.cfg)

    def _export(self):
        jira=self._ensure_connection('jira')
        self._save_trackers()
        project=self._value(self.jira_project).strip()
        if not project:raise ValueError('2단계에서 Jira 프로젝트를 선택하세요')
        issues=jira_issues(jira,project)
        details=[];errors=[]
        for n,i in enumerate(issues,1):
            try:
                d=jira_details(jira,i)
                d['_xray_relations']=xray_relations(jira,d,self.cfg)
                details.append(d)
            except Exception as exc:errors.append({'key':i['key'],'error':str(exc)})
            if n%20==0:self._log(f'Jira 읽기 진행: {n}/{len(issues)}')
        self.issues=details
        save(self.output/'source_issues.json',details)
        save(self.output/'source_export_errors.json',errors)
        # Never mark incomplete output as complete.
        try:save(self.output/'migration_plan.json',build_plan(details,self.cfg))
        except ValueError as exc:self._log('Tracker 미설정: '+str(exc))
        self._log(f'원본 추출 완료 {len(details)}/{len(issues)}건, 실패 {len(errors)}건')

    def _issues(self):
        values=read(self.output/'source_issues.json',None)
        if not isinstance(values,list):raise ValueError('원본 추출을 먼저 실행하세요')
        return values

    def _idmap(self):return read(self.output/'id_map.json',{})

    def _plan_items(self):
        issues=self._issues();self._save_trackers()
        cfg=dict(self.cfg,kind_by_key={i['key']:kind(i) for i in issues})
        res=process_items(issues,cfg,None,self.output/'id_map.json',apply=False)
        save(self.output/'item_operations.json',res['operations'])
        self._log(f'Item 생성 미리보기 {len(res["operations"])}건 · 서버 쓰기 없음')

    def _writable(self,operation):
        if not self._value(self.enabled_apply):raise RuntimeError('4단계의 실제 쓰기 허용 체크박스를 켜세요')
        # Tk dialogs must run on UI thread; thread-safe prompt via event.
        ev=threading.Event();answer=[False]
        def ask():
            answer[0]=messagebox.askyesno('실제 서버 변경 확인',f'{operation}\n\nCodebeamer 서버 데이터를 변경합니다. 진행할까요?')
            ev.set()
        self._post(ask);ev.wait(60)
        if not answer[0]:raise RuntimeError('사용자가 실행 취소')

    def _apply_items(self):
        self._writable('Item 신규 생성')
        verify_adapter(self.cfg)
        cfg=dict(self.cfg,kind_by_key={i['key']:kind(i) for i in self._issues()})
        res=process_items(self._issues(),cfg,self._ensure_connection('cb'),self.output/'id_map.json',apply=True)
        save(self.output/'item_operations.json',res['operations'])
        self._log(f'Item 처리 완료 · 매핑 {len(res["state"])}건')

    def _export_runs(self):
        self._log('Xray Test Run 추출 시작')
        report=extract_xray_test_runs(self._ensure_connection('jira'),self._issues(),self.output)
        self._log(str(report))

    def _plan_runs(self):
        result=apply_test_runs(None,read(self.output/'xray_test_runs.json',[]),self._idmap(),self.output,self.cfg,apply=False)
        self._log(str(result))

    def _apply_runs(self):
        self._writable('Test Run + 테스트 결과 쓰기')
        result=apply_test_runs(self._ensure_connection('cb'),read(self.output/'xray_test_runs.json',[]),self._idmap(),self.output,self.cfg,apply=True)
        self._log(str(result))

    def _history_queue(self):
        queue=build_history_bridge(self._issues(),self._idmap())
        result=execute_java_history_bridge(queue,self.cfg,self.output,apply=False)
        self._log(str(result))

    def _apply_history(self):
        self._writable('Java History Writer 실행')
        queue=build_history_bridge(self._issues(),self._idmap())
        result=execute_java_history_bridge(queue,self.cfg,self.output,apply=True)
        self._log(str(result))

    def _save_relations(self):
        maps={}
        for typ,v in self.link_vars.items():
            value=json.loads(self._value(v).strip() or '{}')
            if not isinstance(value,dict):raise ValueError(f'{typ}: JSON object required')
            maps[typ]=value
        self.cfg['relation_field_map']=maps
        self._save_config()

    def _refs(self,apply):
        self._save_relations()
        if apply:verify_adapter(self.cfg)
        cfg=dict(self.cfg,kind_by_key={i['key']:kind(i) for i in self._issues()})
        ops=relation_stage(self._issues(),cfg,self._idmap(),self._ensure_connection('cb') if apply else None,apply=apply)
        save(self.output/'reference_operations.json',ops)
        from collections import Counter
        self._log('Reference: '+str(dict(Counter(x['status'] for x in ops))))
    def _plan_refs(self):self._refs(False)
    def _apply_refs(self):self._writable('Reference 연결');self._refs(True)

    def _assoc(self,apply):
        result=apply_related_associations(self._ensure_connection('cb') if apply else None,
             self._issues(),self._idmap(),self.output,apply=apply,
             api_prefix=self.cfg.get('cb_api_prefix','/cb/api'))
        self._log('Related Association: '+str(result))
    def _plan_assoc(self):self._assoc(False)
    def _apply_assoc(self):self._writable('Related Association 생성');self._assoc(True)

    def _audit(self):
        report=audit(self._issues(),self._idmap(),read(self.output/'xray_test_runs.json',[]),self.output)
        self._log('검증 보고서 생성: output/audit_report.json · '+str(report))

    def _close(self):
        # Credentials only reside in memory, never persisted.
        self.destroy()

if __name__=='__main__':
    App().mainloop()
