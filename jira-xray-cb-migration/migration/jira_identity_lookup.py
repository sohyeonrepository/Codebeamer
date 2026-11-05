"""Verified Jira-key duplicate lookup based on the supplied Java migrator's
allTrackerItemRefs -> GET item -> getRvnsId pattern.

This adapter does not pretend "lookup" is a Codebeamer REST endpoint.
No match is trusted only after the full tracker enumeration succeeds.
"""
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier


def identity_field(cb, fields):
    """Find one visible actual field named Jira Key and a verified writable FieldValue."""
    from clients.tracker_fields import binding
    candidates = [f for f in fields if (f.get("label") or f.get("name") or "").strip().casefold()
                  == "jira key" and f.get("hidden") is not True and f.get("isHidden") is not True]
    if len(candidates) != 1:
        raise ApiError("JIRA_IDENTITY_FIELD_REQUIRED: 대상 Tracker에 표시되는 'Jira Key' 필드가 "
                       "정확히 한 개 있어야 합니다. 확인된 개수: " + str(len(candidates)))
    field = candidates[0]
    identifier = field_identifier(field)
    if type(identifier) is not int:
        raise ApiError("JIRA_IDENTITY_FIELD_UNVERIFIED: Jira Key 필드 ID를 확인할 수 없습니다.")
    contract = binding(cb.spec, field)
    if not contract or contract.get("value_property") not in ("value", "values"):
        raise ApiError("JIRA_IDENTITY_WRITE_UNVERIFIED: Jira Key FieldValue의 쓰기 타입을 "
                       "서버 Swagger로 확인하지 못했습니다. 원본 ID는 생략할 수 없습니다.")
    if contract["value_property"] != "value":
        raise ApiError("JIRA_IDENTITY_NOT_TEXT: Jira Key는 단일 Text FieldValue여야 합니다.")
    return {"field_id": identifier, "binding": contract, "payload_key": "cb_field_" + str(identifier)}


def item_identity(item, field_id):
    """Read exactly one FieldValue from a complete GET /items/{id} response."""
    if not isinstance(item, dict) or type(item.get("id")) is not int:
        raise ApiError("JIRA_LOOKUP_ITEM_INVALID: Item 상세 조회에서 정수 ID가 없습니다.")
    fields = item.get("customFields")
    if not isinstance(fields, list):
        # No silent assumption that missing customFields means an empty value.
        raise ApiError("JIRA_LOOKUP_FIELDS_UNVERIFIED: Item 상세 GET에서 customFields 전체를 "
                       "확인할 수 없습니다. 조회 결과를 '중복 없음'으로 처리하지 않습니다.")
    found = [f for f in fields if isinstance(f,dict) and f.get("fieldId")==field_id]
    if len(found)>1:
        raise ApiError("JIRA_LOOKUP_DUPLICATE_FIELD: 하나의 Item에 Jira Key 필드가 중복됩니다.")
    if not found:
        return None
    value = found[0].get("value")
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ApiError("JIRA_LOOKUP_NON_TEXT: Jira Key 필드의 응답이 문자열이 아닙니다.")
    return value


def lookup_by_jira_key(cb, tracker_id, jira_key, field_id, page_size=100):
    """Enumerate the whole Tracker, then inspect Item detail for exact identity."""
    if type(tracker_id) is not int or type(field_id) is not int or not isinstance(jira_key,str) or not jira_key:
        raise ApiError("JIRA_LOOKUP_INVALID_INPUT: Tracker/Field ID 또는 Jira Key가 잘못됐습니다.")
    matches, visited = [], set()
    base = "/v3/trackers/" + segment(tracker_id) + "/items"
    for page in range(1, 10002):
        cb.last_api_call = {"capability": "jira_identity_lookup", "phase": "list",
                            "method": "GET", "path_template": "/v3/trackers/{trackerId}/items",
                            "parameter_names": ["trackerId", "page", "pageSize"]}
        response = cb.http.request("GET",base+"?page="+str(page)+"&pageSize="+str(page_size))
        if not isinstance(response, dict):
            raise ApiError("JIRA_LOOKUP_PAGE_UNVERIFIED: 목록 응답이 객체가 아닙니다.")
        items = response.get("itemRefs")
        if items is None:
            items = response.get("items")
        if not isinstance(items, list):
            raise ApiError("JIRA_LOOKUP_PAGE_UNVERIFIED: 목록 응답의 itemRefs/items 배열을 확인할 수 없습니다.")
        total = response.get("total")
        if total is not None and (type(total) is not int or total<0):
            raise ApiError("JIRA_LOOKUP_TOTAL_INVALID: Item 목록 전체 건수를 확인할 수 없습니다.")
        if not items:
            if total is not None and len(visited)<total:
                raise ApiError("JIRA_LOOKUP_INCOMPLETE: 목록 조회가 전체 건수보다 먼저 종료됐습니다.")
            return matches
        for ref in items:
            item_id = ref.get("id") if isinstance(ref,dict) else ref
            if type(item_id) is not int:
                raise ApiError("JIRA_LOOKUP_REF_INVALID: Item 참조 ID가 정수가 아닙니다.")
            if item_id in visited:
                raise ApiError("JIRA_LOOKUP_DUPLICATE_PAGE: 페이지 경계에서 Item ID가 중복됐습니다.")
            visited.add(item_id)
            cb.last_api_call = {"capability":"jira_identity_lookup","phase":"detail",
                                "method":"GET","path_template":"/v3/items/{itemId}",
                                "parameter_names":["itemId"]}
            item = cb.call("item_get", {"itemId":item_id})
            if item.get("id")!=item_id:
                raise ApiError("JIRA_LOOKUP_ITEM_MISMATCH: GET Item 상세 ID가 참조 ID와 다릅니다.")
            if item_identity(item,field_id)==jira_key:
                matches.append({"id":item_id})
                if len(matches)>1:
                    raise ApiError("JIRA_LOOKUP_DUPLICATE: 같은 Jira Key를 가진 CB Item이 둘 이상 존재합니다.")
        if total is not None and len(visited)>=total:
            if len(visited)!=total:
                raise ApiError("JIRA_LOOKUP_COUNT_MISMATCH: 반환 목록 개수가 전체 건수와 다릅니다.")
            return matches
        if len(items)<page_size and total is None:
            # Without a confirmed total, request the next page until empty;
            # never silently regard a short page as the last page.
            continue
    raise ApiError("JIRA_LOOKUP_MAX_PAGES: 전체 페이지 조회를 완료하지 못했습니다.")
