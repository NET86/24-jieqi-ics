#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ICS = ROOT / "24_solar_terms_2015-01-01_2050-12-31.ics"
README = ROOT / "README.md"

START_YEAR = 2015
END_YEAR = 2050
BULK_CHANGE_STOP_THRESHOLD = 24
HKO_URLS = (
    "https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt",
    "https://www.weather.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt",
    "https://my.weather.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt",
)
HKO_ASTRONOMY_XML = (
    "https://www.hko.gov.hk/en/gts/astronomy/data/files/24SolarTerms_{year}.xml"
)

TERM_MAP = {
    "小寒": "小寒",
    "大寒": "大寒",
    "立春": "立春",
    "雨水": "雨水",
    "驚蟄": "惊蛰",
    "惊蛰": "惊蛰",
    "春分": "春分",
    "清明": "清明",
    "穀雨": "谷雨",
    "谷雨": "谷雨",
    "立夏": "立夏",
    "小滿": "小满",
    "小满": "小满",
    "芒種": "芒种",
    "芒种": "芒种",
    "夏至": "夏至",
    "小暑": "小暑",
    "大暑": "大暑",
    "立秋": "立秋",
    "處暑": "处暑",
    "处暑": "处暑",
    "白露": "白露",
    "秋分": "秋分",
    "寒露": "寒露",
    "霜降": "霜降",
    "立冬": "立冬",
    "小雪": "小雪",
    "大雪": "大雪",
    "冬至": "冬至",
}
EXPECTED_TERMS = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
    "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)
TERM_WINDOWS = {
    "小寒": (1, 4, 7),
    "大寒": (1, 19, 22),
    "立春": (2, 3, 5),
    "雨水": (2, 17, 20),
    "惊蛰": (3, 4, 7),
    "春分": (3, 19, 22),
    "清明": (4, 3, 6),
    "谷雨": (4, 19, 22),
    "立夏": (5, 4, 7),
    "小满": (5, 20, 23),
    "芒种": (6, 4, 7),
    "夏至": (6, 20, 23),
    "小暑": (7, 6, 8),
    "大暑": (7, 21, 24),
    "立秋": (8, 6, 9),
    "处暑": (8, 22, 24),
    "白露": (9, 6, 9),
    "秋分": (9, 21, 24),
    "寒露": (10, 7, 9),
    "霜降": (10, 22, 24),
    "立冬": (11, 6, 9),
    "小雪": (11, 21, 24),
    "大雪": (12, 6, 9),
    "冬至": (12, 20, 23),
}

DATE_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日")
VERIFIED_RE = re.compile(r"最近核验：\d{4}-\d{2}-\d{2}")


def decode_hko(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "big5"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("unable to decode HKO response as UTF-8 or Big5")


def validate_term_series(year: int, found: list[tuple[date, str]]) -> None:
    terms = tuple(term for _, term in found)
    if len(found) != 24:
        raise RuntimeError(f"{year}: expected 24 solar terms, found {len(found)}")
    if terms != EXPECTED_TERMS:
        raise RuntimeError(f"{year}: solar-term order/set mismatch: {terms}")

    for event_date, term in found:
        month, min_day, max_day = TERM_WINDOWS[term]
        if event_date.year != year:
            raise RuntimeError(f"{year}: parsed out-of-year date {event_date}")
        if event_date.month != month or not min_day <= event_date.day <= max_day:
            raise RuntimeError(
                f"{year} {term}: implausible date {event_date}; "
                f"expected month {month}, day {min_day}-{max_day}"
            )

    for (left_date, left_term), (right_date, right_term) in zip(found, found[1:]):
        interval = (right_date - left_date).days
        if not 14 <= interval <= 17:
            raise RuntimeError(
                f"{year}: implausible interval {interval} days "
                f"between {left_term} and {right_term}"
            )


def parse_hko_text(year: int, text: str) -> tuple[tuple[date, str], ...]:
    header = text[:500]
    if str(year) not in header or "公曆與農曆日期對照表" not in header or "節氣" not in header:
        raise RuntimeError(f"{year}: response does not look like an HKO calendar table")

    found: list[tuple[date, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = DATE_RE.match(line)
        if not match:
            continue

        source_term = next((term for term in TERM_MAP if line.endswith(term)), None)
        if source_term is None:
            continue

        event_date = date(*(int(part) for part in match.groups()))
        found.append((event_date, TERM_MAP[source_term]))

    validate_term_series(year, found)
    return tuple(found)


def fetch_source(year: int, url_template: str, attempts: int = 2) -> tuple[tuple[date, str], ...]:
    url = url_template.format(year=year)
    request = Request(
        url,
        headers={"User-Agent": "NET86-24-jieqi-ics/1.0 (+https://github.com/NET86/24-jieqi-ics)"},
    )
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                content_type = response.headers.get("Content-Type", "")
                if "text" not in content_type.lower():
                    raise RuntimeError(f"unexpected Content-Type {content_type!r} for {url}")
                text = decode_hko(response.read())
            return parse_hko_text(year, text)
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)

    raise RuntimeError(f"{url}: {last_error}")


def fetch_year(year: int) -> list[tuple[date, str]]:
    results: list[tuple[str, tuple[tuple[date, str], ...]]] = []
    failures: list[str] = []

    # Normal path: two official HKO hostnames agree, so the third is not needed.
    for url_template in HKO_URLS[:2]:
        host = urlparse(url_template).netloc
        try:
            results.append((host, fetch_source(year, url_template)))
        except RuntimeError as exc:
            failures.append(str(exc))

    if len(results) == 2 and results[0][1] == results[1][1]:
        return list(results[0][1])

    # A failure or disagreement invokes the third official hostname as tiebreaker.
    third_template = HKO_URLS[2]
    third_host = urlparse(third_template).netloc
    try:
        results.append((third_host, fetch_source(year, third_template)))
    except RuntimeError as exc:
        failures.append(str(exc))

    groups: dict[tuple[tuple[date, str], ...], list[str]] = {}
    for host, data in results:
        groups.setdefault(data, []).append(host)

    if groups:
        winner, hosts = max(groups.items(), key=lambda item: len(item[1]))
        if len(hosts) >= 2:
            if failures:
                print(f"WARN: {year}: official mirror failure(s): {'; '.join(failures)}", file=sys.stderr)
            if len(groups) > 1:
                disagreeing = [host for data, group_hosts in groups.items() if data != winner for host in group_hosts]
                print(
                    f"WARN: {year}: official mirrors disagreed; "
                    f"accepted consensus from {', '.join(hosts)}; "
                    f"disagreed: {', '.join(disagreeing)}",
                    file=sys.stderr,
                )
            return list(winner)

    detail = "; ".join(failures) if failures else "official mirrors returned different data"
    raise RuntimeError(f"{year}: no 2-of-3 HKO consensus: {detail}")


def fetch_astronomy_xml(year: int, attempts: int = 2) -> tuple[tuple[date, str], ...]:
    url = HKO_ASTRONOMY_XML.format(year=year)
    request = Request(
        url,
        headers={"User-Agent": "NET86-24-jieqi-ics/1.0 (+https://github.com/NET86/24-jieqi-ics)"},
    )
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                content_type = response.headers.get("Content-Type", "")
                if "xml" not in content_type.lower():
                    raise RuntimeError(f"unexpected Content-Type {content_type!r} for {url}")
                payload = response.read()

            root = ET.fromstring(payload)
            rows = root.findall(".//Data")
            if len(rows) != 24:
                raise RuntimeError(f"{year}: astronomy XML expected 24 records, found {len(rows)}")

            found: list[tuple[date, str]] = []
            for term, row in zip(EXPECTED_TERMS, rows):
                month_text = row.findtext("M")
                day_text = row.findtext("D")
                time_text = row.findtext("hm")
                if not month_text or not day_text or not time_text:
                    raise RuntimeError(f"{year}: incomplete astronomy XML row for {term}")
                if not re.fullmatch(r"\d{1,2}:\d{2}", time_text.strip()):
                    raise RuntimeError(f"{year}: invalid astronomy time {time_text!r} for {term}")
                event_date = date(year, int(month_text), int(day_text))
                found.append((event_date, term))

            validate_term_series(year, found)
            return tuple(found)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            RuntimeError,
            ET.ParseError,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)

    raise RuntimeError(f"{url}: {last_error}")


def crosscheck_near_term_astronomy(
    official: dict[int, list[tuple[date, str]]],
) -> list[int]:
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    start = max(START_YEAR, current_year)
    end = min(END_YEAR, current_year + 2)
    checked: list[int] = []

    for year in range(start, end + 1):
        astronomy = fetch_astronomy_xml(year)
        calendar = tuple(official[year])
        if astronomy != calendar:
            mismatches = [
                f"{term}: calendar={calendar_date}, astronomy={astronomy_date}"
                for (calendar_date, term), (astronomy_date, astronomy_term)
                in zip(calendar, astronomy)
                if term != astronomy_term or calendar_date != astronomy_date
            ]
            raise RuntimeError(
                f"{year}: HKO calendar vs astronomy XML mismatch: "
                + "; ".join(mismatches)
            )
        checked.append(year)

    return checked


def parse_existing_events(lines: list[str]) -> dict[tuple[int, str], dict[str, str]]:
    events: dict[tuple[int, str], dict[str, str]] = {}
    current: dict[str, str] | None = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise RuntimeError("nested VEVENT in existing calendar")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise RuntimeError("END:VEVENT without BEGIN:VEVENT in existing calendar")
            required = {"DTSTART", "SUMMARY", "UID"}
            if not required <= current.keys():
                missing = sorted(required.difference(current))
                raise RuntimeError(f"existing VEVENT missing fields: {missing}")
            year = int(current["DTSTART"][:4])
            key = (year, current["SUMMARY"])
            if key in events:
                raise RuntimeError(f"duplicate existing year/term event: {key}")
            events[key] = current
            current = None
            continue
        if current is None:
            continue

        if line.startswith("DTSTAMP:"):
            current["DTSTAMP"] = line[8:]
        elif line.startswith("UID:"):
            current["UID"] = line[4:]
        elif line.startswith("DTSTART;VALUE=DATE:"):
            current["DTSTART"] = line.removeprefix("DTSTART;VALUE=DATE:")
        elif line.startswith("SUMMARY:"):
            current["SUMMARY"] = line[8:]

    if current is not None:
        raise RuntimeError("unterminated VEVENT in existing calendar")
    return events


def validate_existing_events(
    existing: dict[tuple[int, str], dict[str, str]],
) -> None:
    all_keys = {
        (year, term)
        for year in range(START_YEAR, END_YEAR + 1)
        for term in EXPECTED_TERMS
    }
    actual_keys = set(existing)

    if actual_keys != all_keys:
        missing = sorted(all_keys - actual_keys)
        extra = sorted(actual_keys - all_keys)
        raise RuntimeError(
            f"existing calendar term set mismatch; missing={missing}, extra={extra}"
        )

    uids = [event["UID"] for event in existing.values()]
    if len(uids) != len(set(uids)):
        raise RuntimeError("duplicate UID in existing calendar")


def build_calendar(
    official: dict[int, list[tuple[date, str]]],
    existing: dict[tuple[int, str], dict[str, str]],
) -> str:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//NET86//Chinese Solar Terms Calendar//ZH-CN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:中国二十四节气",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "X-WR-CALDESC:2015-2050中国二十四节气",
    ]

    for year in range(START_YEAR, END_YEAR + 1):
        for event_date, term in official[year]:
            old = existing.get((year, term))
            if old is None:
                raise RuntimeError(f"missing existing UID for {year} {term}")
            uid = old["UID"]
            dtstamp = old.get("DTSTAMP") or now_stamp

            end_date = event_date + timedelta(days=1)
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"DTSTAMP:{dtstamp}",
                    f"UID:{uid}",
                    f"DTSTART;VALUE=DATE:{event_date:%Y%m%d}",
                    f"DTEND;VALUE=DATE:{end_date:%Y%m%d}",
                    "STATUS:CONFIRMED",
                    f"SUMMARY:{term}",
                    "END:VEVENT",
                ]
            )

    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"


def update_verified_date(readme: str) -> str:
    verified = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    replacement = f"最近核验：{verified}"
    updated, count = VERIFIED_RE.subn(replacement, readme, count=1)
    if count != 1:
        raise RuntimeError("README must contain exactly one '最近核验：YYYY-MM-DD' marker")
    return updated


def describe_date_changes(
    official: dict[int, list[tuple[date, str]]],
    existing: dict[tuple[int, str], dict[str, str]],
) -> list[str]:
    changes: list[str] = []
    for year in range(START_YEAR, END_YEAR + 1):
        for event_date, term in official[year]:
            old = existing.get((year, term))
            if old is None:
                changes.append(f"{year} {term}: added {event_date}")
                continue
            old_date = datetime.strptime(old["DTSTART"], "%Y%m%d").date()
            if old_date != event_date:
                delta_days = abs((event_date - old_date).days)
                if delta_days > 1:
                    raise RuntimeError(
                        f"{year} {term}: refusing {delta_days}-day date jump "
                        f"{old_date} -> {event_date}"
                    )
                changes.append(f"{year} {term}: {old_date} -> {event_date}")
    return changes


def main() -> int:
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    if current_year > END_YEAR:
        raise RuntimeError(
            f"current year {current_year} exceeds maintained range ending {END_YEAR}; "
            "manual range review required"
        )

    existing_lines = ICS.read_text(encoding="utf-8").splitlines()
    existing = parse_existing_events(existing_lines)
    validate_existing_events(existing)
    readme_text = README.read_text(encoding="utf-8")
    readme = update_verified_date(readme_text)

    official: dict[int, list[tuple[date, str]]] = {}
    for year in range(START_YEAR, END_YEAR + 1):
        official[year] = fetch_year(year)

    astronomy_years = crosscheck_near_term_astronomy(official)

    changes = describe_date_changes(official, existing)
    if len(changes) >= BULK_CHANGE_STOP_THRESHOLD:
        raise RuntimeError(
            f"refusing {len(changes)} automatic date changes; "
            f"bulk-change threshold is {BULK_CHANGE_STOP_THRESHOLD}"
        )
    for change in changes:
        print(f"CHANGE: {change}")

    calendar = build_calendar(official, existing)

    # Remote publication is additionally gated by validate_ics.py in Actions.
    # Nothing is committed if source consensus, parsing, generation, or validation fails.
    ICS.write_text(calendar, encoding="utf-8", newline="\n")
    README.write_text(readme, encoding="utf-8", newline="\n")

    astronomy_range = (
        f"{astronomy_years[0]}-{astronomy_years[-1]}"
        if astronomy_years
        else "none"
    )
    print(
        f"OK: HKO 2-of-3 calendar-source consensus for {START_YEAR}-{END_YEAR}; "
        f"astronomy XML cross-check={astronomy_range}; "
        f"wrote {(END_YEAR - START_YEAR + 1) * 24} solar-term events; "
        f"date changes={len(changes)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
