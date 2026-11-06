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
    config_options=target.get("options")
    log("Choice 옵션 설정 진단","CB Field ID "+str(field_id)+
        " · configuration.options="+("absent" if "options" not in target else
        type(config_options).__name__)+
        " · detail.options="+str(len(detail["options"])))
    if getattr(cb,"spec",None) is None:
        cb.discover()
    operation=cb.operation("tracker_configuration_update")
    if operation.get("method","").upper()!="POST" or operation.get("path")!="/v3/tracker/configuration":
        raise ApiError("서버에서 검증된 Tracker Configuration POST 계약이 없습니다.")

    # In CB 3.0 Field Detail may supply Choice options while Tracker
    # Configuration omits them. Only use the detail options if the target's
    # actual OpenAPI Configuration schema explicitly supports options[].
    from mapping_helper import request_schema,schema_object,prepare_configuration_permissions
    from single_import import validate
    api=cb.spec.get("paths",{}).get(operation["path"],{}).get("post")
    if not isinstance(api,dict):
        raise ApiError("Tracker Configuration POST OpenAPI 계약 없음")
    schema=request_schema(cb.spec,api)
    field_array=schema_object(cb.spec,schema.get("properties",{}).get("fields",{}))
    field_schema=schema_object(cb.spec,field_array.get("items",{}))
    option_schema=schema_object(cb.spec,field_schema.get("properties",{}).get("options",{}))
    if option_schema.get("type")!="array":
        # Distinguish a missing writable option definition from a broken
        # schema traversal. Enumerate documented candidate routes without
        # attempting to write an unsupported field into configuration.
        routes=[]
        for path,verbs in cb.spec.get("paths",{}).items():
            for method,operation_info in verbs.items():
                if (method.lower() in ("post","put","patch") and isinstance(operation_info,dict)
                    and any(token in (path+" "+str(operation_info.get("operationId") or "")).casefold()
                            for token in ("option","choice"))):
                    routes.append(method.upper()+" "+path)
        evidence={
            "tracker_id":tracker,"field_id":field_id,
            "choice_reference_type":detail.get("referenceType"),
            "configuration_option_presence":"options" in target,
            "configuration_option_type":type(config_options).__name__,
            "openapi_root_properties":sorted(schema.get("properties",{})),
            "openapi_field_properties":sorted(field_schema.get("properties",{})),
            "openapi_option_schema":option_schema,
            "candidate_option_write_routes":routes,
            "note":"Read-only diagnostics. No option create request was made."}
        evidence_path=Path(state_root)/("choice-contract-"+str(tracker)+"-"+str(field_id)+".json")
        evidence_path.parent.mkdir(parents=True,exist_ok=True)
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        log("Choice 옵션 계약 진단",str(evidence_path)+
            " · 후보 쓰기 경로 "+str(len(routes))+"개")
        raise ApiError("현재 서버 OpenAPI의 Tracker Configuration 필드 쓰기 스키마에 options 배열이 없습니다. "+
                       "다른 옵션 생성 API가 있는지 계약 진단 파일에서 확인하세요. "+
                       "생성 요청 미실행 · "+str(evidence_path))
    if isinstance(config_options,list):
        existing=deepcopy(config_options)
        existing_names={norm(o.get("name")) for o in existing if isinstance(o,dict)}
        if len(existing_names)!=len(existing):
            raise ApiError("Tracker Configuration 기존 옵션 형식 오류/중복")
        if {norm(o.get("name")) for o in detail["options"]}!=existing_names:
            raise ApiError("Tracker Configuration과 Field Detail 옵션 불일치")
    elif config_options is None:
        # Do NOT assume null implies no options; the read endpoint was the
        # authoritative source and may already include existing options.
        existing=deepcopy(detail["options"])
        log("Choice 옵션 복원","Configuration options 누락; Field Detail "+
            str(len(existing))+"개를 근거로 사용 (OpenAPI 검증 필수)")
    else:
        raise ApiError("Tracker Configuration options 형식 미지원: "+
                       type(config_options).__name__)

    expected=deepcopy(original)
    updated=next(f for f in expected["fields"] if field_identifier(f)==field_id)
    updated["options"]=existing+[{"name":name} for name in missing]
    expected=prepare_configuration_permissions(cb,expected)
    validate(cb.spec,expected,schema)
    log("Choice 옵션 사전검증","OpenAPI Configuration 요청 스키마 검증 성공 · 신규 "+
        str(len(missing))+"개")

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
