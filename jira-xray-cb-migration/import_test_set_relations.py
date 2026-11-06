"""Apply Test Set -> Test Case membership with the actual CB Test Cases TableField.

Using the documented PUT /v3/items/{itemId}/fields/tables/{tableFieldId}:
{"rows":[[{"fieldId":col, "type":"ChoiceFieldValue",
             "values":[{"id":case_id,"type":"TrackerItemReference"}]}], ...]}

Fail closed if field model differs or an existing table has unrelated members.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from clients.http import ApiError
from post_smoke_test import append_event, make_http
from import_case_set_children import read_journal
from mapping_helper import field_identifier


def discover_test_cases_table(cb, tracker_id):
    fields = cb.request("GET",f"/v3/trackers/{tracker_id}/fields")
    if not isinstance(fields,list):
        raise ApiError("Tracker fields list unavailable")
    candidates=[]
    for ref in fields:
        if not isinstance(ref,dict) or type(field_identifier(ref)) is not int:
            raise ApiError("Invalid CB Tracker field listing")
        fid=field_identifier(ref)
        # Full detail is necessary: listing can contain FieldReference only.
        detail=cb.request("GET",f"/v3/trackers/{tracker_id}/fields/{fid}")
        if not isinstance(detail,dict) or detail.get("id")!=fid:
            raise ApiError("CB field detail ID mismatch")
        if detail.get("type")!="TableField" or detail.get("valueModel")!="TableFieldValue":
            continue
        if str(detail.get("name") or "").strip().casefold() not in (
            "test cases","tests","tests association with a test set"):
            continue
        columns=detail.get("columns")
        if not isinstance(columns,list):
            raise ApiError("Test Cases Table columns missing")
        matching=[col for col in columns if isinstance(col,dict) and
                  col.get("type")=="TrackerItemChoiceField" and
                  col.get("referenceType")=="TrackerItemReference" and
                  str(col.get("valueModel","")).startswith("ChoiceFieldValue")]
        if len(matching)!=1 or type(matching[0].get("id")) is not int:
            raise ApiError("Test Cases Table has no unique TrackerItemReference column")
        candidates.append((fid,matching[0]["id"],detail.get("name")))
    if len(candidates)!=1:
        raise ApiError("Expected a unique Codebeamer Test Cases table; found "+str(len(candidates)))
    return candidates[0]


def table_member_ids(item, table_id, col_id):
    if not isinstance(item,dict):
        raise ApiError("Codebeamer Item detail is not an object")
    choices=[v for v in item.get("customFields",[]) if
             isinstance(v,dict) and v.get("fieldId")==table_id]
    if len(choices)>1:
        raise ApiError("Multiple Test Cases tables in one Item")
    if not choices:
        return []
    if choices[0].get("type")!="TableFieldValue" or not isinstance(choices[0].get("values"),list):
        raise ApiError("Test Cases field response is not a TableFieldValue")
    ids=[]
    for row in choices[0]["values"]:
        if not isinstance(row,list):
            raise ApiError("Malformed Test Cases table row")
        refs=[v for v in row if isinstance(v,dict) and v.get("fieldId")==col_id]
        if len(refs)!=1 or refs[0].get("type")!="ChoiceFieldValue" or not isinstance(refs[0].get("values"),list):
            raise ApiError("Malformed Test Case reference cell")
        values=refs[0]["values"]
        if len(values)!=1 or type(values[0].get("id")) is not int or values[0].get("type")!="TrackerItemReference":
            raise ApiError("Test Case reference is not a single TrackerItemReference")
        ids.append(values[0]["id"])
    return ids


def build_table_update(ids,col_id):
    if len(ids)!=len(set(ids)):
        raise ApiError("Duplicate Test Case Item ID in a Test Set")
    return {"rows":[[{"fieldId":col_id,"type":"ChoiceFieldValue",
                       "values":[{"id":cid,"type":"TrackerItemReference"}]}]
                    for cid in ids]}


def main(argv=None):
    p=argparse.ArgumentParser(description="Write validated Test Set/Test Case table references")
    p.add_argument("--config",default="config.local.json")
    p.add_argument("--plan",default=".migration/test-case-set-batch/phase2-verified-id-plan.json")
    p.add_argument("--set-tracker-id",required=True,type=int)
    p.add_argument("--state-dir",default=".migration/test-case-set-batch")
    p.add_argument("--limit",type=int,default=1,help="Test Set count; 0 means all")
    p.add_argument("--prompt-auth",action="store_true")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--confirm-tracker-id",type=int)
    args=p.parse_args(argv)
    if args.set_tracker_id<=0 or args.limit<0:
        p.error("Invalid Tracker ID or limit")
    if args.execute and args.confirm_tracker_id!=args.set_tracker_id:
        p.error("--execute requires --confirm-tracker-id identical to target")
    data=json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    ready=data.get("ready")
    if not isinstance(ready,list):
        raise ApiError("Phase-2 plan contains no ready list")
    root=Path(args.state_dir)
    journal=root/("applied-test-set-relations-"+str(args.set_tracker_id)+".jsonl")
    events=read_journal(journal)
    cfg=json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    cb=make_http(cfg["codebeamer"],root/"cb-relations-requests.jsonl",
                 prompt_auth=args.prompt_auth)
    table_id,col_id,label=discover_test_cases_table(cb,args.set_tracker_id)
    grouped=defaultdict(list)
    for row in ready:
        if (not isinstance(row,dict) or
            type(row.get("test_set_cb_id")) is not int or
            type(row.get("test_case_cb_id")) is not int):
            raise ApiError("Malformed verified relation plan")
        if row["test_case_cb_id"] not in grouped[row["test_set_cb_id"]]:
            grouped[row["test_set_cb_id"]].append(row["test_case_cb_id"])
    applied=skipped=inspected=0
    for set_id,case_ids in grouped.items():
        if args.limit and inspected>=args.limit:
            break
        inspected+=1
        if len(case_ids)>1000:
            raise ApiError("Too many Test Cases in one Set; confirm server maxTestCasesInTestSetOnCreation")
        identity=str(set_id)
        previous=events.get(identity)
        if previous:
            if previous.get("status")=="VERIFIED":
                skipped+=1
                continue
            raise ApiError("Previous uncertain relation PUT on Test Set "+identity)
        current=cb.request("GET",f"/v3/items/{set_id}")
        if not isinstance(current,dict) or current.get("id")!=set_id or (
           current.get("tracker") or {}).get("id")!=args.set_tracker_id:
            raise ApiError("Target Test Set Item/Tracker does not match planned target")
        existing=table_member_ids(current,table_id,col_id)
        if existing:
            if existing==case_ids:
                skipped+=1
                continue
            raise ApiError("Test Set table already contains different members; overwrite refused")
        plan=build_table_update(case_ids,col_id)
        print(json.dumps({"set_id":set_id,"test_case_count":len(case_ids),
                          "table_field":label,"mode":"EXECUTE" if args.execute else "PREVIEW"},
                         ensure_ascii=False),flush=True)
        if not args.execute:
            continue
        append_event(journal,{"identity":identity,"status":"PUT_ATTEMPT",
                              "table_field_id":table_id})
        try:
            cb.request("PUT",f"/v3/items/{set_id}/fields/tables/{table_id}",plan)
        except Exception as exc:
            append_event(journal,{"identity":identity,"status":"PUT_UNCERTAIN_OR_FAILED",
                                  "error_type":type(exc).__name__})
            raise
        actual=cb.request("GET",f"/v3/items/{set_id}")
        if table_member_ids(actual,table_id,col_id)!=case_ids:
            raise ApiError("Test Set relation PUT returned, but readback does not match")
        event={"identity":identity,"status":"VERIFIED","case_ids":case_ids}
        append_event(journal,event)
        events[identity]=event
        applied+=1
    print(json.dumps({"status":"RELATION_TABLE_PHASE","applied_sets":applied,
                      "skipped_sets":skipped,"inspected_sets":inspected,
                      "no_history_replay":True},ensure_ascii=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
