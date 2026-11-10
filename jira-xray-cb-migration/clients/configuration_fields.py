"""Serialize only new field plans; leave all existing Configuration data intact."""
from copy import deepcopy
from clients.http import ApiError


def configured_reference_id(config, tracker, label):
    """Explicit per-Tracker/per-field reservation, never inferred from max(ID)."""
    return config.get('configuration_reference_ids', {}).get(str(tracker), {}).get(label)


def definition_body(spec, definition, current, reference_id=None, planning=False):
    from mapping_helper import request_schema, schema_object
    from single_import import validate
    result = deepcopy(definition)
    operation = (spec or {}).get('paths', {}).get('/v3/tracker/configuration', {}).get('post')
    if not operation:
        if reference_id is not None:
            raise ApiError('referenceId request schema unavailable')
        return result
    fields = request_schema(spec, operation).get('properties', {}).get('fields')
    if not fields:
        if reference_id is not None:
            raise ApiError('referenceId request schema unavailable')
        return result
    item = schema_object(spec, schema_object(spec, fields).get('items', {}))
    properties = item.get('properties', {})
    if not properties:
        if reference_id is not None:
            raise ApiError('referenceId request schema unavailable')
        return result
    reference_schema = properties.get('referenceId')
    if reference_schema is not None:
        reference_schema = schema_object(spec, reference_schema)
        if reference_schema.get('readOnly'):
            if reference_id is not None or 'referenceId' in result:
                raise ApiError('referenceId is read-only in this server schema')
        elif not planning or reference_id is not None:
            selected = reference_id if reference_id is not None else result.get('referenceId')
            if selected is None:
                raise ApiError('신규 referenceId 할당값이 필요합니다. 서버 자동 할당을 가정하지 않습니다. '
                               'codebeamer.configuration_reference_ids[Tracker ID][필드명]에 관리자 확인값을 지정하세요. POST 미실행')
            if type(selected) is not int or not -(2**31) <= selected < 2**31:
                raise ApiError('referenceId must be an int32 integer')
            validate(spec, selected, reference_schema, 'new_field.referenceId')
            for bound, invalid in (('minimum', lambda n: selected < n), ('maximum', lambda n: selected > n)):
                if bound in reference_schema and invalid(reference_schema[bound]):
                    raise ApiError('referenceId outside server schema '+bound)
            used = {f.get(key) for f in current.get('fields', []) for key in ('referenceId', 'id', 'fieldId')
                    if isinstance(f, dict) and isinstance(f.get(key), (int, str))}
            if selected in used or str(selected) in used:
                raise ApiError('referenceId already in use in selected Tracker; no POST')
            if 'referenceId' in result and result['referenceId'] != selected:
                raise ApiError('Conflicting referenceId reservations')
            result['referenceId'] = selected
    elif reference_id is not None:
        raise ApiError('referenceId is not declared in this server request schema')
    if 'options' in result and 'options' not in properties:
        options = result.pop('options')
        if options:
            if 'choiceOptionSetting' not in properties:
                raise ApiError('Configuration schema has no choiceOptionSetting; cannot preserve Choice options')
            observed = [f.get('choiceOptionSetting') for f in current.get('fields', [])
                        if f.get('typeId') == definition.get('typeId')]
            if not any(isinstance(s, dict) and s.get('type') == 'CHOICE_OPTIONS'
                       and isinstance(s.get('choiceOptions'), list) for s in observed):
                raise ApiError('Same-type CHOICE_OPTIONS definition not observed in selected Tracker')
            base = schema_object(spec, properties['choiceOptionSetting'])
            ref = base.get('discriminator', {}).get('mapping', {}).get('CHOICE_OPTIONS')
            if not ref:
                raise ApiError('CHOICE_OPTIONS discriminator schema unavailable; no guessed option payload')
            subtype = schema_object(spec, {'$ref': ref})
            if 'choiceOptions' not in subtype.get('properties', {}):
                raise ApiError('CHOICE_OPTIONS schema does not declare choiceOptions')
            setting = {'type': 'CHOICE_OPTIONS', 'choiceOptions': deepcopy(options)}
            if 'choiceOptionSetting' in result and result['choiceOptionSetting'] != setting:
                raise ApiError('Conflicting internal options and native choiceOptionSetting')
            validate(spec, setting, subtype, 'new_field.choiceOptionSetting')
            result['choiceOptionSetting'] = setting
    if 'reference' in result and 'reference' not in properties:
        if result['reference'] is not None:
            raise ApiError('Nonempty Reference needs a verified Configuration conversion; no POST')
        result.pop('reference')
    unknown = set(result) - set(properties)
    if unknown:
        raise ApiError('New Configuration field has undeclared properties: '+', '.join(sorted(unknown)))
    validate(spec, result, item, 'new_field')
    return result


def read_options(field):
    setting = field.get('choiceOptionSetting')
    if isinstance(setting, dict) and setting.get('type') == 'CHOICE_OPTIONS':
        return setting.get('choiceOptions')
    return field.get('options')


def definition_changes(expected, actual):
    changes = []
    for key, value in expected.items():
        if key == 'choiceOptionSetting' and isinstance(value, dict) and value.get('type') == 'CHOICE_OPTIONS':
            observed = actual.get(key)
            options = read_options(actual)
            planned = value.get('choiceOptions', [])
            if (not isinstance(observed, dict) or observed.get('type') != value['type']
                    or not isinstance(options, list) or len(options) != len(planned)
                    or any(not isinstance(o, dict) or type(o.get('id')) is not int
                           or any(o.get(k) != v for k, v in p.items()) for p, o in zip(planned, options))):
                changes.append(key)
        elif actual.get(key) != value:
            changes.append(key)
    return changes
