"""Regression checks for the Wizard-only Jira Key lookup contract.

Run: python -m unittest discover -s tests -p 'test_wizard_lookup_routing.py'
"""
import inspect
import unittest
from migration.engine import Engine
from migration.wizard import ImportWizard


class WizardLookupRoutingTest(unittest.TestCase):
    def test_wizard_checks_source_scope_before_old_lookup_fallback(self):
        source = inspect.getsource(Engine.issue)
        start = source.index("identity = None")
        end = source.index("if not isinstance(lookup, list)", start)
        branch = source[start:end]
        self.assertIn("if self.wizard:", branch)
        self.assertIn("bindings.get(scope)", branch)
        self.assertIn("JIRA_IDENTITY_BINDING_MISSING", branch)
        self.assertIn("lookup_by_jira_key(self.cb", branch)
        self.assertIn("self.cb.call('lookup'", branch)
        self.assertLess(branch.index("lookup_by_jira_key(self.cb"), branch.index("else:\n            # Preserve the existing explicit contract"))

    def test_wizard_handoff_happens_before_engine_start(self):
        source = inspect.getsource(ImportWizard.execute)
        self.assertLess(source.index("JIRA_IDENTITY_HANDOFF_MISSING"), source.index("report = Engine("))
        self.assertLess(source.index("identity-ready:"), source.index("report = Engine("))


if __name__ == "__main__":
    unittest.main()
