from clients.http import ApiError


def convert(value, kind, mapping):
    if value is None:
        return None
    if kind == 'json':
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if kind == 'raw':
        return value
    if kind == 'text':
        if not isinstance(value, str):
            raise ApiError('Non-text source requires explicit conversion')
        return value
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
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    if kind in ('user', 'status', 'option', 'reference'):
        if isinstance(value, list):
            return [convert(v, kind, mapping) for v in value]
        identity = str(value.get('accountId', value.get('name', value.get('id')))) if isinstance(value, dict) else str(value)
        if identity not in mapping:
            raise ApiError('Missing ' + kind + ' mapping: ' + identity)
        return mapping[identity]
    raise ApiError('Unsupported conversion: ' + kind)


def payload(fields, mappings, settings):
    output = {}
    for source, spec in mappings.items():
        if source not in fields:
            raise ApiError('Source field absent: ' + source)
        group = {'user': 'user_mapping', 'status': 'status_mapping'}.get(spec['type'], 'value_mapping')
        output[spec['target']] = convert(fields[source], spec['type'], settings.get(group, {}))
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
            if expected_key not in spec or field.get(attr) != spec[expected_key]:
                issues.append({'field': key, 'reason': 'unverified or conflicting ' + attr})
    return issues
