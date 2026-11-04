import unittest
from clients.http import ApiError
from clients.xray_dc_contract import inspect_xray_dc, dc_read_path


class StubHttp:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, method, path):
        self.calls.append((method, path))
        return self.result


class XrayDcDiscoveryTests(unittest.TestCase):
    def test_dc_membership_endpoints_are_read_only(self):
        expected = {
            "test_steps": "/api/test/P-1/step",
            "test_sets_for_test": "/api/test/P-1/testsets",
            "test_executions_for_test": "/api/test/P-1/testexecutions",
            "test_plans_for_test": "/api/test/P-1/testplans",
            "test_set_tests": "/api/testset/P-1/test",
            "test_execution_tests": "/api/testexec/P-1/test",
            "test_plan_tests": "/api/testplan/P-1/test",
            "test_plan_executions": "/api/testplan/P-1/testexecution",
        }
        for kind, suffix in expected.items():
            with self.subTest(kind=kind):
                self.assertTrue(dc_read_path(kind, "P-1").endswith(suffix))
                http = StubHttp([{"key": "P-2"}])
                result = inspect_xray_dc(http, kind, "P-1")
                self.assertEqual(http.calls, [("GET", result["path"])])
                self.assertEqual(result["api_family"], "xray-server-dc")
                self.assertFalse(result["complete"])

    def test_run_detail_steps_and_evidence(self):
        for kind in ("test_run", "test_run_steps", "test_run_comment", "test_run_evidence"):
            with self.subTest(kind=kind):
                http = StubHttp({"id": 4017, "status": "PASS"})
                result = inspect_xray_dc(http, kind, 4017)
                self.assertIn("/rest/raven/1.0/api/testrun/4017", result["path"])
                self.assertEqual(result["raw"]["id"], 4017)

    def test_archive_raw_without_exposing_credentials_or_claiming_completeness(self):
        calls = []
        def save(*args):
            calls.append(args)
        payload = {"total": 6, "entries": [{"key": "P-2"}]}
        output = inspect_xray_dc(StubHttp(payload), "test_set_tests", "P-1", save)
        self.assertEqual(calls, [("xray-dc-test_set_tests", "P-1", payload)])
        self.assertFalse(output["pagination_verified"])
        self.assertFalse(output["complete"])
        self.assertFalse(output["write_performed"])

    def test_unknown_or_cloud_api_names_are_rejected_before_network(self):
        http = StubHttp([])
        for operation in ("getTestRuns", "graphql", "cloud_auth", "test_run_activity"):
            with self.assertRaises(ApiError):
                inspect_xray_dc(http, operation, 17)
        for identifier in ("../admin", "P-1?evil=true", "invalid"):
            with self.assertRaises(ApiError):
                inspect_xray_dc(http, "test_set_tests", identifier)
        self.assertEqual(http.calls, [])

    def test_invalid_response_shape_fails_closed(self):
        with self.assertRaises(ApiError):
            inspect_xray_dc(StubHttp("some text"), "test_run", 17)


if __name__ == "__main__":
    unittest.main()
