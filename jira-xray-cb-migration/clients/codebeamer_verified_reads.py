"""Read-only Codebeamer 3.0.0.7 calls recovered from the successful e1e694b flow.

These calls are independent of Swagger discovery. They accept only the response
shapes that the successful single-import code already required.
"""
from copy import deepcopy
import json
import re
import sqlite3
from pathlib import Path
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier


READ_PATHS={
    'projects_get':('/v3/projects','/projects'),
    'project_get':('/v3/projects/{projectId}','/projects/{projectId}'),
    'trackers_get':('/v3/projects/{projectId}/trackers','/projects/{projectId}/trackers'),
    'tracker_get':('/v3/trackers/{trackerId}','/trackers/{trackerId}'),
    'fields':('/v3/trackers/{trackerId}/fields','/trackers/{trackerId}/fields'),
    'tracker_configuration_get':('/v3/tracker/{trackerId}/configuration',),
}


def _path(template,params):
    try:return re.sub(r'\{([^}]+)\}',lambda match:segment(params[match[1]]),template)
    except KeyError as exc:raise ApiError('Missing read parameter: '+str(exc.args[0])) from None


def _read(cb,name,params,valid):
    cached=cb.config.setdefault('verified_read_operations',{}).get(name)
    paths=([cached] if cached else [])+[p for p in READ_PATHS[name] if p!=cached]
    failures=[]
    for template in paths:
        path=_path(template,params)
        try:value=cb.http.request('GET',path)
        except ApiError as exc:failures.append(path+': '+str(exc));continue
        if not valid(value):failures.append(path+': response contract mismatch');continue
        cb.config['verified_read_operations'][name]=template
        cb.config['verified_reads']=True
        return value
    raise ApiError(name+' GET failed: '+'; '.join(failures))


def _rows(value):
    return isinstance(value,list) and all(isinstance(row,dict) and type(row.get('id')) is int for row in value)


def projects(cb):return _read(cb,'projects_get',{},_rows)


def project(cb,project_id):
    return _read(cb,'project_get',{'projectId':project_id},
                 lambda value:isinstance(value,dict) and value.get('id')==project_id)


def trackers(cb,project_id):
    return _read(cb,'trackers_get',{'projectId':project_id},_rows)


def tracker(cb,tracker_id,project_id=None):
    def valid(value):
        if not isinstance(value,dict) or value.get('id')!=tracker_id:return False
        return project_id is None or value.get('project',{}).get('id')==project_id
    return _read(cb,'tracker_get',{'trackerId':tracker_id},valid)


def fields(cb,tracker_id):
    return _read(cb,'fields',{'trackerId':tracker_id},lambda value:isinstance(value,list) and
                 all(isinstance(field,dict) and type(field_identifier(field)) is int for field in value))


def configuration(cb,tracker_id):
    return _read(cb,'tracker_configuration_get',{'trackerId':tracker_id},lambda value:isinstance(value,dict)
                 and isinstance(value.get('fields'),list))


def load_successful_openapi(state_root,jira_url,cb_url):
    """Reuse server evidence archived by SingleImport.prepare, never a different environment."""
    root=Path(state_root).parent
    candidates=[]
    for database in root.glob('**/state.sqlite3'):
        try:
            connection=sqlite3.connect('file:'+database.as_posix()+'?mode=ro',uri=True)
            row=connection.execute('SELECT status,result FROM actions WHERE key=?',('single-import-binding',)).fetchone()
            connection.close()
            if not row or row[0]!='done':continue
            identity=json.loads(row[1])
            if identity.get('jira','').rstrip('/')!=jira_url.rstrip('/') or identity.get('codebeamer','').rstrip('/')!=cb_url.rstrip('/'):
                continue
            for source in database.parent.joinpath('discovery').glob('*.json'):
                document=json.loads(source.read_text(encoding='utf-8'))
                if isinstance(document,dict) and isinstance(document.get('paths'),dict):
                    candidates.append((source.stat().st_mtime,source,document))
        except (OSError,sqlite3.Error,ValueError,KeyError):
            continue
    if not candidates:return None
    _,source,document=max(candidates,key=lambda item:item[0])
    return deepcopy(document),str(source)
