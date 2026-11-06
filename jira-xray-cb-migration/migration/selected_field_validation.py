"""Live GET-only validation of the exact Jira fields selected in the existing Wizard.

No guessed CB IDs and no POST. Every selected field has a separate verdict.
"""
from urllib.parse import quote
from clients.http import ApiError
from mapping_helper import field_identifier


def _norm(value):
    return " ".join(str(value or "").strip().casefold().split())


def _names(value):
    if value is None:
        return []
    found=[]
    for v in value if isinstance(value,list) else [value]:
        if isinstance(v,str):
            name=v
        elif isinstance(v,dict):
            name=v.get("name",v.get("value"))
        else:
            raise ApiError("원본 선택 값 형식 미지원: "+type(v).__name__)
        if not isinstance(name,str) or not name.strip():
            raise ApiError("원본 선택 값에 이름이 없습니다.")
        if _norm(name) not in {_norm(f) for f in found}:
            found.append(name)
    return found



def _get_with_context(http, path, stage, field=None):
    """GET errors are tagged by the operation, never logged with credentials or values."""
    try:
        return http.request("GET", path)
    except ApiError as exc:
        prefix = "API 검증 실패 [" + stage + "]"
        if field is not None:
            prefix += " [Jira 필드 " + str(field) + "]"
        # Http removes full URL/query and request/response bodies from exceptions.
        raise ApiError(prefix + ": " + str(exc)) from None


def validate_selected(wizard, scope, issue_key):
    """Validate a single matching Jira Issue and selected CB fields using GET only.

    Results report names/field metadata and aggregate availability, never source
    field values or credentials. null values are SKIP_NULL, not proof of support.
    """
    entry=wizard.plan.get("scopes",{}).get(scope)
    if not isinstance(entry,dict) or type(entry.get("tracker")) is not int:
        raise ApiError("먼저 Jira 유형과 기존 CB Tracker를 연결하세요.")
    project=entry["project"]
    if not isinstance(issue_key,str) or not issue_key.startswith(project+"-") or not all(
            char.isalnum() or char in "-_" for char in issue_key):
        raise ApiError("현재 프로젝트의 Jira Issue Key를 입력하세요.")
    selected={source:row for source,row in entry.get("rows",{}).items()
              if row.get("selected",False)}
    if not selected:
        raise ApiError("먼저 검증할 Jira 필드를 선택하세요.")
    requested=list(dict.fromkeys(["issuetype","project"]+list(selected)))
    jira=wizard.jira.http
    cb=wizard.cb.http
    issue=_get_with_context(jira,"/rest/api/2/issue/"+quote(issue_key,safe="-_")+
                       "?fields="+",".join(requested),"Jira Issue 상세 조회")
    fields=issue.get("fields") if isinstance(issue,dict) else None
    if not isinstance(fields,dict) or issue.get("key")!=issue_key or (
          fields.get("project") or {}).get("key")!=project or str(
          (fields.get("issuetype") or {}).get("id"))!=str(entry["type"]["id"]):
        raise ApiError("Jira 조회 결과의 Key/프로젝트/Issue Type이 선택과 다릅니다.")
    tracker=entry["tracker"]
    cbconfig=_get_with_context(cb,f"/v3/tracker/{tracker}/configuration",
                               "Codebeamer Tracker Configuration")
    available=cbconfig.get("fields") if isinstance(cbconfig,dict) else None
    if not isinstance(available,list):
        raise ApiError("CB Tracker 필드 설정 전체를 조회할 수 없습니다.")
    by_id={field_identifier(f):f for f in available if isinstance(f,dict)}
    result=[]
    for source,row in selected.items():
        name=(entry.get("analysis",{}).get("fields",{}).get(source) or {}).get("name",source)
        value=fields.get(source)
        spec=row.get("spec") or {}
        target=row.get("target") or {}
        fid=spec.get("field_id")
        verdict="BLOCKED"
        reason="대상 매핑 Field ID 또는 쓰기 계약 미확인"
        if source in ("summary","description"):
            if value is None:
                verdict,reason="SKIP_NULL","원본 null. 쓰기 계약 검증과 별개"
            elif isinstance(value,str):
                verdict,reason="BASELINE","기존 성공한 Name/Description 경로 재사용"
            else:
                reason="기본 텍스트가 아닌 Jira 값"
        elif value is None or value==[]:
            verdict,reason="SKIP_NULL","원본 null/빈 목록. 대상 필드 매핑은 별도 검증 필요"
        elif type(fid) is not int or fid not in by_id:
            reason="현재 CB Tracker 설정에서 정확한 Field ID를 확인하지 못함"
        elif spec.get("selection_unverified") or spec.get("missing_definition"):
            reason="Wizard에 미검증 필드 정의가 남아 있음"
        else:
            try:
                detail=_get_with_context(cb,f"/v3/trackers/{tracker}/fields/{fid}",
                                         "Codebeamer Field Detail",source)
                if not isinstance(detail,dict) or detail.get("id")!=fid:
                    raise ApiError("CB 필드 상세 GET ID 불일치")
                model=str(detail.get("valueModel") or "")
                if source=="reporter":
                    names=_names(value)
                    if detail.get("referenceType")!="UserReference" or not model.startswith("ChoiceFieldValue"):
                        raise ApiError("Reporter는 UserReference/ChoiceFieldValue가 아님")
                    if len(names)!=1:
                        raise ApiError("Reporter에 정확한 Jira 계정 이름 한 개 필요")
                    target_name=(spec.get("value_mapping") or {}).get(names[0],names[0])
                    if isinstance(target_name,dict):
                        target_name=target_name.get("name")
                    if not isinstance(target_name,str) or not target_name:
                        raise ApiError("Reporter 대상 사용자 이름 매핑 미확인")
                    account=_get_with_context(cb,"/v3/users/findByName?name="+quote(target_name,safe=""),
                                              "Codebeamer 사용자 조회",source)
                    if (not isinstance(account,dict) or type(account.get("id")) is not int or
                            account.get("name")!=target_name):
                        raise ApiError("Reporter 사용자 정확히 일치하는 CB ID 조회 실패")
                    verdict,reason="GET_VERIFIED","Jira 사용자 이름 → CB UserReference ID 확인; 쓰기 응답은 아직 미검증"
                elif source in ("priority","security","status"):
                    names=_names(value)
                    if (detail.get("referenceType")!="ChoiceOptionReference" or
                         not model.startswith("ChoiceFieldValue") or
                         not isinstance(detail.get("options"),list)):
                        raise ApiError("ChoiceOptionReference/ChoiceFieldValue 계약 미확인")
                    mapping=spec.get("value_mapping") or {}
                    options=detail["options"]
                    for original in names:
                        remap=mapping.get(original,original)
                        if isinstance(remap,dict):
                            remap=remap.get("name")
                        matches=[o for o in options if isinstance(o,dict) and
                                 _norm(o.get("name"))==_norm(remap) and
                                 type(o.get("id")) is int]
                        if len(matches)!=1:
                            raise ApiError("원본 선택지에 대응하는 CB 옵션 ID 없음/중복")
                    if detail.get("multipleValues") is False and len(names)>1:
                        raise ApiError("단일 선택 CB 필드에 원본 값이 여러 개")
                    verdict,reason="GET_VERIFIED","Choice 옵션 이름·대상 ID 일치; 실제 POST 미검증"
                else:
                    reason="선택 필드의 변환기 검증이 아직 구현되지 않음"
            except ApiError as exc:
                reason=str(exc)
        result.append({"source":source,"source_label":name,
                       "target":target.get("label") or target.get("name") or spec.get("target") or "미매핑",
                       "cb_field_id":fid,"verdict":verdict,"reason":reason,
                       "source_present":value is not None})
    blocked=sum(row["verdict"]=="BLOCKED" for row in result)
    verified=sum(row["verdict"]=="GET_VERIFIED" for row in result)
    return {"mode":"GET_ONLY","jira_key":issue_key,"tracker":tracker,
            "rows":result,"blocked":blocked,"get_verified":verified,
            "post_ready":False,"writes":0}
