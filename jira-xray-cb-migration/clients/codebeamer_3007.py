"""Bind user-supplied Codebeamer 3.0 API routes only when present in server Swagger."""
from clients.http import ApiError
import re


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
    if len(candidates) != 1:
        raise ApiError('Tracker 전체 목록 GET을 Swagger에서 하나로 확인할 수 없습니다. 유형별 /types/{typeId} API에는 실제 Tracker Type 목록 조회가 추가로 필요합니다. typeId를 임의로 지정하지 않습니다.')
    selected = candidates[0]
    if configured:
        for key in ('response_path','response_fields'):
            if key in configured:selected[key] = configured[key]
    operations['trackers_get'] = selected
    return cb.operation('trackers_get')

# Product documentation supplied for this integration. Request shapes still come
# from the connected server; presence here is not a claim of live verification.
ROUTES = {
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
    if 'fields' not in operations and 'tracker_configuration_get' in operations:
        operations['fields'] = dict(operations['tracker_configuration_get'],response_path='fields')
    try:
        project_tracker_list(cb)
    except ApiError:
        pass  # Report this capability when the user requests Tracker discovery.
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
    body['fields'].append(deepcopy(definition))
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
