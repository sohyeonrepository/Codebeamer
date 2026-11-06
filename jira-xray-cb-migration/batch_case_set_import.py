"""Batch Jira Test Case/Test Set → Codebeamer: verified scalar fields only.

Jira Search pagination, single-Item POST per issue, durable journal and resume.
Full project phase-1/phase-2 migration is NOT claimed by this script.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from clients.http import ApiError
from post_smoke_test import append_event, configured_jira_key_field, make_http, resolve_wikitext_field
from test_case_set_post import MAPPINGS, mapped_payload, resolve_direct_fields, source_field_names
from migration.option_batch import prepare_options, choice_rows, add_options
from migration.user_batch import user_rows, prepare_users, add_users
from migration.phase2_manifest import extract_relations


def load_events(path):
    events = {}
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as file:
        for line in file:
            try:
                event = json.loads(line)
            except ValueError:
                raise ApiError("Corrupt batch journal: inspect manually before resuming")
            key = event.get("jira")
            if key:
                events[key] = event
    return events


def search_page(jira, project_key, kind, field_names, offset, size):
    if not isinstance(project_key, str) or not project_key or not all(
            ch.isalnum() or ch in "_-" for ch in project_key):
        raise ApiError("Invalid Jira project key")
    if kind not in MAPPINGS or size < 1 or size > 100:
        raise ApiError("Invalid issue kind / batch size")
    issue_type = MAPPINGS[kind][1]
    jql = 'project = "' + project_key + '" AND issuetype = "' + issue_type + '" ORDER BY id ASC'
    query = urlencode({"jql": jql, "fields": ",".join(field_names),
                       "startAt": offset, "maxResults": size})
    response = jira.request("GET", "/rest/api/2/search?" + query)
    if not isinstance(response, dict) or not isinstance(response.get("issues"), list):
        raise ApiError("Jira Search has no issues list")
    if type(response.get("total")) is not int or response["total"] < 0:
        raise ApiError("Jira Search total is missing")
    if type(response.get("startAt")) is not int or response["startAt"] != offset:
        raise ApiError("Jira Search returned an unexpected page start")
    if len(response["issues"]) > size:
        raise ApiError("Jira Search returned too many issues")
    return response["issues"], response["total"]


def verify_item(cb, item_id, payload):
    actual = cb.request("GET", f"/v3/items/{item_id}")
    if not isinstance(actual, dict) or actual.get("name") != payload["name"]:
        raise ApiError("Item POST returned but Name GET verification failed; ID " + str(item_id))
    if "description" in payload and actual.get("description") != payload["description"]:
        raise ApiError("Item POST returned but Description GET verification failed; ID " + str(item_id))
    expected = {row["fieldId"]: row.get("value") for row in payload.get("customFields", []) if row.get("type") != "ChoiceFieldValue"}
    expected_choices = {row["fieldId"]: {v["id"] for v in row["values"]} for row in payload.get("customFields", []) if row.get("type") == "ChoiceFieldValue"}
    found = actual.get("customFields")
    if (expected or expected_choices) and not isinstance(found, list):
        raise ApiError("Item GET omitted customFields; ID " + str(item_id))
    observed = {field.get("fieldId"): field.get("value") for field in found or []
                if isinstance(field, dict)}
    if any(observed.get(field_id) != value for field_id, value in expected.items()):
        raise ApiError("Item custom fields verification failed; ID " + str(item_id))
    if "assignedTo" in payload:
        actual_assigned = actual.get("assignedTo")
        expected_ids = {v["id"] for v in payload["assignedTo"]["values"]}
        if not isinstance(actual_assigned, list) or {v.get("id") for v in actual_assigned if isinstance(v,dict)} != expected_ids:
            raise ApiError("Item Assigned to verification failed; ID " + str(item_id))
    for field_id, ids in expected_choices.items():
        choice = next((f for f in found if isinstance(f,dict) and f.get("fieldId")==field_id), None)
        actual_values = choice.get("values") if isinstance(choice,dict) else None
        if not isinstance(actual_values,list) or {v.get("id") for v in actual_values if isinstance(v,dict)} != ids:
            raise ApiError("Item Choice options verification failed; ID " + str(item_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded batch Test Case/Test Set direct-field import")
    parser.add_argument("--kind", choices=("case", "set"), required=True)
    parser.add_argument("--jira-project", required=True)
    parser.add_argument("--tracker-id", type=int, required=True)
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--state-dir", default=".migration/test-case-set-batch")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0, help="0=all matching source issues")
    parser.add_argument("--prompt-auth", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--include-options", action="store_true", help="Also migrate mapped Choice/Option fields by exact target option names")
    parser.add_argument("--include-users", action="store_true", help="Resolve mapped Jira users by Codebeamer username, without creating accounts")
    parser.add_argument("--capture-relations", action="store_true", help="Capture Xray relation keys for a later phase-2 run")
    parser.add_argument("--confirm-project", help="Required for writes: exact Jira project key")
    args = parser.parse_args(argv)
    if args.tracker_id <= 0 or not 1 <= args.batch_size <= 100 or args.limit < 0:
        parser.error("Tracker ID positive, batch-size 1..100, limit >= 0")
    if args.execute and args.confirm_project != args.jira_project:
        parser.error("Execute requires --confirm-project identical to --jira-project")

    root = Path(__file__).resolve().parent
    mapping = json.loads((root / "mappings" / MAPPINGS[args.kind][0]).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    state = Path(args.state_dir)
    # Isolate journals by server origin, project, target tracker and scope.
    import hashlib
    identity = "|".join([config["jira"]["url"], config["codebeamer"]["url"],
                         args.jira_project, args.kind, str(args.tracker_id)])
    short = hashlib.sha256(identity.encode()).hexdigest()[:18]
    journal = state / ("batch-" + short + ".jsonl")
    events = load_events(journal)
    manifest = state / ("relations-" + short + ".jsonl")
    jira = make_http(config["jira"], state / "jira-requests.jsonl", prompt_auth=args.prompt_auth)
    cb = make_http(config["codebeamer"], state / "cb-requests.jsonl", prompt_auth=args.prompt_auth)

    columns = ["summary", "description", "issuetype", "project"] + source_field_names(mapping)
    if args.include_options:
        columns += [row["jira"].split(".")[0] for row in choice_rows(mapping)]
    if args.include_users:
        columns += [row["jira"].split(".")[0] for row in user_rows(mapping)]
    if args.capture_relations:
        columns += (["customfield_17775"] if args.kind == "set" else ["customfield_17770", "customfield_17772"])
    columns = list(dict.fromkeys(columns))
    configuration = cb.request("GET", f"/v3/tracker/{args.tracker_id}/configuration")
    cb_fields = configuration.get("fields") if isinstance(configuration, dict) else None
    resolved = resolve_direct_fields(cb_fields, mapping)
    option_bindings = prepare_options(cb, args.tracker_id, cb_fields, mapping) if args.include_options else {}
    user_bindings = prepare_users(cb, args.tracker_id, cb_fields, mapping) if args.include_users else {}
    user_cache = {}
    link_id = None
    if args.kind == "case":
        registration = configured_jira_key_field(config)
        if registration:
            link_id = resolve_wikitext_field(cb, args.tracker_id, registration)
    # Test Set 'Wiki Link / URL' payload is not yet verified. No unsupported write.
    total = offset = inspected = created = skipped = 0
    print(json.dumps({"phase":"start", "mode":"EXECUTE" if args.execute else "PREVIEW",
                      "issue_type":MAPPINGS[args.kind][1],
                      "source_project":args.jira_project, "target_tracker":args.tracker_id,
                      "mapped_text_fields":source_field_names(mapping),
                      "mapped_choice_fields":[row["cb"]["label"] for row in choice_rows(mapping)] if args.include_options else [],
                      "mapped_user_fields":[row["cb"]["label"] for row in user_rows(mapping)] if args.include_users else [],
                      "relation_capture":args.capture_relations,
                      "out_of_scope":"Unsupported Choice / work-config, Comments, Attachments, Relations, Test Run, History"},
                     ensure_ascii=False), flush=True)
    visited = set()
    while True:
        page, reported_total = search_page(jira, args.jira_project, args.kind, columns,
                                           offset, args.batch_size)
        if offset == 0:
            total = reported_total
        elif total != reported_total:
            raise ApiError("Jira search total changed during batch; stop for consistent restart")
        if not page:
            if offset < total:
                raise ApiError("Jira pagination ended before total count")
            break
        for issue in page:
            if args.limit and inspected >= args.limit:
                break
            key = issue.get("key") if isinstance(issue, dict) else None
            src = issue.get("fields") if isinstance(issue, dict) else None
            if not isinstance(key, str) or key in visited or not isinstance(src, dict):
                raise ApiError("Invalid/duplicate Jira issue in pagination")
            if ((src.get("project") or {}).get("key") != args.jira_project or
                    (src.get("issuetype") or {}).get("name") != MAPPINGS[args.kind][1]):
                raise ApiError("Jira page returned an Issue outside selected project/type")
            visited.add(key)
            inspected += 1
            relations = extract_relations(args.kind, issue) if args.capture_relations else []
            old = events.get(key)
            if old is not None:
                if old.get("status") == "VERIFIED":
                    skipped += 1
                    print(json.dumps({"jira":key,"status":"SKIPPED_VERIFIED","cb":old.get("cb")}),
                          flush=True)
                    continue
                raise ApiError("Prior unresolved POST attempt for " + key +
                               ". Inspect Codebeamer and journal before retrying.")
            payload = mapped_payload(mapping, src, resolved, jira_url=config["jira"]["url"],
                                     jira_key=key, link_field_id=link_id)
            if args.include_options:
                overrides = config.get("migration", {}).get("option_name_mapping", {}).get(args.kind, {})
                add_options(payload, src, mapping, option_bindings, overrides)
            if args.include_users:
                overrides = config.get("migration", {}).get("user_name_mapping", {}).get(args.kind, {})
                add_users(payload, src, mapping, user_bindings, cb, overrides, user_cache)
            if not args.execute:
                print(json.dumps({"jira":key,"status":"READY",
                                  "field_count":len(payload.get("customFields", []))},
                                 ensure_ascii=False),flush=True)
                continue
            append_event(journal, {"jira":key,"tracker":args.tracker_id,"status":"POST_ATTEMPT"})
            try:
                result = cb.request("POST", f"/v3/trackers/{args.tracker_id}/items", payload)
            except Exception as exc:
                append_event(journal, {"jira":key,"tracker":args.tracker_id,
                                       "status":"POST_UNKNOWN_OR_FAILED",
                                       "error_type":type(exc).__name__})
                raise
            item_id = result.get("id") if isinstance(result, dict) else None
            if type(item_id) is not int or item_id <= 0:
                raise ApiError("POST response lacked Item ID for " + key + "; reconcile before retry")
            append_event(journal, {"jira":key,"tracker":args.tracker_id,
                                   "status":"POST_RETURNED","cb":item_id})
            verify_item(cb, item_id, payload)
            append_event(journal, {"jira":key,"tracker":args.tracker_id,
                                   "status":"VERIFIED","cb":item_id})
            events[key] = {"jira":key,"status":"VERIFIED","cb":item_id}
            created += 1
            print(json.dumps({"jira":key,"status":"VERIFIED","cb":item_id}),flush=True)
        offset += len(page)
        if offset >= total or (args.limit and inspected >= args.limit):
            break
    print(json.dumps({"status":"COMPLETE_SCALAR_PHASE_ONLY","project":args.jira_project,
                      "kind":args.kind,"source_total":total,"inspected":inspected,
                      "created":created,"skipped":skipped,
                      "note":"Not a full field/relationship migration"},ensure_ascii=False),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
