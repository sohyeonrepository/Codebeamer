"""One-issue live POST smoke test. No tracker/field creation and no Swagger discovery.

Default = read-only preview; --execute explicitly authorizes exactly one POST.
The optional Jira Key WikiText field writes a clickable link, not a bare key.
Run from jira-xray-cb-migration directory.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from clients.http import ApiError, Http


def jira_browse_url(base_url, issue_key):
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.username or parts.password:
        raise ValueError("Invalid Jira URL")
    if not issue_key or not all(ch.isalnum() or ch in "-_" for ch in issue_key):
        raise ValueError("Invalid Jira issue key")
    return base_url.rstrip("/") + "/browse/" + quote(issue_key, safe="-_")


def wiki_link(base_url, issue_key):
    return "[" + issue_key + "|" + jira_browse_url(base_url, issue_key) + "]"


def build_payload(summary, description=None, *, jira_url=None, issue_key=None, link_field_id=None):
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Jira Summary cannot be empty")
    if description is not None and not isinstance(description, str):
        raise ValueError("Jira Description must be a string or null")
    payload = {"name": summary}
    if description is not None:
        payload["description"] = description
    if link_field_id is not None:
        if not isinstance(link_field_id, int) or link_field_id <= 0 or not jira_url or not issue_key:
            raise ValueError("WikiText Jira link requires real field ID, Jira URL and issue key")
        payload["customFields"] = [{
            "fieldId": link_field_id,
            "type": "WikiTextFieldValue",
            "value": wiki_link(jira_url, issue_key),
        }]
    return payload


def append_event(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def prior_attempt(path, issue_key, tracker_id):
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                raise ApiError("Corrupt POST journal. Stop and inspect before retrying.")
            if event.get("jira") == issue_key and event.get("tracker") == tracker_id and event.get("status") == "POST_ATTEMPT":
                return True
    return False


def make_http(settings, log_path, *, prompt_auth=False):
    return Http(
        settings["url"], settings.get("token_env"), log_path,
        auth=settings.get("auth", "bearer"),
        username_env=settings.get("username_env"),
        password_env=settings.get("password_env"),
        allow_http=settings.get("allow_http", False),
        prompt_auth=prompt_auth,
    )


def configured_jira_key_field(config):
    """Field registration is expressed using CB UI metadata, never a required numeric ID."""
    entry = config.get("migration", {}).get("post_smoke", {}).get("jira_key_field")
    if entry is None:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("label"), str) or not entry["label"].strip():
        raise ApiError("migration.post_smoke.jira_key_field.label must be a field name")
    if str(entry.get("type", "WikiText")).casefold() != "wikitext":
        raise ApiError("Jira Key link target must be WikiText")
    return entry


def resolve_wikitext_field(cb, tracker_id, registration):
    """Resolve by the CB field's displayed name from existing Tracker configuration."""
    from mapping_helper import field_identifier
    response = cb.request("GET", f"/v3/tracker/{tracker_id}/configuration")
    fields = response.get("fields") if isinstance(response, dict) else None
    if not isinstance(fields, list):
        raise ApiError("Target Tracker configuration did not return fields")
    normalize = lambda value: "".join(ch for ch in str(value or "").casefold() if ch.isalnum())
    label = normalize(registration["label"])
    matches = [f for f in fields if isinstance(f, dict) and any(
        normalize(f.get(name)) == label for name in ("label", "name"))]
    if len(matches) != 1:
        raise ApiError("Expected exactly one target field with label: " + registration["label"])
    field = matches[0]
    type_name = str(field.get("typeName") or field.get("valueModel") or "")
    if "wikitext" not in normalize(type_name):
        raise ApiError("Jira Key field WikiText type not confirmed in Tracker configuration; no POST")
    field_id = field_identifier(field)
    if type(field_id) is not int or field_id <= 0:
        raise ApiError("Jira Key field internal ID not returned by Codebeamer; no POST")
    # registration (e.g. customfield[6]) is a UI display hint, not assumed to be API field ID.
    return field_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="One Jira issue -> one Codebeamer Item POST smoke test")
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--jira-key", required=True)
    parser.add_argument("--tracker-id", required=True, type=int)
    parser.add_argument("--state-dir", default=".migration/post-smoke")
    parser.add_argument("--prompt-auth", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Actually send exactly one POST")
    parser.add_argument("--confirm-key", help="Must equal --jira-key before POST")
    args = parser.parse_args(argv)
    if args.tracker_id <= 0:
        parser.error("Positive tracker ID required")
    if args.execute and args.confirm_key != args.jira_key:
        parser.error("--execute requires --confirm-key identical to --jira-key")
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    state = Path(args.state_dir)
    journal = state / "post-attempts.jsonl"
    if prior_attempt(journal, args.jira_key, args.tracker_id):
        raise ApiError("POST previously attempted for this Jira Key and Tracker. Inspect CB and journal; no automatic retry.")
    jira = make_http(config["jira"], state / "http-jira.jsonl", prompt_auth=args.prompt_auth)
    cb = make_http(config["codebeamer"], state / "http-cb.jsonl", prompt_auth=args.prompt_auth)
    issue = jira.request("GET", "/rest/api/2/issue/" + quote(args.jira_key, safe="-_") +
                         "?fields=summary,description")
    if not isinstance(issue, dict) or issue.get("key") != args.jira_key:
        raise ApiError("Unexpected Jira issue key; no POST")
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise ApiError("Jira fields missing; no POST")
    field_registration = configured_jira_key_field(config)
    link_field_id = resolve_wikitext_field(cb, args.tracker_id, field_registration) if field_registration else None
    payload = build_payload(
        fields.get("summary"), fields.get("description"),
        jira_url=config["jira"]["url"], issue_key=args.jira_key,
        link_field_id=link_field_id,
    )
    print(json.dumps({
        "mode": "LIVE_POST" if args.execute else "READ_ONLY_PREVIEW",
        "jira_key": args.jira_key,
        "tracker_id": args.tracker_id,
        "payload_fields": list(payload),
        "jira_key_field": field_registration,
        "jira_link": wiki_link(config["jira"]["url"], args.jira_key),
        "note": "Jira issue text is not printed. Tracker required fields may still reject the POST.",
    }, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0

    # Record BEFORE sending any write: a timeout may mean the Item was created.
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id, "status": "POST_ATTEMPT"})
    try:
        result = cb.request("POST", f"/v3/trackers/{args.tracker_id}/items", payload)
    except Exception as exc:
        append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                               "status": "POST_UNCERTAIN_OR_FAILED", "error_type": type(exc).__name__})
        raise
    item_id = result.get("id") if isinstance(result, dict) else None
    if type(item_id) is not int or item_id <= 0:
        append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id, "status": "POST_RETURNED_NO_ID"})
        raise ApiError("POST returned without Item ID; inspect server before retry")
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                           "status": "POST_RETURNED", "cb": item_id})
    actual = cb.request("GET", f"/v3/items/{item_id}")
    if not isinstance(actual, dict) or actual.get("name") != payload["name"]:
        raise ApiError(f"Created Item {item_id}, but GET Name verification failed")
    if "description" in payload and actual.get("description") != payload["description"]:
        raise ApiError(f"Created Item {item_id}, but GET Description verification failed")
    if link_field_id is not None:
        values = actual.get("customFields")
        matches = [f for f in values if isinstance(f, dict) and f.get("fieldId") == link_field_id] if isinstance(values, list) else []
        if not any(f.get("value") == payload["customFields"][0]["value"] for f in matches):
            raise ApiError(f"Created Item {item_id}, but Jira WikiText link verification failed")
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                           "status": "VERIFIED", "cb": item_id})
    print(json.dumps({"status": "VERIFIED", "jira_key": args.jira_key,
                      "cb_item_id": item_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
