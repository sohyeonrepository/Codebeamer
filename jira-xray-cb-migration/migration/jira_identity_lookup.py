"""Verified Jira-key duplicate lookup based on the supplied Java migrator's
allTrackerItemRefs -> GET item -> getRvnsId pattern.

This adapter does not pretend "lookup" is a Codebeamer REST endpoint.
No match is trusted only after the full tracker enumeration succeeds.
"""
from clients.http import ApiError
from clients.jira_client import segment
from mapping_helper import field_identifier



def text_creation_profile(cb, tracker_id, fields=None):
    """Build a Text profile from *this server's* actual Tracker configuration.

    The embedded Jira Connector creates fields internally, so we use its
    reuse/clone principle, NOT its private Java method signatures.  Never
    fabricate a typeId, field permission, or write discriminator.
    """
    from copy import deepcopy
    from clients.tracker_fields import binding
    from clients.codebeamer_3007 import configuration_field_contract
    from mapping_helper import field_identifier, schema_object, creation_templates
    from migration.wizard import target_fields, semantic_type
    if fields is None:
        fields = target_fields(cb, tracker_id)
    configuration = cb.configuration(tracker_id)
    config_fields = configuration.get("fields")
    if not isinstance(config_fields, list):
        raise ApiError("JIRA_IDENTITY_TEMPLATE_CONFIG_MISSING: Tracker 필드 설정 전체를 읽지 못했습니다.")
    by_id = {str(field_identifier(f)):f for f in fields}
    candidates = []
    diagnostics = []
    for original in config_fields:
        observed = dict(original, **by_id.get(str(field_identifier(original)), {}))
        kind, multiple = semantic_type(observed)
        if kind != "text" or multiple is True or str(observed.get("typeName", "")).casefold() in ("wikitext",):
            continue
        if type(original.get("typeId")) is not int or not isinstance(original.get("permission"),dict):
            diagnostics.append("Text 템플릿의 typeId/permission 누락")
            continue
        if original.get("reference") is not None:
            continue
        # A concrete sample field must supply the actual FieldValue model.
        model = observed.get("valueModel")
        if not isinstance(model,str) or not model:
            diagnostics.append("Text FieldValue 모델 미확인")
            continue
        value_binding = binding(cb.spec, observed)
        if not value_binding:
            # The connector's actual TextFieldValue class can be verified from
            # OpenAPI when the legacy field-detail response omits the enum.
            definitions = cb.spec.get("components",{}).get("schemas",
                          cb.spec.get("definitions",{}))
            base = model.split("<",1)[0]
            schema = definitions.get(base)
            if base != "TextFieldValue" or not isinstance(schema,dict):
                diagnostics.append("Text FieldValue 타입 정의 미확인")
                continue
            resolved = schema_object(cb.spec,schema)
            properties = resolved.get("properties",{})
            value_def = schema_object(cb.spec,properties.get("value",{}))
            if value_def.get("type") != "string" or properties.get("value",{}).get("readOnly") is True:
                diagnostics.append("Text FieldValue.value가 쓰기 가능한 문자열이 아님")
                continue
            discriminator = schema_object(cb.spec,properties.get("type",{}))
            if discriminator.get("enum") not in (["TextFieldValue"],) and discriminator.get("const") != "TextFieldValue":
                diagnostics.append("TextFieldValue discriminator 미확인")
                continue
            value_binding = {"fieldId":field_identifier(observed),"type":"TextFieldValue",
                             "value_property":"value"}
        if value_binding.get("value_property") != "value":
            diagnostics.append("Text 필드는 단일 value가 필요")
            continue
        template = next((t for t in creation_templates(configuration,fields)
                         if t["definition"]["typeId"] == original["typeId"]), None)
        if not template:
            diagnostics.append("Text 생성 정의를 구성할 수 없음")
            continue
        definition = deepcopy(template["definition"])
        definition.update(label="Jira Key", mandatory=False, multipleSelection=False)
        try:
            configuration_field_contract(cb,definition,tracker_id)
        except ApiError as exc:
            diagnostics.append("Text 필드 Configuration 계약 불일치: " + str(exc)[:180])
            continue
        fresh_binding = deepcopy(value_binding)
        fresh_binding.pop("fieldId",None)
        candidates.append({"type_name":"Text","definition":template["definition"],
                           "binding":fresh_binding,"creation_method":"configuration",
                           "source_tracker_id":tracker_id,"definition_contract":"verified",
                           "value_contract":"verified"})
    if not candidates:
        raise ApiError("JIRA_IDENTITY_CREATE_UNVERIFIED: Jira Key 신규 필드용 Text 템플릿을 "
                       "실제 Tracker에서 검증할 수 없습니다. " +
                       "; ".join(dict.fromkeys(diagnostics[:4])))
    types = {c["definition"]["typeId"] for c in candidates}
    if len(types) != 1:
        raise ApiError("JIRA_IDENTITY_TEXT_AMBIGUOUS: 복수 Text typeId가 있어 임의 생성하지 않습니다.")
    return candidates[0]


def identity_field(cb, fields, verified_template_binding=None):
    """Find one visible actual field named Jira Key and a verified writable FieldValue."""
    from clients.tracker_fields import binding
    candidates = [f for f in fields if (f.get("label") or f.get("name") or "").strip().casefold()
                  == "jira key" and f.get("hidden") is not True and f.get("isHidden") is not True]
    if len(candidates) != 1:
        raise ApiError("JIRA_IDENTITY_FIELD_REQUIRED: 대상 Tracker에 표시되는 'Jira Key' 필드가 "
                       "정확히 한 개 있어야 합니다. 확인된 개수: " + str(len(candidates)))
    field = candidates[0]
    from migration.wizard import semantic_type
    kind, multiple = semantic_type(field)
    if kind != "text" or multiple is True or str(field.get("typeName","")).casefold() == "wikitext":
        raise ApiError("JIRA_IDENTITY_NOT_TEXT: 기존 Jira Key는 단일 Text 필드여야 합니다.")
    identifier = field_identifier(field)
    if type(identifier) is not int:
        raise ApiError("JIRA_IDENTITY_FIELD_UNVERIFIED: Jira Key 필드 ID를 확인할 수 없습니다.")
    contract = binding(cb.spec, field)
    if not contract and verified_template_binding:
        from migration.wizard import semantic_type
        kind, multiple = semantic_type(field)
        if (kind == "text" and multiple is not True and
                verified_template_binding.get("type") == "TextFieldValue" and
                verified_template_binding.get("value_property") == "value"):
            contract = dict(verified_template_binding, fieldId=identifier)
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
    # The embedded Codebeamer connector resolves the reserved identity
    # field by name; REST Item responses may omit fieldId on some profiles.
    found = [f for f in fields if isinstance(f,dict) and (
             f.get("fieldId")==field_id or
             (f.get("fieldId") is None and
              str(f.get("name") or "").strip().casefold()=="jira key"))]
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
