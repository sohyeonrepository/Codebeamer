"""Page-through Jira comments; capture attachment metadata without downloading binary yet.

This capture is not a Codebeamer write. A later child importer can consume
these per-item records after validating the CB comment/attachment write APIs.
"""
from urllib.parse import quote, urlencode
from clients.http import ApiError


def source_children(jira, key, page_size=100):
    safe_key = quote(key, safe="-_")
    start = 0
    comments = []
    total = None
    while True:
        query = urlencode({"startAt": start, "maxResults": page_size})
        result = jira.request("GET", "/rest/api/2/issue/" + safe_key + "/comment?" + query)
        if not isinstance(result, dict) or not isinstance(result.get("comments"), list):
            raise ApiError("Jira comments page malformed: " + key)
        if type(result.get("total")) is not int or result["total"] < 0:
            raise ApiError("Jira comment total missing: " + key)
        if result.get("startAt", start) != start:
            raise ApiError("Jira comments pagination mismatch")
        if total is not None and result["total"] != total:
            raise ApiError("Jira comments count changed during fetch")
        total = result["total"]
        rows = result["comments"]
        for row in rows:
            if not isinstance(row,dict) or row.get("id") is None or not isinstance(row.get("body"),str):
                raise ApiError("Jira comment id/body missing")
            comments.append({"id":str(row["id"]), "body":row["body"],
                             "author":(row.get("author") or {}).get("name"),
                             "created":row.get("created"), "updated":row.get("updated")})
        start += len(rows)
        if start >= total:
            break
        if not rows:
            raise ApiError("Jira comment pagination truncated")
    if len({row["id"] for row in comments}) != len(comments):
        raise ApiError("Duplicate Jira comment IDs")

    detail = jira.request("GET", "/rest/api/2/issue/" + safe_key + "?fields=attachment")
    if not isinstance(detail, dict) or detail.get("key") != key:
        raise ApiError("Jira attachment detail mismatch")
    attachments = (detail.get("fields") or {}).get("attachment") or []
    if not isinstance(attachments,list):
        raise ApiError("Jira attachments field not an array")
    result = []
    for row in attachments:
        if not isinstance(row,dict) or row.get("id") is None or not isinstance(row.get("filename"),str):
            raise ApiError("Jira attachment entry missing id/filename")
        result.append({"id":str(row["id"]),"filename":row["filename"],
                       "size":row.get("size"),"content":row.get("content"),
                       "author":(row.get("author") or {}).get("name"),
                       "created":row.get("created")})
    if len({row["id"] for row in result}) != len(result):
        raise ApiError("Duplicate Jira attachment IDs")
    return {"comments":comments, "attachments":result}
