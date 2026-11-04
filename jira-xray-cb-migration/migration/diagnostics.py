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
            field_rows.append({
                'blocking_field': field_id is not None and str(fid) == str(field_id),
                'jira_field_id': source, 'jira_field_name': entry.get('analysis', {}).get('fields', {}).get(source, {}).get('name'),
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
    document = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'stage': stage, 'error_type': type(error).__name__,
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
            document['operations'][operation_name] = {
                'method': op['method'], 'path': path,
                'request_properties': sorted(raw.get('properties', {})),
            }
        except Exception as exc:
            document['operations'][operation_name] = {'inspection_error': type(exc).__name__}
    folder = Path(wizard.state.root) / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    # Use one deterministic filename to avoid creating uncontrolled log files.
    dest = folder / 'import-diagnostic.json'
    temp = folder / 'import-diagnostic.tmp'
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(dest)
    return dest
