"""Upsert configured Run fields and verify explicit unrestricted permissions."""
import hashlib
import json
from copy import deepcopy
from clients.http import ApiError


def sync_run_fields(cb, state, tracker, settings, write):
    sync = settings.get('field_sync', {})
    if not sync.get('enabled'):
        return []
    if 'unrestricted_permission' not in sync or sync['unrestricted_permission'] is None:
        raise ApiError('field_sync.unrestricted_permission must be the actual server representation of unrestricted field permissions')
    definitions = deepcopy(sync.get('definitions', []))
    if not definitions:
        templates = sync.get('type_templates', {})
        for source, mapping in settings.get('run_adapter', {}).get('fields', {}).items():
            if mapping.get('builtin'):
                continue
            definition = mapping.get('definition') or templates.get(mapping.get('type'))
            if not definition:
                raise ApiError('No server field type definition for Run field: ' + source)
            definition = deepcopy(definition)
            definition.setdefault('label', mapping.get('label', source))
            definitions.append(definition)
    if not definitions:
        raise ApiError('No Run field definitions provided for synchronization')
    permission = sync['unrestricted_permission']
    current_config = cb.configuration(tracker)
    fields = current_config.get('fields')
    if not isinstance(fields, list):
        raise ApiError('Tracker configuration has no full fields array')
    if isinstance(permission, str):
        structured = [f['permission'] for f in fields if isinstance(f.get('permission'), dict)]
        if structured:
            matches = [p for p in structured if p.get('name', p.get('label', p.get('value'))) == permission
                       or (permission == 'Unrestricted' and p.get('type') == 'UNRESTRICTED')]
            if not matches:
                raise ApiError('Server uses structured permissions; no observed ' + permission + ' permission object')
            if any(p != matches[0] for p in matches):
                raise ApiError('Ambiguous unrestricted permission representation')
            permission = deepcopy(matches[0])
    desired = deepcopy(fields)
    plan = []
    for definition in definitions:
        matches = [f for f in desired if f.get('label') == definition['label']]
        if len(matches) > 1:
            raise ApiError('Ambiguous destination field label: ' + definition['label'])
        if matches:
            field = matches[0]
            if field.get('typeId') != definition['typeId']:
                raise ApiError('Field type conflict; refusing destructive conversion: ' + definition['label'])
            action = 'update'
            for key, value in definition.items():
                if key not in ('referenceId', 'id'):
                    field[key] = deepcopy(value)
        else:
            field = deepcopy(definition)
            desired.append(field)
            action = 'create'
        field['permission'] = deepcopy(permission)
        plan.append({'label': field['label'], 'action': action})
    # User requested unrestricted permissions on ALL fields of this Tracker only.
    for field in desired:
        field['permission'] = deepcopy(permission)
    state.save('schema-plans', tracker, {'fields': desired, 'plan': plan})
    if not write:
        return plan
    cb.operation('tracker_configuration_update')
    if desired != fields:
        body = deepcopy(current_config)
        body['fields'] = desired
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        state.once('schema:' + str(tracker) + ':' + fingerprint,
                   lambda: cb.call('tracker_configuration_update', {'trackerId': tracker}, body))
    actual = cb.configuration(tracker).get('fields', [])
    for expected in desired:
        matches = [f for f in actual if f.get('label') == expected['label']]
        if len(matches) != 1:
            raise ApiError('Field create/update readback failed: ' + expected['label'])
        if any(matches[0].get(k) != v for k, v in expected.items() if k not in ('id', 'referenceId')):
            raise ApiError('Field definition/permission verification failed: ' + expected['label'])
    if any(f.get('permission') != permission for f in actual):
        raise ApiError('Not all Run Tracker fields have unrestricted permissions')
    state.save('target-schema', tracker, actual)
    return plan
