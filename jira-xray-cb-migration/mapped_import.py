"""Bounded mapped-field and binary verification, sharing the migration serializers."""
import hashlib
import re
from copy import deepcopy
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import request_schema, schema_object, field_identifier
from migration.fields import payload
from migration.attachments import transfer
from migration.preservation import rewrite_description
from migration.wizard import hidden, semantic_type


def bind_attachment_api(cb):
    """Only use routes and multipart members present in this server's Swagger."""
    from clients.codebeamer_3007 import prefer_v3_aliases
    routes = {
        'attachment_upload': ('post', r'(?:/v3)?/items/\{itemId\}/attachments'),
        'attachments_get': ('get', r'(?:/v3)?/items/\{itemId\}/attachments'),
        'attachment_get': ('get', r'(?:/v3)?/items/\{itemId\}/attachments/\{attachmentId\}/content'),
    }
    for name, (method, pattern) in routes.items():
        if name not in cb.config.setdefault('operations', {}):
            paths = prefer_v3_aliases([p for p,m in cb.spec['paths'].items()
                                      if re.fullmatch(pattern,p) and method in m])
            if len(paths) != 1:
                raise ApiError(name+': 서버 Swagger에서 API를 하나로 확인하지 못했습니다. 확인된 operation 설정이 필요합니다.')
            cb.config['operations'][name] = {'method':method.upper(),'path':paths[0]}
        op = cb.operation(name)
        if op['method'].lower() != method or '{itemId}' not in op['path'] or op.get('path_parameters') or op.get('body'):
            raise ApiError(name+': Item에 한정된 기본 API가 필요합니다.')
        expected = {'itemId','attachmentId'} if name == 'attachment_get' else {'itemId'}
        if set(re.findall(r'\{([^}]+)\}',op['path'])) != expected:
            raise ApiError(name+': 지원하지 않는 path parameter')
        definition = cb.spec['paths'][op['path']][method]
        parameters = cb.spec['paths'][op['path']].get('parameters',[]) + definition.get('parameters',[])
        for parameter in parameters:
            parameter = schema_object(cb.spec,parameter)
            if parameter.get('required') and parameter.get('in') not in ('path','formData'):
                if parameter.get('in') != 'query' or parameter.get('name') not in op.get('query',{}):
                    raise ApiError(name+': 추가 필수 parameter 설정이 필요합니다: '+parameter.get('name','?'))
    op = cb.operation('attachment_upload')
    definition = cb.spec['paths'][op['path']]['post']
    params = cb.spec['paths'][op['path']].get('parameters',[]) + definition.get('parameters',[])
    members = []
    for p in params:
        if p.get('in') == 'formData' and (p.get('type') == 'file' or p.get('items',{}).get('type') == 'file'):
            members.append(p['name'])
        elif p.get('required') and p.get('in') != 'path':
            raise ApiError('attachment_upload: 추가 필수 parameter: '+p.get('name','?'))
    body = schema_object(cb.spec,definition.get('requestBody',{}))
    content = body.get('content',{}).get('multipart/form-data')
    if content:
        schema = schema_object(cb.spec,content.get('schema',{}))
        for name, value in schema.get('properties',{}).items():
            value = schema_object(cb.spec,value)
            item = schema_object(cb.spec,value.get('items',{})) if value.get('type') == 'array' else value
            if item.get('type') == 'string' and item.get('format') == 'binary':
                members.append(name)
            elif name in schema.get('required',[]):
                raise ApiError('attachment_upload: 추가 필수 multipart member: '+name)
    if len(members) != 1:
        raise ApiError('attachment_upload: Swagger에서 단일 바이너리 multipart 필드를 확인해야 합니다.')
    cb.config['operations']['attachment_upload']['part_name'] = members[0]
    return {n:deepcopy(cb.operation(n)) for n in routes}


def targets(test, fields):
    op = test.operation('item_create')
    properties = request_schema(test.cb.spec,test.cb.spec['paths'][op['path']]['post']).get('properties',{})
    normalize = lambda s: re.sub(r'[^a-z0-9]','',s.lower())
    result = {}
    bindings = test.cb.config.get('field_bindings',{})
    for field in fields:
        if hidden(field): continue
        fid = field_identifier(field)
        label = field.get('label',field.get('name',str(fid)))
        keys = [k for k,b in bindings.items() if b.get('fieldId') == fid]
        if not keys:
            keys = [k for k in properties if normalize(k) == normalize(label) and k not in {'id','tracker','project','parent','children','attachments','comments','associations','customFields'}]
        if len(keys) == 1:
            result[keys[0]] = {'id':fid,'label':label,'binding_ready':True}
            continue
        kind, multiple = semantic_type(field)
        profile = test.config.get('migration',{}).get('wizard',{}).get('field_types',{}).get(kind,{}) if kind else {}
        binding = profile.get('binding')
        key = 'cb_field_' + str(fid)
        if fid is not None and isinstance(binding,dict) and isinstance(binding.get('type'),str) and binding.get('value_property'):
            actual = deepcopy(binding);actual['fieldId']=fid
            test.cb.config.setdefault('field_bindings',{})[key]=actual
            result[key] = {'id':fid,'label':label,'binding_ready':True,'semantic_type':kind,
                           'multipleSelection':multiple,'binding_source':'migration.wizard.field_types.'+kind}
        else:
            # Show the real Tracker field even when it cannot safely be written yet.
            result['unbound_' + str(fid)] = {'id':fid,'label':label,'binding_ready':False,
                'semantic_type':kind,'multipleSelection':multiple,
                'reason':'Codebeamer FieldValue binding이 없습니다. config의 migration.wizard.field_types.'+str(kind)+'.binding 또는 codebeamer.field_bindings가 필요합니다.'}
    return result


def prepare(test, body, fields):
    test.report['scope'] = 'single-issue-mapped-fields-and-attachments'
    saved_inline = test.state.get('single:inline-description')
    if saved_inline and saved_inline[0] == 'done':
        test.report['inline_description'] = saved_inline[1]
    test.report['ancillary_features'] = {'status':'not-tested', 'reason':'Comment/Association/History/R4J/Xray 전체 이관은 이 제한 테스트의 검증 범위가 아닙니다.'}
    available = targets(test,fields)
    mapping = test.profile.get('field_mapping',{})
    jira_config = getattr(test.jira,'config',{})
    visibility = jira_config.get('field_configuration')
    hidden_sources = set()
    if visibility:
        issue_fields = test.issue['fields']
        configuration = test.jira.configured_records(visibility,{
            'projectKey':issue_fields['project']['key'], 'issueTypeId':str(issue_fields['issuetype']['id'])})
        hidden_sources = {str(f['id']) for f in configuration if hidden(f)}
    test.report['source_visibility'] = 'field-configuration' if visibility else 'unknown: Jira Field Configuration adapter is not configured'
    test.report['hidden_sources'] = sorted(hidden_sources)
    test.report['hidden_source_value_risks'] = [k for k in hidden_sources if test.issue['fields'].get(k) not in (None,'',[],{})]
    if mapping.get('summary',{}).get('target') != 'name' or mapping.get('description',{}).get('target') != 'description':
        raise ApiError('Summary → Name, Description → Description 매핑이 필요합니다.')
    used = set()
    for source,spec in mapping.items():
        target = spec.get('target')
        if source in hidden_sources or source in {'attachment','comment','issuelinks','worklog'} or target not in available:
            raise ApiError('매핑 대상 필드가 없거나 Hidden/미지원입니다: '+source+' → '+str(target))
        if not available[target].get('binding_ready'):
            raise ApiError(available[target]['label']+': '+available[target]['reason'])
        if available[target]['id'] in used:
            raise ApiError('동일 CB 필드에 중복 매핑할 수 없습니다.')
        used.add(available[target]['id'])
        if spec.get('type') not in ('text','number','boolean','date','datetime'):
            raise ApiError('이 제한 테스트에는 명시적인 기본 값 타입 매핑이 필요합니다: '+source)
    mapped = payload(test.issue['fields'],mapping,test.config.get('migration',{}))
    body.update(mapped)
    files = test.issue['fields'].get('attachment',[])
    if not isinstance(files,list): raise ApiError('Jira attachment 목록 형식 오류')
    inventory = []
    ids = set()
    for file in files:
        if not isinstance(file,dict) or not all(k in file for k in ('id','filename','size','content')):
            raise ApiError('Jira 첨부 메타데이터에 id/filename/size/content가 필요합니다.')
        if not isinstance(file['filename'],str) or '\r' in file['filename'] or '\n' in file['filename'] or type(file['size']) is not int or file['size']<0 or str(file['id']) in ids:
            raise ApiError('Jira 첨부 메타데이터가 유효하지 않거나 ID가 중복됩니다.')
        ids.add(str(file['id']))
        data = test.jira.http.request('GET',file['content'],raw=True)
        if len(data) != file['size']: raise ApiError('원본 첨부 크기 불일치: '+file['filename'])
        inventory.append(dict(file,sha256=hashlib.sha256(data).hexdigest()))
    test.attachment_inventory = inventory
    test.report['attachment_inventory'] = inventory
    test.report['field_mapping'] = deepcopy(mapping)
    test.report['unmapped_source_fields'] = [k for k in test.issue['fields'] if k not in mapping and k != 'attachment']
    test.report['mapping_targets'] = available
    source = test.issue['fields'].get('description')
    rewritten = rewrite_description(source,files,{str(f['id']):'https://attachment.invalid/'+str(f['id']) for f in files})
    if files:
        test.report['attachment_api'] = bind_attachment_api(test.cb)
        # Validate the image inventory before any write; actual URLs arrive after upload.
        if rewritten != source:
            op = test.cb.operation('item_update')
            if op['method'].upper() != 'PUT' or not re.fullmatch(r'(?:/v3)?/items/\{itemId\}',op['path']) or op.get('body') or op.get('path_parameters'):
                raise ApiError('Inline 이미지: 검증된 Item PUT API가 필요합니다.')
            from single_import import validate
            validate(test.cb.spec,{'description':rewritten},request_schema(test.cb.spec,test.cb.spec['paths'][op['path']]['put']))
    test.state.save('mapped-test','field-mapping',mapping)
    return body


class UploadAdapter:
    """The shared transfer uploads one file; accept an exact one-element API array."""
    def __init__(self,cb,state,receipt_key,file):
        self.cb,self.state,self.receipt_key,self.file=cb,state,receipt_key,file
    def operation(self,name): return self.cb.operation(name)
    def call(self,name,*args,**kwargs):
        value = self.cb.call(name,*args,**kwargs)
        if name == 'attachment_upload' and isinstance(value,list):
            if len(value) != 1: raise ApiError('단일 파일 업로드 응답의 ID 개수가 1이 아닙니다. 재업로드 전에 확인하세요.')
            value = value[0]
        if name == 'attachment_upload' and isinstance(value,dict) and type(value.get('id')) is int:
            # Preserve the confirmed POST ID before a download can fail. Retry only reads it.
            self.state.mark(self.receipt_key,'done',dict(id=value['id'],source_id=str(self.file['id']),
                filename=self.file['filename'],size=self.file['size'],sha256=self.file['sha256']))
        return value


def finish(test,item_id):
    verified=[]; urls={}
    for file in test.attachment_inventory:
        # Cache immutable source bytes for this attempt, compare against approved inventory.
        data = test.jira.http.request('GET',file['content'],raw=True)
        if len(data)!=file['size'] or hashlib.sha256(data).hexdigest()!=file['sha256']:
            raise ApiError('승인 이후 원본 파일이 변경되었습니다: '+file['filename'])
        class Source:
            def request(self,*args,**kwargs): return data
        identity = 'attachment:'+test.issue_key+':'+str(file['id'])
        receipt_key = 'single:upload-receipt:'+str(file['id'])
        prior,receipt = test.state.get(identity),test.state.get(receipt_key)
        if prior and prior[0] == 'pending' and receipt and receipt[0] == 'done':
            result = receipt[1]
            if any(result[k] != file[k] for k in ('filename','size','sha256')):
                raise ApiError('기존 업로드 원본과 현재 파일이 다릅니다. 서버 확인이 필요합니다.')
        else:
            result = transfer(Source(),UploadAdapter(test.cb,test.state,receipt_key,file),test.state,test.issue_key,item_id,file)
        binary = test.cb.call('attachment_get',{'itemId':item_id,'attachmentId':result['id']},raw=True)
        if len(binary)!=file['size'] or hashlib.sha256(binary).hexdigest()!=file['sha256']:
            raise ApiError('CB 재다운로드 파일 크기/해시 불일치: '+file['filename'])
        rows = test.cb.call('attachments_get',{'itemId':item_id})
        if not isinstance(rows,list): raise ApiError('CB 첨부 목록은 전체 배열이어야 합니다.')
        found = [r for r in rows if r.get('id') == result['id']]
        if len(found)!=1 or found[0].get('name',found[0].get('filename'))!=file['filename'] or found[0].get('size')!=file['size']:
            raise ApiError('CB 첨부 ID/파일명/크기 메타데이터 불일치: '+file['filename'])
        test.state.mark(identity,'done',result)
        op = test.cb.operation('attachment_get')
        path = op['path'].replace('{itemId}',segment(item_id)).replace('{attachmentId}',segment(result['id']))
        if op.get('query'):
            from urllib.parse import urlencode
            from clients.codebeamer_client import render
            context={'itemId':item_id,'attachmentId':result['id'],'payload':None}
            path += '?' + urlencode({k:render(v,context) for k,v in op['query'].items()},doseq=True)
        urls[str(file['id'])] = test.cb.http.url(path)
        verified.append(dict(result,verified=True))
        test.report['attachments'] = verified
        test.save()
    source = test.issue['fields'].get('description')
    rewritten = rewrite_description(source,test.attachment_inventory,urls)
    if rewritten != source:
        body = {'description':rewritten}
        digest = hashlib.sha256(rewritten.encode()).hexdigest()
        test.state.once('single:inline:'+digest,lambda:test.cb.call('item_update',{'itemId':item_id},body))
        actual = test.cb.call('item_get',{'itemId':item_id})
        if actual.get('description')!=rewritten: raise ApiError('Inline 이미지 링크 재조회 불일치')
        test.report['inline_description'] = {'verified':True,'description':rewritten}
        test.state.mark('single:inline-description','done',test.report['inline_description'])
    test.report['attachment_counts'] = {'source':len(test.attachment_inventory),'verified':len(verified)}


def verify_fields(test,actual):
    for key,value in test.body.items():
        if key == 'description' and test.report.get('inline_description',{}).get('verified'):
            value = test.report['inline_description']['description']
        if key == 'customFields':
            found = {f['fieldId']:f for f in actual.get(key,[])}
            if any(any(found.get(f['fieldId'],{}).get(k)!=v for k,v in f.items()) for f in value):
                raise ApiError('매핑된 Custom Field 재조회 값 불일치')
        elif actual.get(key)!=value:
            raise ApiError('매핑된 필드 재조회 값 불일치: '+key)
    test.report['mapped_fields_verified'] = True


def launch(previous, parent):
    """A separate state scope never attaches files to the earlier creation-check Item."""
    import json
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox
    from single_import import SingleImport
    from migration.state import State
    config = deepcopy(previous.config)
    profile = config.setdefault('single_import',{})
    profile.pop('tracker_payload',None); profile.pop('field_payload',None)
    profile.pop('item_payload',None)
    profile.update(mapped_attachments=True)
    profile.setdefault('field_mapping',{'summary':{'target':'name','type':'text'},'description':{'target':'description','type':'text'}})
    directory = previous.state.root / 'mapped-attachments'
    state = State(directory)
    saved_mapping = state.load('mapped-test','field-mapping')
    if saved_mapping: profile['field_mapping'] = saved_mapping
    test = SingleImport(previous.jira,previous.cb,state,config,previous.issue_key,previous.project_id,previous.tracker_id)
    state.db.close()
    window = tk.Toplevel(parent); window.title('필드 매핑 · 첨부파일 1건 실제 테스트'); window.geometry('1100x800')
    ttk.Label(window,text='기존 생성 확인 Item과 별도로 새 테스트 Item 1건을 만듭니다. 같은 상태 폴더에서는 재실행 시 해당 Item을 검증합니다.').pack(anchor='w',padx=10,pady=8)
    ttk.Label(window,text='범위: 선택한 기본 타입 필드 + 첨부파일. Comment/History/Association/R4J/Xray 전체 이관 검증은 포함하지 않습니다.').pack(anchor='w',padx=10)
    status = tk.StringVar(value='먼저 사전 조회를 실행하세요.')
    ttk.Entry(window,textvariable=status,state='readonly').pack(fill='x',padx=10,pady=6)
    mapping = ttk.Treeview(window,columns=('cb','jira','type'),show='headings',height=6)
    for key,label in (('cb','Codebeamer 필드'),('jira','Jira 필드'),('type','값 타입')): mapping.heading(key,text=label)
    mapping.pack(fill='x',padx=10)
    controls = ttk.Frame(window); controls.pack(fill='x',padx=10,pady=6)
    cb_box=ttk.Combobox(controls,state='readonly',width=30);cb_box.pack(side='left')
    jira_box=ttk.Combobox(controls,state='readonly',width=30);jira_box.pack(side='left')
    kind=ttk.Combobox(controls,state='readonly',values=['text','number','boolean','date','datetime'],width=12);kind.set('text');kind.pack(side='left')
    output = tk.Text(window,wrap='word'); output.pack(fill='both',expand=True,padx=10)
    active=[False]; target_keys=[]; source_keys=[]; buttons=[]
    def display():
        output.delete('1.0','end');output.insert('end',json.dumps(test.report,ensure_ascii=False,indent=2))
        mapping.delete(*mapping.get_children())
        for source,spec in test.profile['field_mapping'].items():
            mapping.insert('', 'end',iid=source,values=(spec['target'],source,spec['type']))
        if hasattr(test,'issue'):
            source_keys[:] = [k for k in test.issue['fields'] if k not in {'attachment','comment','issuelinks','worklog','project','issuetype'} and k not in test.report.get('hidden_sources',[])]
            jira_box.configure(values=source_keys)
        try:
            available=targets(test,test.report.get('readback',{}).get('fields') or [])
            target_keys[:]=list(available)
            cb_box.configure(values=[available[k]['label']+' / '+k+(' / 설정 필요' if not available[k].get('binding_ready') else '') for k in target_keys])
        except (ApiError,KeyError,TypeError): pass
    def invalidate():
        test.report['complete']=False
        if hasattr(test,'fingerprint'):del test.fingerprint
        status.set('매핑 변경됨. 사전 조회를 다시 실행하세요.');display()
    def assign():
        if active[0] or cb_box.current()<0 or jira_box.current()<0:return
        source=source_keys[jira_box.current()];target=target_keys[cb_box.current()]
        if any(s!=source and m['target']==target for s,m in test.profile['field_mapping'].items()):
            messagebox.showerror('중복 매핑','이미 연결된 CB 필드입니다.',parent=window);return
        test.profile['field_mapping'][source]={'target':target,'type':kind.get()};invalidate()
    def remove():
        if not active[0]:
            for source in mapping.selection():test.profile['field_mapping'].pop(source,None)
            invalidate()
    def task(action):
        if active[0]:return
        active[0]=True
        for button in buttons:button.configure(state='disabled')
        status.set('실제 API 처리 중… 결과는 아래에 표시됩니다.')
        results=queue.Queue()
        def worker():
            test.state=State(directory)
            error=None
            try:
                # Clear expired read-discovery budgets; per-request timeout remains bounded.
                for http in (test.cb.http,test.jira.http):
                    http.read_deadline=None;http.cancel_event=None;http.timeout=15
                action()
            except Exception as exc:
                error=str(exc) if isinstance(exc,ApiError) else type(exc).__name__
            finally:test.state.db.close()
            results.put(error)
        threading.Thread(target=worker,daemon=True).start()
        def poll():
            try:error=results.get_nowait()
            except queue.Empty:window.after(100,poll);return
            active[0]=False
            for button in buttons:button.configure(state='normal')
            display()
            status.set(error or ('검증 완료' if test.report['complete'] else '사전 조회 완료 — blockers와 매핑·첨부 목록을 확인하세요.'))
        window.after(100,poll)
    def apply():
        if test.report['blockers'] or not hasattr(test,'fingerprint'):
            messagebox.showerror('Import 차단','매핑 후 사전 조회를 완료하고 blockers를 해결하세요.',parent=window);return
        if messagebox.askyesno('실제 Import 승인',test.issue_key+' → 테스트 Tracker '+str(test.tracker_id)+'\n새 Item 1건과 원본 첨부파일을 생성하고 검증합니다.\n동일 상태에서 재시도하면 기존 테스트 Item을 사용합니다. 진행할까요?',parent=window):
            task(lambda:test.execute(True))
    footer=ttk.Frame(window);footer.pack(fill='x',padx=10,pady=8)
    for frame,label,command in ((controls,'매핑 저장',assign),(controls,'선택 매핑 해제',remove),(footer,'사전 조회 / 요청 확인',lambda:task(test.prepare)),(footer,'Import…',apply)):
        button=ttk.Button(frame,text=label,command=command);button.pack(side='left',padx=4);buttons.append(button)
    def copy():window.clipboard_clear();window.clipboard_append(status.get()+'\n'+output.get('1.0','end'))
    ttk.Button(footer,text='결과 / 오류 복사',command=copy).pack(side='left',padx=4)
    def close():
        if active[0]:messagebox.showinfo('처리 중','API 처리 결과가 나온 뒤 닫아 주세요.',parent=window)
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close)
    display();parent.wait_window(window)
