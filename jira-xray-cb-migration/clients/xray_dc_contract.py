"""Optional read-only Xray Server/Data Center REST discovery.

Cloud GraphQL and cloud /api/v2 authentication are NOT compatible with Jira
Server/Data Center Xray endpoints. Paths below come from published Xray
Server/DC REST docs; server-specific availability must still be tested.
This module NEVER sends POST/PUT/DELETE or creates Codebeamer records.
"""
from __future__ import annotations

import re
from clients.http import ApiError
from clients.jira_client import segment

DC_READS = {
    "test_steps": "/rest/raven/1.0/api/test/{key}/step",
    "test_sets_for_test": "/rest/raven/1.0/api/test/{key}/testsets",
    "test_executions_for_test": "/rest/raven/1.0/api/test/{key}/testexecutions",
    "test_plans_for_test": "/rest/raven/1.0/api/test/{key}/testplans",
    "test_set_tests": "/rest/raven/1.0/api/testset/{key}/test",
    "test_execution_tests": "/rest/raven/1.0/api/testexec/{key}/test",
    "test_plan_tests": "/rest/raven/1.0/api/testplan/{key}/test",
    "test_plan_executions": "/rest/raven/1.0/api/testplan/{key}/testexecution",
    "test_run": "/rest/raven/1.0/api/testrun/{id}",
    "test_run_steps": "/rest/raven/1.0/api/testrun/{id}/step",
    "test_run_comment": "/rest/raven/1.0/api/testrun/{id}/comment",
    "test_run_evidence": "/rest/raven/1.0/api/testrun/{id}/attachment",
}
_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$")
_RUN_ID = re.compile(r"^[0-9]+$")


def dc_read_path(kind, identifier):
    if kind not in DC_READS:
        raise ApiError("Unverified Xray Server/DC GET operation: " + str(kind))
    value = str(identifier)
    if not (_RUN_ID.fullmatch(value) if kind.startswith("test_run") else _ISSUE_KEY.fullmatch(value)):
        raise ApiError("Invalid Xray " + kind + " identifier; expected Jira issue key or numeric Run ID")
    # Quote path components, even though identifiers are already constrained.
    return DC_READS[kind].replace("{key}", segment(value)).replace("{id}", segment(value))


def inspect_xray_dc(http, kind, identifier, archive=None):
    """Probe a single documented Server/DC endpoint without writing.

    Retain raw payload as evidence. Never declare a membership count final:
    several Xray DC endpoints are paginated on supported versions.
    """
    path = dc_read_path(kind, identifier)
    raw = http.request("GET", path)
    if not isinstance(raw, (list, dict)):
        raise ApiError("Unexpected Xray DC response shape for " + kind)
    if archive is not None:
        archive("xray-dc-" + kind, str(identifier), raw)
    return {
        "api_family": "xray-server-dc",
        "kind": kind,
        "source_id": str(identifier),
        "path": path,
        "raw": raw,
        "pagination_verified": False,
        "complete": False,
        "write_performed": False,
    }
