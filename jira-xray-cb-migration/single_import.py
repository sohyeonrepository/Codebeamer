"""Explicitly scoped live API creation check. Never claims full migration readiness."""
import hashlib
import json
import re
import urllib.parse
from copy import deepcopy
from pathlib import Path
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import request_schema, schema_object, field_identifier

OPERATIONS = {
    'project_get': ('GET', ('getProject',)),
    'tracker_get': ('GET', ('getTracker',)),
    'fields': ('GET', ('getTrackerFields', 'getTrackerItemFields')),
    'item_get': ('GET', ('getTrackerItem',)),
    'item_create': ('POST', ('createTrackerItem',)),
    'tracker_create': ('POST', ('createTracker',)),
    'field_create': ('POST', ('createTrackerField', 'createField')),
}


def validate(spec, value, schema, path='payload'):
    """Validate the supplied JSON against the discovered request, fail closed."""
    schema = schema_object(spec, schema)
    if value is None:
        if schema.get('nullable') or schema.get('x-nullable'):
            return
        raise ApiError(path + ': null is not explicitly allowed')
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float), 'boolean': type(value) is bool}
    if kind in valid and not valid[kind]:
        raise ApiError(path + ': expected ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ApiError(path + ': value outside Swagger enum')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for name in schema.get('required', []):
            if name not in value and not properties.get(name, {}).get('readOnly'):
                raise ApiError(path + '.' + name + ': required by Swagger')
        for name, child in value.items():
            if name in properties:
                if properties[name].get('readOnly'):
                    raise ApiError(path + '.' + name + ': read-only')
                validate(spec, child, properties[name], path + '.' + name)
            elif schema.get('additionalProperties') is False:
                raise ApiError(path + '.' + name + ': not accepted by Swagger')
            elif isinstance(schema.get('additionalProperties'), dict):
                validate(spec, child, schema['additionalProperties'], path + '.' + name)
    if isinstance(value, list) and 'items' in schema:
        for index, child in enumerate(value):
            validate(spec, child, schema['items'], path + '[' + str(index) + ']')


class SingleImport:
    def __init__(self, jira, cb, state, config, issue_key, project_id, tracker_id):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*', issue_key):
            raise ApiError('Exactly one Jira issue key is required; JQL/project selection is not allowed')
        if type(project_id) is not int or project_id <= 0 or type(tracker_id) is not int or tracker_id <= 0:
            raise ApiError('Explicit positive test project/Tracker IDs are required')
        self.jira, self.cb, self.state, self.config = jira, cb, state, config
        self.issue_key, self.project_id, self.tracker_id = issue_key, project_id, tracker_id
        self.cb.write = False
        self.profile = deepcopy(config.get('single_import', {}))
        identity = {'jira': config['jira']['url'], 'codebeamer': config['codebeamer']['url'],
                    'issue_key': issue_key, 'project_id': project_id, 'tracker_id': tracker_id}
        previous = state.get('single-import-binding')
        if previous and previous[1] != identity:
            raise ApiError('Use a different state directory for a different test issue/target')
        state.mark('single-import-binding', 'done', identity)
        self.report = dict(scope='single-issue-create-check', full_migration_complete=False,
                           complete=False, writes=[], blockers=[], requests=[], target=identity,
                           ancillary_features={'status': 'outside-explicit-create-check', 'reason':
                           'Attachment/Comment/Association/Jira History/R4J/Xray are not validated by this creation check. Use the full Wizard for full migration.'})

    def save(self):
        path = self.state.root / 'single-import-report.json'
        path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
        return self.report

    def operation(self, name):
        if name == 'field_create' and name not in self.cb.config.setdefault('operations',{}) and 'tracker_configuration_update' in self.cb.config['operations']:
            self.cb.config['operations'][name] = deepcopy(self.cb.config['operations']['tracker_configuration_update'])
        if name not in self.cb.config.setdefault('operations', {}):
            method, ids = OPERATIONS.get(name, ('', ()))
            matches = [dict(path=path, method=method) for path, methods in self.cb.spec['paths'].items()
                       for verb, op in methods.items() if verb.upper() == method and isinstance(op, dict)
                       and op.get('operationId') in ids]
            if len(matches) != 1:
                raise ApiError(name + ': configure a unique operation from this server Swagger (candidates: ' + ', '.join(ids) + ')')
            self.cb.config['operations'][name] = matches[0]
        op = self.cb.operation(name)
        expected = 'GET' if name in ('project_get','tracker_get','fields','item_get') else 'POST'
        if op['method'].upper() != expected:
            raise ApiError(name + ': only ' + expected + ' is allowed in this test mode')
        config_api = op['path'] == '/v3/tracker/configuration'
        if name in ('item_create', 'field_create') and '{trackerId}' not in op['path'] and not (name == 'field_create' and config_api):
            raise ApiError(name + ': a Swagger path scoped by trackerId is required')
        if name == 'fields' and '{trackerId}' not in op['path']:
            raise ApiError('fields: select a Tracker field-list API, not an Item field-value API')
        if name == 'tracker_create' and '{projectId}' not in op['path'] and not config_api:
            raise ApiError('tracker_create: projectId-scoped API required')
        if op.get('path_parameters') or 'body' in op:
            raise ApiError(name + ': body/path overrides are not allowed in the bounded live test; use the Swagger-native payload')
        return op

    def request(self, name, payload, params):
        op = self.operation(name)
        api = self.cb.spec['paths'][op['path']][op['method'].lower()]
        schema = request_schema(self.cb.spec, api)
        if not isinstance(schema.get('properties'), dict) or not schema['properties']:
            raise ApiError(name + ': Swagger does not provide a usable JSON object request schema')
        validate(self.cb.spec, payload, schema)
        # Validate the actual body serializer too (native customFields vs FieldValue).
        from clients.codebeamer_payloads import request_body
        body = request_body(api.get('operationId'), payload, self.cb.config.get('field_bindings', {}))
        validate(self.cb.spec, body, schema)
        path = re.sub(r'\{([^}]+)\}', lambda m: segment(params[m[1]]), op['path'])
        if op.get('query'):
            from clients.codebeamer_client import render
            path += '?' + urllib.parse.urlencode({k:render(v,dict(params,payload=payload)) for k,v in op['query'].items()},doseq=True)
        result = {'operation': name, 'method': op['method'], 'path': path, 'body': body}
        self.report['requests'].append(result)
        return body

    def prepare(self):
        self.cb.write = False
        self.report.update(blockers=[], requests=[], complete=False)
        def check(stage, action):
            try:
                return action()
            except (ApiError, KeyError, ValueError, OSError, TypeError) as exc:
                self.report['blockers'].append({'stage': stage, 'reason': str(exc) if isinstance(exc,ApiError) else type(exc).__name__})
        if check('swagger', self.cb.discover) is None:
            return self.save()
        self.state.save('discovery','single-openapi',self.cb.spec)
        capabilities = []
        for path, methods in self.cb.spec['paths'].items():
            for method, operation in methods.items():
                if method.lower() not in ('get','post','put','patch') or not isinstance(operation,dict):
                    continue
                capability = {'method':method.upper(),'path':path,'operationId':operation.get('operationId')}
                if method.lower() != 'get':
                    try:capability['request_schema'] = request_schema(self.cb.spec,operation)
                    except (ApiError, KeyError) as exc:capability['schema_blocker'] = str(exc)
                capabilities.append(capability)
        (self.state.root / 'api-capabilities.json').write_text(json.dumps(capabilities,ensure_ascii=False,indent=2),encoding='utf-8')
        self.report['swagger_version'] = self.cb.spec.get('info', {}).get('version')
        self.report['version_notice'] = 'Swagger API version is evidence only; it does not prove the installed product build is 3.0.0.7.'
        for name in ('project_get','tracker_get','fields','item_get','item_create'):
            check('api:' + name, lambda n=name: self.operation(n))
        issue = check('jira-one-issue', lambda: self.jira.http.request('GET', '/rest/api/2/issue/' + segment(self.issue_key) + '?fields=*all'))
        project = check('existing-test-project', lambda: self.cb.call('project_get', {'projectId': self.project_id}))
        tracker = check('existing-test-tracker', lambda: self.cb.call('tracker_get', {'trackerId': self.tracker_id}))
        fields = check('test-tracker-fields', lambda: self.cb.call('fields', {'trackerId': self.tracker_id}))
        if project is not None and project.get('id') != self.project_id:
            self.report['blockers'].append({'stage':'project','reason':'Project ID readback mismatch'})
        if tracker is not None:
            pid = tracker.get('project', {}).get('id')
            if tracker.get('id') != self.tracker_id or pid != self.project_id:
                self.report['blockers'].append({'stage':'tracker','reason':'Tracker ID/project membership not verified; tracker_get must return id and project.id'})
        if fields is not None and not isinstance(fields, list):
            self.report['blockers'].append({'stage':'fields','reason':'fields operation must normalize a complete array'})
        self.report['readback'] = {'project':project, 'tracker':tracker, 'fields':fields}
        self.report['creation_scope'] = {
            'item':'exactly one create; no existing Item updates',
            'tracker':'explicit additional create probe' if self.profile.get('tracker_payload') is not None else 'reuse designated test Tracker; Tracker creation NOT tested',
            'field':'explicit additional create probe' if self.profile.get('field_payload') is not None else 'reuse current fields; Field creation NOT tested'}
        if issue is not None:
            if issue.get('key') != self.issue_key or not isinstance(issue.get('fields', {}).get('summary'), str):
                self.report['blockers'].append({'stage':'jira-one-issue','reason':'Exact Jira key/summary verification failed'})
            else:
                self.issue = issue
                marker = '[Jira single-import check ' + issue['key'] + ' / ' + str(issue['id']) + ']'
                self.marker = marker
                body = deepcopy(self.profile.get('item_payload', {}))
                forbidden = set(body) & {'id','tracker','project','parent','children','associations','comments','attachments'}
                if forbidden:
                    self.report['blockers'].append({'stage':'item','reason':'Target/relationship overrides forbidden: ' + ', '.join(sorted(forbidden))})
                body['name'] = issue['fields']['summary']
                description = issue['fields'].get('description')
                if description is not None and not isinstance(description,str):
                    self.report['blockers'].append({'stage':'description','reason':'Structured Jira Description requires a verified adapter'})
                else:
                    body['description'] = (description or '') + '\n\n' + marker
                    self.body = check('item-request-schema', lambda: self.request('item_create',body,{'trackerId':self.tracker_id}))
        # Optional creation probes are explicit payloads, never inferred from numeric type IDs.
        for name, payload_key, params in (
            ('field_create','field_payload',{'trackerId':self.tracker_id}),
            ('tracker_create','tracker_payload',{'projectId':self.project_id})):
            if self.profile.get(payload_key) is not None:
                payload = self.profile[payload_key]
                if 'id' in payload:
                    self.report['blockers'].append({'stage':name,'reason':'Existing IDs are forbidden in create payloads'})
                if name == 'field_create':
                    if payload.get('mandatory') is not False:
                        self.report['blockers'].append({'stage':name,'reason':'Test Field must explicitly set mandatory=false'})
                    label = payload.get('label', payload.get('name'))
                    digest = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                    known = self.state.get('single:field_create:' + digest)
                    matches = [f for f in fields if f.get('label',f.get('name')) == label] if isinstance(fields,list) else []
                    own = bool(known and known[0] == 'done' and len(matches) == 1 and field_identifier(matches[0]) == known[1].get('id'))
                    if not label or not isinstance(fields,list) or (matches and not own):
                        self.report['blockers'].append({'stage':name,'reason':'New unique Field name and full current field list are required'})
                if name == 'tracker_create' and 'project' in payload and payload['project'] != {'id':self.project_id}:
                    self.report['blockers'].append({'stage':name,'reason':'Tracker payload project must match designated test project'})
                def planned_request(n=name,k=payload_key,p=params):
                    body = self.profile[k]
                    op = self.operation(n)
                    if op['path'] == '/v3/tracker/configuration':
                        if n == 'tracker_create':
                            basic = body.get('basicInformation',{})
                            if basic.get('trackerId') is not None or basic.get('projectId') != self.project_id:
                                raise ApiError('New Tracker Configuration must omit trackerId and use the selected projectId')
                        else:
                            from clients.codebeamer_3007 import configuration_field_contract
                            from mapping_helper import prepare_configuration_permissions
                            required = ('typeId','mandatory','multipleSelection','options','reference','permission')
                            if any(k not in body for k in required) or not isinstance(body['permission'],dict):
                                raise ApiError('Preserving Field Configuration helper requires explicit type/multiplicity/options/reference/Permission metadata')
                            self.field_expected = configuration_field_contract(self.cb,body,self.tracker_id)
                            if body['typeId'] not in {f.get('typeId') for f in self.field_expected['fields']}:
                                raise ApiError('Field typeId has not been verified in the current Tracker Configuration')
                            body = deepcopy(self.field_expected)
                            body['fields'].append(deepcopy(self.profile[k]))
                            body = prepare_configuration_permissions(self.cb,body)
                    return self.request(n,body,p)
                check(name + '-schema', planned_request)
        self.report['requests'].sort(key=lambda r:{'tracker_create':0,'field_create':1,'item_create':2}[r['operation']])
        self.fingerprint = hashlib.sha256(json.dumps(self.report['requests'],sort_keys=True).encode()).hexdigest()
        self.save()
        return self.report

    def execute(self, approved=False):
        if approved is not True:
            raise ApiError('Click Import and explicitly approve the displayed one-issue request')
        if not hasattr(self,'fingerprint') or self.report['blockers']:
            raise ApiError('Read-only preparation must finish without blockers before Import')
        expected = self.fingerprint
        self.prepare()
        if self.report['blockers'] or self.fingerprint != expected:
            raise ApiError('Preflight changed or failed; review the new report before Import')
        self.cb.write = True
        try:
            # These create-only probes never update an existing Tracker/Field/Item.
            for name, payload_key, params in (
                ('tracker_create','tracker_payload',{'projectId':self.project_id}),
                ('field_create','field_payload',{'trackerId':self.tracker_id})):
                if self.profile.get(payload_key) is None:
                    continue
                payload = self.profile[payload_key]
                digest = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                token = 'single:' + name + ':' + digest
                prior = self.state.get(token)
                def create(n=name,p=params,b=payload):
                    if n == 'field_create' and self.operation(n)['path'] == '/v3/tracker/configuration':
                        from mapping_helper import create_confirmed_field
                        actual,_ = create_confirmed_field(self.cb,self.tracker_id,b,self.field_expected,
                            self.state.root / 'single-field-journal.json',approved=True)
                        return {'id':field_identifier(actual)}
                    return self.cb.call(n,p,b)
                created = self.state.once(token,create)
                if type(created.get('id')) is not int:
                    raise ApiError(name + ': actual created ID missing; do not repeat the POST')
                if name == 'tracker_create':
                    actual = self.cb.call('tracker_get', {'trackerId': created['id']})
                    if actual.get('id') != created['id'] or actual.get('project',{}).get('id') != self.project_id:
                        raise ApiError('Created Tracker/project readback mismatch')
                else:
                    actual = self.cb.call('fields', {'trackerId':self.tracker_id})
                    actual = [f for f in actual if field_identifier(f) == created['id']]
                    if len(actual) != 1:
                        raise ApiError('Created Field ID not found on designated test Tracker')
                    for key in ('label','name','typeId','type','mandatory','multipleSelection'):
                        if key in payload and actual[0].get(key) != payload[key]:
                            raise ApiError('Created Field readback differs: ' + key)
                self.report['writes'].append({'operation':name,'id':created['id'],'created_this_run':prior is None,'readback':actual})
                self.save()
            # Only one create identity for this source and target. Unknown POST results stop retry.
            prior = self.state.get('single:item')
            created = self.state.once('single:item', lambda: self.cb.call('item_create', {'trackerId':self.tracker_id}, self.body))
            if type(created.get('id')) is not int:
                raise ApiError('Item create response lacks actual ID; reconcile server before retry')
            actual = self.cb.call('item_get', {'itemId':created['id']})
            if actual.get('id') != created['id'] or actual.get('tracker',{}).get('id') != self.tracker_id:
                raise ApiError('Actual Item ID/test Tracker readback mismatch')
            for key in ('name','description'):
                if actual.get(key) != self.body[key]:
                    raise ApiError('Created Item ' + key + ' readback mismatch')
            self.report['writes'].append({'operation':'item_create','id':created['id'],'created_this_run':prior is None,'readback':actual})
            self.report['complete'] = True
            return self.save()
        except Exception as exc:
            self.report['blockers'].append({'stage':'live-write-or-readback','reason':str(exc) if isinstance(exc,ApiError) else type(exc).__name__})
            self.save()
            raise
        finally:
            self.cb.write = False


def launch_single(test):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root=tk.Tk();root.title('Codebeamer 실제 생성 검증 · Jira Issue 1건')
    root.protocol('WM_DELETE_WINDOW',lambda:(root.quit(),root.destroy()))
    root.geometry('1050x760')
    ttk.Label(root,text='지정한 테스트 범위에만 생성합니다. 기존 Item 수정/삭제 및 전체 이관은 수행하지 않습니다.').pack(pady=8)
    output=tk.Text(root,wrap='word');output.pack(fill='both',expand=True)
    def display():
        output.delete('1.0','end');output.insert('end',json.dumps(test.report,ensure_ascii=False,indent=2))
    def prepare():
        try:test.prepare()
        except Exception as exc:
            test.report['blockers'].append({'stage':'preflight','reason':str(exc) if isinstance(exc,ApiError) else type(exc).__name__})
            test.save()
        display()
    def apply():
        if test.report['blockers'] or not hasattr(test,'fingerprint'):
            messagebox.showerror('Import 차단','읽기 전용 사전 확인의 blockers를 해결하세요.');return
        if not messagebox.askyesno('실제 서버 Import 승인',
            test.issue_key + ' 1건을 테스트 Tracker ' + str(test.tracker_id) + '에 실제 생성합니다.\n표시된 선택적 Tracker/Field POST도 실행합니다. 전체 이관 검증이 아닙니다. 계속하시겠습니까?'):
            return
        try:test.execute(True)
        except Exception as exc:
            messagebox.showerror('실제 Import 차단/실패',str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
        display()
    ttk.Button(root,text='읽기 전용 사전 확인 / 요청 보기',command=prepare).pack(side='left',padx=12,pady=8)
    ttk.Button(root,text='Import…',command=apply).pack(side='right',padx=12,pady=8)
    root.mainloop()


def launch_live_setup(config, state_dir, prompt_auth=False):
    """Select real URLs/project/Tracker in the UI; IDs are never source constants."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    from clients.http import Http
    from clients.jira_client import JiraClient
    from clients.codebeamer_client import CodebeamerClient
    from migration.state import State
    root=tk.Tk();root.title('Codebeamer 3.0.0.7 · 실제 테스트 대상 선택')
    values={}
    for label,key,initial in (
        ('Jira URL','jira',config.get('jira',{}).get('url','')),
        ('Codebeamer URL','cb',config.get('codebeamer',{}).get('url','')),
        ('Swagger JSON URL / file: 경로 (비우면 탐색)','swagger',config.get('codebeamer',{}).get('swagger_url','')),
        ('Jira Issue 키 1개','issue','')):
        ttk.Label(root,text=label).pack(anchor='w',padx=12,pady=(8,0))
        values[key]=tk.StringVar(value=initial)
        ttk.Entry(root,textvariable=values[key],width=85).pack(padx=12)
    projects=ttk.Combobox(root,state='readonly',width=80);projects.pack(padx=12,pady=10)
    trackers=ttk.Combobox(root,state='readonly',width=80);trackers.pack(padx=12,pady=10)
    status=tk.StringVar(value='테스트용 기존 프로젝트와 Tracker를 선택하세요. 아직 쓰기를 수행하지 않습니다.')
    ttk.Label(root,textvariable=status,wraplength=650).pack(padx=12,pady=10)
    selected={}
    def read_operation(cb,name,ids):
        if name not in cb.config.setdefault('operations',{}):
            matches=[{'method':'GET','path':p} for p,methods in cb.spec['paths'].items()
                     for m,o in methods.items() if m.lower()=='get' and isinstance(o,dict) and o.get('operationId') in ids]
            if len(matches)!=1:raise ApiError('Configure '+name+' from the actual Swagger; unique read API not found')
            cb.config['operations'][name]=matches[0]
        return cb.operation(name)
    def connect():
        try:
            current=deepcopy(config)
            current.setdefault('jira',{})['url']=values['jira'].get().strip()
            cb_settings=current.setdefault('codebeamer',{})
            cb_settings.update(url=values['cb'].get().strip(),swagger_url=values['swagger'].get().strip(),profile='3.0.0.7')
            def http(settings):
                Path(state_dir).mkdir(parents=True,exist_ok=True)
                return Http(settings['url'],settings.get('token_env'),log=Path(state_dir)/'requests.jsonl',auth=settings.get('auth','bearer'),
                    username_env=settings.get('username_env'),password_env=settings.get('password_env'),
                    allow_http=settings.get('allow_http',False),prompt_auth=prompt_auth)
            jira=JiraClient(http(current['jira']),current['jira'])
            cb=CodebeamerClient(http(cb_settings),cb_settings,False);cb.discover()
            read_operation(cb,'projects_get',('getProjects',))
            rows=cb.call('projects_get')
            if not isinstance(rows,list):raise ApiError('projects_get must normalize a complete array; configure response_path using actual response')
            selected.update(config=current,jira=jira,cb=cb,projects=rows,connection={k:values[k].get() for k in ('jira','cb','swagger')})
            projects.configure(values=[str(p['id'])+' / '+p.get('name','') for p in rows]);projects.set('');trackers.set('')
            status.set('실제 Swagger와 프로젝트 GET 완료. 테스트 프로젝트를 선택하세요.')
        except Exception as exc:status.set(str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
    def load_trackers(event=None):
        try:
            cb=selected['cb'];pid=selected['projects'][projects.current()]['id']
            read_operation(cb,'trackers_get',('getTrackers','getProjectTrackers'))
            rows=cb.call('trackers_get',{'projectId':pid})
            if not isinstance(rows,list):raise ApiError('trackers_get must normalize a complete project Tracker array')
            selected.update(project_id=pid,trackers=rows)
            trackers.configure(values=[str(t['id'])+' / '+t.get('name','') for t in rows]);trackers.set('')
            status.set('Tracker 목록 GET 완료. 지정 테스트 Tracker를 선택하세요.')
        except Exception as exc:status.set(str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
    projects.bind('<<ComboboxSelected>>',load_trackers)
    def open_test():
        state=None
        try:
            if projects.current()<0 or trackers.current()<0:raise ApiError('Select an existing test project and Tracker')
            if selected.get('connection')!={k:values[k].get() for k in ('jira','cb','swagger')}:
                raise ApiError('Connection values changed; query the projects again')
            state=State(state_dir)
            test=SingleImport(selected['jira'],selected['cb'],state,selected['config'],values['issue'].get().strip(),
                              selected['project_id'],selected['trackers'][trackers.current()]['id'])
            root.withdraw()
            launch_single(test)
            root.deiconify()
        except Exception as exc:messagebox.showerror('실제 테스트 차단',str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
        finally:
            if state:state.db.close()
    ttk.Button(root,text='연결 / 실제 프로젝트 조회 (GET)',command=connect).pack(side='left',padx=12,pady=10)
    ttk.Button(root,text='선택한 Issue 1건의 요청 확인 화면',command=open_test).pack(side='right',padx=12,pady=10)
    root.mainloop()
