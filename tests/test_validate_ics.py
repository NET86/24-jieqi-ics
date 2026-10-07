import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_ics


class CalendarValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = validate_ics.ICS.read_text(encoding="utf-8").splitlines()

    def test_repository_calendar_passes_semantic_validation(self):
        events, errors = validate_ics.validate_lines(self.lines)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), validate_ics.EXPECTED_EVENT_COUNT)

    def test_implausible_manual_date_cannot_pass_offline_validation(self):
        text = "\n".join(self.lines)
        text = text.replace(
            "DTSTART;VALUE=DATE:20150106",
            "DTSTART;VALUE=DATE:20150701",
            1,
        ).replace(
            "DTEND;VALUE=DATE:20150107",
            "DTEND;VALUE=DATE:20150702",
            1,
        )
        _, errors = validate_ics.validate_lines(text.splitlines())
        self.assertTrue(
            any("2015 小寒: implausible date 2015-07-01" in error for error in errors),
            errors,
        )

    def test_term_order_is_checked_not_only_yearly_membership(self):
        text = "\n".join(self.lines)
        first = text.index("SUMMARY:小寒")
        second = text.index("SUMMARY:大寒", first)
        text = (
            text[:first]
            + "SUMMARY:大寒"
            + text[first + len("SUMMARY:小寒"):second]
            + "SUMMARY:小寒"
            + text[second + len("SUMMARY:大寒"):]
        )
        _, errors = validate_ics.validate_lines(text.splitlines())
        self.assertTrue(
            any("solar-term order/set mismatch" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
