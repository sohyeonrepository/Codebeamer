"""Semantic source policy, independent of Connector-internal numeric IDs."""
STANDARD = {'summary', 'description', 'assignee', 'reporter', 'priority', 'status',
            'startdate', 'duedate', 'resolution'}
DEDICATED = {'attachment', 'comment', 'changelog', 'issuelinks', 'worklog'}
REFERENCE_CATALOGS = {'components', 'versions', 'fixVersions'}


def field_policy(source, definition):
    if source in DEDICATED:
        return 'dedicated'
    custom = str(definition.get('schema', {}).get('custom', '')).lower()
    if 'raven' in custom and any(token in custom for token in ('testset', 'testplan', 'testexec', 'testrun')):
        return 'relationship'
    if source in REFERENCE_CATALOGS:
        return 'catalog-value'
    return 'standard' if source in STANDARD else 'custom'


def choice_input(kind):
    return kind in ('option', 'status')


def verify_reference_values(cb, spec):
    """Read actual target items before values may be written; never trust IDs alone."""
    from clients.http import ApiError
    for value in spec['value_mapping'].values():
        if not isinstance(value, dict) or type(value.get('id')) is not int:
            raise ApiError('Reference mapping requires actual target Item IDs')
        actual = cb.call('item_get', {'itemId': value['id']})
        if actual.get('id') != value['id'] or actual.get('tracker', {}).get('id') != spec['reference_tracker_id']:
            raise ApiError('Reference target Item/Tracker mismatch')


def catalog_reference_spec(source, definition, samples, target, profile):
    from clients.http import ApiError
    from mapping_helper import build_spec, field_identifier
    from migration.fields import convert
    from migration.wizard import semantic_type, hidden
    if source not in REFERENCE_CATALOGS or profile.get('field_id') != field_identifier(target):
        raise ApiError('Reference source/Field ID contract mismatch')
    reference = target.get('reference')
    tracker = profile.get('tracker_id')
    # This adapter supports only this observed metadata shape. Other shapes require an adapter.
    if type(tracker) is not int or not isinstance(reference, dict) or reference.get('trackerId') != tracker:
        raise ApiError('Reference target Tracker metadata is unverified')
    _, multiple = semantic_type(definition, True)
    if hidden(target) or semantic_type(target) != ('reference', multiple):
        raise ApiError('Reference type/cardinality/visibility mismatch')
    mapping = profile.get('value_mapping', {})
    for value in samples:
        convert(value, 'reference', mapping, 'id')
    spec = build_spec(source, target, 'reference', profile['target'], mapping, existing=True)
    spec.update(source_identity='id', reference_tracker_id=tracker, source_field_id=source)
    return spec
