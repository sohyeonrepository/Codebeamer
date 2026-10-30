import unittest
from clients.http import ApiError
from clients.codebeamer_payloads import request_body, canonical_fields


class ConfirmedContracts(unittest.TestCase):
    # Synthetic discriminator/property bindings supplied by a mock server.
    bindings = {'source_meta': {'fieldId': 111, 'type': 'MockTextFieldValue', 'value_property': 'value'}}

    def test_custom_field_update_uses_field_values(self):
        result = request_body('updateCustomFieldTrackerItem', {'source_meta': 'original'}, self.bindings)
        self.assertEqual(result, {'fieldValues': [{'fieldId': 111, 'type': 'MockTextFieldValue', 'value': 'original'}]})
        self.assertNotIn('customFields', result)
        with self.assertRaises(ApiError):
            request_body('updateCustomFieldTrackerItem', {'unknown': 'value'}, self.bindings)

    def test_create_uses_native_name_and_custom_fields(self):
        result = request_body('createTrackerItem', {'name': 'Run', 'source_meta': None}, self.bindings)
        self.assertEqual(result['name'], 'Run')
        self.assertEqual(result['customFields'][0]['value'], None)
        with self.assertRaises(ApiError):
            request_body('createTrackerItem', {'source_meta': 'original'}, self.bindings)

    def test_missing_discriminator_and_duplicate_fields_rejected(self):
        with self.assertRaises(ApiError):
            request_body('updateCustomFieldTrackerItem', {'fieldValues': [{'fieldId': 1, 'value': 'a'}]})
        with self.assertRaises(ApiError):
            request_body('updateCustomFieldTrackerItem', {'fieldValues': [
                {'fieldId': 1, 'type': 'Mock', 'value': 'a'}, {'fieldId': 1, 'type': 'Mock', 'value': 'b'}]})

    def test_run_result_contract_requires_real_test_case_reference(self):
        model = {'result': 'PASS', 'testCaseReference': {'id': 123}, 'runTime': 40}
        self.assertEqual(request_body('updateTestRunResult', model), {'updateRequestModels': [model]})
        with self.assertRaises(ApiError):
            request_body('updateTestRunResult', {'result': 'PASS', 'testSetReference': {'id': 123}})
        with self.assertRaises(ApiError):
            request_body('updateTestRunResult', {'updateRequestModels': []})

    def test_tracker_permission_is_structured(self):
        body = {'basicInformation': {'trackerId': 4862420}, 'fields': [{'label': 'x', 'permission': {'type': 'UNRESTRICTED'}}]}
        self.assertEqual(request_body('postTrackerConfiguration', body), body)
        body['fields'][0]['permission'] = 'Unrestricted'
        with self.assertRaises(ApiError):
            request_body('postTrackerConfiguration', body)

    def test_field_value_readback_normalization(self):
        values = [{'fieldId': 111, 'type': 'MockTextFieldValue', 'value': 'original'}]
        self.assertEqual(canonical_fields(values, self.bindings), {'source_meta': 'original'})
        values[0]['type'] = 'DifferentMockType'
        with self.assertRaises(ApiError):
            canonical_fields(values, self.bindings)

    def test_invalid_envelope_is_blocked_before_http(self):
        from clients.codebeamer_client import CodebeamerClient
        class Http:
            def request(self, *args, **kwargs):
                raise AssertionError('Invalid request must not reach server')
        cb = CodebeamerClient(Http(), {'operations': {'run_update': {'operationId': 'updateCustomFieldTrackerItem'}}}, True)
        cb.spec = {'paths': {'/v3/items/{itemId}/fields': {'put': {'operationId': 'updateCustomFieldTrackerItem'}}}}
        with self.assertRaises(ApiError):
            cb.call('run_update', {'itemId': 123}, {'name': 'flat payload'})

    def test_optional_permission_is_preserved_as_omitted(self):
        body={'basicInformation':{'trackerId':4872985},'fields':[{'id':1,'label':'System field'}]}
        self.assertEqual(request_body('postTrackerConfiguration',body),body)
        body['fields'][0]['permission']=None
        with self.assertRaises(ApiError):request_body('postTrackerConfiguration',body)
