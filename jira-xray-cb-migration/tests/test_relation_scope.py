"""Selected relation migration contracts, using an API fake and a real journal."""
from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

from clients.http import ApiError
from clients.codebeamer_client import CodebeamerClient
from migration.preservation import associations
from migration.state import State


TEST_LINK = {'id': 'test', 'name': 'Test', 'outward': 'tests', 'inward': 'is tested by'}


def issue(identity, links=()):
    return {'id': str(identity), 'key': 'P-' + str(identity),
            'fields': {'issuelinks': list(links)}}


def link(identity, other, direction='outwardIssue', link_type=None):
    return {'id': str(identity), 'type': deepcopy(link_type or TEST_LINK),
            direction: {'id': str(other), 'key': 'P-' + str(other)}}


def bundles(*issues):
    return [({'key': 'P'}, row) for row in issues]


class AssociationTarget:
    """Stores canonical relation bodies so readback proves endpoints and type."""
    def __init__(self):
        self.calls = []
        self.records = {}
        self.items = {}
        self.corrupt_readback = False

    def operation(self, name):
        return {'method': 'PUT' if name.endswith('_update') else
                ('POST' if name.endswith('_create') else 'GET')}

    def call(self, name, params=None, data=None):
        params = params or {}
        self.calls.append((name, deepcopy(params), deepcopy(data)))
        if name == 'item_get':
            if params['itemId'] not in self.items:
                raise ApiError('Item readback failed: HTTP 404')
            return deepcopy(self.items[params['itemId']])
        if name == 'association_lookup':
            return [{'id': key} for key, value in self.records.items()
                    if value['sourceId'] == params['sourceId']]
        if name == 'association_create':
            identity = len(self.records) + 900
            self.records[identity] = deepcopy(data)
            return {'id': identity}
        if name == 'association_get':
            result = deepcopy(self.records[params['recordId']])
            if self.corrupt_readback:
                result['fromItemId'], result['toItemId'] = result['toItemId'], result['fromItemId']
            return result
        if name == 'association_update':
            self.records[params['recordId']].update(deepcopy(data))
            return {'id': params['recordId']}
        raise AssertionError('Unexpected API operation: ' + name)

    def writes(self, name='association_create'):
        return [entry for entry in self.calls if entry[0] == name]


class ReferenceTarget(AssociationTarget):
    def __init__(self):
        super().__init__()
        self.multiple = True
        self.values = {}
        self.lose_update_response = False

    def call(self, name, params=None, data=None):
        if not name.startswith('reference_'):
            return super().call(name, params, data)
        params = params or {}
        self.calls.append((name, deepcopy(params), deepcopy(data)))
        if name == 'reference_field_get':
            return {'id': 3, 'hidden': False, 'multiple': self.multiple, 'targetTrackerIds': [20]}
        if name == 'reference_values_get':
            return {'fieldId': 3, 'values': deepcopy(self.values.get(params['itemId'], []))}
        if name == 'reference_values_update':
            self.values[params['itemId']] = deepcopy(data['values'])
            if self.lose_update_response:
                self.lose_update_response = False
                raise ApiError('Lost response after Reference values committed')
            return {'fieldId': 3}
        raise AssertionError('Unexpected Reference operation: ' + name)


class SelectedRelationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(self.directory.name)
        self.addCleanup(self.state.db.close)
        self.cb = AssociationTarget()
        self.settings = {'wizard': {'association_types': {'test': {'type_id': 7}}}}

    def migrated(self, *identities):
        for identity in identities:
            target = 100 + identity
            self.state.mark('item:' + str(identity), 'done', {'id': target})
            self.cb.items[target] = {'id': target, 'tracker': {'id': 10}}

    def assert_counts(self, report, source, selected, verified, excluded, failed):
        self.assertEqual({key: report[key] for key in
                          ('source', 'selected', 'verified', 'excluded', 'failed')},
                         dict(source=source, selected=selected, verified=verified,
                              excluded=excluded, failed=failed))
        self.assertEqual(len(report['relations']), source)

    def test_tests_and_is_tested_by_are_one_directed_relation(self):
        self.migrated(1, 2)
        rows = bundles(issue(1, [link('rel-1', 2)]),
                       issue(2, [link('rel-1', 1, 'inwardIssue')]))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 1, 1, 1, 0, 0)
        self.assertEqual(report['relations'][0]['status'], 'verified')
        self.assertEqual(len(self.cb.writes()), 1)
        self.assertEqual(self.cb.writes()[0][2],
                         {'sourceId': 'rel-1', 'fromItemId': 101, 'toItemId': 102, 'typeId': 7})
        self.assertTrue(any(call[0] == 'association_get' for call in self.cb.calls))

    def test_ten_source_links_with_three_selected_report_seven_excluded(self):
        self.migrated(*range(1, 12))
        selected_type = TEST_LINK
        excluded_type = {'id': 'other', 'name': 'Other', 'outward': 'other', 'inward': 'other'}
        rows = []
        outgoing = []
        for number in range(2, 12):
            kind = selected_type if number < 5 else excluded_type
            identity = 'rel-' + str(number)
            outgoing.append(link(identity, number, link_type=kind))
            rows.append(issue(number, [link(identity, 1, 'inwardIssue', kind)]))
        report = associations(self.cb, self.state, bundles(issue(1, outgoing), *rows), self.settings)
        self.assert_counts(report, 10, 3, 3, 7, 0)
        self.assertEqual(len(self.cb.writes()), 3)
        excluded = [row for row in report['relations'] if row['status'] == 'excluded']
        self.assertEqual(len(excluded), 7)
        self.assertTrue(all(row.get('reason') for row in excluded))

    def test_no_mapping_excludes_without_any_target_api_calls(self):
        report = associations(self.cb, self.state, bundles(issue(1, [link('rel-1', 2)])), {})
        self.assert_counts(report, 1, 0, 0, 1, 0)
        self.assertEqual(report['relations'][0]['status'], 'excluded')
        self.assertTrue(report['relations'][0]['reason'])
        self.assertEqual(self.cb.calls, [])
        self.assertIsNotNone(self.state.load('source-relations', 'rel-1'))

    def test_missing_external_target_fails_one_relation_and_continues(self):
        self.migrated(1, 3)
        rows = bundles(issue(1, [link('missing', 2), link('valid', 3)]), issue(3))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 2, 1, 0, 1)
        failed = [row for row in report['relations'] if row['status'] == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0]['reason'])
        self.assertEqual([entry[2]['sourceId'] for entry in self.cb.writes()], ['valid'])
        self.assertIsNotNone(self.state.load('source-relations', 'missing'))

    def test_invalid_selected_type_fails_without_blocking_other_selected_types(self):
        self.migrated(1, 2, 3)
        invalid_type = dict(TEST_LINK, id='invalid')
        self.settings['wizard']['association_types']['invalid'] = {'type_id': 0}
        rows = bundles(issue(1, [link('invalid', 2, link_type=invalid_type), link('valid', 3)]),
                       issue(2), issue(3))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 2, 1, 0, 1)
        failed = [row for row in report['relations'] if row['status'] == 'failed']
        self.assertEqual(failed[0]['jira_link_id'], 'invalid')
        self.assertTrue(failed[0]['reason'])
        self.assertEqual([entry[2]['sourceId'] for entry in self.cb.writes()], ['valid'])

    def test_same_named_unselected_type_is_not_inferred_from_selected_type(self):
        self.migrated(1, 2, 3)
        same_name_other_id = dict(TEST_LINK, id='different-type-id')
        rows = bundles(issue(1, [link('chosen', 2), link('not-chosen', 3, link_type=same_name_other_id)]),
                       issue(2), issue(3))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 1, 1, 1, 0)
        self.assertEqual([entry[2]['sourceId'] for entry in self.cb.writes()], ['chosen'])

    def test_selected_type_absent_from_server_catalog_or_unavailable_catalog_fails(self):
        self.migrated(1, 2)
        for response in ([{'id': 8, 'name': 'Different type'}], {'values': [], 'total': 10}):
            with self.subTest(response=response):
                read_calls = []

                class CatalogHttp:
                    def request(self, method, path):
                        read_calls.append((method, path))
                        return deepcopy(response)

                client = CodebeamerClient(CatalogHttp(), {}, write=True)
                client.call = self.cb.call
                client.operation = self.cb.operation
                report = associations(client, self.state,
                                      bundles(issue(1, [link('foreign-type', 2)]), issue(2)), self.settings)
                self.assert_counts(report, 1, 1, 0, 0, 1)
                self.assertTrue(report['relations'][0]['reason'])
                self.assertEqual(read_calls, [('GET', '/v3/associations/types')])
                self.assertEqual(self.cb.writes(), [])

    def test_external_target_by_id_or_key_requires_item_readback_before_write(self):
        self.migrated(1)
        self.cb.items[302] = {'id': 302, 'tracker': {'id': 20}}
        for external_key in ('2', 'P-2'):
            with self.subTest(external_key=external_key):
                self.cb.calls.clear()
                self.settings['wizard']['external_item_mapping'] = {external_key: 302}
                relation_id = 'external-' + external_key
                report = associations(self.cb, self.state,
                                      bundles(issue(1, [link(relation_id, 2)])), self.settings)
                self.assert_counts(report, 1, 1, 1, 0, 0)
                operations = [entry[0] for entry in self.cb.calls]
                self.assertLess(operations.index('item_get'), operations.index('association_create'))
                self.assertIn(('item_get', {'itemId': 302}, None), self.cb.calls)
                self.assertEqual(self.cb.writes()[0][2]['toItemId'], 302)

    def test_external_target_get_failure_or_wrong_identity_prevents_relation_write(self):
        self.migrated(1)
        self.settings['wizard']['external_item_mapping'] = {'P-2': 302}
        for response in (None, {'id': 999}):
            with self.subTest(response=response):
                self.cb.calls.clear()
                if response is not None:
                    self.cb.items[302] = response
                report = associations(self.cb, self.state,
                                      bundles(issue(1, [link('external', 2)])), self.settings)
                self.assert_counts(report, 1, 1, 0, 0, 1)
                self.assertTrue(report['relations'][0]['reason'])
                self.assertEqual(self.cb.writes(), [])

    def test_rerun_looks_up_and_verifies_existing_association_without_duplicate(self):
        self.migrated(1, 2)
        rows = bundles(issue(1, [link('rel-1', 2)]), issue(2))
        associations(self.cb, self.state, rows, self.settings)
        resumed = State(self.directory.name)
        self.addCleanup(resumed.db.close)
        report = associations(self.cb, resumed, rows, self.settings)
        self.assert_counts(report, 1, 1, 1, 0, 0)
        self.assertEqual(len(self.cb.writes()), 1)
        self.assertEqual(len([entry for entry in self.cb.calls if entry[0] == 'association_get']), 2)

    def test_reverse_mapping_swaps_source_and_target_once(self):
        self.migrated(1, 2)
        self.settings['wizard']['association_types']['test']['reverse'] = True
        rows = bundles(issue(1, [link('rel-1', 2)]),
                       issue(2, [link('rel-1', 1, 'inwardIssue')]))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 1, 1, 1, 0, 0)
        self.assertEqual(self.cb.writes()[0][2]['fromItemId'], 102)
        self.assertEqual(self.cb.writes()[0][2]['toItemId'], 101)

    def test_distinct_opposite_direction_link_ids_are_not_collapsed(self):
        self.migrated(1, 2)
        rows = bundles(issue(1, [link('a-to-b', 2)]), issue(2, [link('b-to-a', 1)]))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 2, 2, 0, 0)
        self.assertEqual({(row[2]['fromItemId'], row[2]['toItemId']) for row in self.cb.writes()},
                         {(101, 102), (102, 101)})

    def test_conflicting_direction_for_same_link_id_is_failure_before_writes(self):
        self.migrated(1, 2)
        rows = bundles(issue(1, [link('conflict', 2)]), issue(2, [link('conflict', 1)]))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 1, 1, 0, 0, 1)
        self.assertEqual(report['relations'][0]['status'], 'failed')
        self.assertTrue(report['relations'][0]['reason'])
        self.assertEqual(self.cb.writes(), [])

    def test_readback_direction_mismatch_is_not_reported_as_verified(self):
        self.migrated(1, 2)
        self.cb.corrupt_readback = True
        report = associations(self.cb, self.state,
                              bundles(issue(1, [link('rel-1', 2)]), issue(2)), self.settings)
        self.assert_counts(report, 1, 1, 0, 0, 1)
        self.assertEqual(report['relations'][0]['status'], 'failed')
        self.assertTrue(report['relations'][0]['reason'])

    def reference_setup(self):
        self.cb = ReferenceTarget()
        self.migrated(1, 2, 3)
        for identity in (102, 103):
            self.cb.items[identity]['tracker']['id'] = 20
        self.settings['wizard']['association_types']['test'] = {
            'mode': 'reference', 'tracker_id': 10, 'field_id': 3, 'target_tracker_id': 20}

    def test_single_reference_with_two_selected_targets_fails_both_before_update(self):
        self.reference_setup()
        self.cb.multiple = False
        rows = bundles(issue(1, [link('first', 2), link('second', 3)]), issue(2), issue(3))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 2, 0, 0, 2)
        self.assertTrue(all('Single Reference' in row['reason'] for row in report['relations']))
        self.assertEqual(self.cb.writes('reference_values_update'), [])
        self.assertEqual(self.cb.writes(), [])

    def test_reference_wrong_owner_target_tracker_or_reverse_prevents_value_write(self):
        for mismatch in ('owner', 'target', 'reverse'):
            with self.subTest(mismatch=mismatch):
                self.reference_setup()
                if mismatch == 'owner':
                    self.cb.items[101]['tracker']['id'] = 999
                elif mismatch == 'target':
                    self.cb.items[102]['tracker']['id'] = 999
                else:
                    self.settings['wizard']['association_types']['test']['reverse'] = True
                report = associations(self.cb, self.state,
                                      bundles(issue(1, [link('ref', 2)]), issue(2)), self.settings)
                self.assert_counts(report, 1, 1, 0, 0, 1)
                self.assertIn('Tracker', report['relations'][0]['reason'])
                self.assertEqual(self.cb.writes('reference_values_update'), [])
                self.assertEqual(self.cb.writes(), [])

    def test_reference_preserves_existing_values_and_rerun_adds_no_duplicates(self):
        self.reference_setup()
        self.cb.values[101] = [{'id': 99}]
        rows = bundles(issue(1, [link('first', 2), link('second', 3)]), issue(2), issue(3))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 2, 2, 2, 0, 0)
        self.assertEqual(self.cb.values[101], [{'id': 99}, {'id': 102}, {'id': 103}])
        self.assertEqual(len(self.cb.writes('reference_values_update')), 2)
        resumed = State(self.directory.name)
        self.addCleanup(resumed.db.close)
        report = associations(self.cb, resumed, rows, self.settings)
        self.assert_counts(report, 2, 2, 2, 0, 0)
        self.assertEqual(self.cb.values[101], [{'id': 99}, {'id': 102}, {'id': 103}])
        self.assertEqual(len(self.cb.writes('reference_values_update')), 2)

    def test_single_reference_existing_value_is_preserved_with_explicit_failure(self):
        self.reference_setup()
        self.cb.multiple = False
        self.cb.values[101] = [{'id': 99}]
        report = associations(self.cb, self.state,
                              bundles(issue(1, [link('ref', 2)]), issue(2)), self.settings)
        self.assert_counts(report, 1, 1, 0, 0, 1)
        self.assertIn('occupied', report['relations'][0]['reason'])
        self.assertEqual(self.cb.values[101], [{'id': 99}])
        self.assertEqual(self.cb.writes('reference_values_update'), [])

    def test_reference_lost_update_response_retries_by_readback_without_duplicate(self):
        self.reference_setup()
        self.cb.values[101] = [{'id': 99}]
        self.cb.lose_update_response = True
        rows = bundles(issue(1, [link('ref', 2)]), issue(2))
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 1, 1, 0, 0, 1)
        self.assertEqual(self.cb.values[101], [{'id': 99}, {'id': 102}])
        report = associations(self.cb, self.state, rows, self.settings)
        self.assert_counts(report, 1, 1, 1, 0, 0)
        self.assertEqual(self.cb.values[101], [{'id': 99}, {'id': 102}])
        self.assertEqual(len(self.cb.writes('reference_values_update')), 1)


if __name__ == '__main__':
    unittest.main()
