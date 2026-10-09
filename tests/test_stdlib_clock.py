"""Maintenance clock does not depend on external IANA data on Windows."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StandardLibraryClockTests(unittest.TestCase):
    def test_beijing_new_year_selection_without_timezone_database(self):
        code = '''
from datetime import datetime, timezone, timedelta
import sys
from unittest.mock import patch
import zoneinfo
zoneinfo.reset_tzpath(())
sys.path.insert(0, 'scripts')
import update_calendar as u
original = u.ICS.read_bytes()
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 12, 31, 16, tzinfo=timezone.utc).astimezone(tz)
with patch.object(u, 'datetime', Clock), patch.object(u, 'fetch_astronomy_xml', return_value=()) as fetched:
    assert u.update_verified_date('最近核验：2000-01-01') == '最近核验：2027-01-01'
    assert u.crosscheck_near_term_astronomy({2027: [], 2028: [], 2029: []}) == [2027, 2028, 2029]
    assert [c.args[0] for c in fetched.call_args_list] == [2027, 2028, 2029]
assert u.BEIJING_TIMEZONE.utcoffset(None) == timedelta(hours=8)
assert u.ICS.read_bytes() == original
assert not any(name.startswith('tzdata') for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-S', '-B', '-c', code], cwd=ROOT,
            env=dict(os.environ, PYTHONTZPATH='', PYTHONUTF8='1'), capture_output=True,
            text=True, encoding='utf-8', timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
