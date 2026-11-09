"""Existing Codebeamer ChoiceOptionReference mapping by displayed option names.

Never invent option IDs; only OptionChoiceField / ChoiceOptionReference accepted.
Unmapped and ambiguous values block POST before writes.
"""
from clients.http import ApiError
from mapping_helper import field_identifier


def norm(value):
    return " ".join(str(value or "").strip().casefold().split())


def source_value(fields, path):
    parts = path.split(".")
    value = fields.get(parts[0])
    for part in parts[1:]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ApiError("Jira nested field must be an object: " + path)
        value = value.get(part)
    return value


def find_cb_field(cb, tracker, config_fields, row):
    if not isinstance(config_fields, list):
        raise ApiError("Codebeamer tracker field definitions missing")
    label = norm(row["cb"]["label"])
    found = [field for field in config_fields if isinstance(field, dict) and (
        norm(field.get("label")) == label or norm(field.get("name")) == label)]
    if len(found) != 1:
        raise ApiError("Missing or ambiguous Codebeamer choice field: " + row["cb"]["label"])
    field_id = field_identifier(found[0])
    if type(field_id) is not int:
        raise ApiError("CB choice field internal ID unavailable: " + row["cb"]["label"])
    detail = cb.request("GET", f"/v3/trackers/{tracker}/fields/{field_id}")
    if (not isinstance(detail, dict) or detail.get("id") != field_id or
        detail.get("type") != "OptionChoiceField" or
        detail.get("referenceType") != "ChoiceOptionReference" or
        not str(detail.get("valueModel", "")).startswith("ChoiceFieldValue") or
        not isinstance(detail.get("options"), list)):
        raise ApiError("CB field has no verified OptionChoiceField / ChoiceOptionReference model: " + row["cb"]["label"])
    return field_id, detail


def choice_names(value, path):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out = []
    for item in values:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name", item.get("value"))
        else:
            raise ApiError("Unsupported Jira choice input: " + path)
        if not isinstance(name, str) or not name.strip():
            raise ApiError("Jira choice value has no name: " + path)
        if norm(name) not in {norm(v) for v in out}:
            out.append(name)
    return out


def map_choice(row, detail, source, overrides=None):
    names = choice_names(source_value(source, row["jira"]), row["jira"])
    if not names:
        return None
    options = detail["options"]
    refs = []
    for original in names:
        wanted = (overrides or {}).get(original, original)
        matches = [opt for opt in options if isinstance(opt, dict) and
                   norm(opt.get("name")) == norm(wanted) and
                   type(opt.get("id")) is int and opt.get("type") == "ChoiceOptionReference"]
        if len(matches) != 1:
            raise ApiError("Unmapped or ambiguous CB choice option for " + row["jira"] +
                           ": " + original)
        refs.append({"id": matches[0]["id"],"name":matches[0]["name"],
                     "type":"ChoiceOptionReference"})
    if detail.get("multipleValues") is False and len(refs) > 1:
        raise ApiError("Multiple Jira values for single-choice CB field: " + row["cb"]["label"])
    return refs


def choice_rows(mapping):
    # Work/config and User choices are NOT OptionChoiceReferences.
    # Includes e.g. Jira Priority, Squad, Security Level, Labels.
    return [row for row in mapping["phase_1"] if row.get("mode") == "mapped" and
            norm(row["cb"].get("type")) in ("choice", "choice / option") and
            row["cb"].get("registration") != "CREATE_FIELD"]


def prepare_options(cb, tracker, config_fields, mapping):
    return {row["jira"]: find_cb_field(cb, tracker, config_fields, row)
            for row in choice_rows(mapping)}


def add_options(payload, source, mapping, options, overrides=None):
    custom = payload.setdefault("customFields", [])
    for row in choice_rows(mapping):
        field_id, detail = options[row["jira"]]
        aliases = (overrides or {}).get(row["jira"], {})
        refs = map_choice(row, detail, source, aliases)
        if refs is None:
            continue
        if any(f.get("fieldId") == field_id for f in custom):
            raise ApiError("Duplicate target choice field: " + row["cb"]["label"])
        custom.append({"fieldId":field_id, "type":"ChoiceFieldValue", "values":refs})
    if not custom:
        payload.pop("customFields", None)
    return payload


def resolve_option(options, selection):
    """Resolve saved ID mappings and legacy names against current server options."""
    options = [o for o in options if isinstance(o, dict)]
    if isinstance(selection, dict):
        identifier = selection.get('id')
        matches = [o for o in options if type(identifier) is int and o.get('id') == identifier]
    elif isinstance(selection, str) and selection.strip():
        matches = [o for o in options if norm(o.get('name')) == norm(selection)]
    else:
        matches = []
    matches = [o for o in matches if type(o.get('id')) is int and isinstance(o.get('name'), str)]
    if len(matches) != 1:
        raise ApiError('CB 옵션 ID/이름을 유일하게 확인할 수 없습니다: ' + str(selection))
    return matches[0]


def option_default(options, saved, identity, label):
    """Keep explicit saved mappings; unresolved IDs require user review."""
    if identity in saved or label in saved:
        try:
            return resolve_option(options, saved.get(identity, saved.get(label)))['name']
        except ApiError:
            return ''
    matches = [o for o in options if norm(o.get('name')) == norm(label)]
    return matches[0]['name'] if len(matches) == 1 else (label if not matches else '')
