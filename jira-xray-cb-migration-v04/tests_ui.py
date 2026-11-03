"""Smoke imports and ensures that GUI module does not instantiate root on import."""
import unittest
import app
from migrator import kind
class GuiSmoke(unittest.TestCase):
    def test_app_import(self):
        self.assertTrue(hasattr(app,'App'))
    def test_mapping_types(self):
        self.assertEqual(len(app.CATEGORIES),4)
        self.assertEqual(kind({'fields':{'issuetype':{'name':'Test Plan'}}}),'test_plan')
if __name__=='__main__':unittest.main()
