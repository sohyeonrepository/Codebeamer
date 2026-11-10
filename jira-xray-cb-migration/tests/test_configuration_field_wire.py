from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from clients.configuration_fields import definition_body, definition_changes
from clients.http import ApiError, http_failure_detail
from clients.tracker_fields import normalize
from mapping_helper import create_confirmed_field


def contract():
    field = {'type': 'object', 'properties': {
        'typeId': {'type': 'integer'}, 'label': {'type': 'string'},
        'mandatory': {'type': 'boolean'}, 'multipleSelection': {'type': 'boolean'},
        'permission': {'type': 'object'}, 'choiceOptionSetting': {'$ref': '#/components/schemas/BaseChoice'}}}
    return {'paths': {'/v3/tracker/configuration': {'post': {'requestBody': {'content': {
        'application/json': {'schema': {'type': 'object', 'properties': {
            'fields': {'type': 'array', 'items': field}}}}}}}}}, 'components': {'schemas': {
        'BaseChoice': {'type': 'object', 'properties': {'type': {'type': 'string'}},
            'discriminator': {'mapping': {'CHOICE_OPTIONS': '#/components/schemas/Options'}}},
        'Options': {'type': 'object', 'properties': {'type': {'enum': ['CHOICE_OPTIONS']},
            'choiceOptions': {'type': 'array', 'items': {'type': 'object', 'required': ['name'],
                'properties': {'name': {'type': 'string'}, 'id': {'type': 'integer'}}}}}}
    }}}


def baseline():
    return {'basicInformation': {'trackerId': 100}, 'fields': [
        {'id': 1, 'label': 'Priority', 'typeId': 6, 'permission': {'type': 'PER_STATUS'},
         'choiceOptionSetting': {'type': 'CHOICE_OPTIONS', 'choiceOptions': [{'id': 99, 'name': 'Old'}]}}]}


def proposal():
    return {'label': 'Labels', 'typeId': 6, 'mandatory': False, 'multipleSelection': True,
            'permission': {'type': 'PER_STATUS'}, 'reference': None, 'options': [{'name': 'A'}, {'name': 'B'}]}


class ConfigurationFieldWireTests(unittest.TestCase):
    def reference_contract(self, **properties):
        spec = contract()
        item = spec['paths']['/v3/tracker/configuration']['post']['requestBody']['content']['application/json']['schema']['properties']['fields']['items']
        item['properties']['referenceId'] = dict(type='integer', format='int32', **properties)
        return spec

    def test_optional_reference_schema_does_not_mean_automatic_server_allocation(self):
        with self.assertRaisesRegex(ApiError, 'referenceId'):
            definition_body(self.reference_contract(), proposal(), baseline())
        planned = definition_body(self.reference_contract(), proposal(), baseline(), planning=True)
        self.assertNotIn('referenceId', planned)

    def test_explicit_reference_reservation_is_preserved_without_changing_baseline(self):
        before = baseline()
        old = deepcopy(before)
        wire = definition_body(self.reference_contract(), proposal(), before, 55)
        self.assertEqual(wire['referenceId'], 55)
        self.assertEqual(before, old)
        self.assertNotIn('options', wire)

    def test_reference_collision_in_any_id_property_blocks(self):
        for key in ('id', 'referenceId', 'fieldId'):
            before = baseline()
            before['fields'][0][key] = 55
            with self.subTest(key=key), self.assertRaisesRegex(ApiError, 'already in use'):
                definition_body(self.reference_contract(), proposal(), before, 55)

    def test_reference_invalid_values_and_actual_schema_bounds_block(self):
        for value in (True, '55', 2**31, -(2**31)-1):
            with self.subTest(value=value), self.assertRaises(ApiError):
                definition_body(self.reference_contract(), proposal(), baseline(), value)
        for props, value in (({'minimum': 50}, 49), ({'maximum': 60}, 61), ({'enum': [55]}, 56)):
            with self.subTest(props=props), self.assertRaises(ApiError):
                definition_body(self.reference_contract(**props), proposal(), baseline(), value)

    def test_reference_unknown_or_readonly_contract_blocks_explicit_id(self):
        for spec in ({}, contract(), self.reference_contract(readOnly=True)):
            with self.assertRaises(ApiError): definition_body(spec, proposal(), baseline(), 55)

    def test_reference_configuration_is_scoped_to_tracker_and_label(self):
        from clients.configuration_fields import configured_reference_id
        config = {'configuration_reference_ids': {'100': {'Labels': 55}}}
        self.assertEqual(configured_reference_id(config, 100, 'Labels'), 55)
        self.assertIsNone(configured_reference_id(config, 101, 'Labels'))
        self.assertIsNone(configured_reference_id(config, 100, 'Other'))

    def test_missing_reference_blocks_before_journal_and_post(self):
        cb = Mock()
        cb.spec, cb.config, cb.write = self.reference_contract(), {}, False
        cb.configuration.return_value = baseline()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'journal.json'
            with self.assertRaisesRegex(ApiError, 'referenceId'):
                create_confirmed_field(cb, 100, proposal(), baseline(), path, approved=True)
            self.assertFalse(path.exists())
        cb.call.assert_not_called()

    def test_choice_serialization_preserves_values_and_does_not_copy_option_ids(self):
        before, definition = baseline(), proposal()
        originals = deepcopy((before, definition))
        result = definition_body(contract(), definition, before)
        self.assertNotIn('options', result)
        self.assertNotIn('reference', result)
        self.assertEqual(result['choiceOptionSetting'], {'type': 'CHOICE_OPTIONS', 'choiceOptions': definition['options']})
        self.assertTrue(result['multipleSelection'])
        self.assertEqual((before, definition), originals)

    def test_null_internal_properties_removed_for_text(self):
        definition = dict(proposal(), options=None, reference=None, typeId=0)
        result = definition_body(contract(), definition, baseline())
        self.assertNotIn('options', result)
        self.assertNotIn('reference', result)
        self.assertNotIn('choiceOptionSetting', result)

    def test_nonempty_reference_and_unknown_properties_blocked(self):
        for values in ({'reference': {'trackerId': 99}}, {'invented': True}):
            with self.assertRaises(ApiError):
                definition_body(contract(), dict(proposal(), **values), baseline())

    def test_choice_cannot_silently_disappear_without_verified_schema_or_template(self):
        spec = contract()
        del spec['components']['schemas']['BaseChoice']['discriminator']
        with self.assertRaises(ApiError): definition_body(spec, proposal(), baseline())
        with self.assertRaises(ApiError): definition_body(contract(), proposal(), {'fields': []})

    def test_required_option_id_blocks_unverified_id_allocation(self):
        spec = contract()
        spec['components']['schemas']['Options']['properties']['choiceOptions']['items']['required'].append('id')
        with self.assertRaisesRegex(ApiError, 'required'):
            definition_body(spec, proposal(), baseline())

    def test_configuration_post_and_readback_use_native_options(self):
        before = baseline()
        cb = Mock()
        cb.spec, cb.write, cb.config = self.reference_contract(), False, {'configuration_reference_ids': {'100': {'Labels': 55}}}
        cb.operation.return_value = {'method': 'POST', 'path': '/v3/tracker/configuration'}
        after = deepcopy(before)
        cb.configuration.side_effect = [before, after]
        def post(name, params, body):
            self.assertEqual(body['fields'][:-1], before['fields'])
            new = deepcopy(body['fields'][-1])
            self.assertNotIn('options', new)
            self.assertNotIn('reference', new)
            self.assertEqual(new['referenceId'], 55)
            new['id'] = 2
            for i, option in enumerate(new['choiceOptionSetting']['choiceOptions']): option['id'] = 101+i
            after['fields'].append(new)
            return {}
        cb.call.side_effect = post
        with tempfile.TemporaryDirectory() as root:
            created, _ = create_confirmed_field(cb, 100, proposal(), before, Path(root)/'journal.json', approved=True)
            self.assertEqual(created['options'][0]['id'], 101)
            receipt = next(iter(json.loads((Path(root)/'journal.json').read_text()).values()))
            self.assertEqual(receipt['status'], 'done')
        self.assertFalse(cb.write)
        self.assertEqual(cb.call.call_count, 1)
        self.assertEqual(normalize(after['fields'][-1])['options'], created['options'])

    def test_readback_accepts_assigned_ids_but_rejects_missing_or_different_options(self):
        wire = definition_body(contract(), proposal(), baseline())
        actual = deepcopy(wire)
        for i, option in enumerate(actual['choiceOptionSetting']['choiceOptions']): option['id'] = 101+i
        self.assertEqual(definition_changes(wire, actual), [])
        actual['choiceOptionSetting']['choiceOptions'][0]['name'] = 'Different'
        self.assertEqual(definition_changes(wire, actual), ['choiceOptionSetting'])

    def test_known_properties_list_does_not_misclassify_unknown_field_as_permission(self):
        raw = json.dumps({'message': 'Unrecognized field "options" (known properties: "permission", "referenceId")'}).encode()
        message = http_failure_detail(raw, 'application/json', '')
        self.assertIn('options', message)
        self.assertNotIn('permission', message)
