#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ICS = ROOT / "24_solar_terms_2015-01-01_2050-12-31.ics"

EXPECTED_TERMS = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
    "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)
EXPECTED_YEARS = range(2015, 2051)
EXPECTED_EVENT_COUNT = len(EXPECTED_TERMS) * len(EXPECTED_YEARS)


def parse_events(lines: list[str]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line_no, line in enumerate(lines, 1):
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError(f"line {line_no}: nested VEVENT")
            current = {}
            continue

        if line == "END:VEVENT":
            if current is None:
                raise ValueError(f"line {line_no}: END:VEVENT without BEGIN:VEVENT")
            events.append(current)
            current = None
            continue

        if current is None:
            continue

        if line.startswith("UID:"):
            current["UID"] = line[4:]
        elif line.startswith("DTSTART;VALUE=DATE:"):
            current["DTSTART"] = line.removeprefix("DTSTART;VALUE=DATE:")
        elif line.startswith("DTEND;VALUE=DATE:"):
            current["DTEND"] = line.removeprefix("DTEND;VALUE=DATE:")
        elif line.startswith("SUMMARY:"):
            current["SUMMARY"] = line[8:]

    if current is not None:
        raise ValueError("unterminated VEVENT")

    return events


def main() -> int:
    lines = ICS.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        errors.append("calendar must start with BEGIN:VCALENDAR and end with END:VCALENDAR")

    required_metadata = {
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:中国二十四节气",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "X-WR-CALDESC:2015-2050中国二十四节气",
    }
    missing_metadata = sorted(required_metadata.difference(lines))
    if missing_metadata:
        errors.append(f"missing calendar metadata: {', '.join(missing_metadata)}")

    try:
        events = parse_events(lines)
    except ValueError as exc:
        errors.append(str(exc))
        events = []

    if len(events) != EXPECTED_EVENT_COUNT:
        errors.append(
            f"expected {EXPECTED_EVENT_COUNT} VEVENTs, found {len(events)}"
        )

    uids: list[str] = []
    by_year: dict[int, list[str]] = {}

    for index, event in enumerate(events, 1):
        missing = [key for key in ("UID", "DTSTART", "DTEND", "SUMMARY") if key not in event]
        if missing:
            errors.append(f"event {index}: missing {', '.join(missing)}")
            continue

        uid = event["UID"].strip()
        summary = event["SUMMARY"]
        if not uid:
            errors.append(f"event {index}: empty UID")
        else:
            uids.append(uid)

        if summary not in EXPECTED_TERMS:
            errors.append(f"event {index}: unexpected SUMMARY {summary!r}")

        try:
            start = datetime.strptime(event["DTSTART"], "%Y%m%d").date()
            end = datetime.strptime(event["DTEND"], "%Y%m%d").date()
        except ValueError:
            errors.append(
                f"event {index}: invalid date {event['DTSTART']!r}/{event['DTEND']!r}"
            )
            continue

        if end != start + timedelta(days=1):
            errors.append(
                f"event {index}: DTEND must be exactly one day after DTSTART"
            )

        if start.year not in EXPECTED_YEARS:
            errors.append(f"event {index}: year {start.year} is outside 2015-2050")
        else:
            by_year.setdefault(start.year, []).append(summary)

    duplicate_uids = [uid for uid, count in Counter(uids).items() if count > 1]
    if duplicate_uids:
        errors.append(f"duplicate UID(s): {', '.join(sorted(duplicate_uids))}")

    expected_counter = Counter(EXPECTED_TERMS)
    for year in EXPECTED_YEARS:
        actual = Counter(by_year.get(year, []))
        if actual != expected_counter:
            missing = list((expected_counter - actual).elements())
            extra = list((actual - expected_counter).elements())
            errors.append(
                f"{year}: term set mismatch; missing={missing or '-'}, extra={extra or '-'}"
            )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(events)} events, {len(EXPECTED_TERMS)} terms/year, "
        f"{min(EXPECTED_YEARS)}-{max(EXPECTED_YEARS)}, unique UIDs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
