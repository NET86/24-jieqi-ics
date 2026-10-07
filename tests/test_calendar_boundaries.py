"""Offline adversarial date/identity tests independent of the online source fetcher."""
import contextlib
import copy
from datetime import datetime
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import update_calendar as updater
import validate_ics as validator


class CalendarBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = validator.ICS.read_text(encoding='utf-8')
        cls.lines = cls.text.splitlines()

    def test_noncanonical_short_and_non_ascii_dates_are_rejected(self):
        for bad in ('2015016', '201516', '２０１５０１０６', '2015-01-06'):
            with self.subTest(bad=bad):
                text = self.text.replace('DTSTART;VALUE=DATE:20150106',
                                         'DTSTART;VALUE=DATE:' + bad, 1)
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(any('invalid date' in error for error in errors), errors)

    def test_duplicate_singletons_do_not_use_last_value_wins(self):
        pairs = (
            ('DTSTART;VALUE=DATE:20150106', 'DTSTART;VALUE=DATE:20150701'),
            ('DTSTART;VALUE=DATE:20150106', 'dtstart;value=date:20150701'),
            ('DTSTART;VALUE=DATE:20150106', 'DTSTART;VALUE=DATE;X-TEST=1:20150701'),
            ('DTEND;VALUE=DATE:20150107', 'DTEND;VALUE=DATE:20150702'),
            ('SUMMARY:小寒', 'SUMMARY:大暑'),
            ('UID:2015-01-06-lc@infinet.github.io', 'UID:different@example.invalid'),
        )
        for original, duplicate in pairs:
            with self.subTest(duplicate=duplicate):
                text = self.text.replace(original, duplicate + '\n' + original, 1)
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(any('duplicate ' in error for error in errors), errors)

    def test_interval_checked_even_when_both_dates_within_month_windows(self):
        text = self.text
        for old, new in (('DTSTART;VALUE=DATE:20150106', 'DTSTART;VALUE=DATE:20150107'),
                         ('DTEND;VALUE=DATE:20150107', 'DTEND;VALUE=DATE:20150108'),
                         ('DTSTART;VALUE=DATE:20150120', 'DTSTART;VALUE=DATE:20150119'),
                         ('DTEND;VALUE=DATE:20150121', 'DTEND;VALUE=DATE:20150120')):
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        _, errors = validator.validate_lines(text.splitlines())
        self.assertTrue(any('implausible interval 12 days' in error for error in errors), errors)

    def test_exclusive_end_date_must_be_next_day(self):
        text = self.text.replace('DTEND;VALUE=DATE:20150107', 'DTEND;VALUE=DATE:20150108', 1)
        _, errors = validator.validate_lines(text.splitlines())
        self.assertTrue(any('DTEND must be exactly one day' in error for error in errors), errors)

    def test_cli_returns_nonzero_for_original_bad_month_reproducer(self):
        text = self.text.replace('DTSTART;VALUE=DATE:20150106', 'DTSTART;VALUE=DATE:20150701', 1)
        text = text.replace('DTEND;VALUE=DATE:20150107', 'DTEND;VALUE=DATE:20150702', 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'calendar.ics'
            path.write_text(text, encoding='utf-8')
            with mock.patch.object(validator, 'ICS', path), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(validator.main(), 1)

    def test_update_jump_guard_remains_intact_and_no_change_is_idempotent(self):
        existing = updater.parse_existing_events(self.lines)
        official = {}
        for (year, term), event in existing.items():
            official.setdefault(year, []).append((datetime.strptime(event['DTSTART'], '%Y%m%d').date(), term))
        self.assertEqual(updater.describe_date_changes(official, existing), [])
        self.assertEqual(updater.build_calendar(official, existing), '\n'.join(self.lines) + '\n')
        corrupted = copy.deepcopy(existing)
        corrupted[(2015, '小寒')]['DTSTART'] = '20150701'
        with self.assertRaisesRegex(RuntimeError, 'refusing 176-day date jump'):
            updater.describe_date_changes(official, corrupted)
        # The guard neither loosens nor writes a replacement for corrupted local data.
        self.assertEqual(validator.ICS.read_text(encoding='utf-8'), self.text)


if __name__ == '__main__':
    unittest.main()
