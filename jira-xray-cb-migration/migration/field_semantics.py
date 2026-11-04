"""Evidence-based Jira field semantics for the Jira → Codebeamer Wizard.

The Jira type label 'Text / Multi' can refer to free-form string arrays,
while select/multiselect/checkbox fields are Choice. Never infer Choice
from the word Multi alone. Sources: Jira REST /field + createmeta metadata,
and the custom-field metadata pattern used by intland/cb-jira-checklist.
"""
from __future__ import annotations

# Exact Jira custom-field identifiers are evidence, not heuristics based on
# source label or Codebeamer numeric typeId.
_JIRA_SELECT_CUSTOM_TYPES = frozenset({
    "select", "multiselect", "checkboxes", "radiobuttons", "cascadingselect",
    "com.atlassian.jira.plugin.system.customfieldtypes:select",
    "com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
    "com.atlassian.jira.plugin.system.customfieldtypes:checkboxes",
    "com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons",
    "com.atlassian.jira.plugin.system.customfieldtypes:cascadingselect",
})

_GENERIC = {
    "string": "text", "integer": "number", "number": "number",
    "boolean": "boolean", "date": "date", "datetime": "datetime",
    "option": "option", "priority": "option", "status": "status",
    "user": "user", "version": "option", "component": "option",
    "issue": "reference", "issuelink": "reference",
}


def jira_field_semantics(definition):
    """Return (kind, multiple, evidence).

    Returns kind=None when source metadata is insufficient or conflicting.
    The third element explains the source of a classification for reviewing.
    """
    schema = definition.get("schema") or {}
    if not isinstance(schema, dict):
        return None, False, "Jira schema is missing or invalid"
    typ = schema.get("type")
    if not isinstance(typ, str):
        return None, False, "Jira schema.type is unavailable"
    multiple = typ == "array"
    item_type = schema.get("items") if multiple else typ
    kind = _GENERIC.get(item_type)
    custom = str(schema.get("custom") or "").casefold().strip()
    # The packaged Codebeamer Jira Connector uses a per-add-on
    # CustomFieldPlugin registry. Such fields cannot be treated as a
    # generic Choice solely because they expose allowedValues.
    # The published Intland Checklist extension has this exact plugin ID.
    if custom == "com.okapya.jira.checklist:checklist":
        return None, multiple, "Jira Checklist add-on requires explicit jira_checklist adapter and compatible target WikiText plugin"
    explicit_choice = custom in _JIRA_SELECT_CUSTOM_TYPES
    allowed = definition.get("allowedValues")
    choices_present = isinstance(allowed, list) and bool(allowed)
    # Selecting from a known list is Choice even when Jira returns
    # schema.items='string' (some marketplace add-ons behave this way).
    if item_type == "string":
        if explicit_choice:
            return "option", multiple, "Jira schema.custom identifies a select-type field"
        if choices_present:
            # A nonempty allowedValues list is strong evidence of a
            # predefined choice set, regardless of whether values are strings
            # or objects. Do not infer this from sample issue values alone.
            return "option", multiple, "Jira allowedValues defines selectable values"
        return "text", multiple, "free-form Jira string value(s)"
    if explicit_choice and kind not in ("option", None):
        return None, multiple, "Jira schema.custom conflicts with schema.items"
    if kind is None:
        return None, multiple, "unrecognized Jira field schema"
    return kind, multiple, "Jira schema.type/items"


def choice_identity(value):
    """Stable identity for select items with or without Jira option IDs."""
    if isinstance(value, dict):
        for field in ("id", "value", "name"):
            if value.get(field) is not None:
                return str(value[field])
        raise ValueError("Jira Choice object has no id, value or name")
    if isinstance(value, str):
        return value
    raise ValueError("Jira Choice must be a string or option object")
