"""Config-driven, one-Issue Test Case/Test Set field POST check.

Only Name, Description and existing direct Text/WikiText fields.
Choice/User/relations/comments/attachments/system fields are intentionally excluded.
Preview is default; --execute performs at most one POST with a durable attempt journal.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import quote

from clients.http import ApiError
from post_smoke_test import (append_event, build_payload, configured_jira_key_field,
                             jira_browse_url, make_http, prior_attempt, resolve_wikitext_field)

MAPPINGS = {
    "case": ("test_case.mapping.json", "Test"),
    "set": ("test_set.mapping.json", "Test Set"),
}
MODELS = {"text": "TextFieldValue", "wikitext": "WikiTextFieldValue"}


def normalize(value):
    return "".join(c for c in str(value or "").casefold() if c.isalnum())


def source_field_names(mapping):
    names = []
    for row in mapping["phase_1"]:
        if row.get("mode") != "direct":
            continue
        field = row.get("jira")
        if field not in ("summary", "description") and field not in names:
            names.append(field)
    return names


def resolve_direct_fields(config_fields, mapping):
    """Resolve actual API Field IDs by matching existing CB names, not customField[n]."""
    if not isinstance(config_fields, list):
        raise ApiError("Tracker configuration has no fields array")
    resolved = {}
    for row in mapping["phase_1"]:
        if row.get("mode") != "direct" or row["jira"] in ("summary", "description"):
            continue
        expected = normalize(row["cb"]["type"])
        if expected not in MODELS:
            raise ApiError("Unsupported direct field type for " + row["jira"])
        label = normalize(row["cb"]["label"])
        matches = [f for f in config_fields if isinstance(f, dict) and
                   (normalize(f.get("label")) == label or normalize(f.get("name")) == label)]
        if len(matches) != 1:
            raise ApiError("Target CB field missing or ambiguous: " + row["cb"]["label"])
        field = matches[0]
        model = normalize(field.get("typeName") or field.get("valueModel"))
        if expected not in model:
            raise ApiError("CB type mismatch for " + row["cb"]["label"] + "; no POST")
        from mapping_helper import field_identifier
        field_id = field_identifier(field)
        if type(field_id) is not int or field_id <= 0:
            raise ApiError("CB field ID unavailable for " + row["cb"]["label"])
        resolved[row["jira"]] = (field_id, MODELS[expected])
    if len({v[0] for v in resolved.values()}) != len(resolved):
        raise ApiError("Multiple Jira fields map to the same CB field")
    return resolved


def mapped_payload(mapping, source, resolved, *, jira_url, jira_key, link_field_id=None):
    payload = build_payload(source.get("summary"), source.get("description"),
                            jira_url=jira_url, issue_key=jira_key,
                            link_field_id=link_field_id)
    custom = payload.setdefault("customFields", [])
    for jira_field in source_field_names(mapping):
        value = source.get(jira_field)
        # Null is absence, not a signal to delete or overwrite the target.
        if value is None:
            continue
        if not isinstance(value, str):
            raise ApiError("Jira field is not scalar text: " + jira_field + "; no POST")
        field_id, model = resolved[jira_field]
        if any(item["fieldId"] == field_id for item in custom):
            raise ApiError("Duplicate CB custom field for " + jira_field)
        custom.append({"fieldId": field_id, "type": model, "value": value})
    if not custom:
        payload.pop("customFields")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Test Case/Test Set config-driven single POST")
    parser.add_argument("--kind", required=True, choices=tuple(MAPPINGS))
    parser.add_argument("--jira-key", required=True)
    parser.add_argument("--tracker-id", type=int, required=True)
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--state-dir", default=".migration/test-case-set")
    parser.add_argument("--prompt-auth", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-key")
    args = parser.parse_args(argv)
    if args.tracker_id <= 0 or (args.execute and args.confirm_key != args.jira_key):
        parser.error("Positive tracker ID and matching --confirm-key required for --execute")
    base = Path(__file__).resolve().parent
    mapping = json.loads((base / "mappings" / MAPPINGS[args.kind][0]).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    state = Path(args.state_dir)
    journal = state / ("post-attempts-" + args.kind + ".jsonl")
    if prior_attempt(journal, args.jira_key, args.tracker_id):
        raise ApiError("This Jira Key/Tracker has a prior POST attempt in this test journal; do not blindly retry")
    jira = make_http(config["jira"], state / "jira-requests.jsonl", prompt_auth=args.prompt_auth)
    cb = make_http(config["codebeamer"], state / "cb-requests.jsonl", prompt_auth=args.prompt_auth)
    fields_to_read = ["summary", "description", "issuetype"] + source_field_names(mapping)
    issue = jira.request("GET", "/rest/api/2/issue/" + quote(args.jira_key, safe="-_") +
                         "?fields=" + ",".join(fields_to_read))
    if not isinstance(issue, dict) or issue.get("key") != args.jira_key:
        raise ApiError("Unexpected source issue; no POST")
    source = issue.get("fields")
    if not isinstance(source, dict) or (source.get("issuetype") or {}).get("name") != MAPPINGS[args.kind][1]:
        raise ApiError("Jira Issue Type is not " + MAPPINGS[args.kind][1] + "; no POST")
    cb_configuration = cb.request("GET", f"/v3/tracker/{args.tracker_id}/configuration")
    resolved = resolve_direct_fields(cb_configuration.get("fields") if isinstance(cb_configuration, dict) else None,
                                     mapping)
    # The shared post_smoke Jira Key option is for an existing WikiText field.
    # Test Set's Wiki Link / URL model is not guessed and therefore omitted.
    link_id = None
    if args.kind == "case":
        registration = configured_jira_key_field(config)
        if registration:
            link_id = resolve_wikitext_field(cb, args.tracker_id, registration)
    payload = mapped_payload(mapping, source, resolved, jira_url=config["jira"]["url"],
                             jira_key=args.jira_key, link_field_id=link_id)
    print(json.dumps({
        "mode": "EXECUTE" if args.execute else "PREVIEW",
        "kind": args.kind, "jira_key": args.jira_key, "tracker_id": args.tracker_id,
        "requested_source_fields": fields_to_read,
        "posted_field_names": ["Name", "Description"] + [r["cb"]["label"] for r in mapping["phase_1"]
                               if r.get("mode") == "direct" and r["jira"] not in ("summary", "description")
                               and source.get(r["jira"]) is not None],
        "custom_fields": len(payload.get("customFields", [])),
        "excluded": "Choice/User, special system fields, comments, attachments, Test Set/Plan/Run relations",
        "jira_url": jira_browse_url(config["jira"]["url"], args.jira_key),
        "notice": "Preview does not print source text. This branch does not UPDATE prior Items.",
    }, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id, "status": "POST_ATTEMPT"})
    try:
        result = cb.request("POST", f"/v3/trackers/{args.tracker_id}/items", payload)
    except Exception as exc:
        append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                               "status": "POST_UNCERTAIN_OR_FAILED", "error_type": type(exc).__name__})
        raise
    item_id = result.get("id") if isinstance(result, dict) else None
    if type(item_id) is not int or item_id <= 0:
        append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                               "status": "POST_RETURNED_NO_ID"})
        raise ApiError("POST returned without Item ID. Reconcile on server before retrying.")
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                           "status": "POST_RETURNED", "cb": item_id})
    actual = cb.request("GET", f"/v3/items/{item_id}")
    if not isinstance(actual, dict) or actual.get("name") != payload["name"]:
        raise ApiError("POST returned Item " + str(item_id) + ", but Name GET check failed")
    if "description" in payload and actual.get("description") != payload["description"]:
        raise ApiError("POST returned Item " + str(item_id) + ", but Description GET check failed")
    expected = {f["fieldId"]: f["value"] for f in payload.get("customFields", [])}
    got = actual.get("customFields")
    if expected and not isinstance(got, list):
        raise ApiError("POST returned Item " + str(item_id) + ", but GET omitted customFields")
    found = {f.get("fieldId"): f.get("value") for f in got or [] if isinstance(f, dict)}
    missing = [field_id for field_id, value in expected.items() if found.get(field_id) != value]
    if missing:
        raise ApiError("POST returned Item " + str(item_id) +
                       ", but custom field GET mismatch count: " + str(len(missing)))
    append_event(journal, {"jira": args.jira_key, "tracker": args.tracker_id,
                           "status": "VERIFIED", "cb": item_id})
    print(json.dumps({"status": "VERIFIED", "jira_key": args.jira_key, "cb_item_id": item_id},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
