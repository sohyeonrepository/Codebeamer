"""Resolve Run reference field values without assuming Test vs Test Set targets."""
import re
from clients.http import ApiError


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


ALIASES = {
    'test': 'test', 'tests': 'test', 'testid': 'test', 'testissueid': 'test', 'testkey': 'test',
    'testset': 'test_set', 'testsets': 'test_set', 'testsetid': 'test_set', 'testsetissueid': 'test_set',
    'testplan': 'test_plan', 'testplans': 'test_plan', 'testplanid': 'test_plan', 'testplanissueid': 'test_plan',
}


def candidates(run, execution, definitions, overrides=None):
    names = {f['id']: f.get('name', f['id']) for f in definitions}
    aliases = dict(ALIASES)
    aliases.update({normalized(k): v for k, v in (overrides or {}).items()})
    output, seen = [], set()
    for origin, values in [('run', run), ('execution', execution.get('fields', {}))]:
        containers = [values]
        if isinstance(values.get('fields'), dict):
            containers.append(values['fields'])
        for fields in containers:
            for field_id, value in fields.items():
                role = aliases.get(normalized(names.get(field_id, field_id))) or aliases.get(normalized(field_id))
                if not role or value is None:
                    continue
                for entry in value if isinstance(value, list) else [value]:
                    if entry is None or entry == '':
                        continue
                    identity = entry
                    if isinstance(entry, dict):
                        identity = entry.get('key', entry.get('id', entry.get('value')))
                    if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                        raise ApiError('Unsupported reference field value: ' + str(field_id))
                    identity = str(identity).strip()
                    unique = (role, identity)
                    if unique in seen:
                        continue
                    seen.add(unique)
                    output.append({'role': role, 'source_value': identity, 'source_field': field_id, 'origin': origin})
    return output


def resolve_references(cb, run, execution, definitions, adapter):
    refs = candidates(run, execution, definitions, adapter.get('reference_fields'))
    if not refs:
        raise ApiError('No Test/Test Set/Test Plan reference field values found; inspect source Run and Execution fields')
    result = []
    for ref in refs:
        found = cb.call('reference_lookup', {'sourceValue': ref['source_value'], 'referenceRole': ref['role']})
        if not isinstance(found, list) or len(found) != 1:
            raise ApiError('Reference lookup must uniquely match source field ' + ref['source_field'] + ': ' + ref['source_value'])
        destination = found[0].get('id')
        if type(destination) is not int or destination <= 0:
            raise ApiError('Reference lookup has no valid destination Item ID')
        actual = cb.call('item_get', {'itemId': destination})
        if actual.get('id') != destination:
            raise ApiError('Reference target readback failed')
        result.append(dict(ref, target={'id': destination, 'type': 'TrackerItemReference'}))
    return result
