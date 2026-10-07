#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import os
import re
import shutil
import sys
import tempfile
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
HKO_ALLOWED_HOSTS = frozenset(
    urlparse(url).hostname
    for url in (*HKO_URLS, HKO_ASTRONOMY_XML)
    if urlparse(url).hostname
)
TRANSACTION_DIR = ROOT / ".calendar-update-transaction"

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
DTSTAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SEQUENCE_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
DATE_UID_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})-lc@infinet\.github\.io$"
)
QINGMING_UID_RE = re.compile(r"^(?P<year>[0-9]{4})-qingming@net86\.github\.io$")
MAX_SEQUENCE = 2_147_483_647
UID_CONTRACT_SHA256 = "5d92bfa1343431b2cecbac39b3c79a19ae35f462f226580c4b2e789702c6fe4c"
VCALENDAR_REQUIRED_LINES = (
    "PRODID:-//NET86//Chinese Solar Terms Calendar//ZH-CN",
    "VERSION:2.0",
    "X-WR-CALNAME:中国二十四节气",
    "X-WR-TIMEZONE:Asia/Shanghai",
    "X-WR-CALDESC:2015-2050中国二十四节气",
)
VCALENDAR_OPTIONAL_LINES = (
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
)


def calendar_envelope_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        errors.append("calendar must start with BEGIN:VCALENDAR and end with END:VCALENDAR")
    if lines.count("BEGIN:VCALENDAR") != 1 or lines.count("END:VCALENDAR") != 1:
        errors.append("calendar must contain exactly one VCALENDAR")

    top_level: list[tuple[int, str]] = []
    in_event = False
    for line_no, line in enumerate(lines, 1):
        if line == "BEGIN:VEVENT":
            in_event = True
            continue
        if line == "END:VEVENT":
            in_event = False
            continue
        if not in_event:
            top_level.append((line_no, line))

    top_values = [line for _, line in top_level]
    for item in VCALENDAR_REQUIRED_LINES:
        count = top_values.count(item)
        if count != 1:
            errors.append(f"calendar metadata must appear exactly once: {item} (found {count})")
    for item in VCALENDAR_OPTIONAL_LINES:
        count = top_values.count(item)
        if count > 1:
            errors.append(f"calendar metadata must not repeat: {item.partition(':')[0]}")

    allowed = {
        "BEGIN:VCALENDAR",
        "END:VCALENDAR",
        *VCALENDAR_REQUIRED_LINES,
        *VCALENDAR_OPTIONAL_LINES,
    }
    for line_no, line in top_level:
        if line not in allowed:
            errors.append(f"line {line_no}: unsupported VCALENDAR property/form: {line}")
    return errors


def parse_dtstamp(value: str) -> datetime:
    if not DTSTAMP_RE.fullmatch(value):
        raise ValueError(f"invalid UTC DTSTAMP: {value!r}")
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"invalid UTC DTSTAMP: {value!r}") from exc


def parse_sequence(value: str) -> int:
    if not SEQUENCE_RE.fullmatch(value):
        raise ValueError(f"invalid SEQUENCE: {value!r}")
    sequence = int(value)
    if sequence > MAX_SEQUENCE:
        raise ValueError(f"SEQUENCE exceeds {MAX_SEQUENCE}")
    return sequence


def validate_uid(uid: str, year: int, term: str, event_date: date) -> None:
    if term == "清明":
        match = QINGMING_UID_RE.fullmatch(uid)
        if not match or int(match.group("year")) != year:
            raise ValueError(f"{year} {term}: invalid stable UID {uid!r}")
        return

    match = DATE_UID_RE.fullmatch(uid)
    if not match:
        raise ValueError(f"{year} {term}: invalid stable UID {uid!r}")
    try:
        uid_date = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise ValueError(f"{year} {term}: invalid stable UID {uid!r}") from exc
    if uid_date.year != year:
        raise ValueError(
            f"{year} {term}: stable UID year {uid_date.year} does not match event year"
        )
    window = TERM_WINDOWS.get(term)
    if window is None:
        raise ValueError(f"{year} {term}: invalid stable UID {uid!r}")
    month, min_day, max_day = window
    if uid_date.month != month or not min_day <= uid_date.day <= max_day:
        raise ValueError(
            f"{year} {term}: stable UID date {uid_date} is outside the term window"
        )


def uid_contract_digest(events: dict[tuple[int, str], dict[str, str]]) -> str:
    rows = [
        f"{year}\t{term}\t{events[(year, term)]['UID']}\n"
        for year in range(START_YEAR, END_YEAR + 1)
        for term in EXPECTED_TERMS
    ]
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def validate_uid_contract(events: dict[tuple[int, str], dict[str, str]]) -> None:
    digest = uid_contract_digest(events)
    if digest != UID_CONTRACT_SHA256:
        raise RuntimeError(
            "stable UID contract changed; preserve existing event identities "
            "or explicitly review and update UID_CONTRACT_SHA256"
        )


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


def validate_hko_final_url(requested_url: str, final_url: str) -> str:
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if requested.scheme != "https" or requested.hostname not in HKO_ALLOWED_HOSTS:
        raise RuntimeError(f"untrusted HKO request source: {requested_url}")
    if final.scheme != "https" or final.hostname not in HKO_ALLOWED_HOSTS:
        raise RuntimeError(
            f"HKO request redirected outside approved HTTPS hosts: {requested_url} -> {final_url}"
        )
    return final.hostname


def fetch_source(
    year: int,
    url_template: str,
    attempts: int = 2,
) -> tuple[tuple[tuple[date, str], ...], str]:
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
                final_host = validate_hko_final_url(url, response.geturl())
                content_type = response.headers.get("Content-Type", "")
                if "text" not in content_type.lower():
                    raise RuntimeError(f"unexpected Content-Type {content_type!r} for {url}")
                text = decode_hko(response.read())
            return parse_hko_text(year, text), final_host
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)

    raise RuntimeError(f"{url}: {last_error}")


def fetch_year(year: int) -> list[tuple[date, str]]:
    results: dict[str, tuple[tuple[date, str], ...]] = {}
    failures: list[str] = []

    def collect(url_template: str) -> None:
        requested_host = urlparse(url_template).hostname or url_template
        try:
            data, final_host = fetch_source(year, url_template)
            previous = results.get(final_host)
            if previous is not None and previous != data:
                raise RuntimeError(
                    f"{year}: repeated final HKO source {final_host} returned inconsistent data"
                )
            results[final_host] = data
        except RuntimeError as exc:
            failures.append(f"{requested_host}: {exc}")

    # Normal path: two independently resolved official final hosts agree.
    for url_template in HKO_URLS[:2]:
        collect(url_template)

    if len(results) >= 2 and len(set(results.values())) == 1:
        return list(next(iter(results.values())))

    # A failure, duplicate final source, or disagreement invokes the third host.
    collect(HKO_URLS[2])

    groups: dict[tuple[tuple[date, str], ...], list[str]] = {}
    for host, data in results.items():
        groups.setdefault(data, []).append(host)

    if groups:
        winner, hosts = max(groups.items(), key=lambda item: len(item[1]))
        if len(hosts) >= 2:
            if failures:
                print(f"WARN: {year}: official mirror failure(s): {'; '.join(failures)}", file=sys.stderr)
            if len(groups) > 1:
                disagreeing = [
                    host
                    for data, group_hosts in groups.items()
                    if data != winner
                    for host in group_hosts
                ]
                print(
                    f"WARN: {year}: official mirrors disagreed; "
                    f"accepted consensus from {', '.join(hosts)}; "
                    f"disagreed: {', '.join(disagreeing)}",
                    file=sys.stderr,
                )
            return list(winner)

    detail = "; ".join(failures) if failures else (
        "official mirrors did not provide two distinct final-source votes"
    )
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
                validate_hko_final_url(url, response.geturl())
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
    envelope_errors = calendar_envelope_errors(lines)
    if envelope_errors:
        raise RuntimeError("; ".join(envelope_errors))
    events: dict[tuple[int, str], dict[str, str]] = {}
    current: dict[str, str] | None = None
    seen_properties: set[str] = set()

    for line_no, line in enumerate(lines, 1):
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise RuntimeError("nested VEVENT in existing calendar")
            current = {}
            seen_properties.clear()
            continue
        if line == "END:VEVENT":
            if current is None:
                raise RuntimeError("END:VEVENT without BEGIN:VEVENT in existing calendar")
            required = {"DTSTAMP", "DTSTART", "DTEND", "STATUS", "SUMMARY", "UID"}
            if not required <= current.keys():
                missing = sorted(required.difference(current))
                raise RuntimeError(f"existing VEVENT missing fields: {missing}")
            try:
                parse_dtstamp(current["DTSTAMP"])
                if "SEQUENCE" in current:
                    parse_sequence(current["SEQUENCE"])
                if any(
                    len(current[key]) != 8
                    or not current[key].isascii()
                    or not current[key].isdigit()
                    for key in ("DTSTART", "DTEND")
                ):
                    raise ValueError("DATE must contain exactly eight ASCII digits")
                start_date = datetime.strptime(current["DTSTART"], "%Y%m%d").date()
                end_date = datetime.strptime(current["DTEND"], "%Y%m%d").date()
                if end_date != start_date + timedelta(days=1):
                    raise ValueError("DTEND must be exactly one day after DTSTART")
            except ValueError as exc:
                raise RuntimeError(f"invalid existing VEVENT: {exc}") from exc

            year = start_date.year
            try:
                validate_uid(current["UID"], year, current["SUMMARY"], start_date)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            if current["STATUS"] != "CONFIRMED":
                raise RuntimeError(f"{year} {current['SUMMARY']}: STATUS must be CONFIRMED")
            key = (year, current["SUMMARY"])
            if key in events:
                raise RuntimeError(f"duplicate existing year/term event: {key}")
            events[key] = current
            current = None
            continue
        if current is None:
            continue

        property_name = line.partition(":")[0].partition(";")[0].upper()
        allowed = {"DTSTAMP", "UID", "SEQUENCE", "DTSTART", "DTEND", "STATUS", "SUMMARY"}
        if property_name not in allowed:
            raise RuntimeError(f"line {line_no}: unsupported VEVENT property {property_name}")
        if property_name in seen_properties:
            raise RuntimeError(f"line {line_no}: duplicate {property_name} in existing VEVENT")
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
            raise RuntimeError(f"line {line_no}: unsupported VEVENT property form: {line}")

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
    validate_uid_contract(existing)


def build_calendar(
    official: dict[int, list[tuple[date, str]]],
    existing: dict[tuple[int, str], dict[str, str]],
    now: datetime | None = None,
) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("build_calendar now must be timezone-aware")
    now_stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
            old_date = datetime.strptime(old["DTSTART"], "%Y%m%d").date()
            changed = old_date != event_date
            if changed:
                dtstamp = now_stamp
                sequence = parse_sequence(old.get("SEQUENCE", "0")) + 1
                if sequence > MAX_SEQUENCE:
                    raise RuntimeError(f"{year} {term}: SEQUENCE overflow")
                sequence_text: str | None = str(sequence)
            else:
                dtstamp = old["DTSTAMP"]
                sequence_text = old.get("SEQUENCE")

            end_date = event_date + timedelta(days=1)
            event_lines = [
                "BEGIN:VEVENT",
                f"DTSTAMP:{dtstamp}",
                f"UID:{uid}",
            ]
            if sequence_text is not None:
                event_lines.append(f"SEQUENCE:{sequence_text}")
            event_lines.extend(
                [
                    f"DTSTART;VALUE=DATE:{event_date:%Y%m%d}",
                    f"DTEND;VALUE=DATE:{end_date:%Y%m%d}",
                    "STATUS:CONFIRMED",
                    f"SUMMARY:{term}",
                    "END:VEVENT",
                ]
            )
            lines.extend(event_lines)

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


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def recover_publication_transaction() -> None:
    if not TRANSACTION_DIR.exists():
        return
    ready = TRANSACTION_DIR / "READY"
    if not ready.exists():
        shutil.rmtree(TRANSACTION_DIR)
        return

    backup_ics = TRANSACTION_DIR / "calendar.old"
    backup_readme = TRANSACTION_DIR / "readme.old"
    if not backup_ics.exists() or not backup_readme.exists():
        raise RuntimeError("incomplete calendar publication recovery data")

    atomic_write_bytes(ICS, backup_ics.read_bytes())
    atomic_write_bytes(README, backup_readme.read_bytes())
    shutil.rmtree(TRANSACTION_DIR)


def validate_staged_outputs(calendar: str, readme: str) -> None:
    events = parse_existing_events(calendar.splitlines())
    validate_existing_events(events)
    by_year: dict[int, list[tuple[date, str]]] = {}
    for (year, term), event in events.items():
        event_date = datetime.strptime(event["DTSTART"], "%Y%m%d").date()
        by_year.setdefault(year, []).append((event_date, term))
    for year in range(START_YEAR, END_YEAR + 1):
        ordered = sorted(by_year.get(year, []))
        validate_term_series(year, ordered)

    if len(VERIFIED_RE.findall(readme)) != 1:
        raise RuntimeError("staged README must contain exactly one verification date marker")


def publish_outputs(calendar: str, readme: str) -> None:
    recover_publication_transaction()
    validate_staged_outputs(calendar, readme)

    TRANSACTION_DIR.mkdir()
    atomic_write_bytes(TRANSACTION_DIR / "calendar.old", ICS.read_bytes())
    atomic_write_bytes(TRANSACTION_DIR / "readme.old", README.read_bytes())
    atomic_write_bytes(TRANSACTION_DIR / "calendar.new", calendar.encode("utf-8"))
    atomic_write_bytes(TRANSACTION_DIR / "readme.new", readme.encode("utf-8"))
    atomic_write_bytes(TRANSACTION_DIR / "READY", b"ready\n")

    try:
        os.replace(TRANSACTION_DIR / "calendar.new", ICS)
        os.replace(TRANSACTION_DIR / "readme.new", README)
    except BaseException:
        recover_publication_transaction()
        raise
    else:
        shutil.rmtree(TRANSACTION_DIR)



def main() -> int:
    recover_publication_transaction()
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

    # Validate both generated artifacts before either target is replaced.
    # A transaction backup restores both files after ordinary write failure and
    # on the next run after a crash between the two replacements.
    publish_outputs(calendar, readme)

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
