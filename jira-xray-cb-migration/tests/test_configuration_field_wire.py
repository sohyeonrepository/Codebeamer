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
        cb.spec, cb.write, cb.config = contract(), False, {}
        cb.operation.return_value = {'method': 'POST', 'path': '/v3/tracker/configuration'}
        after = deepcopy(before)
        cb.configuration.side_effect = [before, after]
        def post(name, params, body):
            self.assertEqual(body['fields'][:-1], before['fields'])
            new = deepcopy(body['fields'][-1])
            self.assertNotIn('options', new)
            self.assertNotIn('reference', new)
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
