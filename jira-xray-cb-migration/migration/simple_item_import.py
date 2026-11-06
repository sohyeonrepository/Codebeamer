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


def _field(cb, tracker):
    configuration = cb.configuration(tracker)
    fields = configuration.get("fields")
    if not isinstance(fields, list):
        raise ApiError("기존 Tracker Configuration의 fields 목록을 읽을 수 없습니다.")
    matches = [f for f in fields if isinstance(f, dict) and any(
        normalized_jira_key_label(f.get(k)) == "jirakey" for k in ("label", "name"))]
    if len(matches) != 1:
        raise ApiError("대상 Tracker에 단일 Text 'jira key' 필드가 정확히 1개 필요합니다. "
                       "발견: " + str(len(matches)) + ". UI에서 수동 생성하고 재시도하세요.")
    field = matches[0]
    field_id = field_identifier(field)
    # Tracker Configuration often omits the semantic Text type. Use the
    # existing Field list/detail for the SAME Field ID, not a guessed typeId.
    if type(field_id) is int and not field.get("typeName") and not field.get("valueModel"):
        listed = cb.fields(tracker)
        if not isinstance(listed, list):
            raise ApiError("기존 Jira Key의 필드 타입을 조회하지 못했습니다.")
        matching = [f for f in listed if isinstance(f, dict) and field_identifier(f) == field_id]
        if len(matching) != 1:
            raise ApiError("기존 Jira Key Field ID의 타입 상세 조회가 필요합니다.")
        field = dict(field, **matching[0])
    kind, multi = semantic_type(field)
    if type(field_id) is not int or kind != "text" or multi is True or (
            str(field.get("typeName", "")).casefold() == "wikitext"):
        raise ApiError("기존 Jira Key 필드는 Field ID가 있는 단일 Text 타입이어야 합니다.")
    return field_id


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



def _supported_custom_field_values(entry, fields, source_values, jira_key_field):
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
        if model not in ("TextFieldValue","WikiTextFieldValue"):
            raise ApiError("필드 "+source+": 현재 1차 Import 지원 타입(Text/WikiText) 아님: "+
                           (model or type_name or "미확인"))
        if type_name and type_name not in ("text","wikitext","wikitextfield","wikitextfieldvalue"):
            raise ApiError("필드 "+source+": CB 타입 불일치: "+type_name)
        if not isinstance(value,str):
            raise ApiError("필드 "+source+": Jira 문자열이 아닌 값은 임의 변환하지 않음")
        if any(row["fieldId"]==fid for row in prepared):
            raise ApiError("필드 "+source+": 선택된 필드끼리 CB Field ID 중복")
        prepared.append({"fieldId":fid,"type":model,"value":value})
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
    if str((fields.get("project") or {}).get("key"))+":"+str(
            (fields.get("issuetype") or {}).get("id"))!=scope:
        raise ApiError("선택 프로젝트/Issue Type과 Jira 이슈가 다릅니다.")
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

    field_id = _field(wizard.cb, tracker)
    log("기존 Jira Key", "CB Field ID " + str(field_id) + " 재사용 (신규 생성 없음)")
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
    extras=_supported_custom_field_values(entry,list(indexed.values()),source_values,field_id)
    log("필드 준비","추가 Text/WikiText "+str(len(extras))+"개 · 요청 전 검증 완료")
    journal = Path(wizard.state.root) / "migration-state.jsonl"
    log("중복 조회", "선택 Tracker의 기존 Jira Key 검색")
    matches = lookup_by_jira_key(wizard.cb, tracker, key, field_id)
    if matches:
        item_id = matches[0]["id"]
        log("중복 발견", key + " → CB " + str(item_id) + " · 신규 생성 생략")
        record = {"jira": key, "status": "SKIPPED", "cb": item_id, "tracker": tracker}
        _journal(journal, record)
        return {"complete": True, "counts": {"created": 0, "skipped": 1},
                "issues": [{"key": key, "status": "skipped", "target_id": item_id}],
                "errors": []}

    # A previous uncertain write cannot be blindly reissued. Reconciliation
    # above guarantees that an existing Jira Key will never be duplicated.
    payload = {"name": summary,
               "customFields": [{"fieldId": field_id, "type": "TextFieldValue", "value": key}]+extras}
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
    if item_identity(actual, field_id) != key or actual.get("name") != summary:
        raise ApiError("생성된 Item의 Jira Key 또는 Name이 예상 값과 다릅니다. CB Item ID " + str(item_id))
    if "description" in payload and actual.get("description") != description:
        raise ApiError("생성된 Item의 Description 확인 실패. CB Item ID " + str(item_id))
    actual_custom=actual.get("customFields")
    if extras and not isinstance(actual_custom,list):
        raise ApiError("CB Item "+str(item_id)+" 생성 후 customFields GET 누락; 확인 필요")
    actual_by_id={v.get("fieldId"):v.get("value") for v in (actual_custom or [])
                  if isinstance(v,dict)}
    missing=[v["fieldId"] for v in extras if actual_by_id.get(v["fieldId"])!=v["value"]]
    if missing:
        raise ApiError("CB Item "+str(item_id)+" 생성 후 추가 필드 불일치 "+
                       str(len(missing))+"건. 재시도 전 서버 확인 필요")
    record = {"jira": key, "status": "CREATED", "cb": item_id, "tracker": tracker}
    _journal(journal, record)
    log("Item 생성 성공", key + " → CB Item " + str(item_id))
    return {"complete": True, "counts": {"created": 1, "skipped": 0},
            "issues": [{"key": key, "status": "verified", "target_id": item_id}],
            "errors": []}
