"""Transport-level adversarial regression: complete parsed data must not hide invalid HTTP provenance."""
from __future__ import annotations
from datetime import datetime
import http.client
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from urllib.request import HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_calendar as updater


class WireSocket:
    def __init__(self, wire): self.wire = wire
    def makefile(self, mode): return io.BytesIO(self.wire)


class WireResponse:
    """Exercise actual http.client.HTTPResponse fixed-length read semantics."""
    def __init__(self, url, payload, length_extra=0):
        data = (b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload) + length_extra).encode()
                + b"\r\nContent-Type: text/plain\r\n\r\n" + payload)
        self.response = http.client.HTTPResponse(WireSocket(data))
        self.response.begin()
        self.status = self.response.status
        self.url = url

    def __enter__(self): return self
    def __exit__(self, *args): self.response.close()
    def geturl(self): return self.url
    def read(self, n): return self.response.read(n)
    @property
    def length(self): return self.response.length


class HkoWireIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = updater.ICS.read_bytes()
        events = updater.parse_existing_events(cls.original.decode("utf-8").splitlines())
        cls.series = tuple(
            (datetime.strptime(events[(2026, name)]["DTSTART"], "%Y%m%d").date(), name)
            for name in updater.EXPECTED_TERMS)
        cls.text = ("2026 公曆與農曆日期對照表 節氣\n" + "\n".join(
            f"{day.year}年{day.month}月{day.day}日 {term}"
            for day, term in cls.series) + "\n").encode()
        cls.xml = ("<Root>" + "".join(
            f"<Data><M>{day.month}</M><D>{day.day}</D><hm>12:00</hm></Data>"
            for day, _ in cls.series) + "</Root>").encode()
        cls.text_url = updater.HKO_URLS[0].format(year=2026)
        cls.xml_url = updater.HKO_ASTRONOMY_XML.format(year=2026)

    def follow(self, request, timeout, *, target, payload):
        request.timeout = timeout
        response = WireResponse(self.text_url if payload == self.text else self.xml_url, payload)
        handler = HTTPRedirectHandler()
        handler.parent = types.SimpleNamespace(open=lambda *args, **kwargs: response)
        returned = handler.http_error_302(request, io.BytesIO(), 302, "Found", {"location": target})
        self.assertIn(target, request.redirect_dict)
        return returned

    def test_valid_fixed_length_text_still_passes(self):
        with patch.object(updater, "urlopen", side_effect=lambda *_args, **_kw: WireResponse(self.text_url, self.text)):
            series, host = updater.fetch_source(2026, updater.HKO_URLS[0], attempts=1)
        self.assertEqual(series, self.series)
        self.assertEqual(host, "www.hko.gov.hk")

    def test_valid_fixed_length_astronomy_still_passes(self):
        with patch.object(updater, "urlopen", side_effect=lambda *_args, **_kw: WireResponse(self.xml_url, self.xml)):
            self.assertEqual(updater.fetch_astronomy_xml(2026, attempts=1), self.series)

    def test_text_declared_length_truncation_rejected_even_with_24_good_terms(self):
        with patch.object(updater, "urlopen", side_effect=lambda *_args, **_kw: WireResponse(self.text_url, self.text, 81)):
            with self.assertRaisesRegex(RuntimeError, "incomplete HKO response"):
                updater.fetch_source(2026, updater.HKO_URLS[0], attempts=1)

    def test_xml_declared_length_truncation_rejected_even_with_24_good_terms(self):
        with patch.object(updater, "urlopen", side_effect=lambda *_args, **_kw: WireResponse(self.xml_url, self.xml, 81)):
            with self.assertRaisesRegex(RuntimeError, "incomplete HKO response"):
                updater.fetch_astronomy_xml(2026, attempts=1)

    def test_http_downgrade_then_approved_https_final_cannot_pass(self):
        opener = lambda request, timeout: self.follow(
            request, timeout, target="http://untrusted.invalid/step", payload=self.text)
        with patch.object(updater, "urlopen", side_effect=opener):
            with self.assertRaisesRegex(RuntimeError, "untrusted HKO|redirected outside"):
                updater.fetch_source(2026, updater.HKO_URLS[0], attempts=1)

    def test_unknown_https_intermediate_cannot_pass(self):
        opener = lambda request, timeout: self.follow(
            request, timeout, target="https://untrusted.invalid/step", payload=self.text)
        with patch.object(updater, "urlopen", side_effect=opener):
            with self.assertRaisesRegex(RuntimeError, "untrusted HKO|redirected outside"):
                updater.fetch_source(2026, updater.HKO_URLS[0], attempts=1)

    def test_approved_https_intermediate_still_passes(self):
        opener = lambda request, timeout: self.follow(
            request, timeout, target="https://www.weather.gov.hk/approved", payload=self.text)
        with patch.object(updater, "urlopen", side_effect=opener):
            found, _ = updater.fetch_source(2026, updater.HKO_URLS[0], attempts=1)
        self.assertEqual(found, self.series)

    def test_retry_after_unsafe_redirect_uses_fresh_request(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return self.follow(request, timeout, target="http://untrusted.invalid/first", payload=self.text)
            return WireResponse(self.text_url, self.text)
        with patch.object(updater, "urlopen", side_effect=opener), patch.object(updater.time, "sleep"):
            found, _ = updater.fetch_source(2026, updater.HKO_URLS[0], attempts=2)
        self.assertEqual(found, self.series)
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])

    def test_xml_unsafe_intermediate_rejected(self):
        opener = lambda request, timeout: self.follow(
            request, timeout, target="http://untrusted.invalid/xml", payload=self.xml)
        with patch.object(updater, "urlopen", side_effect=opener):
            with self.assertRaisesRegex(RuntimeError, "untrusted HKO|redirected outside"):
                updater.fetch_astronomy_xml(2026, attempts=1)

    @classmethod
    def tearDownClass(cls):
        assert updater.ICS.read_bytes() == cls.original


if __name__ == "__main__":
    unittest.main()
