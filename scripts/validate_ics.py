#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

from update_calendar import (
    END_YEAR,
    EXPECTED_TERMS,
    START_YEAR,
    parse_dtstamp,
    parse_sequence,
    validate_calendar_envelope,
    validate_term_series,
    validate_uid,
    validate_uid_contract,
)

ROOT = Path(__file__).resolve().parents[1]
ICS = ROOT / "24_solar_terms_2015-01-01_2050-12-31.ics"

EXPECTED_YEARS = range(START_YEAR, END_YEAR + 1)
EXPECTED_EVENT_COUNT = len(EXPECTED_TERMS) * len(EXPECTED_YEARS)


def parse_events(lines: list[str]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    seen_properties: set[str] = set()

    for line_no, line in enumerate(lines, 1):
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError(f"line {line_no}: nested VEVENT")
            current = {}
            seen_properties.clear()
            continue

        if line == "END:VEVENT":
            if current is None:
                raise ValueError(f"line {line_no}: END:VEVENT without BEGIN:VEVENT")
            events.append(current)
            current = None
            continue

        if current is None:
            continue

        # The published calendar intentionally uses a small VEVENT surface.
        # Reject semantic extensions that would change client behavior.
        property_name = line.partition(":")[0].partition(";")[0].upper()
        allowed = {"DTSTAMP", "UID", "SEQUENCE", "DTSTART", "DTEND", "STATUS", "SUMMARY"}
        if property_name not in allowed:
            raise ValueError(f"line {line_no}: unsupported VEVENT property {property_name}")
        if property_name in seen_properties:
            raise ValueError(f"line {line_no}: duplicate {property_name} in VEVENT")
        seen_properties.add(property_name)

        if line.startswith("DTSTAMP:"):
            current["DTSTAMP"] = line[8:]
        elif line.startswith("UID:"):
            current["UID"] = line[4:]
        elif line.startswith("SEQUENCE:"):
            current["SEQUENCE"] = line[9:]
        elif line.startswith("DTSTART;VALUE=DATE:"):
            current["DTSTART"] = line.removeprefix("DTSTART;VALUE=DATE:")
        elif line.startswith("DTEND;VALUE=DATE:"):
            current["DTEND"] = line.removeprefix("DTEND;VALUE=DATE:")
        elif line.startswith("STATUS:"):
            current["STATUS"] = line[7:]
        elif line.startswith("SUMMARY:"):
            current["SUMMARY"] = line[8:]
        else:
            raise ValueError(f"line {line_no}: unsupported VEVENT property form: {line}")

    if current is not None:
        raise ValueError("unterminated VEVENT")

    return events


def validate_lines(lines: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []

    try:
        validate_calendar_envelope(lines)
    except RuntimeError as exc:
        errors.append(str(exc))

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
    by_year: dict[int, list[tuple[date, str]]] = {}
    uid_events: dict[tuple[int, str], dict[str, str]] = {}

    for index, event in enumerate(events, 1):
        missing = [key for key in ("DTSTAMP", "UID", "DTSTART", "DTEND", "STATUS", "SUMMARY") if key not in event]
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
        if event["STATUS"] != "CONFIRMED":
            errors.append(f"event {index}: STATUS must be CONFIRMED")

        try:
            parse_dtstamp(event["DTSTAMP"])
        except ValueError as exc:
            errors.append(f"event {index}: {exc}")
        if "SEQUENCE" in event:
            try:
                parse_sequence(event["SEQUENCE"])
            except ValueError as exc:
                errors.append(f"event {index}: {exc}")

        try:
            if any(len(event[key]) != 8 or not event[key].isascii() or not event[key].isdigit()
                   for key in ("DTSTART", "DTEND")):
                raise ValueError("DATE must contain exactly eight ASCII digits")
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

        try:
            validate_uid(uid, start.year, summary, start)
        except ValueError as exc:
            errors.append(f"event {index}: {exc}")

        if start.year not in EXPECTED_YEARS:
            errors.append(
                f"event {index}: year {start.year} is outside {START_YEAR}-{END_YEAR}"
            )
        else:
            by_year.setdefault(start.year, []).append((start, summary))
            if summary in EXPECTED_TERMS:
                uid_events[(start.year, summary)] = {"UID": uid}

    duplicate_uids = [uid for uid, count in Counter(uids).items() if count > 1]
    if duplicate_uids:
        errors.append(f"duplicate UID(s): {', '.join(sorted(duplicate_uids))}")

    expected_counter = Counter(EXPECTED_TERMS)
    for year in EXPECTED_YEARS:
        series = by_year.get(year, [])
        actual = Counter(term for _, term in series)
        if actual != expected_counter:
            missing = list((expected_counter - actual).elements())
            extra = list((actual - expected_counter).elements())
            errors.append(
                f"{year}: term set mismatch; missing={missing or '-'}, extra={extra or '-'}"
            )
            continue
        try:
            validate_term_series(year, series)
        except RuntimeError as exc:
            errors.append(str(exc))

    expected_uid_keys = {
        (year, term)
        for year in EXPECTED_YEARS
        for term in EXPECTED_TERMS
    }
    if set(uid_events) == expected_uid_keys and not duplicate_uids:
        try:
            validate_uid_contract(uid_events)
        except RuntimeError as exc:
            errors.append(str(exc))

    return events, errors


def main() -> int:
    lines = ICS.read_text(encoding="utf-8").splitlines()
    events, errors = validate_lines(lines)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(events)} events, {len(EXPECTED_TERMS)} terms/year, "
        f"{min(EXPECTED_YEARS)}-{max(EXPECTED_YEARS)}, unique UIDs, semantic dates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
