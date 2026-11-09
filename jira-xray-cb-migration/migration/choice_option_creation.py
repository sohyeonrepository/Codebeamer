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



def _append_with_dedicated_api(cb, tracker, field_id, desired, missing, state_root, log):
    """Create missing Choice options using an exact server-advertised POST route.

    Do not fall back to unrelated option/filter/library endpoints or guess a
    payload. Any uncertain POST remains pending for explicit reconciliation.
    """
    import json
    import hashlib
    from mapping_helper import request_schema, option_payload
    from single_import import validate

    route="/trackers/{trackerId}/fields/{fieldId}/option"
    api=cb.spec.get("paths",{}).get(route,{}).get("post")
    if not isinstance(api,dict):
        raise ApiError("별도 Choice 옵션 POST API 계약을 찾지 못했습니다: "+route+
                       " · 요청 미실행")
    try:
        schema=request_schema(cb.spec,api)
        requests=[option_payload(cb.spec,schema,name) for name in missing]
        for body in requests:
            validate(cb.spec,body,schema)
    except (ApiError,KeyError,TypeError,ValueError) as exc:
        # The official v3 docs describe multi-Choice ITEM values, not the
        # request body of this separate option-creation endpoint. Preserve
        # precise contract evidence without guessing a name/value property.
        from mapping_helper import schema_object
        try:
            resolved=schema_object(cb.spec,schema) if "schema" in locals() else {}
        except (ApiError,KeyError,TypeError,ValueError):
            resolved={}
        evidence={
            "tracker_id":tracker,"field_id":field_id,
            "endpoint":route,"method":"POST",
            "request_schema_type":resolved.get("type"),
            "request_schema_properties":sorted(resolved.get("properties",{})),
            "request_schema_required":resolved.get("required",[]),
            "request_schema_items_type":(resolved.get("items") or {}).get("type") if isinstance(resolved.get("items"),dict) else None,
            "request_body_media_types":sorted((api.get("requestBody") or {}).get("content",{})),
            "operation_parameter_shapes":[{"name":p.get("name"),"in":p.get("in"),
                  "required":p.get("required"),"type":(p.get("schema") or {}).get("type")}
                  for p in api.get("parameters",[]) if isinstance(p,dict)],
            "error_type":type(exc).__name__,
            "stage":"schema_validation_before_post",
            "note":"No option POST executed. No user values or credentials included."
        }
        evidence_path=Path(state_root)/("choice-option-post-contract-"+
                                      str(tracker)+"-"+str(field_id)+".json")
        evidence_path.parent.mkdir(parents=True,exist_ok=True)
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str),
                                 encoding="utf-8")
        log("Choice 옵션 요청 형식 미확인",str(evidence_path)+
            " · schema.type="+str(evidence["request_schema_type"])+
            " · properties="+repr(evidence["request_schema_properties"]))
        raise ApiError("Choice 옵션 POST 요청 스키마 검증 실패 (쓰기 미실행): "+
                       str(exc)+". 전용 API 스키마 진단: "+str(evidence_path)) from None
    # Local state contains no credential or request payload. Existing
    # options are authoritative from field detail, not Tracker configuration.
    path=Path(state_root)/("choice-add-"+hashlib.sha256(
        (str(tracker)+":"+str(field_id)).encode()).hexdigest()[:20]+".json")
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        previous=json.loads(path.read_text(encoding="utf-8"))
        if previous.get("status")=="pending":
            # A pending write must be reconciled against the actual server.
            # Old receipts do not record the in-flight option name; never
            # assume absence proves failure or replay an unknown request.
            pending_name=previous.get("pending_name")
            current=cb.http.request("GET","/v3/trackers/"+segment(tracker)+
                                    "/fields/"+segment(field_id))
            if not isinstance(current,dict) or field_identifier(current)!=field_id:
                raise ApiError("이전 옵션 POST 결과가 미확정이고 CB Field Detail 조회에 실패했습니다. 재POST 금지.")
            options=current.get("options")
            if not isinstance(options,list):
                raise ApiError("이전 옵션 POST 결과 미확정 · CB 옵션 목록 확인 불가 · 재POST 금지.")
            matched=[o for o in options if isinstance(o,dict) and pending_name is not None
                     and norm(o.get("name"))==norm(pending_name) and type(o.get("id")) is int]
            if pending_name is not None and len(matched)==1:
                # The uncertain request is now verified as present. This does
                # not authorize resending any other unfinished POST.
                log("Choice 이전 POST 복구","Field "+str(field_id)+
                    " · 이전 옵션 ID "+str(matched[0]["id"])+" 서버 확인")
                previous["status"]="verified_partial"
                previous["created_names"]=list(dict.fromkeys(
                    (previous.get("created_names") or [])+[pending_name]))
                previous.pop("pending_name",None)
                path.write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding="utf-8")
            else:
                raise ApiError("이전 옵션 POST 결과 미확정 · Tracker "+str(tracker)+
                               " / Field "+str(field_id)+
                               (" / 요청 옵션 "+str(pending_name) if pending_name else
                                " / 구버전 기록에 요청 옵션명이 없음")+
                               ". 현재 CB Detail에서 유일한 Option ID를 확인하지 못했습니다. "+
                               "옵션이 없더라도 이전 POST를 자동 재전송하지 않습니다. "+
                               "서버의 옵션 생성 내역과 이전 POST 결과를 확인해 주세요.")
    def record(status, created_names, pending_name=None):
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps({"status":status,"tracker":tracker,
            "field_id":field_id,"created_names":created_names,
            "pending_name":pending_name if status=="pending" else None,
            "requested_count":len(missing),"route_template":route},
            ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(path)

    previous_write=cb.write
    previous_operation=cb.config.setdefault("operations",{}).get("choice_option_create")
    cb.config["operations"]["choice_option_create"]={"method":"POST","path":route}
    created_names=[]
    try:
        cb.write=True
        for name,body in zip(missing,requests):
            # Read immediately before every write to avoid duplicate options
            # if another client added the same name while the wizard was open.
            latest=cb.http.request("GET","/v3/trackers/"+segment(tracker)+
                                   "/fields/"+segment(field_id))
            if not isinstance(latest,dict) or field_identifier(latest)!=field_id:
                raise ApiError("옵션 POST 전 Field Detail 조회 실패; 요청 중단")
            if not diff_options(latest,[name]):
                log("Choice 옵션 중복방지","옵션이 이미 존재함 · 별도 POST 생략")
                created_names.append(name)
                continue
            record("pending",created_names,pending_name=name)
            log("Choice 옵션 POST","필드 "+str(field_id)+" · 누락된 옵션 1개 생성 요청")
            post_result=cb.call("choice_option_create",{"trackerId":tracker,"fieldId":field_id},body)
            # POST success is not proof of an Option ID: verify the server's
            # authoritative Field Detail. Keep diagnostic metadata only.
            after=cb.http.request("GET","/v3/trackers/"+segment(tracker)+
                                  "/fields/"+segment(field_id))
            if not isinstance(after,dict) or field_identifier(after)!=field_id:
                raise ApiError("옵션 POST 이후 Field Detail 확인 실패. 동일 요청 재실행 금지.")
            matching=[o for o in after.get("options",[]) if isinstance(o,dict)
                      and norm(o.get("name"))==norm(name)
                      and type(o.get("id")) is int]
            if len(matching)!=1:
                diagnostic={
                    "tracker_id":tracker,"field_id":field_id,
                    "stage":"option_post_readback",
                    "post_response_type":type(post_result).__name__,
                    "post_response_keys":sorted(post_result) if isinstance(post_result,dict) else [],
                    "field_detail_id_matches":field_identifier(after)==field_id,
                    "field_detail_options_type":type(after.get("options")).__name__,
                    "field_detail_option_count":len(after.get("options")) if isinstance(after.get("options"),list) else None,
                    "matching_name_count":sum(1 for o in after.get("options",[])
                        if isinstance(o,dict) and norm(o.get("name"))==norm(name))
                        if isinstance(after.get("options"),list) else None,
                    "matching_with_integer_id_count":len(matching),
                    "note":"Readback uncertain; original option POST must not be repeated automatically."
                }
                evidence=Path(state_root)/("choice-option-readback-"+str(tracker)+"-"+str(field_id)+".json")
                evidence.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding="utf-8")
                log("Choice 옵션 생성 결과 미확정","Field "+str(field_id)+
                    " · 옵션 ID 상세 재조회 불일치 · 진단: "+str(evidence))
                raise ApiError("옵션 POST는 실행됐으나 Field Detail의 옵션 이름/ID 검증에 실패했습니다. "+
                               "GET /v3/trackers/"+str(tracker)+"/fields/"+str(field_id)+
                               "의 options를 확인하세요. 중복 생성 방지를 위해 자동 재POST 금지. "+
                               "진단 파일: "+str(evidence))
            created_names.append(name)
            record("verified_partial",created_names)
            log("Choice 옵션 ID 확인","Field "+str(field_id)+
                " · Option ID "+str(matching[0]["id"]))
        final=cb.http.request("GET","/v3/trackers/"+segment(tracker)+
                              "/fields/"+segment(field_id))
        if not isinstance(final,dict) or field_identifier(final)!=field_id or diff_options(final,desired):
            raise ApiError("요청 옵션 전체 검증 실패. 생성 상태 확인 전 재실행 금지.")
        record("verified",created_names)
        log("Choice 옵션 생성 완료","Tracker "+str(tracker)+" · Field "+str(field_id)+
            " · 서버에서 옵션 "+str(len(missing))+"개 확인")
        return final
    finally:
        cb.write=previous_write
        if previous_operation is None:
            cb.config["operations"].pop("choice_option_create",None)
        else:
            cb.config["operations"]["choice_option_create"]=previous_operation


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
        # The server exposes a dedicated POST /trackers/{trackerId}/fields/{fieldId}/option.
        # Use only its exact OpenAPI request contract, not the Configuration
        # fallback which has no writable options property.
        if isinstance(cb.spec.get("paths",{}).get(
                "/trackers/{trackerId}/fields/{fieldId}/option",{}).get("post"),dict):
            log("Choice 옵션 전용 API","Tracker Configuration options 미지원 · 별도 POST 계약 검증")
            return _append_with_dedicated_api(
                cb,tracker,field_id,desired,missing,state_root,log)
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
