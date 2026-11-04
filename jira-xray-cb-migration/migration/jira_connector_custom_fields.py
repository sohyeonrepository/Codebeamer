"""Optional Jira Checklist custom-field adapter, inspired by intland/cb-jira-checklist.

Implements data conversion only. Does NOT invoke codeBeamer internal APIs,
create native revisions, or assert support for Codebeamer 3.x plugin APIs.

Reference: https://github.com/intland/cb-jira-checklist
Original source copyright (c) 2021 Intland Software GmbH, BSD-3-Clause.
This Python implementation is independently written for the migration pipeline.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from migration.dates import parse_iso

WIKI_HEADER = "[{Checklist\r\n\r\n"
WIKI_FOOTER = "\r\n}]"
_STATUS_NAMES = {"notApplicable": "N/A", "inProgress": "In Progress", "blocked": "Blocked"}


class ChecklistConversionError(ValueError):
    pass


def _stripped(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _iso(value: str) -> str:
    """Accept Jira offset timestamps or checklist's older date-only formats."""
    try:
        parsed = parse_iso(value)
    except (ValueError, TypeError):
        parsed = None
        for fmt in ("%d/%b/%y", "%d/%m/%y", "%d/%b/%Y", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ChecklistConversionError("Unsupported Jira Checklist date format: " + value)
    if parsed.tzinfo is None:
        # A date without a timezone cannot be safely reinterpreted as UTC.
        return parsed.isoformat()
    return parsed.isoformat()


def _markup(text: str) -> str:
    """Safe limited Markdown-to-Codebeamer wiki conversion.

    Only simple inline syntax is rewritten. Unknown formatting remains intact;
    attachment/link relinking is handled by the normal migration pipeline.
    """
    text = re.sub(r"(?<!\\)\*\*([^*\n]+)\*\*", r"__\1__", text)
    text = re.sub(r"(?<!\\)\*([^*\n]+)\*", r"''\1''", text)
    return text


def to_codebeamer_items(source: list[dict], *, priority_mapping: dict | None = None) -> list[dict]:
    """Map Checklist for Jira list items to the legacy CB Checklist Wiki plugin JSON.

    Do not mutate the original source; retain unknown attributes to avoid data loss.
    Jira status/priority mappings must be supplied or source IDs are kept explicitly.
    """
    if not isinstance(source, list):
        raise ChecklistConversionError("Checklist source must be a JSON array")
    converted = []
    for index, raw in enumerate(source):
        if not isinstance(raw, dict):
            raise ChecklistConversionError(f"Checklist item {index} must be an object")
        item = deepcopy(raw)
        item.pop("rank", None)  # Jira-only, read-only ranking
        if item.pop("isHeader", False):
            item["header"] = True
        if item.pop("option", False) or item.get("globalItemId") is not None:
            item["pinned"] = True
        if isinstance(item.get("name"), str):
            content = _markup(item["name"])
            if "\n>>" in content:
                name, description = content.split("\n>>", 1)
                item["name"] = name.strip()
                item["description"] = description.strip()
            else:
                item["name"] = content
        priority_id = item.pop("priorityId", None)
        if priority_id is not None:
            target = (priority_mapping or {}).get(str(priority_id))
            if target is None:
                item["priorityId"] = priority_id  # unresolved, never silently assign a wrong option
            else:
                item["priority"] = deepcopy(target)
        due_date = item.pop("dueDate", None)
        if due_date:
            item["endDate"] = _iso(str(due_date))
        status = item.get("status")
        if isinstance(status, dict):
            status_id = status.get("id")
            if status_id == "none":
                item.pop("status", None)
            elif status_id is not None:
                item["status"] = {
                    "name": status.get("name") or _STATUS_NAMES.get(str(status_id)) or str(status_id),
                    "style": str(status_id),
                }
        converted.append(item)
    return converted


def to_codebeamer_wiki(source: list[dict], *, priority_mapping: dict | None = None) -> str:
    items = to_codebeamer_items(source, priority_mapping=priority_mapping)
    return WIKI_HEADER + json.dumps(items, ensure_ascii=False, indent=2) + WIKI_FOOTER


def parse_checklist_changes(lines: str | None) -> dict[int, str]:
    """Parse numbered Checklist changelog lines into an ordered, lossless delta map.

    The result is source evidence, NOT a CB TrackerItemRevisionDto. For example
    '1) [added] first\n2) [removed] second' returns both numbered descriptions.
    """
    if not lines:
        return {}
    if not isinstance(lines, str):
        raise ChecklistConversionError("Checklist changelog must be text")
    changes = {}
    key = None
    buffer = []
    for raw in lines.splitlines():
        match = re.match(r"^\s*(\d+)\)\s*(.*)$", raw)
        if match:
            if key is not None:
                changes[key] = "\n".join(buffer).strip()
            key, buffer = int(match.group(1)), [match.group(2)]
        elif key is not None:
            buffer.append(raw)
        elif "items were reordered" in raw.lower():
            changes[9999] = "[reordered]"
        elif raw.strip():
            raise ChecklistConversionError("Unrecognized checklist changelog line: " + raw[:100])
    if key is not None:
        changes[key] = "\n".join(buffer).strip()
    return changes


def checklist_metadata(source_field: dict) -> dict:
    """Propose field metadata; never write a tracker configuration automatically."""
    if not isinstance(source_field, dict):
        raise ChecklistConversionError("Checklist field metadata must be an object")
    allowed = source_field.get("allowedValues") or []
    if not isinstance(allowed, list):
        raise ChecklistConversionError("allowedValues must be a list")
    return {
        "target_field_type": "WikiText",
        "multipleSelection": False,
        "breakRow": True,
        "colspan": 3,
        "defaultValue": to_codebeamer_wiki(allowed),
        "requires_user_confirmation": True,
    }
