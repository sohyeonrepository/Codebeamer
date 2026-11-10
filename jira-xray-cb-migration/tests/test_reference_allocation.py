from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from clients.http import ApiError
from clients.reference_allocation import allocate, policy_for
from mapping_helper import create_confirmed_field


POLICY = {'minimum': 40, 'maximum': 44, 'verified': True, 'evidence': 'Synthetic test contract only'}


class ReferenceAllocationTests(unittest.TestCase):
    def choice_current(self):
        return {'basicInformation': {'trackerId': 100}, 'fields': [
            {'referenceId': 1000, 'typeId': 6, 'choiceOptionSetting': {'type': 'CHOICE_OPTIONS', 'choiceOptions': []}},
            {'referenceId': 1011, 'typeId': 5, 'choiceOptionSetting': {'type': 'MEMBERS'}}]}

    def test_supplied_choice_policy_selects_1010_without_user_input(self):
        current = self.choice_current()
        policy = policy_for({}, 100, 6, current, {'typeId': 6, 'options': [{'name': 'A'}]})
        self.assertEqual((policy['minimum'], policy['maximum']), (1010, 9999))
        self.assertEqual(allocate(current, {'type': 'integer'}, policy), 1010)
        current['fields'].append({'referenceId': 1010, 'typeId': 6})
        self.assertEqual(allocate(current, {'type': 'integer'}, policy), 1012)
        self.assertFalse(policy['server_write_verified'])

    def test_default_choice_policy_is_not_applied_to_text_or_unknown_tracker_layout(self):
        current = self.choice_current()
        self.assertIsNone(policy_for({}, 100, 0, current, {'typeId': 0, 'options': None}))
        self.assertIsNone(policy_for({}, 100, 6, {'basicInformation': {'trackerId': 100}, 'fields': []},
                                     {'typeId': 6, 'options': []}))
        current['fields'][0]['referenceId'] = 10000
        self.assertIsNone(policy_for({}, 100, 6, current, {'typeId': 6, 'options': []}))
        with self.assertRaises(ApiError): policy_for({}, 999, 6, current, {'options': []})

    def test_first_gap_not_max_plus_one_and_no_input_mutation(self):
        current = {'fields': [{'referenceId': 40}, {'id': 42}, {'fieldId': '44'}, {'id': 2000000}]}
        before = deepcopy(current)
        self.assertEqual(allocate(current, {}, POLICY), 41)
        self.assertEqual(current, before)

    def test_next_field_uses_updated_configuration(self):
        current = {'fields': [{'referenceId': 40}]}
        first = allocate(current, {}, POLICY)
        current['fields'].append({'referenceId': first})
        self.assertEqual(allocate(current, {}, POLICY), 42)

    def test_range_exhaustion_and_server_bound_intersection(self):
        self.assertEqual(allocate({'fields': []}, {'minimum': 42, 'maximum': 43}, POLICY), 42)
        with self.assertRaisesRegex(ApiError, 'No unused'):
            allocate({'fields': [{'referenceId': n} for n in range(40, 45)]}, {}, POLICY)

    def test_large_int32_range_does_not_scan_every_integer(self):
        policy = dict(POLICY, minimum=1, maximum=2**31-1)
        self.assertEqual(allocate({'fields': [{'id': 1}, {'id': 2**31-1}]}, {}, policy), 2)

    def test_schema_enum_requires_no_manual_policy(self):
        self.assertEqual(allocate({'fields': [{'referenceId': 7}]}, {'enum': [9, 7, 8]}), 8)
        with self.assertRaises(ApiError): allocate({'fields': [{'id': 7}]}, {'enum': [7]})

    def test_no_range_inferred_from_int32_or_observed_ids(self):
        current = {'fields': [{'referenceId': 10000}, {'referenceId': 10074}]}
        self.assertIsNone(allocate(current, {'type': 'integer', 'format': 'int32'}))
        self.assertIsNone(allocate(current, {'minimum': 0, 'maximum': 2**31-1}))

    def test_unverified_policy_rejected_and_tracker_type_scoping(self):
        for policy in ({}, dict(POLICY, verified=False), dict(POLICY, evidence=''), dict(POLICY, minimum=True)):
            with self.assertRaises(ApiError): allocate({'fields': []}, {}, policy)
        config = {'configuration_reference_ranges': {'100': {'6': POLICY}}}
        self.assertEqual(policy_for(config, 100, 6), POLICY)
        self.assertIsNone(policy_for(config, 101, 6))
        self.assertIsNone(policy_for(config, 100, 0))

    def test_automatic_allocation_reaches_post_journal_and_readback(self):
        from tests import test_configuration_field_wire as fixture
        before, definition = fixture.baseline(), fixture.proposal()
        after = deepcopy(before)
        cb = Mock()
        cb.spec = fixture.ConfigurationFieldWireTests().reference_contract()
        cb.config = {'configuration_reference_ranges': {'100': {'6': POLICY}}}
        cb.write = False
        cb.configuration.side_effect = [before, after]
        def post(name, params, body):
            self.assertEqual(body['fields'][:-1], before['fields'])
            created = deepcopy(body['fields'][-1])
            self.assertEqual(created['referenceId'], 40)
            for i, option in enumerate(created['choiceOptionSetting']['choiceOptions']): option['id'] = i+100
            after['fields'].append(created)
            return {}
        cb.call.side_effect = post
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'journal.json'
            created, _ = create_confirmed_field(cb, 100, definition, before, path, approved=True)
            self.assertEqual(created['referenceId'], 40)
            receipt = next(iter(json.loads(path.read_text()).values()))
            self.assertEqual(receipt['status'], 'done')
            self.assertEqual(receipt['reference_allocation_policy'], POLICY)
        self.assertFalse(cb.write)

    def test_choice_creation_uses_source_policy_without_manual_config(self):
        from tests import test_configuration_field_wire as fixture
        before, definition = fixture.baseline(), fixture.proposal()
        before['fields'][0]['referenceId'] = 1000
        after = deepcopy(before)
        cb = Mock()
        cb.spec = fixture.ConfigurationFieldWireTests().reference_contract()
        cb.config, cb.write = {}, False
        cb.configuration.side_effect = [before, after]
        def post(name, params, body):
            created = deepcopy(body['fields'][-1])
            self.assertEqual(created['referenceId'], 1010)
            self.assertNotIn('options', created)
            self.assertTrue(created['multipleSelection'])
            self.assertEqual([o['id'] for o in created['choiceOptionSetting']['choiceOptions']], [1, 2])
            for i, option in enumerate(created['choiceOptionSetting']['choiceOptions']): option['id'] = i+1
            after['fields'].append(created)
            return {}
        cb.call.side_effect = post
        with tempfile.TemporaryDirectory() as root:
            created, _ = create_confirmed_field(cb, 100, definition, before, Path(root)/'journal.json', approved=True)
            self.assertEqual(created['referenceId'], 1010)
        cb.call.assert_called_once()

    def test_native_position_and_visibility_are_new_field_only(self):
        from tests import test_configuration_field_wire as fixture
        from clients.configuration_fields import definition_body
        before, definition = fixture.baseline(), fixture.proposal()
        before['fields'][0].update(referenceId=1000, position=50, hidden=True)
        original = deepcopy(before)
        spec = fixture.ConfigurationFieldWireTests().reference_contract()
        props = spec['paths']['/v3/tracker/configuration']['post']['requestBody']['content']['application/json']['schema']['properties']['fields']['items']['properties']
        props.update(position={'type': 'integer'}, hidden={'type': 'boolean'}, listable={'type': 'boolean'}, title={'type': 'string'})
        policy = policy_for({}, 100, 6, before, definition)
        wire = definition_body(spec, definition, before, allocation_policy=policy)
        self.assertEqual(wire['position'], 60)
        self.assertFalse(wire['hidden'])
        self.assertTrue(wire['listable'])
        self.assertEqual(wire['title'], definition['label'])
        self.assertEqual(before, original)
        before['fields'][0]['position'] = 8995
        with self.assertRaisesRegex(ApiError, 'position'):
            definition_body(spec, definition, before, allocation_policy=policy)
