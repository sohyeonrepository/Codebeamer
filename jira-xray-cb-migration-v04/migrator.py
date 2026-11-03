#!/usr/bin/env python3
"""Jira DC/Xray -> Codebeamer migration prototype v0.1.

SAFE DEFAULT: only extracts Jira data, builds target plan, saves immutable source evidence.
Use --apply-items to create/update basic Codebeamer tracker items *only* after mapping config.
References, Test Run writes, native history writes are NOT attempted in v0.1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

ISSUE_TYPES = {"Test Plan": "test_plan", "Test": "test_case", "Test Case": "test_case",
               "Test Set": "test_set", "Test Execution": "test_execution"}
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$", re.I)


def save(path: Path, payload: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class Client:
    def __init__(self, host: str, user: str, token: str):
        if not host.startswith(("https://", "http://")):
            raise ValueError("host must start with https:// or http://")
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, token)
        self.session.headers["Accept"] = "application/json"
        self.session.verify = True

    def request(self, method: str, path: str, **kwargs):
        for attempt in range(3):
            try:
                response = self.session.request(method, self.host + path, timeout=45, allow_redirects=False, **kwargs)
                if method == "GET" and response.status_code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                if 300 <= response.status_code < 400:raise RuntimeError('Redirect refused; verify server URL')
                response.raise_for_status()
                if not response.content:
                    return {}
                try:
                    return response.json()
                except ValueError as e:
                    raise RuntimeError(f"Non-JSON response from {path}; verify URL and authentication") from e
            except requests.RequestException:
                if attempt == 2 or method != "GET":
                    raise
                time.sleep(attempt + 1)


def jira_issues(api: Client, project: str):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project):
        raise ValueError("Invalid Jira project key")
    jql = f'project = "{project}" AND issuetype in ("Test Plan","Test","Test Case","Test Set","Test Execution") ORDER BY key'
    start, out = 0, []
    while True:
        page = api.request("GET", "/rest/api/2/search", params={
            "jql": jql, "startAt": start, "maxResults": 100, "fields": "*all"})
        batch = page.get("issues", [])
        out.extend(batch)
        start += len(batch)
        if not isinstance(page.get("total"),int):raise ValueError("Pagination total unavailable")
        if not batch and start < page["total"]:raise ValueError("Incomplete pagination: empty page before total")
        if start >= page["total"]:
            return out


def paginate(api: Client, endpoint: str, property_name: str):
    start, out = 0, []
    while True:
        page = api.request("GET", endpoint, params={"startAt": start, "maxResults": 100})
        batch = page.get(property_name, [])
        out.extend(batch)
        start += len(batch)
        if not isinstance(page.get("total"),int):raise ValueError("Pagination total unavailable")
        if not batch and start < page["total"]:raise ValueError("Incomplete pagination: empty page before total")
        if start >= page["total"]:
            return out


def jira_details(api: Client, issue: dict):
    key = issue["key"]
    out = dict(issue)
    # Jira Data Center: if /changelog is disabled on a given version, mark as error
    # rather than silently declaring the history complete.
    out["_history"] = paginate(api, f"/rest/api/2/issue/{key}/changelog", "values")
    out["_comments"] = paginate(api, f"/rest/api/2/issue/{key}/comment", "comments")
    return out


def kind(issue: dict):
    return ISSUE_TYPES.get(issue.get("fields", {}).get("issuetype", {}).get("name"))


def extract_keys(obj: Any):
    """Read explicit issue-key members from Xray response; do not guess relationships."""
    found = set()
    def walk(x):
        if isinstance(x, list):
            for v in x: walk(v)
        elif isinstance(x, dict):
            for field in ("key", "issueKey", "testKey", "testExecutionKey"):
                v = x.get(field)
                if isinstance(v, str) and KEY_PATTERN.fullmatch(v): found.add(v)
            for k in ("results", "tests", "testExecutions", "entries", "data", "values"):
                if k in x: walk(x[k])
    walk(obj)
    return sorted(found)


def xray_relations(api: Client, issue: dict, cfg: dict):
    """Paths are version-dependent; user config must provide verified Xray DC paths."""
    result = {}
    paths = cfg.get("xray_paths", {}).get(kind(issue), {})
    for name, pattern in paths.items():
        if "{key}" not in pattern:
            raise ValueError(f"Xray path for {name} must contain {{key}}")
        url = pattern.replace("{key}", issue["key"])
        payload = api.request("GET", url)
        result[name] = {"keys": extract_keys(payload), "raw": payload}
    return result


def load_config(path: Path):
    cfg = read(path)
    if not isinstance(cfg, dict): raise ValueError("Invalid config JSON")
    for field in ("jira_project", "cb_tracker_ids"):
        if field not in cfg: raise ValueError(f"Missing config: {field}")
    return cfg


def build_plan(issues: list[dict], cfg: dict):
    tracker_ids = cfg["cb_tracker_ids"]
    plan = []
    for issue in issues:
        typ = kind(issue)
        if typ is None: continue
        tracker = tracker_ids.get(typ)
        if not isinstance(tracker, int) or tracker <= 0:
            raise ValueError(f"Set valid cb_tracker_ids.{typ}; no default tracker is assumed")
        fields = issue.get("fields", {})
        plan.append({
            "jira_key": issue["key"], "kind": typ, "cb_tracker_id": tracker,
            "summary": fields.get("summary") or issue["key"],
            "description_source": fields.get("description"),
            "field_mappings_pending": cfg.get("field_map", {}).get(typ, {}),
            "history_entries": len(issue.get("_history", [])),
            "comment_count": len(issue.get("_comments", [])),
            "xray_relations": {k: v["keys"] for k, v in issue.get("_xray_relations", {}).items()},
            "jira_issue_links": fields.get("issuelinks", []),
            "attachments": [{"id": v.get("id"), "filename": v.get("filename"), "content": v.get("content")}
                             for v in fields.get("attachment", [])],
            "status": "PLANNED",
        })
    return plan


def cb_item_create(api: Client, tracker_id: int, summary: str):
    # Codebeamer 3.x Swagger needs to be validated against target 3.0.0.7 first.
    # Keep create endpoint in config to avoid hard-coded assumptions.
    raise NotImplementedError("CB creation is intentionally blocked pending validated POST schema")


def export(args):
    cfg = load_config(Path(args.config))
    out = Path(args.output)
    api = Client(os.environ["JIRA_URL"], os.environ["JIRA_USER"], os.environ["JIRA_TOKEN"])
    issues = jira_issues(api, cfg["jira_project"])
    details, failures = [], []
    for issue in issues:
        try:
            item = jira_details(api, issue)
            item["_xray_relations"] = xray_relations(api, item, cfg)
            details.append(item)
        except Exception as e:
            failures.append({"jira_key": issue["key"], "error": str(e)})
    save(out / "source_issues.json", details)
    save(out / "errors.json", failures)
    save(out / "migration_plan.json", build_plan(details, cfg))
    save(out / "id_map.json", read(out / "id_map.json", {}))
    save(out / "history_java_queue.json", [
        {"jira_key": i["key"], "jira_history": i["_history"], "jira_comments": i["_comments"]}
        for i in details])
    print(f"Exported={len(details)} failed={len(failures)} planned={len(build_plan(details,cfg))}")
    print(f"Review: {out / 'migration_plan.json'} and {out / 'errors.json'}")
    if failures: return 2
    return 0


def validate(args):
    out = Path(args.output)
    plan = read(out / "migration_plan.json", [])
    keys = [x["jira_key"] for x in plan]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate Jira keys in migration plan")
    mapped = read(out / "id_map.json", {})
    print(json.dumps({"planned": len(plan), "mapped": len(mapped),
                      "unmapped": sorted(set(keys) - set(mapped)),
                      "history_write_status": "NOT_IMPLEMENTED",
                      "reference_write_status": "NOT_IMPLEMENTED",
                      "test_run_write_status": "NOT_IMPLEMENTED"},
                     ensure_ascii=False, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=["export", "validate"])
    p.add_argument("--config", default="config.example.json")
    p.add_argument("--output", default="output")
    args = p.parse_args()
    try:
        return export(args) if args.operation == "export" else validate(args)
    except (KeyError, ValueError, requests.RequestException, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
