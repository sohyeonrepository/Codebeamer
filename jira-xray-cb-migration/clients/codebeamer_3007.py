"""Bind user-supplied Codebeamer 3.0 API routes only when present in server Swagger."""
from clients.http import ApiError
import re


def prefer_v3_aliases(paths):
    """Collapse only exact version-prefix aliases, keeping unrelated routes distinct."""
    available = set(paths)
    return [path for path in paths if path.startswith('/v3/') or '/v3'+path not in available]


def project_reads(cb):
    """Bind the complete project list and one-project GET from actual Swagger routes."""
    operations=cb.config.setdefault('operations',{})
    shapes={
        'projects_get':r'(?:/v3)?/projects/?',
        'project_get':r'(?:/v3)?/projects/\{(?:projectId|id)\}/?'}
    selected={}
    for name,shape in shapes.items():
        configured=operations.get(name)
        if configured:
            try:
                current=cb.operation(name)
                if current['method'].upper()=='GET' and re.fullmatch(shape,current['path']):
                    selected[name]=current;continue
            except ApiError:
                pass
        paths=[p for p,methods in cb.spec['paths'].items()
               if re.fullmatch(shape,p) and isinstance(methods.get('get'),dict)]
        v3=[p for p in paths if p.startswith('/v3/')]
        paths=prefer_v3_aliases(paths)
        if v3:paths=v3
        if len(paths)!=1:
            raise ApiError(name+': 프로젝트 GET 경로를 Swagger에서 하나로 확인할 수 없습니다: '+', '.join(paths))
        operation={'method':'GET','path':paths[0]}
        if configured:
            for key in ('response_path','response_fields'):
                if key in configured:operation[key]=configured[key]
        operations[name]=operation
        selected[name]=cb.operation(name)
    return selected


def project_tracker_list(cb):
    """Choose a complete project list by route shape, not operationId alone."""
    operations = cb.config.setdefault('operations',{})
    configured = operations.get('trackers_get')
    def full_list(path):
        return bool(re.search(r'/projects/\{(?:projectId|id)\}/trackers/?$',path)) and set(re.findall(r'\{([^}]+)\}',path)) <= {'projectId','id'}
    if configured:
        current = cb.operation('trackers_get')
        if current['method'].upper() == 'GET' and full_list(current['path']):
            return current
    candidates = [{'method':'GET','path':path} for path,methods in cb.spec['paths'].items()
                  if full_list(path) and isinstance(methods.get('get'),dict)]
    preferred = prefer_v3_aliases([c['path'] for c in candidates])
    candidates = [c for c in candidates if c['path'] in preferred]
    if not candidates:
        typed = [p for p,m in cb.spec['paths'].items() if re.fullmatch(r'(?:/v3)?/projects/\{(?:projectId|id)\}/trackers/types/\{typeId\}/?',p) and isinstance(m.get('get'),dict)]
        types = [p for p,m in cb.spec['paths'].items() if re.fullmatch(r'(?:/v3)?/trackers/types/?',p) and isinstance(m.get('get'),dict)]
        typed, types = prefer_v3_aliases(typed), prefer_v3_aliases(types)
        if len(typed) == len(types) == 1:
            operations['tracker_types_get'] = {'method':'GET','path':types[0]}
            operations['trackers_get'] = {'method':'GET','path':typed[0], 'aggregate_types':True}
            return cb.operation('trackers_get')
        available = ', '.join(p for p,m in cb.spec['paths'].items() if 'tracker' in p.lower() and 'get' in m)
        raise ApiError('Tracker 목록 조회에 필요한 전체 목록 GET 또는 Tracker Type 목록 GET + 유형별 GET을 Swagger에서 확인하지 못했습니다. typeId를 임의로 지정하지 않습니다. 발견된 Tracker GET 경로: '+available)
    if len(candidates) != 1:
        raise ApiError('Tracker 전체 목록 GET 경로가 여러 개입니다: '+', '.join(c['path'] for c in candidates))
    selected = candidates[0]
    if configured:
        for key in ('response_path','response_fields'):
            if key in configured:selected[key] = configured[key]
    operations['trackers_get'] = selected
    return cb.operation('trackers_get')


def typed_project_trackers(cb, params):
    """Read real type IDs and merge complete lists; never return partial success."""
    def rows(value, label):
        if not isinstance(value,list) or any(not isinstance(r,dict) or type(r.get('id')) is not int for r in value):
            raise ApiError(label+' 응답은 실제 정수 id가 있는 전체 배열이어야 합니다. 응답 형식 또는 페이지 처리를 확인하세요.')
        return value
    types = rows(cb.call('tracker_types_get'), 'Tracker Type GET')
    merged = {}
    for type_id in dict.fromkeys(t['id'] for t in types):
        try:
            trackers = rows(cb.call('trackers_get',dict(params,typeId=type_id)), '유형별 Tracker GET')
        except ApiError as exc:
            raise ApiError('Tracker typeId='+str(type_id)+' 조회 실패; 부분 목록은 표시하지 않습니다: '+str(exc)) from exc
        for tracker in trackers:
            merged.setdefault(tracker['id'],tracker)
    return list(merged.values())

# Product documentation supplied for this integration. Request shapes still come
# from the connected server; presence here is not a claim of live verification.
ROUTES = {
    'tracker_get': ('GET','/v3/trackers/{trackerId}'),
    'tracker_configuration_get': ('GET','/v3/tracker/{trackerId}/configuration'),
    'tracker_configuration_update': ('POST','/v3/tracker/configuration'),
    'tracker_create': ('POST','/v3/tracker/configuration'),
    'item_create': ('POST','/v3/trackers/{trackerId}/items'),
    'item_get': ('GET','/v3/items/{itemId}'),
    'item_update': ('PUT','/v3/items/{itemId}'),
    'item_fields_update': ('PUT','/v3/items/{itemId}/fields'),
    'history': ('GET','/v3/items/{itemId}/history'),
    'comment_create': ('POST','/v3/items/{itemId}/comments'),
    'comments_get': ('GET','/v3/items/{itemId}/comments'),
    'relations_get': ('GET','/v3/items/{itemId}/relations'),
    'association_create': ('POST','/v3/associations'),
    'attachments_get': ('GET','/v3/items/{itemId}/attachments'),
}


def bind(cb):
    operations = cb.config.setdefault('operations',{})
    status = {}
    for name,(method,path) in ROUTES.items():
        present = method.lower() in cb.spec['paths'].get(path,{})
        if present and name not in operations:
            operations[name] = {'method':method,'path':path}
        status[name] = {'documented_method':method,'documented_path':path,'present_in_server_swagger':present}
    # Swagger variants may expose the verified tracker GET with a singular
    # resource name or a generic {id} parameter. Bind only documented GETs.
    if 'tracker_get' not in operations:
        paths = [p for p, methods in cb.spec['paths'].items()
                 if re.fullmatch(r'(?:/v3)?/trackers?/\{(?:trackerId|id)\}/?', p)
                 and isinstance(methods.get('get'), dict)]
        v3 = [p for p in paths if p.startswith('/v3/')]
        candidates = v3 or prefer_v3_aliases(paths)
        if len(candidates) == 1:
            operations['tracker_get'] = {'method': 'GET', 'path': candidates[0]}
    if 'fields' not in operations and 'tracker_configuration_get' in operations:
        operations['fields'] = dict(operations['tracker_configuration_get'],response_path='fields')
    try:
        project_tracker_list(cb)
    except ApiError:
        pass  # Report this capability when the user requests Tracker discovery.
    try:
        project_reads(cb)
    except ApiError:
        pass  # Report exact project capability when the Wizard requests it.
    cb.config['verified_3007_routes'] = status
    return status


def configuration_field_contract(cb, definition, tracker):
    """Read-only check for the preserving whole-configuration fallback."""
    from copy import deepcopy
    from mapping_helper import request_schema, prepare_configuration_permissions
    from single_import import validate
    op = cb.operation('tracker_configuration_update')
    if op['method'].upper() != 'POST' or op['path'] != '/v3/tracker/configuration':
        raise ApiError('Configuration Field creation requires the documented POST /v3/tracker/configuration')
    current = cb.configuration(tracker)
    if current.get('basicInformation',{}).get('trackerId') != tracker or not isinstance(current.get('fields'),list):
        raise ApiError('Full current Tracker Configuration and matching trackerId required')
    body = deepcopy(current)
    from clients.configuration_fields import definition_body, configured_reference_id
    reserved = configured_reference_id(cb.config, tracker, definition.get('label'))
    body['fields'].append(definition_body(cb.spec, definition, current, reserved, planning=True))
    body = prepare_configuration_permissions(cb,body)
    schema = request_schema(cb.spec,cb.spec['paths'][op['path']]['post'])
    validate(cb.spec,body,schema)
    return current


def new_tracker_configuration(cb, template_tracker, project_id, name):
    from copy import deepcopy
    from mapping_helper import request_schema, schema_object, prepare_configuration_permissions
    from single_import import validate
    op = cb.operation('tracker_create')
    if op['method'].upper() != 'POST' or op['path'] != '/v3/tracker/configuration' or op.get('body') or op.get('path_parameters'):
        raise ApiError('New Tracker Configuration requires the documented native POST without body/path overrides')
    current = cb.configuration(template_tracker)
    if current.get('basicInformation',{}).get('trackerId') != template_tracker:
        raise ApiError('Template Tracker ID was not verified')
    schema = request_schema(cb.spec,cb.spec['paths'][op['path']]['post'])
    basic_schema = schema_object(cb.spec,schema.get('properties',{}).get('basicInformation',{}))
    properties = basic_schema.get('properties',{})
    if 'projectId' not in properties or 'name' not in properties:
        raise ApiError('Swagger must identify basicInformation.projectId/name for a new Tracker Configuration')
    body = deepcopy(current)
    basic = body['basicInformation']
    basic.pop('trackerId',None)
    basic.update(projectId=project_id,name=name)
    if 'trackerId' in basic_schema.get('required',[]):
        identifier = schema_object(cb.spec,properties.get('trackerId',{}))
        if identifier.get('nullable') or identifier.get('x-nullable'):
            basic['trackerId'] = None
        else:
            raise ApiError('Swagger requires a non-null trackerId: new Tracker request contract is not established')
    body = prepare_configuration_permissions(cb,body)
    validate(cb.spec,body,schema)
    return body
