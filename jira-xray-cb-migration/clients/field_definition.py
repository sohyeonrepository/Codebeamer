"""Definition-only planning from the supplied server Configuration schema."""
from copy import deepcopy
from clients.http import ApiError


def plan_definition(cb, definition, configuration):
    from mapping_helper import request_schema, schema_object
    proposed = deepcopy(definition)
    if any(k in proposed for k in ('id', 'fieldId', 'referenceId')):
        raise ApiError('New definition must not copy an existing field ID')
    op = cb.operation('tracker_configuration_update')
    if op.get('method','').upper() != 'POST' or op.get('path') != '/v3/tracker/configuration':
        raise ApiError('Definition creation requires POST /v3/tracker/configuration')
    schema = schema_object(cb.spec, request_schema(cb.spec, cb.spec['paths'][op['path']]['post']))
    fields = schema_object(cb.spec, schema.get('properties',{}).get('fields',{}))
    field = schema_object(cb.spec, fields.get('items',{}))
    if 'referenceId' not in field.get('required',[]):
        return proposed
    identifier = schema_object(cb.spec, field.get('properties',{}).get('referenceId',{}))
    if identifier.get('type') != 'integer' or identifier.get('readOnly'):
        raise ApiError('Required referenceId has no writable integer creation contract')
    used = {f.get('referenceId',f.get('id')) for f in configuration['fields']}
    candidates = identifier.get('enum')
    if candidates is None:
        lower, upper = identifier.get('minimum'), identifier.get('maximum')
        if type(lower) is not int or type(upper) is not int or lower <= 0 or upper < lower:
            raise ApiError('Required referenceId allocation range is unverified; supply server schema with verified bounds or enum')
        lower += identifier.get('exclusiveMinimum') is True
        upper -= identifier.get('exclusiveMaximum') is True
        # At most len(used)+1 consecutive IDs need inspection to find a free ID.
        candidates = range(lower, min(upper + 1, lower + len(used) + 1))
    available = next((n for n in candidates if type(n) is int and n > 0 and n not in used), None)
    if available is None:
        raise ApiError('No unused referenceId in the verified server contract')
    proposed['referenceId'] = available
    return proposed
