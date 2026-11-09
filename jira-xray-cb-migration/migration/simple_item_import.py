"""Minimal one-Issue Jira TEST -> Codebeamer Item write path.

Bypasses the legacy import/preservation pipeline. Never edits tracker configuration.
Only a selected existing Tracker and a manually prepared scalar Text Jira Key.
"""
import json
from pathlib import Path

from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier
from migration.jira_identity_lookup import normalized_jira_key_label, item_identity, lookup_by_jira_key
from migration.wizard import semantic_type
from urllib.parse import quote


def _identity_profile(cb, tracker):
    """Find the unique Jira Key Text/WikiText field using both read endpoints.

    A missing Configuration display label does not mean the field is absent.
    Never guess a Field ID or assume an unknown field model.
    """
    configuration=cb.configuration(tracker)
    configured=configuration.get("fields") if isinstance(configuration,dict) else None
    if not isinstance(configured,list):
        raise ApiError("Jira Key 확인 실패: Tracker Configuration fields 응답이 없습니다.")
    listed=cb.fields(tracker)
    if not isinstance(listed,list):
        raise ApiError("Jira Key 확인 실패: Tracker Field 목록 응답이 없습니다.")
    merged={}
    unnamed=0
    for collection,source in ((configured,"configuration"),(listed,"field-list")):
        for row in collection:
            if not isinstance(row,dict):
                continue
            fid=field_identifier(row)
            if type(fid) is not int or fid<=0:
                unnamed+=1
                continue
            merged.setdefault(fid,{})
            # Do not overwrite a populated field name/model with missing metadata.
            for k,v in row.items():
                if v is not None and v!="":
                    merged[fid][k]=v
            merged[fid]["_sources"]=sorted(set(merged[fid].get("_sources",[])+[source]))
    matches=[(fid,f) for fid,f in merged.items() if any(
        normalized_jira_key_label(f.get(k))=="jirakey" for k in ("label","name"))]
    if not matches:
        observed=[str(f.get("label") or f.get("name")) for f in merged.values()
                  if any("jira" in str(f.get(k) or "").casefold()
                         for k in ("label","name"))]
        raise ApiError("Jira Key 필드를 찾지 못했습니다. Tracker="+str(tracker)+
                       ", Configuration 필드="+str(len(configured))+
                       ", Field 목록="+str(len(listed))+
                       ", ID 미확인="+str(unnamed)+
                       ", Jira 이름 후보="+(", ".join(observed[:8]) if observed else "없음")+
                       ". CB Tracker에 단일 Text 'Jira Key' 필드를 사용하세요.")
    if len(matches)!=1:
        raise ApiError("Jira Key 이름의 실제 Field ID가 "+str(len(matches))+
                       "개입니다 ("+", ".join(str(i) for i,_ in matches)+
                       "). 중복 필드를 정리하세요.")
    field_id,field=matches[0]
    if not field.get("typeName") and not field.get("valueModel"):
        try:
            detail=cb.http.request("GET","/v3/trackers/"+segment(tracker)+
                                    "/fields/"+segment(field_id))
        except ApiError as exc:
            raise ApiError("Jira Key 필드 ID "+str(field_id)+
                           " 타입 상세 조회 실패: "+str(exc)) from None
        if not isinstance(detail,dict) or field_identifier(detail)!=field_id:
            raise ApiError("Jira Key 필드 ID "+str(field_id)+
                           " 상세 응답 ID가 일치하지 않습니다.")
        field.update(detail)
    kind,multi=semantic_type(field)
    model=str(field.get("valueModel") or "").split("<",1)[0]
    explicit_type=str(field.get("typeName") or "")
    if multi is True:
        raise ApiError("Jira Key 필드는 단일값이어야 합니다 (Field ID "+str(field_id)+").")
    typename=explicit_type.casefold()
    if model=="WikiTextFieldValue" or typename in ("wikitext","wikitextfield"):
        if model and model!="WikiTextFieldValue":
            raise ApiError("Jira Key WikiText 타입 정보 불일치: "+model)
        return {"field_id":field_id,"type":"WikiTextFieldValue","mode":"wiki"}
    if model=="TextFieldValue" or typename in ("text","textfield"):
        if model and model!="TextFieldValue":
            raise ApiError("Jira Key Text 타입 정보 불일치: "+model)
        return {"field_id":field_id,"type":"TextFieldValue","mode":"text"}
    raise ApiError("Jira Key 필드는 찾았지만 Text/WikiText 타입을 확인하지 못했습니다. "+
                   "Field ID "+str(field_id)+", typeName="+repr(explicit_type)+
                   ", valueModel="+repr(model))


def _field(cb,tracker):
    """Original UI preflight compatibility: verified field ID."""
    return _identity_profile(cb,tracker)["field_id"]

def _read_issue(wizard, scope):
    key = wizard.config["jira"].get("single_issue_key")
    if not key:
        raise ApiError("1건 실서버 테스트에서는 먼저 Jira Issue Key 하나를 선택해야 합니다.")
    issue = wizard.jira.http.request("GET", "/rest/api/2/issue/" + segment(key) +
                                    "?fields=summary,description,issuetype,project")
    if issue.get("key") != key:
        raise ApiError("조회한 Jira Issue Key가 선택한 Key와 다릅니다.")
    fields = issue.get("fields") or {}
    if str(fields.get("project", {}).get("key")) + ":" + str(fields.get("issuetype", {}).get("id")) != scope:
        raise ApiError("선택한 Jira Issue가 현재 Project/Issue Type과 일치하지 않습니다.")
    if not isinstance(fields.get("summary"), str) or not fields["summary"].strip():
        raise ApiError("Jira Summary가 비어 있습니다.")
    description = fields.get("description")
    if description is not None and not isinstance(description, str):
        raise ApiError("Jira Description이 텍스트가 아닙니다. 변환 없이 기록하지 않습니다.")
    return key, fields["summary"], description


def _journal(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()



def _supported_custom_field_values(entry, fields, source_values, jira_key_field, cb=None, progress=None):
    """Prepare requested scalar CB fields before POST, never drop selected data.

    The first-import contract currently covers Text/WikiText via actual
    Tracker field metadata. Complex values must be supported explicitly.
    """
    from clients.tracker_fields import normalize
    prepared=[]
    selected={key:row for key,row in entry.get("rows",{}).items() if row.get("selected",False)}
    by_id={field_identifier(f):normalize(f) for f in fields if isinstance(f,dict)}
    for source,row in selected.items():
        if source in ("summary","description"):
            continue
        value=source_values.get(source)
        if value is None:
            continue
        spec=row.get("spec") or {}
        fid=spec.get("field_id")
        if type(fid) is not int or fid not in by_id:
            raise ApiError("필드 "+source+": 대상 CB Field ID 미설정/미확인. 매핑을 지정하세요.")
        if fid==jira_key_field:
            raise ApiError("필드 "+source+": Jira Key 예약 필드와 대상 Field ID 충돌")
        if spec.get("selection_unverified") or spec.get("missing_definition"):
            raise ApiError("필드 "+source+": 대상 필드 정의/선택 미검증")
        actual=by_id[fid]
        model=str(actual.get("valueModel") or "").split("<",1)[0]
        type_name=str(actual.get("typeName") or "").casefold()
        if model=="ChoiceFieldValue" and actual.get("referenceType")=="ChoiceOptionReference":
            from migration.option_batch import choice_names, norm
            if actual.get("referenceType")!="ChoiceOptionReference" or not isinstance(actual.get("options"),list):
                raise ApiError("필드 "+source+": CB ChoiceOptionReference/options 상세가 없습니다.")
            mapping=spec.get("value_mapping") or {}
            source_names=choice_names(value,source)
            if not source_names:
                continue
            options=actual["options"]
            refs=[]
            for name in source_names:
                wanted=mapping.get(name)
                if wanted is None:
                    raise ApiError("필드 "+source+": Jira 옵션 '"+name+"'의 CB 대상 옵션 매핑이 없습니다.")
                from migration.option_batch import resolve_option
                try:
                    option=resolve_option(options,wanted)
                except ApiError as exc:
                    raise ApiError("필드 "+source+": Jira 옵션 '"+name+"' 매핑 확인 실패: "+str(exc)) from exc
                if option.get("type")!="ChoiceOptionReference":
                    raise ApiError("필드 "+source+": 대상 옵션 참조 타입 미확인")
                matches=[option]
                refs.append({"id":matches[0]["id"],"name":matches[0]["name"],
                             "type":"ChoiceOptionReference"})
            if actual.get("multipleValues") is False and len(refs)>1:
                raise ApiError("필드 "+source+": 단일 Choice 필드에 Jira 값이 여러 개입니다.")
            custom={"fieldId":fid,"type":"ChoiceFieldValue","values":refs}
        elif model=="ChoiceFieldValue" and actual.get("referenceType")=="UserReference":
            if cb is None:
                raise ApiError("User 매핑: CB 사용자 조회 클라이언트 없음")
            from migration.user_auto_mapping import resolve
            refs=resolve(cb,source,value,spec.get("user_mapping"),progress)
            if not refs:
                continue
            if actual.get("multipleValues") is False and len(refs)>1:
                raise ApiError("User 필드 "+source+": CB 단일 사용자 필드에 여러 사용자 입력 불가")
            custom={"fieldId":fid,"type":"ChoiceFieldValue","values":refs}
        elif model in ("TextFieldValue","WikiTextFieldValue"):
            if type_name and type_name not in ("text","wikitext","wikitextfield","wikitextfieldvalue"):
                raise ApiError("필드 "+source+": CB 타입 불일치: "+type_name)
            if not isinstance(value,str):
                raise ApiError("필드 "+source+": Jira 문자열이 아닌 값은 임의 변환하지 않음")
            custom={"fieldId":fid,"type":model,"value":value}
        else:
            raise ApiError("필드 "+source+": 현재 1차 Import 지원 타입(Text/WikiText/Choice) 아님: "+
                           (model or type_name or "미확인"))
        if any(row["fieldId"]==fid for row in prepared):
            raise ApiError("필드 "+source+": 선택된 필드끼리 CB Field ID 중복")
        prepared.append(custom)
    return prepared


def _read_selected_issue(wizard, scope, selected):
    key=wizard.config["jira"].get("single_issue_key")
    if not key:
        raise ApiError("1차 Import할 Jira Issue Key를 지정하세요.")
    requested=list(dict.fromkeys(["summary","description","issuetype","project"]+sorted(selected)))
    issue=wizard.jira.http.request("GET","/rest/api/2/issue/"+quote(key,safe="-_")+
                                   "?fields="+",".join(requested))
    if not isinstance(issue,dict) or issue.get("key")!=key:
        raise ApiError("Jira Issue 조회 결과 불일치")
    fields=issue.get("fields")
    if not isinstance(fields,dict):
        raise ApiError("Jira Issue fields 누락")
    actual_project=(fields.get("project") or {}).get("key")
    actual_type=fields.get("issuetype") or {}
    expected_scope=wizard.plan["scopes"].get(scope) or {}
    expected_project=expected_scope.get("project")
    expected_type=expected_scope.get("type") or {}
    # Compare the actual metadata, not only a string generated from the
    # scope key. In particular, Jira Issue Type IDs must not be guessed from
    # labels or confused with CB Tracker IDs.
    if actual_project!=expected_project or str(actual_type.get("id"))!=str(expected_type.get("id")):
        raise ApiError(
            "Jira Issue 프로젝트/유형 불일치 · Jira "+key+
            " 실제 프로젝트="+str(actual_project)+
            ", Issue Type="+str(actual_type.get("name"))+
            " (ID "+str(actual_type.get("id"))+")"+
            " · 선택 프로젝트="+str(expected_project)+
            ", Issue Type="+str(expected_type.get("name"))+
            " (ID "+str(expected_type.get("id"))+")"+
            " · Scope="+str(scope)+
            ". 다른 유형이라면 Tracker 매핑을 변경하세요. POST는 실행하지 않았습니다."
        )
    if not isinstance(fields.get("summary"),str) or not fields["summary"].strip():
        raise ApiError("Jira Summary 없음: Item 생성 불가")
    if fields.get("description") is not None and not isinstance(fields["description"],str):
        raise ApiError("Jira Description 형식 미지원")
    return key,fields

def run(wizard, *, progress=None):
    """Create ONE selected Jira Issue; reconcile an existing Jira Key without update."""
    log = progress or (lambda stage, detail: None)
    scopes = wizard.plan.get("scopes", {})
    if len(scopes) != 1:
        raise ApiError("첫 실서버 테스트는 Issue Type/Tracker 하나만 선택해야 합니다.")
    scope, entry = next(iter(scopes.items()))
    tracker = entry.get("tracker")
    if type(tracker) is not int or tracker <= 0:
        raise ApiError("기존 Codebeamer Tracker를 선택하세요. 신규 생성은 사용하지 않습니다.")
    selected = {name for name, row in entry.get("rows", {}).items() if row.get("selected", False)}
    if "summary" not in selected:
        raise ApiError("Summary → Name 필드를 반드시 선택하세요.")

    identity=_identity_profile(wizard.cb,tracker)
    field_id=identity["field_id"]
    log("기존 Jira Key", "CB Field ID "+str(field_id)+
        " · "+identity["type"]+" 재사용 (신규 생성 없음)")
    key, source_values = _read_selected_issue(wizard, scope, selected)
    summary, description = source_values["summary"],source_values.get("description")
    log("Jira 조회", key + " · 선택 필드 "+str(len(selected))+"개")
    cb_fields=wizard.cb.configuration(tracker).get("fields")
    if not isinstance(cb_fields,list):
        raise ApiError("Codebeamer Tracker 필드 설정 조회 실패")
    # Field detail can supply types omitted by Tracker configuration.
    cb_details=wizard.cb.fields(tracker)
    if not isinstance(cb_details,list):
        raise ApiError("Codebeamer Tracker 필드 상세 조회 실패")
    indexed={field_identifier(row):dict(row) for row in cb_fields if isinstance(row,dict)}
    for row in cb_details:
        if isinstance(row,dict):
            indexed.setdefault(field_identifier(row),{}).update(row)
    # Configuration and list APIs may omit the FieldValue discriminator.
    # Fetch selected actual field detail, not customField[n] or a guessed ID.
    for selected_field,row in entry.get("rows",{}).items():
        if not row.get("selected") or selected_field in ("summary","description"):
            continue
        if source_values.get(selected_field) is None:
            continue
        selected_id=(row.get("spec") or {}).get("field_id")
        if type(selected_id) is not int or selected_id not in indexed:
            raise ApiError("필드 "+selected_field+": 대상 실제 CB Field ID를 선택하세요.")
        source_kind=semantic_type(entry["analysis"]["fields"].get(selected_field,{}),True)[0]
        if (not indexed[selected_id].get("valueModel") or
                source_kind=="user" or
                str(indexed[selected_id].get("valueModel") or "").startswith("ChoiceFieldValue") and
                not indexed[selected_id].get("referenceType") or
                indexed[selected_id].get("referenceType")=="ChoiceOptionReference" and
                not isinstance(indexed[selected_id].get("options"),list)):
            try:
                detail=wizard.cb.http.request("GET",
                    "/v3/trackers/"+segment(tracker)+"/fields/"+segment(selected_id))
            except ApiError as exc:
                raise ApiError("필드 "+selected_field+" 상세 GET 실패: "+str(exc)) from None
            if not isinstance(detail,dict) or field_identifier(detail)!=selected_id:
                raise ApiError("필드 "+selected_field+": CB 상세 조회 ID 불일치")
            indexed[selected_id].update(detail)
    extras=_supported_custom_field_values(entry,list(indexed.values()),source_values,field_id,
                                          cb=wizard.cb,progress=log)
    log("필드 준비","추가 Text/WikiText/Choice/User "+str(len(extras))+"개 · 요청 전 검증 완료")
    journal = Path(wizard.state.root) / "migration-state.jsonl"
    log("중복 조회", "선택 Tracker의 기존 Jira Key 검색")
    matches = lookup_by_jira_key(wizard.cb, tracker, key, field_id)
    if matches:
        item_id = matches[0]["id"]
        log("중복 발견", key + " → CB " + str(item_id) + " · 신규 생성 생략")
        record = {"jira": key, "status": "SKIPPED", "cb": item_id, "tracker": tracker}
        _journal(journal, record)
        # A preexisting Item is not evidence the newly selected fields were
        # migrated. Do not silently count it as first-pass completion.
        if extras:
            log("기존 Item 검출", "추가 선택 필드는 UPDATE하지 않았습니다. 신규 생성 완료로 집계하지 않습니다.")
            return {"complete": False, "counts": {"created": 0, "skipped": 1,
                    "pending_update": 1},
                    "issues": [{"key": key, "status": "existing_update_required",
                                "target_id": item_id}],
                    "errors": [{"source": key, "error_code": "EXISTING_ITEM_NOT_UPDATED",
                                "reason": "기존 Item 존재; 신규 추가 필드 미반영. UPDATE 경로 별도 필요"}]}
        return {"complete": True, "counts": {"created": 0, "skipped": 1},
                "issues": [{"key": key, "status": "skipped", "target_id": item_id}],
                "errors": []}

    # A previous uncertain write cannot be blindly reissued. Reconciliation
    # above guarantees that an existing Jira Key will never be duplicated.
    if identity["mode"]=="wiki":
        from post_smoke_test import wiki_link
        identity_value=wiki_link(wizard.config["jira"]["url"],key)
    else:
        identity_value=key
    # CB's native Assigned to is an explicit TrackerItem property, not a
    # customFields entry. The live Field Detail identifies this via
    # trackerItemField=assignedTo. Never infer native behavior from ID=5.
    native_fields={}
    custom_extras=[]
    for value in extras:
        definition=indexed.get(value["fieldId"]) or {}
        native=definition.get("trackerItemField")
        if native=="assignedTo":
            if (definition.get("referenceType")!="UserReference" or
                    value.get("type")!="ChoiceFieldValue" or
                    not isinstance(value.get("values"),list)):
                raise ApiError("Assigned to 필드의 UserReference 쓰기 계약 불일치")
            if "assignedTo" in native_fields:
                raise ApiError("Assigned to 중복 매핑")
            native_fields["assignedTo"]=value
            log("User 필드 요청","Assigned to → Item.assignedTo · CB Field ID "+
                str(value["fieldId"])+" · User ID "+
                ",".join(str(ref["id"]) for ref in value["values"]))
        else:
            custom_extras.append(value)
    payload = {"name": summary,
               "customFields": [{"fieldId": field_id, "type": identity["type"],
                                 "value":identity_value}]+custom_extras,
               **native_fields}
    if "description" in selected and description is not None:
        payload["description"] = description
    if "summary" not in selected:
        raise ApiError("Item Name으로 필요한 Summary는 체크해야 합니다.")
    _journal(journal, {"jira": key, "status": "CREATE_ATTEMPT", "tracker": tracker})
    log("Item POST", "POST /v3/trackers/{trackerId}/items")
    try:
        created = wizard.cb.http.request("POST", "/v3/trackers/" + segment(tracker) + "/items", payload)
    except Exception as exc:
        _journal(journal, {"jira": key, "status": "RECONCILIATION_REQUIRED",
                           "tracker": tracker, "error_type": type(exc).__name__})
        raise

    item_id = created.get("id") if isinstance(created, dict) else None
    if type(item_id) is not int:
        _journal(journal, {"jira": key, "status": "RECONCILIATION_REQUIRED", "tracker": tracker})
        raise ApiError("POST 후 CB Item ID를 확인하지 못했습니다. 재실행 전 Jira Key 중복 조회가 필요합니다.")
    _journal(journal, {"jira": key, "status": "POST_RETURNED", "cb": item_id, "tracker": tracker})
    actual = wizard.cb.http.request("GET", "/v3/items/" + segment(item_id))
    observed_key=item_identity(actual, field_id)
    observed_name=actual.get("name")
    key_matches=observed_key==key
    name_matches=observed_name==summary
    if not key_matches or not name_matches:
        # The POST has already returned an Item ID. Do not retry a create
        # because readback differs: preserve the ID for reconciliation.
        _journal(journal, {"jira":key,"cb":item_id,"tracker":tracker,
                           "status":"POST_VERIFY_MISMATCH",
                           "jira_key_matches":key_matches,
                           "name_matches":name_matches})
        log("POST 후 검증 불일치","CB Item ID "+str(item_id)+
            " · Jira Key "+("일치" if key_matches else "불일치/누락")+
            " · Name "+("일치" if name_matches else "불일치/누락")+
            " · GET name type="+type(observed_name).__name__+
            " · expected name length="+str(len(summary))+
            " · observed name length="+
            (str(len(observed_name)) if isinstance(observed_name,str) else "n/a"))
        failure=ApiError("POST는 Item ID "+str(item_id)+"로 반환됐으나 저장 후 검증 실패: "+
                         ("Jira Key " if not key_matches else "")+
                         ("Name " if not name_matches else "")+
                         "불일치. 중복 생성 금지; 기존 Item의 실제 값을 확인하세요.")
        # Attach a strict, non-sensitive structure for JSON diagnostics.
        # Never record actual Jira Key, Summary or server-returned field values.
        failure.safe_verification={
            "phase":"post_create_readback",
            "cb_item_id":item_id,
            "cb_tracker_id":tracker,
            "jira_key_field_id":field_id,
            "jira_key_match":key_matches,
            "name_match":name_matches,
            "expected_name_length":len(summary),
            "observed_name_length":len(observed_name) if isinstance(observed_name,str) else None,
            "observed_name_type":type(observed_name).__name__,
            "outcome":"item_created_but_readback_mismatch",
            "retry_create_safe":False
        }
        raise failure
    if "description" in payload and actual.get("description") != description:
        raise ApiError("생성된 Item의 Description 확인 실패. CB Item ID " + str(item_id))
    actual_custom=actual.get("customFields")
    if custom_extras and not isinstance(actual_custom,list):
        raise ApiError("CB Item "+str(item_id)+" 생성 후 customFields GET 누락; 확인 필요")
    actual_by_id={v.get("fieldId"):v for v in (actual_custom or [])
                  if isinstance(v,dict)}

    def observed_ids(data):
        if isinstance(data,dict):
            refs=data.get("values")
        elif isinstance(data,list):
            refs=data
        else:
            return None
        if not isinstance(refs,list):
            return None
        if any(not isinstance(ref,dict) or type(ref.get("id")) is not int for ref in refs):
            return None
        return sorted(ref["id"] for ref in refs)

    missing=[]
    for requested in extras:
        fid=requested["fieldId"]
        definition=indexed.get(fid) or {}
        native=definition.get("trackerItemField")
        received=actual.get(native) if native=="assignedTo" else actual_by_id.get(fid)
        # A server may mirror a native field in customFields. Verify its
        # authoritative native property first and do not confuse a missing
        # customFields entry with lost data.
        if received is None and native=="assignedTo":
            received=actual_by_id.get(fid)
        if requested["type"]=="ChoiceFieldValue":
            expected_ids=observed_ids(requested)
            found_ids=observed_ids(received)
            if expected_ids is None or found_ids!=expected_ids:
                missing.append({"field_id":fid,"location":native or "customFields",
                                "expected_user_ids":expected_ids,
                                "observed_user_ids":found_ids,
                                "observed_shape":type(received).__name__})
                log("필드 불일치",str(fid)+" · "+
                    str(native or "customFields")+" · 요청 ID="+str(expected_ids)+
                    " · 실제 ID="+str(found_ids)+
                    " · 응답 형식="+type(received).__name__)
            else:
                log("필드 검증 성공",str(fid)+" · "+str(native or "customFields")+
                    " · 참조 ID="+str(found_ids))
        elif not isinstance(received,dict) or received.get("value")!=requested.get("value"):
            missing.append({"field_id":fid,"location":native or "customFields",
                            "observed_shape":type(received).__name__})
            log("필드 불일치",str(fid)+" · "+
                str(native or "customFields")+" · 값/응답 형식 불일치")
    if missing:
        _journal(journal, {"jira": key, "status": "RECONCILIATION_REQUIRED",
                           "cb": item_id, "tracker": tracker,
                           "missing_fields":missing})
        raise ApiError("CB Item "+str(item_id)+" 생성 후 추가 필드 불일치 "+
                       str(len(missing))+"건: "+
                       ", ".join(str(m["field_id"])+"/"+m["location"] for m in missing)+
                       ". 재POST하지 말고 서버에서 확인하세요.")
    record = {"jira": key, "status": "CREATED", "cb": item_id, "tracker": tracker}
    _journal(journal, record)
    log("Item 생성 성공", key + " → CB Item " + str(item_id))
    return {"complete": True, "counts": {"created": 1, "skipped": 0},
            "issues": [{"key": key, "status": "verified", "target_id": item_id}],
            "errors": []}
