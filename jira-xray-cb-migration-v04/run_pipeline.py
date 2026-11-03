#!/usr/bin/env python3
"""Stage-based v0.2 pipeline over immutable Jira export.
By default generates plans; --apply-items requires a verified CB Swagger adapter.
"""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from migrator import Client, read, save
from writer import (verify_adapter, process_items, relation_stage, build_history_bridge,
                    AdapterError)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('stage', choices=['plan','items','relations','history-bridge'])
    p.add_argument('--config',default='config.json')
    p.add_argument('--output',default='output')
    p.add_argument('--apply-items',action='store_true')
    p.add_argument('--apply-relations',action='store_true')
    args=p.parse_args()
    directory=Path(args.output)
    cfg=read(Path(args.config),{})
    if not cfg: raise ValueError('Config missing')
    issues=read(directory/'source_issues.json',None)
    if not isinstance(issues,list): raise ValueError('Export source_issues.json first')
    from migrator import kind
    cfg['kind_by_key']={x['key']:kind(x) for x in issues}
    id_path=directory/'id_map.json'
    ids=read(id_path,{})
    if args.stage=='history-bridge':
        q=build_history_bridge(issues,ids)
        save(directory/'history_bridge_queue.json',q)
        print(f'Prepared {len(q)} Java history jobs (no history writes performed)')
        return
    if args.stage=='plan':
        print(f'Issues: {len(issues)}, mapped IDs: {len(ids)}; review migration_plan.json')
        return
    apply=(args.stage=='items' and args.apply_items) or (args.stage=='relations' and args.apply_relations)
    if apply: verify_adapter(cfg)
    cb=None
    if apply:
        cb=Client(os.environ['CB_URL'],os.environ['CB_USER'],os.environ['CB_TOKEN'])
    if args.stage=='items':
        result=process_items(issues,cfg,cb,id_path,apply=apply)
        # Do not include secrets; payloads can contain customer data, keep output local.
        save(directory/'item_operations.json',result['operations'])
        print(f'Item actions {len(result["operations"])}; apply={apply}; IDs={len(result["state"])}')
    else:
        actions=relation_stage(issues,cfg,ids,cb,apply=apply)
        save(directory/'reference_operations.json',actions)
        counts={s:sum(x['status']==s for x in actions) for s in set(x['status'] for x in actions)}
        print(json.dumps({'edges':len(actions),'statuses':counts,'apply':apply},indent=2))

if __name__=='__main__':
    main()
