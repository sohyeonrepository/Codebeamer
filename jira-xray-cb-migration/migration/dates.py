"""Normalize Jira timezone offsets for Python versions with strict ISO parsers."""
import re
from datetime import datetime


def normalize_iso(value):
    if not isinstance(value,str):
        raise ValueError('Timestamp must be a string')
    if value.endswith('Z'):
        value=value[:-1]+'+00:00'
    return re.sub(r'([+-]\d{2})(\d{2})$',r'\1:\2',value)


def parse_iso(value):
    return datetime.fromisoformat(normalize_iso(value))
