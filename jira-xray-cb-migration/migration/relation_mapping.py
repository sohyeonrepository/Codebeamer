"""Read-only relation catalogs and direction-aware mapping contracts."""
from copy import deepcopy
from clients.http import ApiError


def catalogs(jira, cb):
    source = jira.http.request('GET', '/rest/api/2/issueLinkType')
    source = source.get('issueLinkTypes') if isinstance(source,dict) else None
    target = cb.http.request('GET', '/v3/associations/types')
    if not isinstance(source,list) or not isinstance(target,list):
        raise ApiError('Relation Type API requires complete Jira issueLinkTypes / CB array responses')
    for rows, is_jira in ((source,True),(target,False)):
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or not row.get('name') or row.get('id') is None:
                raise ApiError('Relation Type missing ID/name')
            if not is_jira and (type(row['id']) is not int or row['id']<=0):
                raise ApiError('CB Association Type ID must be a positive integer')
            if is_jira and any(not isinstance(row.get(k),str) or not row[k] for k in ('inward','outward')):
                raise ApiError('Jira Link Type requires inward/outward labels')
            if str(row['id']) in seen:raise ApiError('Duplicate Relation Type ID')
            seen.add(str(row['id']))
    return {'jira':source,'codebeamer':target}


def normalize(value):
    if type(value) is int: value={'type_id':value,'reverse':False,'mode':'association'}
    if not isinstance(value,dict):raise ApiError('Jira Link Type has no saved relation mapping')
    result=deepcopy(value)
    if type(result.get('reverse',False)) is not bool:raise ApiError('Relation direction must be boolean')
    result.setdefault('reverse',False);result.setdefault('mode','association')
    if result['mode']=='association':
        if type(result.get('type_id')) is not int or result['type_id']<=0:raise ApiError('Select an actual CB Association Type')
    elif result['mode']=='reference':
        for key in ('tracker_id','field_id','target_tracker_id'):
            if type(result.get(key)) is not int or result[key]<=0:raise ApiError('Reference mapping requires '+key)
    else:raise ApiError('Unsupported relation mapping mode')
    return result


def verify_reference(cb, mapping):
    """Server-specific reference contracts must be explicit; no guessed FieldValue."""
    for name in ('reference_field_get','reference_values_get','reference_values_update'):
        op=cb.operation(name)
        expected=('PUT','PATCH') if name.endswith('update') else ('GET',)
        if op['method'].upper() not in expected:raise ApiError('Invalid Reference API method: '+name)
    metadata=cb.call('reference_field_get',{'trackerId':mapping['tracker_id'],'fieldId':mapping['field_id']})
    if (not isinstance(metadata,dict) or metadata.get('id')!=mapping['field_id'] or
        type(metadata.get('multiple')) is not bool or metadata.get('hidden') is not False or
        not isinstance(metadata.get('targetTrackerIds'),list) or mapping['target_tracker_id'] not in metadata['targetTrackerIds']):
        raise ApiError('Reference field ID/Hidden/cardinality/allowed target Tracker contract unverified')
    return metadata


def write_reference(cb,state,identity,source,target,mapping):
    metadata=verify_reference(cb,mapping)
    for item,tracker in ((source,mapping['tracker_id']),(target,mapping['target_tracker_id'])):
        actual=cb.call('item_get',{'itemId':item})
        if actual.get('id')!=item or actual.get('tracker',{}).get('id')!=tracker:
            raise ApiError('Reference direction or Item Tracker mismatch')
    params={'itemId':source,'trackerId':mapping['tracker_id'],'fieldId':mapping['field_id']}
    def read_values():
        actual=cb.call('reference_values_get',params)
        if not isinstance(actual,dict) or actual.get('fieldId')!=mapping['field_id'] or not isinstance(actual.get('values'),list):
            raise ApiError('Reference readback requires fieldId and complete values array')
        if any(not isinstance(v,dict) or type(v.get('id')) is not int for v in actual['values']):
            raise ApiError('Invalid Reference Item IDs')
        return actual['values']
    current=read_values()
    if target not in [v['id'] for v in current]:
        if current and not metadata['multiple']:raise ApiError('Single Reference field already occupied; existing reference preserved')
        expected=current+[{'id':target}]
        cb.call('reference_values_update',params,{'fieldId':mapping['field_id'],'values':expected})
        if read_values()!=expected:raise ApiError('Reference values/direction readback mismatch')
    state.mark('reference-relation:'+identity,'done',{'source':source,'target':target,'field_id':mapping['field_id']})


def issue_references(value):
    if value is None:return []
    rows=value if isinstance(value,list) else [value]
    identities=[]
    for row in rows:
        identity=row.get('key',row.get('id')) if isinstance(row,dict) else row
        if not isinstance(identity,(str,int)) or not str(identity).strip():
            raise ApiError('Issue Picker requires actual Jira Issue key/id; text conversion is not supported')
        identities.append(str(identity))
    return list(dict.fromkeys(identities))


def picker_references(cb,state,bundles,settings):
    indexed={identity:issue for _,issue in bundles for identity in (str(issue['id']),issue['key'])}
    for project,issue in bundles:
        scope=project['key']+':'+str(issue['fields']['issuetype']['id'])
        for source,spec in settings.get('field_mapping',{}).get(scope,{}).items():
            if not spec.get('deferred_reference'):continue
            state.save('source-references',issue['key']+':'+source,{'source':source,'value':issue['fields'].get(source)})
            for identity in issue_references(issue['fields'].get(source)):
                other=indexed.get(identity)
                if other is None:raise ApiError('Issue Picker target outside selected migration scope: '+identity)
                origin=state.get('item:'+str(issue['id']));destination=state.get('item:'+str(other['id']))
                if not origin or not destination or origin[0]!='done' or destination[0]!='done':
                    raise ApiError('Issue Picker target Item ID not ready: '+identity)
                target_scope=other['fields']['project']['key']+':'+str(other['fields']['issuetype']['id'])
                mapping={'mode':'reference','tracker_id':settings['tracker_mapping'][scope],
                         'field_id':spec['field_id'],'target_tracker_id':settings['tracker_mapping'][target_scope]}
                write_reference(cb,state,issue['key']+':'+source+':'+identity,origin[1]['id'],destination[1]['id'],mapping)
