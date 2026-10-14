"""Offline voting and scheduling regressions; no official network or publishing."""
import contextlib
from collections import Counter
from email.message import Message
from datetime import datetime, timedelta
import io
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_calendar as u
import validate_ics as v


class SourceConsensusBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = u.ICS.read_bytes()
        events = u.parse_existing_events(cls.raw.decode().splitlines())
        cls.official = {
            year: [(datetime.strptime(events[(year, term)]['DTSTART'], '%Y%m%d').date(), term)
                   for term in u.EXPECTED_TERMS]
            for year in range(u.START_YEAR, u.END_YEAR + 1)}

    def test_inconsistent_final_host_cannot_keep_its_first_vote(self):
        first = tuple(self.official[2015])
        changed = None
        for index, (day, term) in enumerate(first):
            for delta in (-1, 1):
                candidate = list(first)
                candidate[index] = (day + timedelta(days=delta), term)
                try:
                    u.validate_term_series(2015, candidate)
                except RuntimeError:
                    continue
                changed = tuple(candidate)
                break
            if changed is not None:
                break
        self.assertIsNotNone(changed)
        votes = [(first, 'www.hko.gov.hk'), (changed, 'www.hko.gov.hk'),
                 (first, 'my.weather.gov.hk')]
        with patch.object(u, 'fetch_source', side_effect=votes), self.assertRaisesRegex(RuntimeError, 'inconsistent'):
            u.fetch_year(2015)
        self.assertEqual(u.ICS.read_bytes(), self.raw)

    def test_full_range_uses_bounded_concurrent_years(self):
        lock = threading.Lock()
        active = peak = 0
        calls = []
        def fetch(year, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(year)
            try:
                # Models a blocked mirror without waiting through real 20s timeouts.
                time.sleep(0.02)
                return self.official[year]
            finally:
                with lock:
                    active -= 1
        with patch.object(u, 'fetch_year', side_effect=fetch), \
                patch.object(u, 'crosscheck_near_term_astronomy', return_value=[]), \
                patch.object(u, 'publish_outputs') as publish, contextlib.redirect_stdout(io.StringIO()):
            u.main()
        self.assertEqual(sorted(calls), list(range(2015, 2051)))
        self.assertGreater(peak, 1, 'serial 36-year requests repeat a failed mirror timeout budget')
        self.assertLessEqual(peak, 6)
        publish.assert_called_once()
        self.assertEqual(u.ICS.read_bytes(), self.raw)

    def test_unicode_separator_is_not_a_calendar_content_line(self):
        text = self.raw.decode().replace('BEGIN:VEVENT\r\n', 'BEGIN:VEVENT\u2028', 1)
        with patch.object(v, 'ICS') as file, contextlib.redirect_stderr(io.StringIO()):
            file.read_text.return_value = text
            file.read_bytes.return_value = text.encode()
            self.assertEqual(v.main(), 1)
        with self.assertRaises(RuntimeError):
            u.validate_staged_outputs(text, '最近核验：2026-10-14')

    def test_request_and_backoff_share_remaining_budget(self):
        now = [100.0]
        calls = []
        def opened(request, timeout):
            calls.append(timeout)
            now[0] += timeout
            raise TimeoutError('mirror timeout')
        def sleep(seconds):
            now[0] += seconds
        with patch.object(u.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(u.time, 'sleep', side_effect=sleep), patch.object(u, 'urlopen', side_effect=opened):
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                u.fetch_source(2015, u.HKO_URLS[0], deadline=105.0)
        self.assertEqual(calls, [5.0])
        self.assertEqual(now[0], 105.0)

    def test_redirect_body_is_closed_and_next_hop_uses_remaining_time(self):
        class Body:
            closed = False
            def read(self, *args):
                raise AssertionError('deadline bypass through redirect body read')
            def close(self): self.closed = True
        body = Body()
        request = Request(u.HKO_URLS[0].format(year=2015))
        request.timeout = 20
        opened = []
        handler = u.HkoDeadlineRedirects(105)
        handler.parent = types.SimpleNamespace(open=lambda req, timeout: opened.append((req, timeout)) or 'response')
        headers = Message()
        headers['Location'] = u.HKO_URLS[1].format(year=2015)
        with patch.object(u.time, 'monotonic', return_value=103):
            self.assertEqual(handler.http_error_302(request, body, 302, 'Found', headers), 'response')
        self.assertTrue(body.closed)
        self.assertEqual(opened[0][1], 2)

    def test_body_that_arrives_after_deadline_is_closed_and_not_accepted(self):
        now = [100.0]
        class Response(io.BytesIO):
            status = 200
            length = None
            def geturl(self): return u.HKO_URLS[0].format(year=2015)
            def read1(self, size):
                now[0] += 6
                return super().read1(size)
        response = Response(b'late source')
        with patch.object(u.time,'monotonic',side_effect=lambda:now[0]),patch.object(u,'urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'budget'):
                u.fetch_source(2015,u.HKO_URLS[0],attempts=1,deadline=105)
        self.assertTrue(response.closed)

    def test_one_failed_mirror_across_all_36_years_keeps_two_healthy_votes(self):
        calls = []
        lock = threading.Lock()
        class Response(io.BytesIO):
            status = 200
            def __init__(self, url, payload):
                super().__init__(payload)
                self.url, self.length = url, len(payload)
            def geturl(self): return self.url
        def opened(request, timeout):
            url = request.full_url
            with lock:
                calls.append(url)
            year = int(url.rsplit('/T', 1)[1][:4])
            if url == u.HKO_URLS[0].format(year=year):
                raise TimeoutError('unhealthy first mirror')
            text = f'{year} 公曆與農曆日期對照表 節氣\n' + '\n'.join(
                f'{day.year}年{day.month}月{day.day}日 {term}' for day, term in self.official[year])
            return Response(url, text.encode())
        with patch.object(u, 'urlopen', side_effect=opened), patch.object(u.time, 'sleep'), \
                patch.object(u, 'crosscheck_near_term_astronomy', return_value=[]), \
                patch.object(u, 'publish_outputs') as published, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            u.main()
        counts = Counter(calls)
        for year in range(2015, 2051):
            self.assertEqual([counts[template.format(year=year)] for template in u.HKO_URLS], [2, 1, 1])
        self.assertEqual(len(calls), 144)
        self.assertEqual(published.call_args.args[0].encode(), self.raw)


if __name__ == '__main__':
    unittest.main()
