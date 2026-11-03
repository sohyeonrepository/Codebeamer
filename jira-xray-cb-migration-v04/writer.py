"""Config-driven Codebeamer REST writer.

Writes are disabled unless explicitly invoked with --apply and a server-specific,
verified API adapter configuration. This module never calls private CB Java APIs.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
import requests

KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")

class AdapterError(Exception): pass


def _path(path: str, *, tracker_id: int | None = None, item_id: int | None = None) -> str:
    if not isinstance(path, str) or not path.startswith('/') or '://' in path:
        raise AdapterError('Endpoint must be a same-server relative path')
    for name, value in [('tracker_id', tracker_id), ('item_id', item_id)]:
        token = '{'+name+'}'
        if token in path:
            if not isinstance(value, int) or value <= 0:
                raise AdapterError(f'Missing {name} for endpoint')
            path = path.replace(token, str(value))
    if '{' in path or '}' in path:
        raise AdapterError('Unexpected endpoint placeholder')
    return path


def verify_adapter(cfg: dict) -> None:
    spec = cfg.get('cb_api', {})
    if not isinstance(spec, dict) or not spec.get('verified_against_3_0_0_7'):
        raise AdapterError('Set cb_api.verified_against_3_0_0_7=true only after Swagger schema confirmation')
    for key in ('create_item', 'update_item', 'read_item', 'set_reference', 'create_association'):
        route = spec.get(key)
        if not isinstance(route, dict) or route.get('method') not in ('GET', 'POST', 'PUT', 'PATCH') or not route.get('path'):
            raise AdapterError(f'Complete cb_api.{key} from real Swagger first')
        _path(route['path'], tracker_id=123, item_id=456)


def cb_call(client: Any, route: dict, *, payload: dict | None = None,
            tracker_id: int | None = None, item_id: int | None = None) -> Any:
    kwargs = {'json': payload} if payload is not None else {}
    return client.request(route['method'], _path(route['path'], tracker_id=tracker_id, item_id=item_id), **kwargs)


def item_id(result: Any, path: str = 'id') -> int:
    data = result
    for part in path.split('.'):
        if not isinstance(data, dict):
            raise AdapterError('CB response is missing configured item ID path')
        data = data.get(part)
    if not isinstance(data, int) or data <= 0:
        raise AdapterError('CB response has no valid integer item ID')
    return data


def render(value: Any, variables: dict) -> Any:
    """Template safely substitutes whole {{names}} with typed values; not arbitrary code."""
    if isinstance(value, str):
        token = re.fullmatch(r'\{\{([a-z_]+)\}\}', value)
        if token:
            name = token.group(1)
            if name not in variables:
                raise AdapterError(f'Unknown payload variable {name}')
            return variables[name]
        return value
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    return value


def source_field(issue: dict, name: str) -> Any:
    node = issue.get('fields', {})
    for section in name.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(section)
    return node


def prepare_field_values(issue: dict, mappings: dict) -> list:
    """Field-mapping output only. API representation is configured, never guessed."""
    out = []
    for jira_path, cb_field_id in mappings.items():
        if not isinstance(cb_field_id, int) or cb_field_id <= 0:
            raise AdapterError(f'Field map {jira_path}: CB field ID must be positive int')
        out.append({'fieldId': cb_field_id, 'value': source_field(issue, jira_path)})
    return out


def create_body(issue: dict, tracker_id: int, cfg: dict) -> dict:
    variables = {
        'tracker_id': tracker_id, 'jira_key': issue['key'],
        'summary': source_field(issue, 'summary') or issue['key'],
        'description': source_field(issue, 'description') or '',
        'mapped_fields': prepare_field_values(issue, cfg.get('field_map', {}).get(cfg['kind_by_key'][issue['key']], {})),
    }
    template = cfg['cb_api'].get('item_create_body')
    if not isinstance(template, dict):
        raise AdapterError('cb_api.item_create_body must be verified JSON object')
    if variables['mapped_fields'] and '{{mapped_fields}}' not in json.dumps(template):
        raise AdapterError('Selected fields are absent from item_create_body')
    return render(template, variables)


def process_items(issues: list[dict], cfg: dict, cb: Any, state_file: Path,
                  *, apply: bool) -> dict:
    from migrator import kind, save, read
    if apply:verify_adapter(cfg)
    state = read(state_file, {})
    attempts_path=state_file.with_suffix('.attempts.json')
    attempts=read(attempts_path,{})
    work = []
    for issue in issues:
        typ = kind(issue)
        if typ is None: continue
        tracker_id = cfg['cb_tracker_ids'].get(typ)
        if not isinstance(tracker_id, int) or tracker_id <= 0:
            raise AdapterError(f'Missing CB tracker ID for {typ}')
        cfg['kind_by_key'] = {**cfg.get('kind_by_key', {}), issue['key']: typ}
        if issue['key'] in state:
            if apply:
                actual=cb_call(cb,cfg['cb_api']['read_item'],item_id=state[issue['key']])
                if actual.get('id')!=state[issue['key']] or actual.get('tracker',{}).get('id')!=tracker_id:
                    raise AdapterError('Saved Item ID/Tracker readback mismatch')
            work.append({'jira_key': issue['key'], 'action': 'SKIP_ALREADY_MAPPED', 'cb_item_id': state[issue['key']]})
            continue
        body = create_body(issue, tracker_id, cfg)
        work.append({'jira_key': issue['key'], 'action': 'CREATE_ITEM', 'tracker_id': tracker_id, 'body': body})
        if apply:
            if issue['key'] in attempts:raise AdapterError('Uncertain prior Item POST; reconcile remotely before retry')
            attempts[issue['key']]={'status':'PENDING','tracker_id':tracker_id,'body':body}
            save(attempts_path,attempts)
            result = cb_call(cb, cfg['cb_api']['create_item'], payload=body, tracker_id=tracker_id)
            new_id = item_id(result, cfg['cb_api'].get('created_item_id_path', 'id'))
            attempts[issue['key']].update(id=new_id,status='READBACK_PENDING');save(attempts_path,attempts)
            actual=cb_call(cb,cfg['cb_api']['read_item'],item_id=new_id)
            if actual.get('id')!=new_id or actual.get('tracker',{}).get('id')!=tracker_id or any(actual.get(k)!=v for k,v in body.items()):
                raise AdapterError('Created Item payload/Tracker readback mismatch')
            attempts[issue['key']]['status']='VERIFIED';save(attempts_path,attempts)
            state[issue['key']] = new_id
            save(state_file, state)  # persistent checkpoint after every success
    return {'state': state, 'operations': work}


def reference_edges(issues: list[dict], cfg: dict) -> list[dict]:
    """Relationships are collected from explicit Xray API exports. Missing endpoints are not presumed complete."""
    edges = {}
    relationship_map = cfg.get('relation_field_map', {})
    for issue in issues:
        typ = cfg['kind_by_key'].get(issue['key'])
        rels = issue.get('_xray_relations', {})
        for relation, value in rels.items():
            mapping = relationship_map.get(typ, {}).get(relation)
            if not mapping: continue
            if not isinstance(mapping.get('cb_reference_field_id'), int):
                raise AdapterError('Reference mapping must specify CB reference field ID')
            for dest in value.get('keys', []):
                edge = (issue['key'], dest, mapping['cb_reference_field_id'])
                edges[edge] = {'source': issue['key'], 'target': dest,
                               'cb_reference_field_id': mapping['cb_reference_field_id'],
                               'relation_name': relation}
    return list(edges.values())


def relation_stage(issues: list[dict], cfg: dict, id_map: dict,
                   client: Any, *, apply: bool) -> list[dict]:
    """Group all Reference targets by (source item, field).

    Prevents the prior edge-by-edge PUT from replacing a multi-reference field
    with the last linked Test Case. A verified bulk payload schema is mandatory.
    """
    if apply:verify_adapter(cfg)
    from collections import defaultdict
    groups=defaultdict(set)
    for edge in reference_edges(issues,cfg):
        key=(edge['source'],edge['cb_reference_field_id'])
        groups[key].add(edge['target'])
    operations=[]
    template=cfg.get('cb_api',{}).get('reference_body_bulk')
    for (source,field_id),targets in sorted(groups.items()):
        a=id_map.get(source)
        missing=sorted(x for x in targets if not id_map.get(x))
        ids=sorted(int(id_map[x]) for x in targets if id_map.get(x))
        op={'source':source,'cb_reference_field_id':field_id,'targets':sorted(targets),
            'source_item_id':a,'target_item_ids':ids,'unresolved_keys':missing}
        if not a or missing:op['status']='UNRESOLVED_NOT_WRITTEN'
        elif template is None:op['status']='BLOCKED_BULK_REFERENCE_SCHEMA_NOT_VERIFIED'
        elif apply:
            route=cfg['cb_api'].get('read_reference')
            if not route or route.get('method')!='GET':raise AdapterError('Reference complete readback adapter missing')
            current=cb_call(client,route,item_id=a)
            if not isinstance(current,dict) or current.get('fieldId')!=field_id or type(current.get('multiple')) is not bool or not isinstance(current.get('values'),list):
                raise AdapterError('Reference adapter requires fieldId/multiple/complete values array')
            existing=current['values']
            if any(type(v) is not int or v<=0 for v in existing):raise AdapterError('Invalid existing reference IDs')
            merged=sorted(set(existing)|set(ids))
            if not current['multiple'] and len(merged)>1:raise AdapterError('Single Reference cannot hold multiple targets')
            body=render(template,{'source_item_id':a,'target_item_ids':merged,'field_id':field_id})
            if set(existing)!=set(merged):cb_call(client,cfg['cb_api']['set_reference'],payload=body,item_id=a)
            actual=cb_call(client,route,item_id=a)
            if actual.get('fieldId')!=field_id or actual.get('multiple')!=current['multiple'] or sorted(actual.get('values',[]))!=merged:raise AdapterError('Reference readback mismatch')
            op['status']='READBACK_VERIFIED'
        else:op['status']='PLANNED'
        operations.append(op)
    return operations

def build_history_bridge(issues: list[dict], id_map: dict) -> list[dict]:
    result=[]
    for issue in issues:
        records=sorted(issue.get('_history', []), key=lambda x:(x.get('created',''), str(x.get('id',''))))
        result.append({'jiraKey':issue['key'], 'codebeamerItemId':id_map.get(issue['key']),
                       'revisions':records,'comments':issue.get('_comments',[]),
                       'status':'JAVA_BRIDGE_PENDING',
                       'warning':'Never claim revisions are restored until Java write and GET readback match'})
    return result
