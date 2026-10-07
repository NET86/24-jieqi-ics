"""Offline adversarial date/identity tests independent of the online source fetcher."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
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
                self.assertTrue(
                    any(
                        'duplicate ' in error
                        or 'unsupported VEVENT property form' in error
                        for error in errors
                    ),
                    errors,
                )

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


    def test_dtstamp_is_required_unique_and_strict_utc(self):
        first = 'DTSTAMP:20190912T184136Z'
        for bad in (
            'DTSTAMP:not-a-date',
            'DTSTAMP:20260230T120000Z',
            'DTSTAMP:20260101T120000',
            'DTSTAMP:20260101T246000Z',
        ):
            with self.subTest(bad=bad):
                text = self.text.replace(first, bad, 1)
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(any('invalid UTC DTSTAMP' in error for error in errors), errors)
                with self.assertRaisesRegex(RuntimeError, 'invalid existing VEVENT'):
                    updater.parse_existing_events(text.splitlines())

        missing = self.text.replace(first + '\n', '', 1)
        _, errors = validator.validate_lines(missing.splitlines())
        self.assertTrue(any('missing DTSTAMP' in error for error in errors), errors)
        with self.assertRaisesRegex(RuntimeError, 'missing fields'):
            updater.parse_existing_events(missing.splitlines())

        duplicate = self.text.replace(first, first + '\n' + first, 1)
        with self.assertRaisesRegex(ValueError, 'duplicate DTSTAMP'):
            validator.parse_events(duplicate.splitlines())
        with self.assertRaisesRegex(RuntimeError, 'duplicate DTSTAMP'):
            updater.parse_existing_events(duplicate.splitlines())

    def test_revision_advances_dtstamp_and_sequence_once(self):
        existing = updater.parse_existing_events(self.lines)
        official = {}
        for (year, term), event in existing.items():
            official.setdefault(year, []).append(
                (datetime.strptime(event['DTSTART'], '%Y%m%d').date(), term)
            )

        key = (2026, '小寒')
        old = existing[key]
        old_date = datetime.strptime(old['DTSTART'], '%Y%m%d').date()
        revised_date = old_date + timedelta(days=1)
        official[2026] = [
            (revised_date if term == '小寒' else event_date, term)
            for event_date, term in official[2026]
        ]
        fixed_now = datetime(2026, 10, 7, 4, 5, 6, tzinfo=timezone.utc)
        revised = updater.build_calendar(official, existing, now=fixed_now)
        revised_events = updater.parse_existing_events(revised.splitlines())
        changed = revised_events[key]
        self.assertEqual(changed['UID'], old['UID'])
        self.assertEqual(changed['DTSTART'], revised_date.strftime('%Y%m%d'))
        self.assertEqual(changed['DTSTAMP'], '20261007T040506Z')
        self.assertEqual(changed['SEQUENCE'], '1')

        _, errors = validator.validate_lines(revised.splitlines())
        self.assertEqual(errors, [])

        repeated = updater.build_calendar(
            official,
            revised_events,
            now=fixed_now + timedelta(days=1),
        )
        self.assertEqual(repeated, revised)

        preversioned = copy.deepcopy(existing)
        preversioned[key]['SEQUENCE'] = '7'
        revised_preversioned = updater.build_calendar(official, preversioned, now=fixed_now)
        parsed_preversioned = updater.parse_existing_events(revised_preversioned.splitlines())
        self.assertEqual(parsed_preversioned[key]['SEQUENCE'], '8')

    def test_uid_contract_rejects_corruption_but_allows_one_day_revision(self):
        for bad_uid in (
            'manual-corrupt@example.invalid',
            '2099-12-31-lc@infinet.github.io',
            '2015-07-01-lc@infinet.github.io',
        ):
            with self.subTest(uid=bad_uid):
                text = self.text.replace(
                    'UID:2015-01-06-lc@infinet.github.io',
                    'UID:' + bad_uid,
                    1,
                )
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(any('stable UID' in error for error in errors), errors)
                with self.assertRaisesRegex(RuntimeError, 'stable UID'):
                    updater.parse_existing_events(text.splitlines())

        wrong_qingming = self.text.replace(
            'UID:2015-qingming@net86.github.io',
            'UID:2016-qingming@net86.github.io',
            1,
        )
        _, errors = validator.validate_lines(wrong_qingming.splitlines())
        self.assertTrue(any('invalid stable UID' in error for error in errors), errors)

    def test_same_window_uid_mutation_breaks_stable_identity_contract(self):
        text = self.text.replace(
            'UID:2015-01-06-lc@infinet.github.io',
            'UID:2015-01-07-lc@infinet.github.io',
            1,
        )
        _, errors = validator.validate_lines(text.splitlines())
        self.assertTrue(any('stable UID contract changed' in error for error in errors), errors)
        parsed = updater.parse_existing_events(text.splitlines())
        with self.assertRaisesRegex(RuntimeError, 'stable UID contract changed'):
            updater.validate_existing_events(parsed)

    def test_vevent_semantics_are_closed_to_unreviewed_extensions(self):
        for text, pattern in (
            (
                self.text.replace(
                    'STATUS:CONFIRMED',
                    'RRULE:FREQ=DAILY\nSTATUS:CONFIRMED',
                    1,
                ),
                'unsupported VEVENT property RRULE',
            ),
            (
                self.text.replace('STATUS:CONFIRMED', 'STATUS:CANCELLED', 1),
                'STATUS must be CONFIRMED',
            ),
        ):
            with self.subTest(pattern=pattern):
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(any(pattern in error for error in errors), errors)
                with self.assertRaisesRegex(RuntimeError, pattern):
                    updater.parse_existing_events(text.splitlines())

    def test_calendar_metadata_is_singleton_and_complete(self):
        mutations = (
            self.text.replace('PRODID:-//NET86//Chinese Solar Terms Calendar//ZH-CN\n', '', 1),
            self.text.replace('VERSION:2.0', 'VERSION:2.0\nVERSION:2.0', 1),
            self.text.replace('CALSCALE:GREGORIAN', 'CALSCALE:GREGORIAN\nCALSCALE:GREGORIAN', 1),
            self.text.replace('METHOD:PUBLISH', 'METHOD:PUBLISH\nMETHOD:PUBLISH', 1),
            self.text.replace('BEGIN:VEVENT', 'BEGIN:VCALENDAR\nBEGIN:VEVENT', 1),
        )
        for text in mutations:
            with self.subTest(text=text[:80]):
                _, errors = validator.validate_lines(text.splitlines())
                self.assertTrue(errors)

        without_method = self.text.replace('METHOD:PUBLISH\n', '', 1)
        _, errors = validator.validate_lines(without_method.splitlines())
        self.assertEqual(errors, [])


    def test_hko_final_source_must_remain_approved_https(self):
        requested = "https://www.hko.gov.hk/path"
        self.assertEqual(
            updater.validate_hko_final_url(requested, "https://www.weather.gov.hk/path"),
            "www.weather.gov.hk",
        )
        for final in ("https://evil.example/path", "http://www.hko.gov.hk/path"):
            with self.subTest(final=final), self.assertRaisesRegex(RuntimeError, "redirected outside"):
                updater.validate_hko_final_url(requested, final)

    def test_duplicate_final_hko_source_does_not_count_twice(self):
        existing = updater.parse_existing_events(self.lines)
        data = tuple(
            (
                datetime.strptime(event["DTSTART"], "%Y%m%d").date(),
                term,
            )
            for (year, term), event in existing.items()
            if year == 2015
        )
        calls = []

        def fake_fetch(year, template, attempts=2):
            calls.append(template)
            if len(calls) <= 2:
                return data, "www.hko.gov.hk"
            return data, "my.weather.gov.hk"

        with mock.patch.object(updater, "fetch_source", side_effect=fake_fetch):
            self.assertEqual(updater.fetch_year(2015), list(data))
        self.assertEqual(len(calls), 3)

    def test_publication_failure_restores_both_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ics = root / "calendar.ics"
            readme = root / "README.md"
            transaction = root / ".calendar-update-transaction"
            ics.write_text(self.text, encoding="utf-8")
            old_readme = "最近核验：2026-09-19\n"
            readme.write_text(old_readme, encoding="utf-8")
            new_readme = "最近核验：2026-10-07\n"
            real_replace = updater.os.replace

            def fail_second_target(source, destination):
                if Path(source).name == "readme.new" and Path(destination) == readme:
                    raise OSError("simulated README replace failure")
                return real_replace(source, destination)

            with (
                mock.patch.object(updater, "ICS", ics),
                mock.patch.object(updater, "README", readme),
                mock.patch.object(updater, "TRANSACTION_DIR", transaction),
                mock.patch.object(updater.os, "replace", side_effect=fail_second_target),
            ):
                with self.assertRaisesRegex(OSError, "simulated README replace failure"):
                    updater.publish_outputs(self.text, new_readme)

            self.assertEqual(ics.read_text(encoding="utf-8"), self.text)
            self.assertEqual(readme.read_text(encoding="utf-8"), old_readme)
            self.assertFalse(transaction.exists())

    def test_leftover_ready_transaction_restores_on_next_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ics = root / "calendar.ics"
            readme = root / "README.md"
            transaction = root / ".calendar-update-transaction"
            old_readme = "最近核验：2026-09-19\n"
            ics.write_text("corrupted calendar\n", encoding="utf-8")
            readme.write_text("corrupted readme\n", encoding="utf-8")
            transaction.mkdir()
            (transaction / "calendar.old").write_text(self.text, encoding="utf-8")
            (transaction / "readme.old").write_text(old_readme, encoding="utf-8")
            (transaction / "READY").write_text("ready\n", encoding="utf-8")

            with (
                mock.patch.object(updater, "ICS", ics),
                mock.patch.object(updater, "README", readme),
                mock.patch.object(updater, "TRANSACTION_DIR", transaction),
            ):
                updater.recover_publication_transaction()

            self.assertEqual(ics.read_text(encoding="utf-8"), self.text)
            self.assertEqual(readme.read_text(encoding="utf-8"), old_readme)
            self.assertFalse(transaction.exists())


if __name__ == '__main__':
    unittest.main()
