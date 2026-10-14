"""Offline cross-year safety and CRLF serialization regressions."""
import copy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_calendar as updater
import validate_ics as validator


class CrossYearCrlfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = updater.ICS.read_bytes()
        cls.events = updater.parse_existing_events(cls.raw.decode("utf-8").splitlines())
        cls.official = {}
        for (year, term), event in cls.events.items():
            event_date = datetime.strptime(event["DTSTART"], "%Y%m%d").date()
            cls.official.setdefault(year, []).append((event_date, term))
        cls.official = {year: sorted(series) for year, series in cls.official.items()}

    def shifted(self, changes):
        official = copy.deepcopy(self.official)
        for year, term, new_date in changes:
            official[year] = [(new_date if name == term else day, name)
                              for day, name in official[year]]
        return official

    def test_within_year_valid_shifts_are_rejected_across_years(self):
        changes = [(2017, "冬至", date(2017, 12, 23)),
                   (2018, "小寒", date(2018, 1, 4))]
        for change in changes:
            official = self.shifted([change])
            # Each individual change passes the old per-year check. The new
            # boundary check must also reject each resulting 13-day gap.
            updater.validate_term_series(change[0], official[change[0]])
            with self.assertRaisesRegex(RuntimeError, "implausible interval 13 days"):
                updater.validate_calendar_series(official)
        official = self.shifted(changes)
        for year in (2017, 2018):
            updater.validate_term_series(year, official[year])
        with self.assertRaisesRegex(RuntimeError, "implausible interval 12 days"):
            updater.validate_calendar_series(official)
        with self.assertRaisesRegex(RuntimeError, "implausible interval 12 days"):
            updater.build_calendar(official, self.events)

        # Mutate in memory while preserving the fixed UID contract.
        events = copy.deepcopy(self.events)
        for year, term, new_date in changes:
            events[(year, term)]["DTSTART"] = new_date.strftime("%Y%m%d")
            events[(year, term)]["DTEND"] = (new_date + timedelta(days=1)).strftime("%Y%m%d")
        with self.assertRaisesRegex(RuntimeError, "implausible interval 12 days"):
            updater.validate_existing_events(events)
        text = self.raw.decode("utf-8")
        for old, new in (("DTSTART;VALUE=DATE:20171222", "DTSTART;VALUE=DATE:20171223"),
                         ("DTEND;VALUE=DATE:20171223", "DTEND;VALUE=DATE:20171224"),
                         ("DTSTART;VALUE=DATE:20180105", "DTSTART;VALUE=DATE:20180104"),
                         ("DTEND;VALUE=DATE:20180106", "DTEND;VALUE=DATE:20180105")):
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        errors = validator.validate_lines(text.splitlines())[1]
        self.assertTrue(any("implausible interval 12 days" in e for e in errors), errors)
        with self.assertRaisesRegex(RuntimeError, "implausible interval 12 days"):
            updater.validate_staged_outputs(text, "最近核验：2026-10-13")

    def test_consecutive_subset_boundary_is_checked_without_bridging_missing_years(self):
        official = self.shifted([(2017, "冬至", date(2017, 12, 23)),
                                 (2018, "小寒", date(2018, 1, 4))])
        with self.assertRaisesRegex(RuntimeError, "implausible interval 12 days"):
            updater.validate_calendar_series({year: official[year] for year in (2017, 2018)})
        updater.validate_calendar_series({year: official[year] for year in (2017, 2019)})

    def test_published_and_serialized_bytes_have_only_final_terminated_crlf(self):
        generated = updater.build_calendar(self.official, self.events,
                                           now=datetime(2026, 10, 13, tzinfo=timezone.utc))
        for raw in (self.raw, generated.encode("utf-8")):
            self.assertTrue(raw.endswith(b"\r\n"))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            self.assertNotIn(b"\r", raw.replace(b"\r\n", b""))
        self.assertEqual(updater.parse_existing_events(generated.splitlines()), self.events)
        self.assertEqual(validator.validate_lines(generated.splitlines())[1], [])


if __name__ == "__main__":
    unittest.main()
