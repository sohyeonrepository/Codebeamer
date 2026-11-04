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
        identity = str(value.get('accountId', value.get('name', value.get('id')))) if isinstance(value, dict) else str(value)
        if identity_key and isinstance(value,dict): identity = str(value.get(identity_key))
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
        output[spec['target']] = convert(fields[source], spec['type'], spec.get('value_mapping', settings.get(group, {})), spec.get('source_identity'))
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
