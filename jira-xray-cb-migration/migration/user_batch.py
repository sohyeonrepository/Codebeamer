"""Resolve UserReference fields using CB findByName without manually entering user IDs.

Only Jira assignee -> native assignedTo and explicit CB UserChoiceField rows.
Missing/nonmatching users are blockers. No user creation.
"""
from urllib.parse import quote
from clients.http import ApiError
from migration.option_batch import norm, source_value
from mapping_helper import field_identifier


def user_rows(mapping):
    return [r for r in mapping.get("phase_1", []) if r.get("mode") == "mapped" and
            ("user" in norm(r["cb"].get("type")) or norm(r["cb"].get("type")) == "members")]


def source_users(value, field):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        if isinstance(v, str):
            name = v
        elif isinstance(v, dict):
            name = v.get("name")
        else:
            raise ApiError("Invalid Jira user value in " + field)
        if not isinstance(name,str) or not name.strip():
            raise ApiError("Jira user name not present: " + field)
        if name not in out:
            out.append(name)
    return out


def prepare_users(cb, tracker, config_fields, mapping):
    prepared = {}
    for row in user_rows(mapping):
        label = norm(row["cb"]["label"])
        fields = [f for f in config_fields if isinstance(f,dict) and (
            norm(f.get("label")) == label or norm(f.get("name")) == label)]
        if len(fields) != 1:
            raise ApiError("CB User field missing/ambiguous: " + row["cb"]["label"])
        field_id = field_identifier(fields[0])
        if type(field_id) is not int:
            raise ApiError("CB User field ID missing")
        detail = cb.request("GET", f"/v3/trackers/{tracker}/fields/{field_id}")
        if (not isinstance(detail,dict) or detail.get("id") != field_id or
            detail.get("type") != "UserChoiceField" or
            detail.get("referenceType") != "UserReference" or
            not str(detail.get("valueModel", "")).startswith("ChoiceFieldValue")):
            raise ApiError("CB UserReference contract not confirmed: " + row["cb"]["label"])
        prepared[row["jira"]] = (field_id, detail)
    return prepared


def add_users(payload, source, mapping, prepared, cb, name_map=None, cache=None):
    cache = cache if cache is not None else {}
    name_map = name_map or {}
    for row in user_rows(mapping):
        jira_field = row["jira"]
        field_id, detail = prepared[jira_field]
        values = []
        for jira_name in source_users(source_value(source,jira_field),jira_field):
            cb_name = name_map.get(jira_field, {}).get(jira_name, jira_name)
            if not isinstance(cb_name,str) or not cb_name:
                raise ApiError("Invalid CB user mapping: " + jira_field)
            if cb_name not in cache:
                cb_user = cb.request("GET", "/v3/users/findByName?name=" + quote(cb_name,safe=""))
                if not isinstance(cb_user,dict) or type(cb_user.get("id")) is not int or cb_user.get("name") != cb_name:
                    raise ApiError("CB user lookup did not uniquely match " + jira_field)
                cache[cb_name] = cb_user["id"]
            values.append({"id":cache[cb_name],"type":"UserReference"})
        if not values:
            continue
        if detail.get("multipleValues") is False and len(values) > 1:
            raise ApiError("Multiple users for single-value field: " + jira_field)
        field_value = {"fieldId":field_id,"type":"ChoiceFieldValue","values":values}
        if row["cb"].get("registration") == "assignedTo":
            if "assignedTo" in payload:
                raise ApiError("Duplicate Assigned to")
            payload["assignedTo"] = field_value
        else:
            target = payload.setdefault("customFields",[])
            if any(f.get("fieldId") == field_id for f in target):
                raise ApiError("Duplicate user target field")
            target.append(field_value)
    return payload
