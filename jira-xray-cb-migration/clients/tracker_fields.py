"""Normalize observed Tracker field metadata; numeric type IDs are never guessed."""
from copy import deepcopy
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier, schema_object

TYPES = {'TextField':'Text', 'WikiTextField':'Wikitext', 'WikitextField':'Wikitext',
         'IntegerField':'Integer', 'DecimalField':'Decimal', 'BooleanField':'Boolean',
         'BoolField':'Boolean', 'DateField':'Date', 'DateTimeField':'Datetime',
         'OptionChoiceField':'Choice', 'UserChoiceField':'User'}


def normalize(field):
    result = deepcopy(field)
    if field.get('name'): result['label'] = field['name']
    raw_type = field.get('type')
    if isinstance(raw_type, dict): raw_type = raw_type.get('name')
    if isinstance(raw_type, str) and raw_type in TYPES: result['typeName'] = TYPES[raw_type]
    if not result.get('typeName'):
        model = str(field.get('valueModel', '')).split('<', 1)[0]
        models = {'TextFieldValue':'Text','WikiTextFieldValue':'Wikitext','IntegerFieldValue':'Integer',
                  'DecimalFieldValue':'Decimal','BooleanFieldValue':'Boolean','DateFieldValue':'Date'}
        if model in models: result['typeName'] = models[model]
    if type(field.get('multipleValues')) is bool: result['multipleSelection'] = field['multipleValues']
    return result


def read(cb, tracker):
    from clients.codebeamer_verified_reads import fields, configuration
    listed = fields(cb, tracker)
    warnings = []
    try: config = configuration(cb, tracker)
    except ApiError as exc:
        config = {'fields': []}
        warnings.append('Tracker Configuration: '+str(exc))
    indexed = {str(field_identifier(f)): f for f in config['fields']}
    result = []
    for row in listed:
        fid = field_identifier(row)
        merged = dict(indexed.get(str(fid), {}), **row)
        try:
            detail = cb.http.request('GET', '/v3/trackers/'+segment(tracker)+'/fields/'+segment(fid))
            if not isinstance(detail, dict) or field_identifier(detail) != fid:
                raise ApiError('Field Detail ID/response mismatch')
            merged.update(detail)
        except ApiError as exc:
            warnings.append('Field '+str(fid)+': '+str(exc))
        normalized = normalize(merged)
        normalized['visibility_unknown'] = not any(type(normalized.get(k)) is bool for k in ('hidden','isHidden','visible'))
        result.append(normalized)
    cb.config.setdefault('tracker_field_evidence', {})[str(tracker)] = {'configuration':config, 'warnings':warnings}
    return result


def binding(spec, field):
    """A valueModel alone cannot prove the writable member or discriminator."""
    model = field.get('valueModel')
    if not spec or not isinstance(model, str): return None
    models = spec.get('components', {}).get('schemas', spec.get('definitions', {}))
    name = model if model in models else model.split('<', 1)[0]
    if name not in models: return None
    schema = schema_object(spec, models[name])
    props = schema.get('properties', {})
    members = [key for key in ('value', 'values') if key in props]
    discriminator = schema_object(spec, props.get('type', {}))
    enums = discriminator.get('enum', [])
    if len(members) != 1 or enums != [name]: return None
    return {'fieldId':field_identifier(field), 'type':name, 'value_property':members[0]}
