"""GET-only field creation diagnostics. Never writes CB or migration state."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import zipfile
from datetime import datetime
from copy import deepcopy
from clients.http import Http, ApiError
from clients.codebeamer_client import CodebeamerClient
from mapping_helper import field_identifier, schema_object, request_schema
from single_import import validate


class ReadOnlyHttp(Http):
    def request(self, method, *args, **kwargs):
        if method.upper() != 'GET':
            raise ApiError('Diagnostic permits GET only; write blocked')
        return super().request(method, *args, **kwargs)


def read_journal(path):
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as archive:
            names=[n for n in archive.namelist() if n.lower().endswith('.json')]
            if len(names)!=1 or archive.getinfo(names[0]).file_size>16*1024*1024:
                raise ValueError('ZIP must contain exactly one JSON journal up to 16 MiB')
            raw=archive.read(names[0])
    else:
        if path.stat().st_size>16*1024*1024:raise ValueError('Journal too large')
        raw=path.read_bytes()
    document=json.loads(raw.decode('utf-8-sig'))
    if not isinstance(document,dict):raise ValueError('Journal must be an object')
    return document,hashlib.sha256(raw).hexdigest()


def permission_shape(value):
    if not isinstance(value,dict):return {'shape':type(value).__name__}
    result={'type':value.get('type'),'keys':sorted(value)}
    mapping=value.get('accessPermissionsPerStatusMap')
    if isinstance(mapping,dict):
        result['status_entry_counts']={k:len(v) if isinstance(v,list) else type(v).__name__ for k,v in mapping.items()}
    if isinstance(value.get('accessPermissions'),list):result['entry_count']=len(value['accessPermissions'])
    return result


def collect(cb, tracker, journal, fingerprint):
    report={'tracker_id':tracker,'journal_sha256':fingerprint,'writes':0,'checks':{},'errors':[],
            'note':'GET-only inspection. Past POST response bodies cannot be recovered from summarized errors.'}
    current=None; fields=[]
    for name,path in [('configuration',f'/v3/tracker/{tracker}/configuration'),('field_list',f'/v3/trackers/{tracker}/fields')]:
        try:
            response=cb.http.request('GET',path)
            if name=='configuration':
                if not isinstance(response,dict) or not isinstance(response.get('fields'),list):raise ApiError('Configuration fields missing')
                current=response;fields=response['fields']
            report['checks'][name]={'status':'read','response_type':type(response).__name__}
        except (ApiError,ValueError) as exc:report['errors'].append({'stage':name,'type':type(exc).__name__,'message':str(exc)})
    try:
        cb.discover()
        report['checks']['swagger']={'status':'read','version':cb.spec.get('info',{}).get('version')}
    except (ApiError,ValueError) as exc:
        report['errors'].append({'stage':'swagger','type':type(exc).__name__,'message':str(exc)})
    reports=[]
    for record in journal.values():
        if not isinstance(record,dict) or record.get('tracker')!=tracker:continue
        body=record.get('request',{}).get('body',{})
        old=body.get('fields',[])
        if not old:continue
        new=old[-1]
        matches=[f for f in fields if isinstance(f,dict) and f.get('label',f.get('name'))==new.get('label')]
        row={'journal_status':record.get('status'),'new_label':new.get('label'), 'type_id':new.get('typeId'),
             'permission':permission_shape(new.get('permission')),'existing_field_ids':[field_identifier(f) for f in matches],
             'post_response_recorded':'post_response' in record,'permission_changes':[], 'schema_errors':[]}
        row['field_presence'] = ('unknown' if current is None else 'present' if matches else 'absent')
        row['server_write_verified'] = False
        # Only export the bounded allowlisted evidence produced by Http; do not
        # export arbitrary response bodies or legacy error messages.
        evidence=record.get('http_error_evidence')
        if isinstance(evidence,dict):
            from clients.http import http_error_evidence
            allowed=http_error_evidence(b'{}','application/json',None)
            row['http_error_evidence']={key:evidence[key] for key in allowed if key in evidence}
        by_id={str(field_identifier(f)):f for f in fields if isinstance(f,dict)}
        for f in old[:-1]:
            fid=str(field_identifier(f));actual=by_id.get(fid)
            if actual is None or actual.get('permission')!=f.get('permission'):
                row['permission_changes'].append({'field_id':fid,'current_field_found':actual is not None,
                    'requested':permission_shape(f.get('permission')),
                    'current':permission_shape(actual.get('permission')) if actual else None})
        for field in matches:
            fid=field_identifier(field)
            if type(fid) is int:
                try:
                    detail=cb.http.request('GET',f'/v3/trackers/{tracker}/fields/{fid}')
                    row.setdefault('field_details',[]).append({k:detail.get(k) for k in ('id','type','valueModel','referenceType','multipleValues')})
                except ApiError as exc:row['schema_errors'].append(str(exc))
        if isinstance(cb.spec,dict):
            api=cb.spec.get('paths',{}).get('/v3/tracker/configuration',{}).get('post')
            if not api:row['schema_errors'].append('POST /v3/tracker/configuration schema absent')
            else:
                try:validate(cb.spec,body,request_schema(cb.spec,api))
                except (ApiError,ValueError,KeyError) as exc:row['schema_errors'].append(str(exc))
            models=cb.spec.get('components',{}).get('schemas',cb.spec.get('definitions',{}))
            base=models.get('BaseTrackerFieldPermission',{})
            mapping=base.get('discriminator',{}).get('mapping',{})
            ref=mapping.get((new.get('permission') or {}).get('type'))
            if ref:
                try:
                    selected=schema_object(cb.spec,{'$ref':ref})
                    row['permission_schema']=selected
                    validate(cb.spec,new['permission'],selected,'new_field.permission')
                except (ApiError,ValueError,KeyError) as exc:row['schema_errors'].append(str(exc))
            else:row['schema_errors'].append('Permission subtype discriminator contract unavailable')
        reports.append(row)
    report['records']=reports
    if not reports:report['errors'].append({'stage':'journal','message':'No field creation record for selected Tracker'})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.local.json')
    parser.add_argument('--journal')
    parser.add_argument('--tracker',type=int)
    parser.add_argument('--output',default='diagnostics')
    parser.add_argument('--swagger',help='Exact Swagger URL or file: JSON path')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    settings=deepcopy(config['codebeamer']);settings.pop('profile',None) # Do not bind unrelated write operations.
    if args.swagger:settings['swagger_url']=args.swagger
    journal_path=Path(args.journal or input('Failed field journal JSON/ZIP path: ').strip().strip('"'))
    journal,fingerprint=read_journal(journal_path)
    trackers=sorted({r['tracker'] for r in journal.values() if isinstance(r,dict) and type(r.get('tracker')) is int})
    tracker=args.tracker or (trackers[0] if len(trackers)==1 else int(input('Tracker ID: ')))
    if tracker<=0:raise ValueError('Positive Tracker ID required')
    url=settings.get('url') or input('Codebeamer API base URL: ').strip()
    http=ReadOnlyHttp(url,settings.get('token_env'),auth=settings.get('auth','basic'),
        username_env=settings.get('username_env'),password_env=settings.get('password_env'),
        allow_http=settings.get('allow_http',False),prompt_auth=True,timeout=15)
    http.read_deadline=time.monotonic()+60
    report=collect(CodebeamerClient(http,settings,write=False),tracker,journal,fingerprint)
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    destination=output/('field-diagnostic-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.zip')
    # No settings file, auth header, full Configuration or original request is included.
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.json',json.dumps(report,ensure_ascii=False,indent=2))
    print('GET-only diagnostic saved:',destination.resolve())
    print('Review report.json before sharing. No POST or state reset was performed.')

if __name__=='__main__':
    try:main()
    except (ApiError,ValueError,OSError,KeyError) as exc:
        print('Diagnostic failed:',str(exc));raise SystemExit(1)
