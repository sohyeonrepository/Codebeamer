"""Persist source Xray Test Set/Case relations for later phase-2 resolution.

No relation write is attempted here. Every raw relation is accompanied by Jira
source key; pending target keys are resolved using verified batch journals later.
"""
from clients.http import ApiError


def relation_values(value, field):
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    keys = []
    for row in rows:
        if isinstance(row, str):
            key = row
        elif isinstance(row, dict):
            key = row.get("key") or row.get("issueKey")
        else:
            raise ApiError("Unexpected Xray relation shape in " + field)
        if not isinstance(key, str) or not key or "-" not in key:
            raise ApiError("Xray relation does not contain a Jira Key: " + field)
        if key not in keys:
            keys.append(key)
    return keys


def extract_relations(kind, issue):
    source = issue["key"]
    fields = issue["fields"]
    rows = []
    if kind == "set":
        source_fields = (("customfield_17775","set_contains_test"),)
    elif kind == "case":
        source_fields = (("customfield_17770","test_in_set"),
                         ("customfield_17772","test_in_plan"))
    else:
        raise ApiError("Unsupported phase-2 source kind")
    for field, relationship in source_fields:
        if field not in fields:
            raise ApiError("Jira relation field not returned: " + field)
        for target in relation_values(fields[field],field):
            rows.append({"source_jira_key":source,"target_jira_key":target,
                         "jira_field":field,"relation":relationship})
    return rows
