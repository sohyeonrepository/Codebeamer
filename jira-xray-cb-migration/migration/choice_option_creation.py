"""Append verified missing Choice options to an existing CB Tracker field.

Requires an explicit user approval, a configuration write operation already
verified against the target's OpenAPI, and server read-back. Never manufacture
Option IDs or rewrite unrelated field settings.
"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from clients.http import ApiError
from mapping_helper import field_identifier
from migration.option_batch import norm
from clients.jira_client import segment


def option_names(values):
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def diff_options(detail, desired):
    if not isinstance(detail,dict) or detail.get("referenceType")!="ChoiceOptionReference":
        raise ApiError("ChoiceOptionReference 필드가 아닙니다.")
    if not str(detail.get("valueModel") or "").startswith("ChoiceFieldValue"):
        raise ApiError("ChoiceFieldValue 계약을 확인하지 못했습니다.")
    options=detail.get("options")
    if not isinstance(options,list):
        raise ApiError("CB 실제 옵션 목록을 읽을 수 없습니다.")
    keys={}
    for row in options:
        if not isinstance(row,dict) or not isinstance(row.get("name"),str):
            raise ApiError("CB 옵션 형식을 확인할 수 없습니다.")
        key=norm(row["name"])
        if key in keys:
            raise ApiError("CB 동일 이름 옵션이 중복되었습니다: "+row["name"])
        keys[key]=row
    requested=option_names(desired)
    missing=[name for name in requested if norm(name) not in keys]
    return missing


def append_missing(cb, tracker, field_id, desired, state_root, approved=False, progress=None):
    if approved is not True:
        raise ApiError("옵션 추가는 별도 승인이 필요합니다.")
    log=progress or (lambda stage,detail:None)
    detail=cb.http.request("GET","/v3/trackers/"+segment(tracker)+"/fields/"+segment(field_id))
    if not isinstance(detail,dict) or field_identifier(detail)!=field_id:
        raise ApiError("대상 Choice Field ID 확인 실패")
    missing=diff_options(detail,desired)
    if not missing:
        log("Choice 옵션","이미 모든 옵션이 존재합니다.")
        return detail

    original=cb.configuration(tracker)
    rows=original.get("fields") if isinstance(original,dict) else None
    if not isinstance(rows,list):
        raise ApiError("Tracker Configuration 필드 목록 조회 실패")
    matches=[f for f in rows if isinstance(f,dict) and field_identifier(f)==field_id]
    if len(matches)!=1:
        raise ApiError("Tracker Configuration의 대상 필드를 유일하게 찾지 못했습니다.")
    target=matches[0]
    if not isinstance(target.get("options"),list):
        raise ApiError("Tracker Configuration 옵션 목록 형식 미확인: 쓰기 중단")
    existing=target["options"]
    existing_names={norm(o.get("name")) for o in existing if isinstance(o,dict)}
    if len(existing_names)!=len(existing):
        raise ApiError("Tracker Configuration 기존 옵션이 중복되거나 형식이 잘못되었습니다.")
    if {norm(o.get("name")) for o in detail["options"]}!=existing_names:
        raise ApiError("Tracker Configuration과 Field Detail의 옵션이 일치하지 않습니다.")
    if getattr(cb,"spec",None) is None:
        cb.discover()
    operation=cb.operation("tracker_configuration_update")
    if operation.get("method","").upper()!="POST":
        raise ApiError("서버에서 검증된 Tracker Configuration POST 계약이 없습니다.")

    # Preserve the full current configuration; only append new name-only
    # option definitions. The backend assigns authoritative option IDs.
    expected=deepcopy(original)
    original_options=next(f for f in expected["fields"] if field_identifier(f)==field_id)["options"]
    for name in missing:
        original_options.append({"name":name})

    journal_path=Path(state_root)/("choice-add-"+hashlib.sha256((str(tracker)+":"+str(field_id)).encode()).hexdigest()[:20]+".json")
    journal_path.parent.mkdir(parents=True,exist_ok=True)
    if journal_path.exists():
        prior=json.loads(journal_path.read_text(encoding="utf-8"))
        if prior.get("status")=="pending":
            raise ApiError("이전 옵션 추가가 미확정입니다. 서버 상태를 확인하세요. 자동 재요청 금지.")
    latest=cb.configuration(tracker)
    if latest!=original:
        raise ApiError("옵션 생성 직전 Tracker 설정이 변경되었습니다. 다시 조회하세요.")
    journal_path.write_text(json.dumps({"status":"pending","tracker":tracker,
        "field_id":field_id,"missing":missing},ensure_ascii=False,indent=2),encoding="utf-8")
    log("Choice 옵션 생성","필드 "+str(field_id)+" · 신규 "+str(len(missing))+"개")
    previous=cb.write
    try:
        cb.write=True
        cb.call("tracker_configuration_update",{"trackerId":tracker},expected)
    finally:
        cb.write=previous

    after=cb.http.request("GET","/v3/trackers/"+segment(tracker)+"/fields/"+segment(field_id))
    if not isinstance(after,dict) or field_identifier(after)!=field_id:
        raise ApiError("옵션 생성 후 상세 GET 검증 실패; 재요청하지 마세요.")
    remaining=diff_options(after,desired)
    if remaining:
        raise ApiError("옵션 추가 후 검증 불일치: "+", ".join(remaining)+"; 재요청하지 마세요.")
    after_configuration=cb.configuration(tracker)
    old_by_id={field_identifier(f):f for f in original["fields"] if isinstance(f,dict)}
    new_by_id={field_identifier(f):f for f in after_configuration.get("fields",[]) if isinstance(f,dict)}
    for fid,old in old_by_id.items():
        if fid!=field_id and new_by_id.get(fid)!=old:
            raise ApiError("옵션 추가 후 다른 필드 설정 변경 감지. 서버 확인이 필요합니다.")
    journal_path.write_text(json.dumps({"status":"verified","tracker":tracker,
        "field_id":field_id,"added":missing},ensure_ascii=False,indent=2),encoding="utf-8")
    log("Choice 옵션 검증","신규 "+str(len(missing))+"개 · 서버 재조회 확인")
    return after
