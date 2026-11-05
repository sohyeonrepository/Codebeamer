"""Safe, structured migration diagnostics: no Jira issue values or credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path


def _schema_summary(spec, model):
    if not isinstance(model, str) or not isinstance(spec, dict):
        return {'value_model': model if isinstance(model, str) else None, 'schema_found': False}
    definitions = spec.get('components', {}).get('schemas', spec.get('definitions', {}))
    name = model if model in definitions else model.split('<', 1)[0]
    raw = definitions.get(name)
    if not isinstance(raw, dict):
        return {'value_model': model, 'schema_name': name, 'schema_found': False}
    from mapping_helper import schema_object
    try:
        schema = schema_object(spec, raw)
        props = schema.get('properties', {})
        t = props.get('type', {})
        return {'value_model': model, 'schema_name': name, 'schema_found': True,
                'schema_properties': sorted(props),
                'type_discriminator_enum': t.get('enum') if isinstance(t, dict) else None,
                'writable_value_members': [p for p in ('value', 'values')
                                           if p in props and not props[p].get('readOnly')]}
    except (KeyError, TypeError, ValueError) as exc:
        return {'value_model': model, 'schema_name': name, 'schema_found': True,
                'schema_inspection_error': type(exc).__name__}


def error_classification(error):
    """Classify predictable failures without recording potentially sensitive raw text."""
    import re
    message = str(error)
    if 'Source fields/values changed' in message:
        return 'SOURCE_CHANGED', 'Jira field analysis changed between GET and Import'
    if 'GET transport failure' in message or 'Connection deadline exceeded' in message:
        return 'READ_TIMEOUT', 'Read-only API request timed out or failed'
    if 'POST transport failure' in message or 'PUT transport failure' in message:
        return 'WRITE_OUTCOME_UNKNOWN', 'Write request may have reached the server; reconcile before retry'
    if 'Changelog incomplete' in message:
        return 'JIRA_HISTORY_INCOMPLETE', 'Jira change history cannot be fetched completely'
    if '중복' in message or 'duplicate' in message.lower():
        return 'FIELD_MAPPING_CONFLICT', 'Two or more selected source fields target the same Codebeamer field'
    if 'valueModel' in message or 'FieldValue' in message:
        return 'FIELD_WRITE_CONTRACT', 'A mapped field has an unverified writable value contract'
    if 'New Field requires' in message or 'Tracker Configuration request schema missing' in message:
        return 'FIELD_CREATION_CONTRACT', 'Codebeamer field creation request contract is missing'
    if '지원하지 않는 값 변환' in message:
        return 'SOURCE_VALUE_CONVERSION', 'Jira source-to-target value conversion is unsupported'
    match = re.search(r'\\bGET\\s+[^\\s]+\\s+failed:\\s*HTTP\\s+(\\d+)', message)
    if match:
        return 'HTTP_READ_FAILURE', 'GET HTTP status '+match.group(1)
    return 'UNCLASSIFIED', 'See the error displayed in the Import Wizard and recent request metadata'


def write_diagnostic(wizard, stage, error, field_id=None, issue_scope=None):
    """Write minimal safe metadata needed to reproduce a contract failure."""
    from clients.tracker_fields import binding
    spec = wizard.cb.spec
    scopes = {}
    for scope, entry in wizard.plan.get('scopes', {}).items():
        if issue_scope and scope != issue_scope:
            continue
        field_rows = []
        for source, row in entry.get('rows', {}).items():
            if not row.get('selected', True):
                continue
            target = row.get('target') or {}
            selected = row.get('spec') or {}
            fid = selected.get('field_id')
            model = target.get('valueModel')
            jira_definition = entry.get('analysis', {}).get('fields', {}).get(source, {})
            jira_schema = jira_definition.get('schema') if isinstance(jira_definition.get('schema'),dict) else {}
            field_rows.append({
                'blocking_field': field_id is not None and str(fid) == str(field_id),
                'jira_field_id': source, 'jira_field_name': jira_definition.get('name'),
                'jira_schema_type': jira_schema.get('type'),
                'jira_schema_items': jira_schema.get('items'),
                'jira_schema_custom': jira_schema.get('custom'),
                'jira_allowed_value_count': len(jira_definition.get('allowedValues',[])) if isinstance(jira_definition.get('allowedValues'),list) else None,
                'jira_observed_nonnull_count': sum(v is not None and v != '' and v != [] for v in entry.get('analysis',{}).get('values',{}).get(source,[])),
                'mapping_status': row.get('status'),
                'cb_field_id': fid,
                'cb_field_name': target.get('label', target.get('name')),
                'cb_field_type': target.get('typeName', target.get('type')),
                'cb_type_id': target.get('typeId'),
                'cb_field_hidden': target.get('hidden'),
                'cb_multiple': target.get('multipleSelection'),
                'mapping_target': selected.get('target'),
                'conversion': selected.get('type'),
                'confirmed_binding': bool(selected.get('binding')),
                'derived_binding': bool(binding(spec, target)) if spec else False,
                'field_schema': _schema_summary(spec, model),
            })
        scopes[scope] = {
            'jira_issue_type': entry.get('type', {}).get('name'),
            'cb_tracker_id': entry.get('tracker'),
            'selected_field_count': sum(bool(r.get('selected', True)) for r in entry.get('rows', {}).values()),
            'fields': field_rows,
        }
    error_code, error_detail = error_classification(error)
    document = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'stage': stage, 'error_type': type(error).__name__,
        'error_code': error_code, 'error_detail': error_detail,
        # ApiError strings can contain server URLs or user values; keep only a
        # controlled summary, not arbitrary exception text.
        'error_summary': 'Preflight or Import failed; see the selected field contract data.',
        'blocking_field_id': field_id,
        'codebeamer_profile': wizard.cb.config.get('profile'),
        'openapi_loaded': isinstance(spec, dict),
        'openapi_version': spec.get('openapi', spec.get('swagger')) if isinstance(spec, dict) else None,
        'operations': {},
        'scopes': scopes,
    }
    for operation_name in ('item_create', 'item_update', 'item_get'):
        try:
            op = wizard.cb.operation(operation_name)
            path = op.get('path', '')
            if not path.startswith('/') or '?' in path:
                continue
            operation = spec['paths'][path][op['method'].lower()]
            from mapping_helper import request_schema
            raw = request_schema(spec, operation) if op['method'].upper() != 'GET' else {}
            native_shapes = {}
            from mapping_helper import schema_object
            for key in ('resolution', 'resolutions', 'status', 'priority'):
                field = raw.get('properties', {}).get(key)
                if not isinstance(field, dict):
                    continue
                try:
                    expanded = schema_object(spec, field)
                    item = expanded.get('items')
                    resolved_item = schema_object(spec, item) if isinstance(item,dict) else {}
                    native_shapes[key] = {
                        'type': expanded.get('type'),
                        'read_only': expanded.get('readOnly',False),
                        'is_array': expanded.get('type') == 'array',
                        'item_type': resolved_item.get('type'),
                        'item_properties': sorted(resolved_item.get('properties',{})),
                        'property_type': expanded.get('format'),
                    }
                except Exception as inner:
                    native_shapes[key] = {'schema_error':type(inner).__name__}
            document['operations'][operation_name] = {
                'method': op['method'], 'path': path,
                'request_properties': sorted(raw.get('properties', {})),
                'native_choice_shapes': native_shapes,
            }
        except Exception as exc:
            document['operations'][operation_name] = {'inspection_error': type(exc).__name__}
    # Only metadata-only HTTP audit entries are included; no request body,
    # URL query, credentials, issue values or response body.
    audit = Path(wizard.state.root) / 'requests.jsonl'
    if audit.exists():
        try:
            recent = audit.read_text(encoding='utf-8').splitlines()[-12:]
            document['recent_http_requests'] = []
            safe_keys = ('method', 'path_hash', 'status', 'request_bytes',
                         'response_bytes', 'elapsed_ms', 'attempt',
                         'error_type', 'route_category')
            for line in recent:
                row = json.loads(line)
                if isinstance(row, dict):
                    document['recent_http_requests'].append({k:row[k] for k in safe_keys if k in row})
        except (OSError, ValueError, TypeError):
            document['recent_http_requests_error'] = 'audit metadata unavailable'
    folder = Path(wizard.state.root) / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    # Use one deterministic filename to avoid creating uncontrolled log files.
    dest = folder / 'import-diagnostic.json'
    temp = folder / 'import-diagnostic.tmp'
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(dest)
    return dest
