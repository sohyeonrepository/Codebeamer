"""Additional working migration stages: Xray runs, CB test-run write, history bridge, auditing.

Safe by default: export/plan; write operations require explicit --apply.
Does not fabricate historic Codebeamer revision timestamps.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from migrator import Client, read, save, kind
from writer import AdapterError, cb_call, _path, item_id, render, reference_edges

KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]*-\d+$', re.I)
RESULTS = {'PASS':'PASSED','PASSED':'PASSED','FAIL':'FAILED','FAILED':'FAILED',
           'BLOCKED':'BLOCKED','TODO':None,'EXECUTING':None,'ABORTED':None,
           'NOT RUN':None,'NOT_EXECUTED':None,'SKIPPED':None,'SKIP':None,
           'SUCCESS':'PASSED','FAILURE':'FAILED','BLOCK':'BLOCKED'}


def run_status(record):
    raw=record.get('status')
    if isinstance(raw, dict): raw=raw.get('name') or raw.get('status')
    if raw is None: return None
    key=str(raw).strip().upper()
    if not key:return None
    if key not in RESULTS:raise AdapterError('Unsupported nonblank verdict: '+key)
    return RESULTS[key]


def run_key(r):
    return str(r.get('id') or r.get('testRunId') or '').strip()


def normalize_runstub(stub):
    if not isinstance(stub, dict): raise ValueError('Expected Xray run object')
    if not run_key(stub): raise ValueError('Xray run record has no id')
    return stub


def extract_xray_test_runs(jira, issues, output: Path, *, details=True):
    """Use documented Xray Server/DC GET test/{key}/testruns and testrun/{id}/.
    Abort an individual Test's export on any error, never say it is fully exported.
    """
    results=[]; errors=[]
    for issue in issues:
        if kind(issue)!='test_case': continue
        key=issue['key']
        if not KEY_RE.fullmatch(key):
            errors.append({'key':key,'error':'invalid key'});continue
        try:
            listing=jira.request('GET',f'/rest/raven/1.0/api/test/{quote(key)}/testruns')
            if not isinstance(listing,(list,dict)):raise ValueError('Unsupported Xray listing shape')
            if isinstance(listing,dict) and not any(k in listing for k in ('testRuns','results')):raise ValueError('Missing Xray results array')
            rows=listing if isinstance(listing,list) else listing.get('testRuns', listing.get('results'))
            if isinstance(listing,dict) and ((listing.get('total',len(rows))!=len(rows)) or listing.get('isLast') is False):raise ValueError('Xray pagination incomplete; export blocked')
            if not isinstance(rows,list):raise ValueError('Unsupported Xray test runs listing shape')
            for row in rows:
                row=normalize_runstub(row)
                runid=run_key(row)
                if not runid.isdigit():raise ValueError(f'Non-numeric Xray test run id: {runid}')
                try:
                    payload=jira.request('GET',f'/rest/raven/1.0/api/testrun/{runid}/') if details else row
                    results.append({'test_key':key,'xray_run_id':runid,'data':payload,
                                    'status':run_status(payload if isinstance(payload,dict) else row)})
                except Exception as err:
                    errors.append({'key':key,'run_id':runid,'error':str(err)})
        except Exception as err:
            errors.append({'key':key,'error':str(err)})
    save(output/'xray_test_runs.json',results)
    save(output/'xray_test_run_errors.json',errors)
    return {'runs':len(results),'errors':len(errors)}


def test_execution_key(run):
    d=run.get('data',{})
    for key in ('testExecKey','testExecutionKey','testExecIssueKey'):
        v=d.get(key)
        if isinstance(v,str) and KEY_RE.fullmatch(v):return v
    node=d.get('testExec') or d.get('testExecution')
    if isinstance(node,dict):return node.get('key')
    return None


def build_run_groups(runs, id_map):
    """Group by Xray Test Execution (analogous to Kefico sessionID grouping).

    Preserve all source records; unsupported nonblank results are blocked.
    Skipped/blank verdicts remain linked but are not assigned a CB result.
    """
    groups=collections.defaultdict(list); blocked=[]
    permitted={'PASS','PASSED','SUCCESS','FAIL','FAILED','FAILURE','BLOCK','BLOCKED',
               'TODO','EXECUTING','ABORTED','NOT RUN','NOT_EXECUTED','SKIPPED','SKIP',''}
    for row in runs:
        e=test_execution_key(row)
        if not e or e not in id_map or row['test_key'] not in id_map:
            blocked.append({'xray_run_id':row['xray_run_id'],'reason':'MISSING_EXECUTION_OR_CASE_MAPPING',
                            'execution_key':e,'test_key':row['test_key']});continue
        d=row.get('data',{})
        raw=d.get('status')
        if isinstance(raw,dict):raw=raw.get('name') or raw.get('status')
        raw=str(raw or '').strip().upper()
        if raw not in permitted:
            blocked.append({'xray_run_id':row['xray_run_id'],'reason':'UNSUPPORTED_VERDICT',
                            'verdict':raw,'execution_key':e,'test_key':row['test_key']});continue
        groups[e].append(row)
    return dict(groups), blocked


def _latest_run(row):
    d=row.get('data',{})
    # Prefer explicit Xray update time; retain stable tiebreaker.
    return (str(d.get('modifiedDate') or d.get('finishedOn') or d.get('updated') or ''),
            str(row.get('xray_run_id') or ''))


def test_run_operations(runs,id_map,state):
    groups,blocked=build_run_groups(runs,id_map)
    result=[]; superseded=[]
    for e,rows in sorted(groups.items()):
        by_case=collections.defaultdict(list)
        for r in rows:by_case[r['test_key']].append(r)
        winners=[]
        for case,versions in sorted(by_case.items()):
            versions=sorted(versions,key=_latest_run,reverse=True)
            winners.append(versions[0])
            for old in versions[1:]:
                superseded.append({'xray_run_id':old['xray_run_id'],
                                   'reason':'SUPERSEDED_BY_LATEST_RUN',
                                   'selected_xray_run_id':versions[0]['xray_run_id'],
                                   'execution_key':e,'test_key':case})
        result.append({'execution_key':e,'cb_execution_item_id':id_map[e],
                       'cb_test_case_ids':[id_map[r['test_key']] for r in winners],
                       'xray_runs':winners,'superseded':[x for x in superseded if x['execution_key']==e],
                       'cb_test_set_run_id':state.get(e),
                       'status':'ALREADY_CREATED' if e in state else 'PENDING'})
    return result,blocked+superseded


def cb_path(prefix, suffix):
    # User config sets complete CB v1 REST prefix, e.g. /cb/rest, not /cb/api.
    if not prefix.startswith('/') or '://' in prefix or '{' in prefix:return None
    return prefix.rstrip('/')+suffix


def apply_test_runs(cb, runs, id_map, output: Path, cfg:dict, *, apply=False):
    if apply:
        save(output/'test_run_blocked.json',{'status':'BLOCKED','reason':'3.0.0.7 Run create/result/readback, child attachments and full execution history contracts unverified'})
        raise AdapterError('BLOCKED: Test Run lifecycle contract unverified; no request sent')
    state_path=output/'test_run_id_map.json'
    state=read(state_path,{})
    operations, blocked=test_run_operations(runs,id_map,state)
    prefix=cfg.get('cb_v1_prefix','')
    if apply and not prefix:
        raise AdapterError('Set cb_v1_prefix to actual CB REST v1 root (NOT /api/v3)')
    for operation in operations:
        e=operation['execution_key']
        if operation['cb_test_set_run_id'] is None and apply:
            path=cb_path(prefix,'/testRun')
            params=[('testCaseIds',int(x)) for x in operation['cb_test_case_ids']]
            # Empty JSON body is explicitly documented by PTC for this endpoint.
            # An ambiguity after an HTTP timeout is not blindly retried.
            result=cb.request('POST',path,params=params,json={})
            run_id=item_id(result,'id') if isinstance(result,dict) and isinstance(result.get('id'),int) else None
            if run_id is None:
                uri=result.get('uri','') if isinstance(result,dict) else ''
                match=re.fullmatch(r'/item/(\d+)',uri)
                if not match:raise AdapterError(f'Cannot parse CB test run id for {e}: no id/uri')
                run_id=int(match.group(1))
            state[e]=run_id
            save(state_path,state)
            operation['cb_test_set_run_id']=run_id
        if operation['cb_test_set_run_id'] and apply:
            payload={}
            for row in operation['xray_runs']:
                itemid=id_map[row['test_key']]
                data=row['data']
                if row.get('status') is not None:
                    payload[str(itemid)]={'success':row['status']}
                    comment=data.get('comment') or data.get('commentText') or data.get('annotation')
                    if comment:payload[str(itemid)]['conclusion']=str(comment)
                else:
                    operation.setdefault('unwritten_verdicts',[]).append({
                        'xray_run_id':row['xray_run_id'],'test_key':row['test_key'],
                        'source_status':data.get('status'),'reason':'NOT_RUN_OR_SKIPPED'})
            if payload:
                cb.request('POST',cb_path(prefix,f"/testRun/{operation['cb_test_set_run_id']}/result"),json=payload)
                operation['status']='RESULT_REQUEST_SENT_VERIFY_READBACK'
            else:
                operation['status']='LINKS_CREATED_NO_RESULT_WRITE'
        elif operation['cb_test_set_run_id']:
            operation['status']='RESULT_PENDING'
    save(output/'test_run_operations.json',operations)
    save(output/'test_run_blocked.json',blocked)
    return {'planned_groups':len(operations),'blocked_records':len(blocked),
            'written_groups':sum(x['status']=='RESULT_REQUEST_SENT_VERIFY_READBACK' for x in operations)}


def execute_java_history_bridge(queue, config, output:Path, *, apply=False):
    """External Java writer contract: NDJSON input -> NDJSON result, one acknowledgment per issue.
    Java writer must use licensed/supported deployment. Native history is never faked.
    """
    input_path=output/'history_bridge_queue.json'
    save(input_path,queue)
    if not apply:
        return {'exported':len(queue),'written':0,'mode':'JAVA_WRITER_INPUT_ONLY'}
    raise AdapterError('BLOCKED: Native History Writer and independent revision readback are not implemented; queue preserved')
    argv=config.get('java_history_command')
    if not isinstance(argv,list) or not argv or any(not isinstance(s,str) for s in argv):
        raise AdapterError('Configure java_history_command as argv array of validated Java history writer')
    # Command must use {input} and {output} exact args; no shell execution.
    if not any('{input}' in arg for arg in argv) or not any('{output}' in arg for arg in argv):
        raise AdapterError('Java command must accept {input} and {output} placeholders')
    report=output/'java_history_result.json'
    args=[s.replace('{input}',str(input_path.resolve())).replace('{output}',str(report.resolve())) for s in argv]
    subprocess.run(args,check=True,timeout=3600)
    acknowledgments=read(report,None)
    if not isinstance(acknowledgments,list):raise AdapterError('History Java writer did not return a valid result array')
    expected={x['jiraKey'] for x in queue}
    completed={x.get('jiraKey') for x in acknowledgments if x.get('native_history_verified') is True}
    return {'exported':len(queue),'written_and_verified':len(completed),'unverified':sorted(expected-completed)}


def linked_issue_edges(issues):
    """Capture Jira Linked Issues separately; caller maps them to CB Related association."""
    edges={}
    for issue in issues:
        for link in issue.get('fields',{}).get('issuelinks',[]):
            if ('inwardIssue' in link)==('outwardIssue' in link):
                raise AdapterError('Link must have exactly one direction')
            inward='inwardIssue' in link
            other=link['inwardIssue' if inward else 'outwardIssue']['key']
            a,b=(other,issue['key']) if inward else (issue['key'],other)
            identity=str(link['id'])
            row={'source':a,'target':b,'jira_link_ids':[identity],'jira_link_type':link.get('type',{})}
            if identity in edges and edges[identity]!=row:raise AdapterError('Conflicting Jira Link ID')
            edges[identity]=row
    return list(edges.values())


def audit(issues, id_map, runs, out:Path):
    keys={i['key'] for i in issues}
    missing=sorted(keys-set(id_map))
    refs=[]
    for i in issues:
        for rel,entry in i.get('_xray_relations',{}).items():
            for dst in entry.get('keys',[]):
                refs.append({'source':i['key'],'target':dst,'type':rel,'ready':i['key'] in id_map and dst in id_map})
    report={'source_issues':len(keys),'mapped_issues':len(keys & set(id_map)),
            'unmapped_issue_keys':missing,'source_xray_relations':len(refs),
            'mappable_xray_relations':sum(x['ready'] for x in refs),
            'source_xray_runs':len(runs),'native_history_verified':False,
            'result_verification':'REQUIRES_TARGET_READBACK'}
    save(out/'audit_report.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['xray-runs','test-runs','history-java','audit','linked-issues'])
    p.add_argument('--config',default='config.json')
    p.add_argument('--output',default='output')
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    cfg=read(Path(args.config),{})
    out=Path(args.output)
    issues=read(out/'source_issues.json',[])
    id_map=read(out/'id_map.json',{})
    runs=read(out/'xray_test_runs.json',[])
    if args.stage=='xray-runs':
        jira=Client(os.environ['JIRA_URL'],os.environ['JIRA_USER'],os.environ['JIRA_TOKEN'])
        result=extract_xray_test_runs(jira,issues,out)
    elif args.stage=='test-runs':
        cb=Client(os.environ['CB_URL'],os.environ['CB_USER'],os.environ['CB_TOKEN']) if args.apply else None
        result=apply_test_runs(cb,runs,id_map,out,cfg,apply=args.apply)
    elif args.stage=='history-java':
        from writer import build_history_bridge
        result=execute_java_history_bridge(build_history_bridge(issues,id_map),cfg,out,apply=args.apply)
    elif args.stage=='linked-issues':
        links=linked_issue_edges(issues)
        save(out/'linked_issue_related_plan.json',links)
        result={'planned_related_associations':len(links),'written':0}
    else:result=audit(issues,id_map,runs,out)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())


def association_type_id(types, name='related'):
    for t in types:
        if str(t.get('name','')).casefold()==name.casefold():
            if isinstance(t.get('id'),int):return t['id']
    raise AdapterError(f'Association type {name!r} unavailable in target Codebeamer')


def apply_related_associations(cb, issues, id_map, output:Path, *, apply=False, api_prefix='/cb/api'):
    """Codebeamer v3 documented POST /v3/associations; type discovered on target.
    Save exact pair checkpoint; do not create duplicate edges on rerun.
    """
    if apply:
        save(output/'related_association_blocked.json',{'status':'BLOCKED','reason':'Association create/lookup/readback contract unverified','source':issues})
        raise AdapterError('BLOCKED: Association reconciliation/readback contract unverified; no request sent')
    edges=linked_issue_edges(issues)
    state_path=output/'related_association_state.json'
    done=read(state_path,{})
    operations=[]
    type_id=None
    if apply:
        types=cb.request('GET',api_prefix.rstrip('/')+'/v3/associations/types')
        type_id=association_type_id(types,'related')
    for edge in edges:
        a,b=edge['source'],edge['target']
        key=f'{a}|{b}'
        if key in done:status='ALREADY_SENT'
        elif a not in id_map or b not in id_map:status='UNMAPPED_ENDPOINT'
        elif not apply:status='PLANNED'
        else:
            body={
                'name':f'Jira linked issue {a} / {b}',
                'from':{'id':int(id_map[a]),'type':'TrackerItemReference'},
                'to':{'id':int(id_map[b]),'type':'TrackerItemReference'},
                'type':{'id':type_id},
                'propagatingSuspects':False,
                'biDirectionalPropagation':False,
                'propagatingDependencies':False,
            }
            response=cb.request('POST',api_prefix.rstrip('/')+'/v3/associations',json=body)
            done[key]={'result':response,'status':'REQUEST_SENT_NEEDS_READBACK'}
            save(state_path,done)
            status='REQUEST_SENT_NEEDS_READBACK'
        operations.append({**edge,'status':status})
    save(output/'related_association_operations.json',operations)
    return {'total':len(operations),'sent':sum(x['status']=='REQUEST_SENT_NEEDS_READBACK' for x in operations),
            'unmapped':sum(x['status']=='UNMAPPED_ENDPOINT' for x in operations)}
