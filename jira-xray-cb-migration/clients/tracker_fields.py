"""Normalize observed Tracker field metadata; numeric type IDs are never guessed."""
from copy import deepcopy
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier, schema_object

TYPES = {'TextField':'Text', 'WikiTextField':'Wikitext', 'WikitextField':'Wikitext',
         'IntegerField':'Integer', 'DecimalField':'Decimal', 'BooleanField':'Boolean',
         'BoolField':'Boolean', 'DateField':'Date', 'DateTimeField':'Datetime', 'DatetimeField':'Datetime',
         'OptionChoiceField':'Choice', 'UserChoiceField':'User',
         'TrackerItemChoiceField':'Reference', 'ReferenceField':'Reference'}


def normalize(field):
    result = deepcopy(field)
    if field.get('name'): result['label'] = field['name']
    raw_type = field.get('type')
    if isinstance(raw_type, dict): raw_type = raw_type.get('name')
    if isinstance(raw_type, str) and raw_type in TYPES: result['typeName'] = TYPES[raw_type]
    if not result.get('typeName'):
        model = str(field.get('valueModel', '')).split('<', 1)[0]
        models = {'TextFieldValue':'Text','WikiTextFieldValue':'Wikitext','IntegerFieldValue':'Integer',
                  'DecimalFieldValue':'Decimal','BooleanFieldValue':'Boolean','DateFieldValue':'Date',
                  'DateTimeFieldValue':'Datetime','DatetimeFieldValue':'Datetime'}
        if model in models: result['typeName'] = models[model]
        elif model == 'ChoiceFieldValue':
            reference=field.get('referenceType')
            if reference == 'ChoiceOptionReference':
                result['typeName'] = 'Choice'
            elif reference == 'UserReference':
                result['typeName'] = 'User'
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
    # The list endpoint may omit newly created fields while Tracker
    # Configuration includes them. Merge both by actual server Field ID,
    # then request Field Detail for each candidate.
    listed_by_id={}
    for row in listed:
        fid=field_identifier(row)
        if type(fid) is int:
            listed_by_id[fid]=row
    config_by_id={}
    for row in config.get('fields',[]):
        if not isinstance(row,dict):
            continue
        fid=field_identifier(row)
        if type(fid) is int:
            config_by_id[fid]=row
    result=[]
    for fid in dict.fromkeys([*listed_by_id,*config_by_id]):
        merged=dict(config_by_id.get(fid,{}),**listed_by_id.get(fid,{}))
        try:
            detail=cb.http.request('GET','/v3/trackers/'+segment(tracker)+'/fields/'+segment(fid))
            if not isinstance(detail,dict) or field_identifier(detail)!=fid:
                raise ApiError('Field Detail ID/response mismatch')
            merged.update(detail)
        except ApiError as exc:
            warnings.append('Field '+str(fid)+': '+str(exc))
        normalized=normalize(merged)
        normalized['visibility_unknown']=not any(
            type(normalized.get(k)) is bool for k in ('hidden','isHidden','visible'))
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
