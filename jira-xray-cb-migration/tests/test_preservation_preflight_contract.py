"""Protect against reintroducing synthetic lookup requirements to Wizard preflight.

Run: python -m unittest discover -s tests -p test_preservation_preflight_contract.py
"""
import inspect
import unittest
from migration.preservation import preflight
from migration.engine import Engine


class PreservationPreflightContractTest(unittest.TestCase):
    def test_no_synthetic_lookup_required_by_preflight(self):
        source = inspect.getsource(preflight)
        section = source.split("required =", 1)[1].split("for name in required:", 1)[0]
        self.assertNotIn("'lookup'", section)
        self.assertNotIn("'tracker_lookup'", section)
        self.assertNotIn("'attachment_lookup'", section)
        self.assertNotIn("'comment_lookup'", section)

    def test_wizard_engine_never_calls_old_lookup_on_missing_identity(self):
        source = inspect.getsource(Engine.issue)
        section = source.split("identity = None", 1)[1].split("if not isinstance(lookup, list)", 1)[0]
        self.assertIn("JIRA_IDENTITY_BINDING_MISSING", section)
        self.assertIn("lookup_by_jira_key", section)


if __name__ == "__main__":
    unittest.main()
