import unittest
from unittest.mock import Mock
from clients.codebeamer_client import CodebeamerClient
from clients.http import ApiError

class OperationInitializationTests(unittest.TestCase):
    def test_operation_initializes_once_and_uses_discovered_binding(self):
        cb=CodebeamerClient(Mock(),{})
        def discover():
            cb.spec={'paths':{'/v3/tracker/configuration':{'post':{}}}}
            cb.config['operations']={'tracker_configuration_update':{
                'path':'/v3/tracker/configuration','method':'POST'}}
        cb.discover=Mock(side_effect=discover)
        self.assertEqual(cb.operation('tracker_configuration_update')['method'],'POST')
        cb.operation('tracker_configuration_update')
        cb.discover.assert_called_once()
        cb.http.request.assert_not_called()

    def test_discovery_failure_cannot_execute_request(self):
        cb=CodebeamerClient(Mock(),{},write=True)
        cb.discover=Mock(side_effect=ApiError('Definition unavailable'))
        with self.assertRaisesRegex(ApiError,'item_create.*요청 미실행'):
            cb.call('item_create',{}, {'name':'test'})
        cb.http.request.assert_not_called()
        self.assertIsNone(cb.spec)

    def test_loaded_contract_still_rejects_absent_route(self):
        cb=CodebeamerClient(Mock(),{'operations':{'item_create':{'path':'/missing','method':'POST'}}})
        cb.spec={'paths':{}}
        cb.discover=Mock()
        with self.assertRaisesRegex(ApiError,'Operation not present'):
            cb.operation('item_create')
        cb.discover.assert_not_called()
        cb.http.request.assert_not_called()
