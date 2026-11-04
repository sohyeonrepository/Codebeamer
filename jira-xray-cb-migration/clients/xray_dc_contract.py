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


def collect_xray_dc(http, kind, identifier, profile, archive=None):
    """Collect only a locally verified envelope; retain every raw page.

    profile: verified=True, items_key, total_key, identity_key,
    offset_param, limit_param, page_size and max_pages. Dotted keys are
    deliberately unsupported: deployment adapters must describe the envelope.
    A bare array is accepted only with explicit unpaginated=True.
    """
    path = dc_read_path(kind, identifier)
    if profile.get('verified') is not True:
        raise ApiError('Xray response/pagination contract requires verification: ' + kind)
    identity = profile.get('identity_key')
    if not isinstance(identity, str) or not identity:
        raise ApiError('Xray collection requires an identity_key')
    unpaginated = profile.get('unpaginated') is True
    size, maximum = profile.get('page_size', 100), profile.get('max_pages', 1000)
    if any(type(v) is not int or v < 1 for v in (size, maximum)):
        raise ApiError('Invalid Xray pagination bounds')
    if not unpaginated and any(not isinstance(profile.get(k), str) or not profile[k]
                              for k in ('items_key', 'total_key', 'offset_param', 'limit_param')):
        raise ApiError('Xray collection requires explicit envelope and pagination parameters')
    output, seen, expected = [], set(), None
    for page in range(1 if unpaginated else maximum):
        query = {} if unpaginated else {profile['offset_param']: len(output), profile['limit_param']: size}
        from urllib.parse import urlencode
        raw = http.request('GET', path + ('?' + urlencode(query) if query else ''))
        if archive is not None:
            archive('xray-dc-' + kind, str(identifier) + '-page-' + str(page), raw)
        if unpaginated:
            items = raw
            total = len(raw) if isinstance(raw, list) else None
        else:
            items = raw.get(profile['items_key']) if isinstance(raw, dict) else None
            total = raw.get(profile['total_key']) if isinstance(raw, dict) else None
        if not isinstance(items, list) or type(total) is not int or total < 0:
            raise ApiError('Invalid Xray collection envelope: ' + kind)
        if expected is not None and total != expected:
            raise ApiError('Xray total changed during collection: ' + kind)
        expected = total
        for item in items:
            value = item.get(identity) if isinstance(item, dict) else None
            if not isinstance(value, (str, int)) or isinstance(value, bool) or str(value) == '':
                raise ApiError('Missing Xray member identity: ' + kind)
            key = str(value)
            if key in seen:
                raise ApiError('Duplicate Xray member identity: ' + key)
            seen.add(key)
            output.append(item)
        if len(output) > total:
            raise ApiError('Xray collection exceeds declared total')
        if len(output) == total:
            return {'kind': kind, 'source_id': str(identifier), 'items': output,
                    'complete': True, 'pagination_verified': True, 'write_performed': False}
        if not items:
            raise ApiError('Xray page missing before declared total')
    raise ApiError('Xray pagination bound reached before completion')
