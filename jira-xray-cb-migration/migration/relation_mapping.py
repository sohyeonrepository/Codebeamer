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
    elif result['mode'] in ('reference','reference_create'):
        keys=('tracker_id','field_id','target_tracker_id') if result['mode']=='reference' else ('tracker_id','target_tracker_id')
        for key in keys:
            if type(result.get(key)) is not int or result[key]<=0:raise ApiError('Reference mapping requires '+key)
        if result['mode']=='reference_create' and (not isinstance(result.get('creation'),dict) or not result['creation'].get('definition')):
            raise ApiError('Reference creation definition contract is missing')
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


def ensure_reference_field(cb,state,type_id,mapping):
    """Reuse the Configuration helper; reconcile an uncertain POST before any retry."""
    import hashlib
    from mapping_helper import create_confirmed_field, field_identifier
    from clients.codebeamer_3007 import configuration_field_contract
    definition=deepcopy(mapping['creation']['definition'])
    tracker=mapping['tracker_id']
    token='relation-field:'+str(type_id)+':'+str(tracker)+':'+hashlib.sha256(
        __import__('json').dumps(definition,sort_keys=True).encode()).hexdigest()
    current=cb.configuration(tracker)
    matches=[f for f in current.get('fields',[]) if f.get('label',f.get('name'))==definition['label']]
    prior=state.get(token)
    if prior:
        if len(matches)!=1 or field_identifier(matches[0]) is None:
            raise ApiError('Reference field creation uncertain/missing: reconcile server before retry')
        created=matches[0]
        if prior[1].get('id') is not None and prior[1]['id']!=field_identifier(created):
            raise ApiError('Reference Field ID differs from creation receipt')
        for key,value in definition.items():
            if created.get(key)!=value:raise ApiError('Reference field definition changed: '+key)
        baseline=prior[1].get('baseline')
        if baseline:
            for key,value in baseline.items():
                if key!='fields' and current.get(key)!=value:raise ApiError('Existing Tracker setting changed: '+key)
            for old in baseline['fields']:
                same=next((f for f in current['fields'] if field_identifier(f)==field_identifier(old)),None)
                if not same or any(same.get(k)!=v for k,v in old.items()):raise ApiError('Existing field/Permission preservation failed')
        elif prior[0]!='done':raise ApiError('Reference creation has no reconciliation baseline')
    else:
        if matches:raise ApiError('Reference name already exists; select the existing Field ID')
        expected=configuration_field_contract(cb,definition,tracker)
        journal=state.root/('relation-field-'+hashlib.sha256(token.encode()).hexdigest()+'.json')
        def before_post():state.mark(token,'pending',{'baseline':expected})
        created,current=create_confirmed_field(cb,tracker,definition,expected,journal,
            approved=True,before_post=before_post,allow_reference=True)
    field_id=field_identifier(created)
    state.mark(token,'done',{'id':field_id})
    state.save('relation-field',token,{'field_id':field_id,'tracker_id':tracker,'configuration':current})
    return dict(mapping,mode='reference',field_id=field_id)


def migrate_relations(cb,state,bundles,settings):
    from migration.preservation import record
    mappings=settings.get('wizard',{}).get('association_types',{})
    external=settings.get('wizard',{}).get('external_item_mapping',{})
    report={'source':0,'selected':0,'verified':0,'excluded':0,'failed':0,'relations':[]}
    collected={}
    selected_issue_ids={str(issue['id']) for _,issue in bundles}
    for _,issue in bundles:
        for index,link in enumerate(issue['fields'].get('issuelinks',[])):
            link_id=str(link.get('id','missing:'+issue['key']+':'+str(index)))
            row={'jira_link_id':link_id,'jira_link_type_id':str(link.get('type',{}).get('id','')),
                 'source_type':deepcopy(link.get('type',{})),'raw':{'issue_key':issue['key'],'link':deepcopy(link)}}
            try:
                if 'id' not in link or not row['jira_link_type_id']:raise ApiError('Jira relation ID/type ID missing')
                if ('inwardIssue' in link)==('outwardIssue' in link):raise ApiError('Jira link must have exactly one direction')
                inward='inwardIssue' in link
                other=link['inwardIssue'] if inward else link['outwardIssue']
                if other.get('id') is None:raise ApiError('Jira linked Issue ID missing')
                a,b=(other,issue) if inward else (issue,other)
                row.update(source_issue_id=str(a['id']),target_issue_id=str(b['id']),source_key=a.get('key'),target_key=b.get('key'))
            except (ApiError,KeyError,TypeError) as exc:
                row['collection_error']=str(exc) if isinstance(exc,ApiError) else 'Malformed Jira link response'
            if link_id in collected:
                prior=collected[link_id]
                keys=('jira_link_type_id','source_issue_id','target_issue_id')
                if any(prior.get(k)!=row.get(k) for k in keys) or row.get('collection_error'):
                    prior['collection_error']='Conflicting source representations for the same Jira Link ID'
                continue
            collected[link_id]=row
    report['source']=len(collected)
    def fail(row,exc):
        row.update(status='failed',error=str(exc) if isinstance(exc,ApiError) else type(exc).__name__)
        row['reason']=row['error']
    def endpoint(identity,key):
        if identity in selected_issue_ids:
            saved=state.get('item:'+identity)
            if not saved or saved[0]!='done':raise ApiError('Target Item ID not ready for Jira Issue '+str(key or identity))
            target=saved[1]['id']
        else:
            target=external.get(identity,external.get(key))
            if type(target) is not int or target<=0:
                raise ApiError('Linked Issue outside scope without explicit CB Item mapping: '+str(key or identity))
        actual=cb.call('item_get',{'itemId':target})
        if not isinstance(actual,dict) or actual.get('id')!=target:raise ApiError('Explicit/migrated CB Item ID readback failed for '+str(key or identity))
        return target
    actual_types=None
    catalog_error=None
    from clients.codebeamer_client import CodebeamerClient
    if isinstance(cb,CodebeamerClient) and any(mappings.get(r['jira_link_type_id']) is not None for r in collected.values()):
        try:
            actual_types=cb.http.request('GET','/v3/associations/types')
            if not isinstance(actual_types,list):raise ApiError('Association Type API must return a complete array')
        except ApiError as exc:catalog_error=exc
    prepared=[]
    for row in collected.values():
        identity=row['jira_link_id']
        state.save('source-relations',identity,row)
        selected=mappings.get(row['jira_link_type_id'])
        if selected is None and not row.get('collection_error'):
            row.update(status='excluded',reason='No user-selected mapping for this Jira Link Type')
        else:
            report['selected']+=1
            try:
                if row.get('collection_error'):raise ApiError(row['collection_error'])
                mapping=normalize(selected)
                original=mapping.get('source_type')
                if original and any(original.get(k)!=row['source_type'].get(k) for k in ('id','name','inward','outward')):
                    raise ApiError('Jira Link Type metadata changed; review saved mapping')
                row.update(mode=mapping['mode'],direction='reverse' if mapping['reverse'] else 'normal',mapping=deepcopy(mapping))
                source=endpoint(row['source_issue_id'],row.get('source_key'))
                target=endpoint(row['target_issue_id'],row.get('target_key'))
                if mapping['reverse']:source,target=target,source
                row.update(source_cb_item_id=source,target_cb_item_id=target)
                if mapping['mode']=='association':
                    if catalog_error:raise catalog_error
                    if actual_types is not None and mapping['type_id'] not in [t.get('id') for t in actual_types]:
                        raise ApiError('Selected CB Association Type no longer exists')
                    row['association_type_id']=mapping['type_id']
                else:
                    # Verify ownership before creating a Reference field or updating values.
                    for item,tracker in ((source,mapping['tracker_id']),(target,mapping['target_tracker_id'])):
                        if cb.call('item_get',{'itemId':item}).get('tracker',{}).get('id')!=tracker:
                            raise ApiError('Reference owner/target Tracker does not match selected direction; change direction or Tracker')
                    if mapping['mode']=='reference_create':
                        mapping=ensure_reference_field(cb,state,row['jira_link_type_id'],mapping)
                        mappings[row['jira_link_type_id']]=deepcopy(mapping)
                        state.save('wizard','relation-mapping',mappings)
                    metadata=verify_reference(cb,mapping)
                    row.update(reference_field_id=mapping['field_id'],multiple=metadata['multiple'])
                prepared.append((row,mapping))
            except (ApiError,KeyError,TypeError,ValueError) as exc:fail(row,exc)
    # A single reference receiving multiple destinations fails as a group before any value write.
    groups={}
    for row,mapping in prepared:
        if mapping['mode']=='reference':
            groups.setdefault((row['source_cb_item_id'],mapping['field_id']),[]).append(row)
    for group in groups.values():
        if any(not row['multiple'] for row in group) and len({r['target_cb_item_id'] for r in group})>1:
            for row in group:fail(row,ApiError('Single Reference has multiple selected targets; choose a multiple Reference or revise mapping'))
    for row,mapping in prepared:
        if row.get('status')=='failed':continue
        source,target=row['source_cb_item_id'],row['target_cb_item_id']
        try:
            if mapping['mode']=='association':
                row['cb_relation_id']=record(cb,state,'association',row['jira_link_id'],
                    {'fromItemId':source,'toItemId':target,'typeId':mapping['type_id']},{'itemId':source})
            else:write_reference(cb,state,row['jira_link_id'],source,target,mapping)
            row['status']='verified'
        except (ApiError,KeyError,TypeError,ValueError) as exc:fail(row,exc)
    for row in collected.values():
        report[{'verified':'verified','excluded':'excluded','failed':'failed'}[row['status']]]+=1
        state.save('relation-results',row['jira_link_id'],row)
        report['relations'].append(row)
    report['counts_match']=report['source']==report['verified']+report['excluded']+report['failed']
    state.save('reports','relations',report)
    return report
