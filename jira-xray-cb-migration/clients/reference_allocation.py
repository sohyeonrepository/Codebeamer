"""First-free allocation, scoped to an evidenced type-specific ID domain.

The supplied KEFICO JAR scans 10000..19999 for Text only. That range is not
a default here: it is not evidence of a Choice range or target-version support.
"""
from copy import deepcopy
from clients.http import ApiError


def policy_for(config, tracker, type_id, current=None, definition=None):
    policy = config.get('configuration_reference_ranges', {}).get(str(tracker), {}).get(str(type_id))
    if policy is not None:
        return deepcopy(policy)
    if not isinstance(current, dict) or not isinstance(definition, dict):
        return None
    if current.get('basicInformation', {}).get('trackerId') != tracker:
        raise ApiError('Automatic referenceId policy requires the selected Tracker Configuration')
    # The newly supplied 3.0.0.7 reference mapper starts Choice allocation at
    # 1010 and declares the Choice family domain as 1000..9999. Activate only
    # with matching native Choice definitions in this actual Tracker.
    options = definition.get('options')
    native = definition.get('choiceOptionSetting')
    if not isinstance(options, list) and not (isinstance(native, dict) and native.get('type') == 'CHOICE_OPTIONS'):
        return None
    observed = [f for f in current.get('fields', []) if f.get('typeId') == type_id]
    settings = [f.get('choiceOptionSetting') for f in observed]
    if not any(isinstance(s, dict) and s.get('type') == 'CHOICE_OPTIONS' for s in settings):
        return None
    if any(isinstance(s, dict) and s.get('type') != 'CHOICE_OPTIONS' for s in settings):
        raise ApiError('Conflicting native field families for automatic Choice referenceId allocation')
    custom = [f.get('referenceId') for f in observed
              if type(f.get('referenceId')) is int and f['referenceId'] >= 1000]
    if not custom or any(n > 9999 for n in custom):
        return None
    return {'minimum': 1010, 'maximum': 9999, 'verified': True,
            'evidence': 'Supplied 3.0.0.7 mapper.IdAllocator.take + matching current Tracker CHOICE_OPTIONS fields',
            'server_write_verified': False, 'family': 'choice'}


def used_ids(current):
    used = set()
    for field in current.get('fields', []):
        for key in ('referenceId', 'id', 'fieldId'):
            value = field.get(key)
            if type(value) is int:
                used.add(value)
            elif isinstance(value, str) and value.lstrip('-').isdigit():
                used.add(int(value))
    return used


def allocate(current, schema, policy=None):
    """Return None without a proven domain; never infer a range from existing IDs."""
    used = used_ids(current)
    lower, upper = -(2**31), 2**31-1
    for key in ('minimum', 'maximum'):
        if key in schema and type(schema[key]) is not int:
            raise ApiError('referenceId allocation requires integer schema bounds')
    lower = max(lower, schema.get('minimum', lower))
    upper = min(upper, schema.get('maximum', upper))
    if policy is not None:
        if (not isinstance(policy, dict) or policy.get('verified') is not True
                or not isinstance(policy.get('evidence'), str) or not policy['evidence'].strip()
                or type(policy.get('minimum')) is not int or type(policy.get('maximum')) is not int
                or policy['minimum'] > policy['maximum']):
            raise ApiError('Type-specific referenceId range needs verified bounds and evidence')
        lower, upper = max(lower, policy['minimum']), min(upper, policy['maximum'])
    enum = schema.get('enum')
    if enum is not None:
        if not isinstance(enum, list) or any(type(n) is not int for n in enum):
            raise ApiError('referenceId enum is not an integer domain')
        candidates = sorted(n for n in enum if lower <= n <= upper and n not in used)
        if candidates:
            return candidates[0]
        raise ApiError('No unused referenceId remains in the server enum')
    if policy is None:
        return None  # int32/minimum/maximum alone are not a field allocation policy.
    candidate = lower
    for occupied in sorted(n for n in used if lower <= n <= upper):
        if occupied != candidate:
            break
        candidate += 1
    if candidate > upper:
        raise ApiError('No unused referenceId remains in the verified type-specific range')
    return candidate
