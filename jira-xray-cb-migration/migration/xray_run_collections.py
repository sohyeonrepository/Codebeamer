"""Bind separately collected Run steps/evidence to existing verified writers."""
from clients.http import ApiError
from migration.fields import payload
from migration.run_files import inventory


def prepare(cb, settings, run):
    """Read-only preflight; no unsupported collection can disappear silently."""
    steps, files = [], []
    adapter = settings.get('run_step_adapter', {})
    for operation, collected in run.get('dc_collections', {}).items():
        if operation not in ('test_run_steps', 'test_run_evidence'):
            raise ApiError('Unsupported separately collected Run data: ' + operation)
        if collected.get('complete') is not True or str(collected.get('source_id')) != str(run['id']):
            raise ApiError('Run collection incomplete or belongs to another Run: ' + operation)
        rows = collected.get('items')
        if not isinstance(rows, list):
            raise ApiError('Run collection requires an items array')
        if operation == 'test_run_evidence':
            files.extend(inventory({'evidence': rows}))
            continue
        files.extend(inventory(rows))
        if rows and not adapter.get('fields'):
            raise ApiError('Collected Run steps require verified run_step_adapter.fields before Import')
        identities = set()
        for row in rows:
            identity = row.get(adapter.get('identity_key', 'id')) if isinstance(row, dict) else None
            if identity is None or str(identity) in identities:
                raise ApiError('Missing/duplicate Run step identity')
            identities.add(str(identity))
            missing = set(row) - set(adapter['fields'])
            if missing:
                raise ApiError('Unmapped Run step fields: ' + ', '.join(sorted(missing)))
            # Check status/result/defect transforms against every actual source value.
            payload(row, adapter['fields'], settings)
            for spec in adapter['fields'].values():
                if spec.get('reference_tracker_id'):
                    from migration.system_fields import verify_reference_values
                    verify_reference_values(cb, spec)
            steps.append((str(identity), row))
        if rows:
            for operation_name in ('run_step_lookup','run_step_create','run_step_get','run_step_update'):
                operation = cb.operation(operation_name)
                expected = ('GET',) if operation_name.endswith(('_lookup','_get')) else ('POST',) if operation_name.endswith('_create') else ('PUT','PATCH','POST')
                if operation.get('method','').upper() not in expected:
                    raise ApiError('Unverified Run/Step operation method: ' + operation_name)
    seen_files = {}
    for metadata in files:
        identity = str(metadata['id'])
        if identity in seen_files and seen_files[identity] != metadata:
            raise ApiError('Run collection evidence has conflicting metadata')
        seen_files[identity] = metadata
    if files:
        for name in ('attachment_lookup','attachment_upload','attachment_get'):
            cb.operation(name)
    return steps, files


def write_steps(cb, state, settings, run, target_run_id, steps):
    from migration.records import mapped_record
    ids = []
    for step_id, row in steps:
        ids.append(mapped_record(cb, state, settings, 'run_step', str(run['id']) + ':' + step_id,
                                 row, {'runId': target_run_id, 'itemId': target_run_id},
                                 settings['run_step_adapter']['fields']))
    return ids


def prepare_test_steps(cb, settings, issue):
    """Test Case procedure steps use their own server-verified record adapter."""
    collection = issue.get('_xray_dc', {}).get('test_steps')
    if collection is None:
        return [], []
    synthetic = {'id':issue['key'], 'dc_collections':{'test_run_steps':collection}}
    translated = dict(settings, run_step_adapter=settings.get('test_step_adapter', {}))
    # Reuse data checks while validating Test Step operations, never Run Step paths.
    class TestStepOperations:
        def operation(self, name):
            return cb.operation(name.replace('run_step_', 'test_step_'))
        def call(self, *args, **kwargs):
            return cb.call(*args, **kwargs)
    try:
        return prepare(TestStepOperations(), translated, synthetic)
    except ApiError as exc:
        raise ApiError(str(exc).replace('run_step_adapter', 'test_step_adapter').replace('Run step', 'Test Case step')) from exc


def write_test_steps(cb, state, settings, source_http, issue, target_item, steps, files):
    from migration.records import mapped_record
    from migration.attachments import transfer
    verified = []
    for identity, row in steps:
        verified.append(mapped_record(cb, state, settings, 'test_step', issue['key'] + ':' + identity,
                                      row, {'itemId':target_item}, settings['test_step_adapter']['fields']))
    seen = {}
    for metadata in files:
        identity = str(metadata['id'])
        if identity in seen:
            if seen[identity] != metadata:
                raise ApiError('Test Step attachment has conflicting metadata')
            continue
        seen[identity] = metadata
        transfer(source_http, cb, state, 'test-step:' + issue['key'], target_item, metadata)
    return {'source':issue['key'],'step_ids':verified,'evidence_count':len(seen),'status':'verified'}
