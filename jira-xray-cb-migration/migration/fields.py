from migration.dates import parse_iso
from clients.http import ApiError


def convert(value, kind, mapping, identity_key=None):
    if value is None:
        return None
    if kind == 'jira_checklist':
        from migration.jira_connector_custom_fields import to_codebeamer_wiki
        return to_codebeamer_wiki(value, priority_mapping=mapping)
    if kind == 'json':
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if kind == 'raw':
        return value
    if kind == 'stringify':
        # Explicit conversion to a scalar Text/WikiText/Wiki Link field.
        # Unlike "raw", this never sends a numeric or structured payload
        # into a string-only Codebeamer FieldValue.
        import json
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value).lower() if isinstance(value, bool) else str(value)
        if isinstance(value, list):
            return '\\n'.join(convert(part, 'stringify', mapping) for part in value)
        if isinstance(value, dict):
            for key in ('value', 'name', 'displayName', 'key'):
                if isinstance(value.get(key), (str, int, float)):
                    return str(value[key])
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        raise ApiError('Unsupported source for explicit Text conversion')
    if kind == 'parse_number':
        from decimal import Decimal, InvalidOperation
        if isinstance(value, bool):
            raise ApiError('Boolean cannot be converted to Number')
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            raise ApiError('Jira value is not a valid number')
        if not parsed.is_finite():
            raise ApiError('Non-finite number is not supported')
        return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
    if kind == 'parse_boolean':
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.strip().casefold() in ('true', 'yes', 'y', '1'):
                return True
            if value.strip().casefold() in ('false', 'no', 'n', '0'):
                return False
        raise ApiError('Jira text cannot be converted safely to Boolean')
    if kind == 'text':
        if not isinstance(value, str):
            raise ApiError('Non-text source requires explicit conversion')
        return value
    if kind in ('text_multi', 'text_list'):
        if not isinstance(value, list) or any(not isinstance(part, str) for part in value):
            raise ApiError('Jira multi-text requires an array of strings; values cannot be silently dropped')
        if kind == 'text_list':
            return list(value)
        # Codebeamer Text/WikiText is a single text value; keep line breaks and source JSON.
        return '\n'.join(value)
    if kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ApiError('Expected numeric source')
        return value
    if kind == 'boolean':
        if not isinstance(value, bool):
            raise ApiError('Expected boolean source')
        return value
    if kind in ('date', 'datetime'):
        from datetime import datetime
        parse_iso(value)
        return value
    if kind in ('user', 'status', 'option', 'reference'):
        if isinstance(value, list):
            return [convert(v, kind, mapping, identity_key) for v in value]
        if isinstance(value, dict):
            if identity_key:
                identifiers = [value.get(identity_key)]
            elif kind == 'user':
                identifiers = [value.get(k) for k in ('accountId', 'id', 'name', 'displayName')]
            elif kind in ('option', 'status'):
                # Legacy mappings use display names, newer mappings may use ID.
                identifiers = [value.get(k) for k in ('name', 'value', 'id')]
            else:
                identifiers = [value.get(k) for k in ('id', 'key', 'name')]
            matches = [(str(i), mapping[str(i)]) for i in identifiers
                       if i is not None and str(i) in mapping]
            if not matches:
                raise ApiError('Missing ' + kind + ' mapping for source identifiers')
            # Multiple aliases with conflicting targets cannot be resolved safely.
            if any(mapped != matches[0][1] for _, mapped in matches[1:]):
                raise ApiError('Conflicting ' + kind + ' mapping aliases for one source value')
            return matches[0][1]
        identity = str(value)
        if identity not in mapping:
            raise ApiError('Missing ' + kind + ' mapping: ' + identity)
        return mapping[identity]
    raise ApiError('Unsupported conversion: ' + kind)


def payload(fields, mappings, settings):
    output = {}
    for source, spec in mappings.items():
        if spec.get('deferred_reference'):continue
        if source not in fields:
            raise ApiError('Source field absent: ' + source)
        group = {'user': 'user_mapping', 'status': 'status_mapping'}.get(spec['type'], 'value_mapping')
        converted = convert(fields[source], spec['type'],
                            spec.get('value_mapping', settings.get(group, {})),
                            spec.get('source_identity'))
        if spec.get('native_array'):
            if spec['target'] != 'resolutions' or spec['type'] != 'option':
                raise ApiError('Only verified native Resolution may use an array payload')
            # The Jira Resolution field is often empty on unclosed Issues.
            # Omit it rather than POSTing null into a native array property.
            if converted is None:
                continue
            if isinstance(converted, dict):
                converted = [converted]
            if not isinstance(converted, list) or any(
                not isinstance(v, dict) or type(v.get('id')) is not int for v in converted
            ):
                raise ApiError('Native Resolution requires mapped numeric Codebeamer option IDs')
        output[spec['target']] = converted
    return output


def compare_definitions(source, target, mappings):
    issues = []
    targets = {str(f.get('id', f.get('referenceId'))): f for f in target}
    sources = {f['id']: f for f in source}
    for key, spec in mappings.items():
        field = targets.get(str(spec.get('field_id')))
        if key not in sources or field is None:
            issues.append({'field': key, 'reason': 'source or target definition missing'})
            continue
        # Type IDs are deployment-specific and must be explicitly reconciled.
        for attr, expected_key in [('typeId', 'expected_type_id'), ('mandatory', 'expected_mandatory'),
                                   ('multipleSelection', 'expected_multiple'), ('options', 'expected_options'),
                                   ('reference', 'expected_reference')]:
            if spec.get('deferred_reference') and expected_key not in spec:continue
            if expected_key not in spec or field.get(attr) != spec[expected_key]:
                issues.append({'field': key, 'reason': 'unverified or conflicting ' + attr})
    return issues
