"""System field mapping must use explicit native Item contracts, never guessed IDs."""
import unittest
from clients.codebeamer_payloads import native_field_property


def request(properties):
    return {'requestBody': {'content': {'application/json': {
        'schema': {'type': 'object', 'properties': properties}
    }}}}


class NativeFieldContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = {'components': {'schemas': {'ChoiceOptionReference': {
            'type': 'object',
            'properties': {'id': {'type': 'integer'}, 'name': {'type': 'string'}}
        }}}}
        self.good = request({'resolution': {'$ref': '#/components/schemas/ChoiceOptionReference'}})

    def test_legacy_singular_resolution_is_not_a_writable_array(self):
        self.assertIsNone(
            native_field_property(self.spec, self.good, 'resolution',
                                  {'id': 15, 'label': 'Resolution'}, self.good))

    def test_native_resolution_array_from_server_item_schema(self):
        shape = {'resolutions': {'type': 'array',
                 'items': {'$ref': '#/components/schemas/ChoiceOptionReference'}}}
        operation = request(shape)
        self.assertEqual(native_field_property(self.spec, operation, 'resolution',
                                               {'id': 15, 'label': 'Resolution'}, operation),
                         'resolutions')

    def test_resolution_array_must_be_writable_and_id_sufficient(self):
        from copy import deepcopy
        operation = request({'resolutions': {'type': 'array',
                             'items': {'$ref': '#/components/schemas/ChoiceOptionReference'}}})
        readonly = request({'resolutions': {'type': 'array', 'readOnly': True,
                            'items': {'$ref': '#/components/schemas/ChoiceOptionReference'}}})
        self.assertIsNone(native_field_property(self.spec, readonly, 'resolution',
                          {'id': 15, 'label': 'Resolution'}, operation))
        self.spec['components']['schemas']['ChoiceOptionReference']['required'] = ['id', 'type']
        self.assertIsNone(native_field_property(self.spec, operation, 'resolution',
                          {'id': 15, 'label': 'Resolution'}, operation))

    def test_native_resolution_payload_omits_null_and_wraps_choice_id(self):
        from migration.fields import payload
        mapping = {'resolution': {
            'type': 'option', 'target': 'resolutions', 'native_array': True,
            'value_mapping': {'Done': {'id': 42}}
        }}
        self.assertEqual(payload({'resolution': None}, mapping, {}), {})
        self.assertEqual(payload({'resolution': {'name': 'Done'}}, mapping, {}),
                         {'resolutions': [{'id': 42}]})

    def test_no_field_id_shortcut_or_schema_guess(self):
        self.assertIsNone(native_field_property(self.spec, self.good, 'resolution',
                          {'id': 15, 'label': 'Other'}, self.good))
        self.assertIsNone(native_field_property(self.spec, self.good, 'resolution',
                          {'id': 15, 'label': 'Resolution'}))
        self.assertIsNone(native_field_property(self.spec,
                          request({'name': {'type':'string'}}), 'resolution',
                          {'id': 15, 'label': 'Resolution'}, self.good))

    def test_readonly_or_scalar_values_are_not_assumed_writable(self):
        readonly = request({'resolution': {'type':'object', 'readOnly':True,
                           'properties': {'id': {'type':'integer'}}}})
        scalar = request({'resolution': {'type':'integer'}})
        for bad in (readonly, scalar):
            self.assertIsNone(native_field_property(self.spec, bad, 'resolution',
                              {'id': 15, 'label':'Resolution'}, self.good))
            self.assertIsNone(native_field_property(self.spec, self.good, 'resolution',
                              {'id': 15, 'label':'Resolution'}, bad))


if __name__ == '__main__':
    unittest.main()
