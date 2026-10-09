"""Offline transport failures must use the existing retries and mirror quorum."""
from datetime import datetime
import http.client
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_calendar as updater


class Response:
    status = 200

    def __init__(self, url, payload, content_type='text/plain', error=None):
        self.url, self.payload, self.error = url, payload, error
        self.headers = {'Content-Type': content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        if self.error is not None:
            raise self.error
        return self.payload[:size]


class TransportRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = updater.ICS.read_bytes()
        events = updater.parse_existing_events(cls.original.decode('utf-8').splitlines())
        cls.series = tuple((datetime.strptime(events[(2026, term)]['DTSTART'], '%Y%m%d').date(), term)
                           for term in updater.EXPECTED_TERMS)
        cls.text = ('2026 公曆與農曆日期對照表 節氣\n' + '\n'.join(
            f'{day.year}年{day.month}月{day.day}日 {term}' for day, term in cls.series)).encode('utf-8')
        cls.xml = ('<Root>' + ''.join(
            f'<Data><M>{day.month}</M><D>{day.day}</D><hm>12:00</hm></Data>'
            for day, _ in cls.series) + '</Root>').encode('utf-8')

    def test_text_retries_body_disconnect_then_accepts_valid_response(self):
        url = updater.HKO_URLS[0].format(year=2026)
        for error in (http.client.IncompleteRead(b'partial', 30), ConnectionResetError('reset')):
            with self.subTest(error=type(error).__name__), mock.patch.object(updater.time, 'sleep'), \
                    mock.patch.object(updater, 'urlopen', side_effect=[
                        Response(url, b'', error=error), Response(url, self.text)]) as opened:
                found, host = updater.fetch_source(2026, updater.HKO_URLS[0])
                self.assertEqual(found, self.series)
                self.assertEqual(host, 'www.hko.gov.hk')
                self.assertEqual(opened.call_count, 2)

    def test_xml_retries_transport_disconnect(self):
        url = updater.HKO_ASTRONOMY_XML.format(year=2026)
        for error in (http.client.IncompleteRead(b'partial', 30), ConnectionResetError('reset')):
            with self.subTest(error=type(error).__name__), mock.patch.object(updater.time, 'sleep'), \
                    mock.patch.object(updater, 'urlopen', side_effect=[
                        Response(url, b'', 'application/xml', error), Response(url, self.xml, 'application/xml')]) as opened:
                self.assertEqual(updater.fetch_astronomy_xml(2026), self.series)
                self.assertEqual(opened.call_count, 2)

    def test_failed_first_mirror_does_not_bypass_two_other_hosts(self):
        seen = []
        def open_response(request, **kwargs):
            url = request.full_url
            seen.append(url)
            error = ConnectionResetError('reset') if url == updater.HKO_URLS[0].format(year=2026) else None
            return Response(url, self.text, error=error)
        with mock.patch.object(updater.time, 'sleep'), mock.patch.object(updater, 'urlopen', side_effect=open_response):
            self.assertEqual(updater.fetch_year(2026), list(self.series))
        self.assertEqual(len(seen), 4)
        self.assertEqual(len(set(seen)), 3)
        self.assertEqual(updater.ICS.read_bytes(), self.original)

    def test_all_transport_failures_remain_failure_and_do_not_publish(self):
        with mock.patch.object(updater.time, 'sleep'), \
                mock.patch.object(updater, 'urlopen', side_effect=http.client.RemoteDisconnected('closed')) as opened:
            with self.assertRaisesRegex(RuntimeError, 'no 2-of-3 HKO consensus'):
                updater.fetch_year(2026)
        self.assertEqual(opened.call_count, 6)
        self.assertEqual(updater.ICS.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
