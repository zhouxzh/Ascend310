import io
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from photoframe_push import PhotoFramePushClient, PushError, normalize_base_url
from app import _jpeg_to_bmp
from PIL import Image


class _Response:
    def __init__(self, status=200, body=b'{"status":"success"}', headers=None):
        self.status = status
        self.body = body
        self.headers = dict(headers or {})
        self.closed = False

    def read(self, size=-1):
        return self.body[:size]

    def close(self):
        self.closed = True

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class PhotoFramePushTests(unittest.TestCase):
    def test_base_url_is_explicit_and_normalized(self):
        self.assertEqual(normalize_base_url("HTTP://192.168.1.76:80///"), "http://192.168.1.76:80")
        for value in ("", "ftp://192.168.1.76", "http://user:pass@192.168.1.76", "http://192.168.1.76/?x=1"):
            with self.subTest(value=value):
                with self.assertRaises(PushError):
                    normalize_base_url(value)

    def test_posts_raw_jpeg_to_verified_display_image_endpoint(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response()

        result = PhotoFramePushClient(opener=opener, retry_delay_seconds=0).push_jpeg(
            "http://192.168.1.76/", b"jpeg-bytes", photo_id=12, etag='"abc"'
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.attempts, 1)
        request, timeout = requests[0]
        self.assertIsInstance(request, Request)
        self.assertEqual(request.full_url, "http://192.168.1.76/api/display-image")
        self.assertEqual(request.data, b"jpeg-bytes")
        self.assertEqual(request.get_header("Content-type"), "image/jpeg")
        self.assertEqual(request.get_header("X-album-photo-id"), "12")
        self.assertEqual(timeout, 8.0)

    def test_transient_errors_are_bounded(self):
        calls = []

        def opener(request, timeout):
            calls.append(1)
            raise URLError("offline")

        with self.assertRaises(PushError):
            PhotoFramePushClient(opener=opener, attempts=2, retry_delay_seconds=0).push_jpeg("http://e1002", b"x")
        self.assertEqual(len(calls), 2)

    def test_client_does_not_retry_permanent_http_errors(self):
        calls = []

        def opener(request, timeout):
            calls.append(1)
            raise HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b"invalid"))

        with self.assertRaises(PushError):
            PhotoFramePushClient(opener=opener, attempts=3, retry_delay_seconds=0).push_jpeg("http://e1002", b"x")
        self.assertEqual(len(calls), 1)

    def test_retry_after_transient_http_error_reuses_original_payload(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.data)
            if len(requests) == 1:
                raise HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
            return _Response()

        result = PhotoFramePushClient(opener=opener, attempts=2, retry_delay_seconds=0).push_jpeg(
            "http://e1002", b"original-jpeg"
        )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(requests, [b"original-jpeg", b"original-jpeg"])

    def test_waveshare_dataup_uses_raw_bmp_body(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return _Response(body="上传成功".encode("utf-8"))

        bmp = b"BM" + b"\x00" * 30
        result = PhotoFramePushClient(opener=opener, retry_delay_seconds=0).push_bmp(
            "http://192.168.1.77", bmp, photo_id=4, etag='"bmp"'
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.full_url, "http://192.168.1.77/dataUP")
        self.assertEqual(request.data, bmp)
        self.assertEqual(request.get_header("Content-type"), "image/bmp")
        self.assertEqual(request.get_header("X-album-transport"), "waveshare-dataup-raw")

    def test_waveshare_dataup_rejects_non_bmp(self):
        with self.assertRaises(PushError):
            PhotoFramePushClient(opener=lambda request, timeout: _Response()).push_bmp("http://e1002", b"jpeg")

    def test_case7_push_requires_verified_response_marker(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return _Response(headers={"X-Case7-Push": "1"})

        result = PhotoFramePushClient(opener=opener, retry_delay_seconds=0).push_case7_jpeg(
            "http://192.168.1.78", b"jpeg", photo_id=5
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(requests[0].full_url, "http://192.168.1.78/api/case7/push")
        self.assertEqual(requests[0].get_header("X-case7-push"), "1")

    def test_case7_push_rejects_unmodified_or_unverified_response(self):
        with self.assertRaises(PushError):
            PhotoFramePushClient(
                opener=lambda request, timeout: _Response(), retry_delay_seconds=0
            ).push_case7_jpeg("http://e1002", b"jpeg")

    def test_server_demo_conversion_is_exact_800x480_rgb_bmp(self):
        source = io.BytesIO()
        Image.new("RGB", (32, 16), (10, 20, 30)).save(source, "JPEG")
        bmp = _jpeg_to_bmp(source.getvalue())
        with Image.open(io.BytesIO(bmp)) as image:
            self.assertEqual(image.format, "BMP")
            self.assertEqual(image.size, (800, 480))
            self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
