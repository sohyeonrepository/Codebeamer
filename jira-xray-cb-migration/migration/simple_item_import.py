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
    if not selected or not selected.issubset({"summary", "description"}):
        raise ApiError("첫 테스트에서는 Summary, Description 필드만 체크하세요. Jira Key는 자동으로 기록합니다.")

    field_id = _field(wizard.cb, tracker)
    log("기존 Jira Key", "CB Field ID " + str(field_id) + " 재사용 (신규 생성 없음)")
    key, summary, description = _read_issue(wizard, scope)
    log("Jira 조회", key + " · Summary/Description 확인")
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
               "customFields": [{"fieldId": field_id, "type": "TextFieldValue", "value": key}]}
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
    record = {"jira": key, "status": "CREATED", "cb": item_id, "tracker": tracker}
    _journal(journal, record)
    log("Item 생성 성공", key + " → CB Item " + str(item_id))
    return {"complete": True, "counts": {"created": 1, "skipped": 0},
            "issues": [{"key": key, "status": "verified", "target_id": item_id}],
            "errors": []}
