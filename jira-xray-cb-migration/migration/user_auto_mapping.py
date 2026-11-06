"""Resolve Jira users to existing CB users by verified account identity.

No guessed IDs; display names are for UI only. Ambiguity/unmatched users
require a manually approved CB user ID. This module never creates accounts.
"""
from urllib.parse import quote
from clients.http import ApiError


def source_users(value, source):
    if value is None:
        return []
    users=value if isinstance(value,list) else [value]
    result=[]
    for user in users:
        if isinstance(user,str):
            record={"name":user,"displayName":user}
        elif isinstance(user,dict):
            record=user
        else:
            raise ApiError("User 원본 형식 미지원: "+source)
        identity=record.get("accountId") or record.get("name") or record.get("key")
        if not isinstance(identity,str) or not identity:
            raise ApiError("Jira User 식별자 없음: "+source)
        if identity not in [u[0] for u in result]:
            result.append((identity,record))
    return result


def resolve(cb, source, value, overrides=None, progress=None):
    """Return verified UserReferences or raise an explicit manual-mapping error."""
    overrides=overrides or {}
    log=progress or (lambda stage,detail:None)
    refs=[]
    for identity,jira in source_users(value,source):
        explicit=overrides.get(identity)
        display=jira.get("displayName") or jira.get("name") or identity
        if explicit is not None:
            if not isinstance(explicit,dict) or type(explicit.get("id")) is not int:
                raise ApiError("User 수동 매핑 ID 형식 오류: "+source+" / "+identity)
            user=cb.http.request("GET","/v3/users/"+str(explicit["id"]))
            if not isinstance(user,dict) or user.get("id")!=explicit["id"]:
                raise ApiError("CB User 수동 매핑 ID 조회 실패: "+source+" / "+identity)
            if explicit.get("name") and user.get("name")!=explicit["name"]:
                raise ApiError("CB User 수동 매핑 계정 변경 감지: "+source+" / "+identity)
            log("User 수동 매핑",str(display)+" → "+str(user.get("name"))+" (#"+str(user["id"])+")")
            refs.append({"id":user["id"],"type":"UserReference"})
            continue
        account=jira.get("name")
        # Jira displayName is NOT a stable account key. Never resolve by
        # displayName alone, even when a single matching name exists.
        if not isinstance(account,str) or not account:
            raise ApiError("User 수동 매핑 필요: "+source+" / "+str(display)+
                           " · Jira accountId="+identity+
                           " · CB에서 검증할 Jira 로그인 ID(name)가 없습니다.")
        found=cb.http.request("GET","/v3/users/findByName?name="+quote(account,safe=""))
        if not isinstance(found,dict) or type(found.get("id")) is not int or found.get("name")!=account:
            raise ApiError("User 수동 매핑 필요: "+source+" / "+str(display)+
                           " · Jira 계정="+account+" · 동일한 CB 계정이 확인되지 않습니다.")
        log("User 자동 매핑",str(display)+" ("+account+") → "+
            str(found.get("firstName") or "")+" "+str(found.get("lastName") or "")+
            " / CB ID "+str(found["id"]))
        refs.append({"id":found["id"],"type":"UserReference"})
    return refs
