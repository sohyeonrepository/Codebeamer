"""Request envelopes confirmed from the user's Codebeamer OpenAPI contracts.

FieldValue discriminator names/value properties come from server-derived bindings;
this module never invents a concrete FieldValue type for an arbitrary source value.
"""
from copy import deepcopy
from clients.http import ApiError


def field_values(values, bindings):
    result = []
    for key, value in values.items():
        binding = bindings.get(key)
        if not binding:
            raise ApiError('No Codebeamer FieldValue binding for ' + key)
        if type(binding.get('fieldId')) is not int or not isinstance(binding.get('type'), str):
            raise ApiError('FieldValue binding requires actual fieldId and discriminator type: ' + key)
        member = binding.get('value_property')
        if not member or member in ('fieldId', 'type', 'name'):
            raise ApiError('FieldValue binding requires the actual value property: ' + key)
        field = {'fieldId': binding['fieldId'], 'type': binding['type'], member: deepcopy(value)}
        if 'name' in binding:
            field['name'] = binding['name']
        result.append(field)
    validate_values(result)
    return result


def validate_values(values):
    if not isinstance(values, list):
        raise ApiError('Codebeamer FieldValues must be an array')
    identifiers = set()
    for field in values:
        if not isinstance(field, dict) or not isinstance(field.get('type'), str) or not field['type']:
            raise ApiError('Each Codebeamer FieldValue requires its actual discriminator type')
        if type(field.get('fieldId')) is not int:
            raise ApiError('Each Codebeamer FieldValue requires its destination fieldId')
        if field['fieldId'] in identifiers:
            raise ApiError('Duplicate Codebeamer fieldId in one modification')
        identifiers.add(field['fieldId'])


def request_body(operation_id, body, bindings=None):
    if operation_id not in ('createTrackerItem', 'updateCustomFieldTrackerItem', 'updateTestRunResult', 'postTrackerConfiguration'):
        return body
    if not isinstance(body, dict):
        raise ApiError('Confirmed Codebeamer operation requires a JSON object: ' + operation_id)
    body = deepcopy(body)
    bindings = bindings or {}
    if operation_id == 'updateCustomFieldTrackerItem':
        if 'fieldValues' not in body and 'tableValues' not in body:
            body = {'fieldValues': field_values(body, bindings)}
        if set(body) - {'fieldValues', 'tableValues'}:
            raise ApiError('Field update accepts fieldValues/tableValues; native TrackerItem body cannot be sent here')
        for key in ('fieldValues', 'tableValues'):
            if key in body:
                validate_values(body[key])
        if not body.get('fieldValues') and not body.get('tableValues'):
            raise ApiError('Empty field update cannot generate a meaningful revision')
    elif operation_id == 'createTrackerItem':
        # Move explicitly bound canonical values into customFields; retain native properties.
        custom = {key: body.pop(key) for key in list(body) if key in bindings}
        if custom:
            body.setdefault('customFields', []).extend(field_values(custom, bindings))
        if 'fieldValues' in body:
            raise ApiError('TrackerItem creation uses customFields, not the fieldValues update envelope')
        if 'customFields' in body:
            validate_values(body['customFields'])
        if not isinstance(body.get('name'), str) or not body['name'].strip():
            raise ApiError('Test Runs Tracker requires a nonempty native name at creation')
    elif operation_id == 'updateTestRunResult':
        if 'updateRequestModels' not in body and 'result' in body:
            body = {'updateRequestModels': [body]}
        models = body.get('updateRequestModels')
        if not isinstance(models, list) or not models:
            raise ApiError('Run result update requires nonempty updateRequestModels')
        if set(body) - {'updateRequestModels', 'parentResultPropagation'}:
            raise ApiError('Unsupported Run result update envelope property')
        if 'parentResultPropagation' in body and type(body['parentResultPropagation']) is not bool:
            raise ApiError('parentResultPropagation must be boolean')
        for model in models:
            reference = model.get('testCaseReference') if isinstance(model, dict) else None
            if not isinstance(model, dict) or not isinstance(model.get('result'), str) or not model['result']:
                raise ApiError('Each Run result model requires a result string')
            if not isinstance(reference, dict) or type(reference.get('id')) is not int:
                raise ApiError('Native Run result requires an actual testCaseReference; Test Set/Plan reference cannot substitute')
            if 'customFields' in model:
                validate_values(model['customFields'])
    else:
        basic, fields = body.get('basicInformation'), body.get('fields')
        if not isinstance(basic, dict) or type(basic.get('trackerId')) is not int or not isinstance(fields, list):
            raise ApiError('Tracker configuration update requires basicInformation.trackerId and fields array')
        for field in fields:
            if not isinstance(field, dict) or ('permission' in field and not isinstance(field['permission'], dict)):
                raise ApiError('Tracker field permissions must use an object, e.g. type=UNRESTRICTED')
    return body


def canonical_fields(response, bindings):
    """Normalize a configured server field-value array for exact value comparison."""
    if not isinstance(response, list):
        raise ApiError('Field readback must select the actual complete FieldValue array')
    indexed = {}
    for field in response:
        if field['fieldId'] in indexed:
            raise ApiError('Duplicate destination FieldValue in readback')
        indexed[field['fieldId']] = field
    result = {}
    for key, binding in bindings.items():
        field = indexed.get(binding['fieldId'])
        if field is None or field.get('type') != binding['type'] or binding['value_property'] not in field:
            raise ApiError('Field readback type/value mismatch for ' + key)
        result[key] = field[binding['value_property']]
    return result
