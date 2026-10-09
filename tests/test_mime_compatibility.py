"""Official HKO content validation tolerates misleading HTTP MIME metadata."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_calendar as c


class MimeCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events = c.parse_existing_events(c.ICS.read_text(encoding="utf-8").splitlines())
        cls.official = [
            (
                datetime.strptime(events[(2015, term)]["DTSTART"], "%Y%m%d").date(),
                term,
            )
            for term in c.EXPECTED_TERMS
        ]
        reverse = {value: key for key, value in c.TERM_MAP.items()}
        text = ["2015年 公曆與農曆日期對照表 節氣"]
        for day, term in cls.official:
            text.append(f"{day.year}年{day.month}月{day.day}日 {reverse[term]}")
        cls.text = ("\n".join(text) + "\n").encode("utf-8")
        cls.xml = ("<Root>" + "".join(
            f"<Data><M>{day.month}</M><D>{day.day}</D><hm>12:00</hm></Data>"
            for day, _ in cls.official
        ) + "</Root>").encode("utf-8")

    @staticmethod
    def fake_response(url, payload, mime):
        response = mock.MagicMock()
        response.status = 200
        response.headers = {"Content-Type": mime} if mime is not None else {}
        response.geturl.return_value = url
        response.read.return_value = payload
        response.__enter__.return_value = response
        return response

    def test_official_text_is_accepted_with_generic_or_missing_mime(self):
        for mime in ("application/octet-stream", None):
            with self.subTest(mime=mime):
                url = c.HKO_URLS[0].format(year=2015)
                response = self.fake_response(url, self.text, mime)
                with mock.patch.object(c, "urlopen", return_value=response):
                    events, host = c.fetch_source(2015, c.HKO_URLS[0], attempts=1)
                self.assertEqual(events, tuple(self.official))
                self.assertEqual(host, "www.hko.gov.hk")
                response.read.assert_called_once_with(c.MAX_HKO_RESPONSE_BYTES + 1)

    def test_official_xml_is_accepted_with_generic_or_missing_mime(self):
        for mime in ("application/octet-stream", None):
            with self.subTest(mime=mime):
                url = c.HKO_ASTRONOMY_XML.format(year=2015)
                response = self.fake_response(url, self.xml, mime)
                with mock.patch.object(c, "urlopen", return_value=response):
                    events = c.fetch_astronomy_xml(2015, attempts=1)
                self.assertEqual(events, tuple(self.official))
                response.read.assert_called_once_with(c.MAX_HKO_RESPONSE_BYTES + 1)

    def test_invalid_body_is_still_rejected_independently_of_mime(self):
        for variant, payload, mime in (
            ("text", b"Access denied", "text/plain"),
            ("xml", b"<html>Access denied</html>", "application/xml"),
        ):
            with self.subTest(variant=variant):
                url = (c.HKO_URLS[0].format(year=2015) if variant == "text"
                       else c.HKO_ASTRONOMY_XML.format(year=2015))
                response = self.fake_response(url, payload, mime)
                with mock.patch.object(c, "urlopen", return_value=response):
                    with self.assertRaisesRegex(RuntimeError, "does not look like|expected 24"):
                        if variant == "text":
                            c.fetch_source(2015, c.HKO_URLS[0], attempts=1)
                        else:
                            c.fetch_astronomy_xml(2015, attempts=1)


if __name__ == "__main__":
    unittest.main()
