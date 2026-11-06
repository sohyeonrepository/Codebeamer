"""Read-only bridge for existing connected Jira/Codebeamer compact Wizard."""
import json
from pathlib import Path
from urllib.parse import quote
from clients.http import ApiError
from batch_case_set_import import enrich_direct_field_types
from test_case_set_post import MAPPINGS, mapped_payload, resolve_direct_fields, source_field_names
from migration.option_batch import source_value


def selected_scope(wizard, scope):
    entry = wizard.plan.get("scopes", {}).get(scope)
    if not isinstance(entry,dict):
        raise ApiError("먼저 기존 UI에서 Jira Issue Type과 CB Tracker를 연결하세요.")
    name = (entry.get("type") or {}).get("name")
    kind = next((k for k,(_,expected) in MAPPINGS.items() if name==expected), None)
    if kind is None:
        raise ApiError("Test Set 또는 Test Case(Test)를 선택하세요.")
    project,tracker = entry.get("project"),entry.get("tracker")
    if type(tracker) is not int or not isinstance(project,str) or not project:
        raise ApiError("프로젝트와 기존 Tracker를 먼저 연결하세요.")
    valid = {row.get("id") for row in wizard.plan.get("projects",{}).get(project,{}).get("trackers",[])
             if isinstance(row,dict)}
    if tracker not in valid:
        raise ApiError("CB Tracker가 선택한 프로젝트의 실제 목록과 다릅니다.")
    return kind,project,tracker


def required_source_columns(mapping):
    fields=["summary","description","issuetype","project"]
    for section in ("phase_1","special","phase_2"):
        for row in mapping.get(section,[]):
            source=row.get("jira")
            if not isinstance(source,str) or source in ("key","comment","attachment") or source.startswith("xray:"):
                continue
            source=source.split(".",1)[0]
            if source not in fields:
                fields.append(source)
    return fields


def preview(wizard, scope, key):
    kind,project,tracker=selected_scope(wizard,scope)
    if (not isinstance(key,str) or not key.startswith(project+"-") or
        not all(ch.isalnum() or ch in "-_" for ch in key)):
        raise ApiError("선택한 Jira 프로젝트의 Issue Key를 입력하세요.")
    path=Path(__file__).resolve().parent.parent/"mappings"/MAPPINGS[kind][0]
    mapping=json.loads(path.read_text(encoding="utf-8-sig"))
    if mapping.get("issue_type") != MAPPINGS[kind][1]:
        raise ApiError("매핑 파일의 Jira Issue Type이 현재 선택과 일치하지 않습니다.")
    requested=required_source_columns(mapping)
    issue=wizard.jira.http.request("GET", "/rest/api/2/issue/"+quote(key,safe="-_")+
                                    "?fields="+",".join(requested))
    if not isinstance(issue,dict) or issue.get("key")!=key or not isinstance(issue.get("fields"),dict):
        raise ApiError("Jira Issue 조회 결과가 잘못되었습니다.")
    src=issue["fields"]
    if (src.get("issuetype") or {}).get("name")!=MAPPINGS[kind][1] or (
        src.get("project") or {}).get("key")!=project:
        raise ApiError("Jira Issue의 프로젝트 또는 Issue Type이 선택과 다릅니다.")
    config=wizard.cb.http.request("GET",f"/v3/tracker/{tracker}/configuration")
    cb_fields=enrich_direct_field_types(wizard.cb.http,tracker,
                   config.get("fields") if isinstance(config,dict) else None,mapping)
    blockers=[]
    direct_payload=None
    try:
        resolved=resolve_direct_fields(cb_fields,mapping)
        direct_payload=mapped_payload(mapping,src,resolved,
                          jira_url=wizard.jira.http.base,jira_key=key)
    except (ApiError,KeyError,ValueError) as exc:
        blockers.append("Text/WikiText 필드: "+str(exc))
    coverage=[]
    for section in ("phase_1","special","phase_2"):
        for row in mapping.get(section,[]):
            field=row.get("jira","")
            cb=row.get("cb") or {}
            mode=row.get("mode")
            stage=("기본값 변환 확인" if direct_payload is not None else "확인 실패") if mode=="direct" else (
                  "Choice/User/참조 계약 확인 필요" if mode=="mapped" else
                  "별도 후처리 또는 추가 계약 검증 필요")
            value=key if field=="key" else (
                source_value(src,field) if field and not field.startswith("xray:") else None)
            coverage.append({"source":field,"target":cb.get("label","-"),"stage":stage,
                             "non_null":value is not None,"section":section})
    return {"mode":"READ_ONLY","kind":kind,"jira_key":key,"project":project,
            "tracker":tracker,"mapped_fields":coverage,"blockers":blockers,
            "direct_payload_fields":list(direct_payload or {}),
            "complete":False,"note":"이번 버튼은 원본/대상 GET과 매핑 검수만 수행하며 POST하지 않습니다."}


def first_issue_key(wizard, scope):
    """Auto-select one Jira Issue of the user-selected project/type (read-only)."""
    from batch_case_set_import import search_page
    kind,project,_=selected_scope(wizard,scope)
    issues,total=search_page(wizard.jira.http,project,kind,
                            ["summary","issuetype","project"],0,1)
    if total==0 or not issues:
        raise ApiError("선택한 Jira 프로젝트/Issue Type에 조회 가능한 이슈가 없습니다.")
    key=issues[0].get("key") if isinstance(issues[0],dict) else None
    if not isinstance(key,str) or not key.startswith(project+"-"):
        raise ApiError("Jira 자동 선택 결과의 Issue Key가 잘못되었습니다.")
    return key
