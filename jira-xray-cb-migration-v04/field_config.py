"""Fail-closed tracker field add-on using full Codebeamer TrackerConfiguration.

Faithful to the saved GET->POST pattern observed for tracker 10161.
Caller must supply API-confirmed field type/template, and server writes require
explicit confirmation. Never posts partial fields or deletes existing fields.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
from typing import Any
from migrator import save

class FieldCreationError(ValueError):pass

# Obtained from user-provided live GET response; labels and permissions are changeable.
FIELD_TYPE_IDS={'Text':0,'Integer':1,'Date':3,'Choice':6,'User':7,'Reference':7}

def build_field_config(current:dict, spec:dict)->dict:
    if not isinstance(current.get('basicInformation'),dict) or not isinstance(current.get('fields'),list):
        raise FieldCreationError('GET response must contain basicInformation and fields array')
    info=current['basicInformation']
    if not isinstance(info.get('trackerId'),int) or info['trackerId']<=0:
        raise FieldCreationError('Cannot identify tracker')
    label=spec.get('label','').strip()
    if not label:raise FieldCreationError('Field label required')
    if any(f.get('label','').lower()==label.lower() for f in current['fields']):
        raise FieldCreationError('Duplicate field label')
    typ=spec.get('type')
    if typ not in FIELD_TYPE_IDS:raise FieldCreationError('Unsupported field type')
    if not isinstance(spec.get('referenceId'),int) or spec['referenceId']<=0:
        raise FieldCreationError('referenceId must be assigned explicitly and confirmed unused')
    if any(f.get('referenceId')==spec['referenceId'] for f in current['fields']):
        raise FieldCreationError('referenceId collision')
    field={
        'referenceId':spec['referenceId'],
        'typeId':FIELD_TYPE_IDS[typ],
        'position':int(spec.get('position',max((f.get('position',0) for f in current['fields'] if isinstance(f.get('position'),int)),default=0)+10)),
        'label':label,
        'hidden':False,'listable':False,'union':False,'mandatory':False,
        'mandatoryExceptInStatus':[], 'multipleSelection':typ in ('Choice','User','Reference') and bool(spec.get('multipleSelection')),
        'propagateSuspect':False,'reversedSuspect':False,'propagateDependencies':False,
        'omitSuspectedWhenChange':False,'omitMerge':False,'newLine':False,
        'permission':spec.get('permission',{'type':'UNRESTRICTED'}),
        'computedFieldReferences':[],
    }
    if typ=='Date':field['dateFieldSettings']={'displayYear':True,'displayMonth':True,'displayDay':True,'displayTime':bool(spec.get('displayTime',True))}
    if typ=='Choice':
        options=spec.get('options',[])
        if not options:raise FieldCreationError('Choice requires options')
        field['choiceOptionSetting']={'type':'CHOICE_OPTIONS','choiceOptions':[{'id':i+1,'name':str(n)} for i,n in enumerate(options)]}
    elif typ=='User':field['choiceOptionSetting']={'type':'CHOICE_USERS','referenceFilters':[{'id':info['trackerId'],'filterStatusId':0}]}
    elif typ=='Reference':
        filters=spec.get('referenceFilters')
        if not isinstance(filters,list) or not filters:raise FieldCreationError('Reference requires validated referenceFilters')
        field['choiceOptionSetting']={'type':'CHOICE_WORK_CONFIG_ITEMS','referenceFilters':filters}
    candidate=copy.deepcopy(current)
    candidate['fields'].append(field)
    # Structural preservation: not a single pre-existing field may be edited.
    if candidate['fields'][:-1] != current['fields'] or candidate['basicInformation']!=current['basicInformation']:
        raise FieldCreationError('Unexpected configuration mutation')
    return candidate

def propose_and_apply_field(client:Any,tracker_id:int,spec:dict,out:Path,*,apply:bool=False,
                            get_path:str='/cb/api/v3/tracker/{tracker_id}/configuration',
                            post_path:str='/cb/api/v3/tracker/configuration')->dict:
    if tracker_id<=0:raise FieldCreationError('tracker_id must be positive')
    if apply:raise FieldCreationError('BLOCKED: type IDs, option IDs and reference allocation contract require server validation; preview only')
    before=client.request('GET',get_path.format(tracker_id=tracker_id))
    if before['basicInformation'].get('trackerId')!=tracker_id:
        raise FieldCreationError('Wrong tracker response')
    candidate=build_field_config(before,spec)
    backup=out/f'field_config_before_{tracker_id}.json'
    save(backup,before)
    save(out/f'field_config_candidate_{tracker_id}.json',candidate)
    if not apply:return {'status':'PREVIEW','existing_fields':len(before['fields']), 'new_fields':len(candidate['fields'])}
    # Concurrency guard: fail rather than overwriting another user's change.
    fresh=client.request('GET',get_path.format(tracker_id=tracker_id))
    if fresh!=before:raise FieldCreationError('Tracker Configuration changed since preview; aborting POST')
    # Codebeamer POST response shapes vary; verification is based on GET readback.
    client.request('POST',post_path,json=candidate)
    after=client.request('GET',get_path.format(tracker_id=tracker_id))
    original={f['referenceId']:f for f in before['fields']}
    latest={f['referenceId']:f for f in after.get('fields',[])}
    if not all(id_ in latest and latest[id_]==f for id_,f in original.items()):
        raise FieldCreationError('POST returned but a pre-existing field changed. Inspect backup immediately.')
    created=latest.get(spec['referenceId'])
    if not created or created.get('label')!=spec['label'] or created.get('typeId')!=FIELD_TYPE_IDS[spec['type']]:
        raise FieldCreationError('POST returned but the new field did not match; inspect GET result')
    save(out/f'field_config_after_{tracker_id}.json',after)
    return {'status':'READBACK_VERIFIED','referenceId':spec['referenceId'],'fieldCount':len(after['fields'])}
