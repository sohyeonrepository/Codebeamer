import unittest
from unittest.mock import patch
from datetime import timedelta,datetime
from migration.dates import normalize_iso,parse_iso
from migration.execution_events import timestamp
from clients.http import ApiError

class JiraDateTests(unittest.TestCase):
    def test_compact_offsets_are_normalized_before_builtin_parser(self):
        for value,expected in [('2025-06-18T14:48:05.330+0900','2025-06-18T14:48:05.330+09:00'),
                               ('2025-06-18T14:48:05-0430','2025-06-18T14:48:05-04:30'),
                               ('2025-06-18T14:48:05Z','2025-06-18T14:48:05+00:00')]:
            with patch('migration.dates.datetime') as parser:
                parse_iso(value)
                parser.fromisoformat.assert_called_once_with(expected)
    def test_jira_offset_and_fraction_preserved(self):
        actual=timestamp('2025-06-18T14:48:05.330+0900')
        self.assertEqual(actual.utcoffset(),timedelta(hours=9))
        self.assertEqual(actual.microsecond,330000)
        self.assertEqual(actual,datetime.fromisoformat('2025-06-18T05:48:05.330+00:00'))
    def test_naive_history_still_rejected(self):
        with self.assertRaises(ApiError):timestamp('2025-06-18T14:48:05')
    def test_invalid_date_not_hidden(self):
        with self.assertRaises(ValueError):parse_iso('invalid+0900')
        self.assertEqual(normalize_iso('2025-06-18'),'2025-06-18')
