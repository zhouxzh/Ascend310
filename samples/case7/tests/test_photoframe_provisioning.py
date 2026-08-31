import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from photoframe_provisioning import (
    PhotoFrameProvisioner,
    ProvisionError,
    normalize_device_url,
)


class _Response:
    def __init__(self, body=b"{}", status=200):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, limit=-1):
        if limit is not None and limit >= 0:
            return self.body[:limit]
        return self.body

    def close(self):
        self.closed = True


def _json(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class PhotoFrameProvisioningTests(unittest.TestCase):
    def test_device_url_is_private_literal_http_root_only(self):
        self.assertEqual(normalize_device_url("http://192.168.1.137/"), "http://192.168.1.137")
        self.assertEqual(normalize_device_url("http://10.2.3.4:80"), "http://10.2.3.4")
        for value in (
            "https://192.168.1.137",
            "http://localhost",
            "http://127.0.0.1",
            "http://169.254.1.10",
            "http://8.8.8.8",
            "http://192.168.1.137:8080",
            "http://192.168.1.137/api/config",
            "http://192.168.1.137/?x=1",
            "http://user:pass@192.168.1.137",
            "http://[fd00::1]",
        ):
            with self.subTest(value=value), self.assertRaises(ProvisionError):
                normalize_device_url(value)

    def test_provision_reads_identity_writes_config_verifies_and_rotates(self):
        calls = []
        desired_image = "http://192.168.1.135:7860/api/devices/demo/photoframe"
        desired_cron = ["*/30 * *"]
        saved = {
            "auto_rotate": False,
            "rotate_cron": ["0 */12 *"],
            "rotation_mode": "storage",
            "image_url": "",
            "display_orientation": "landscape",
            "display_rotation_deg": 180,
            "deep_sleep_enabled": True,
            "save_downloaded_images": True,
        }

        def opener(request: Request, timeout: float):
            calls.append((request.get_method(), request.full_url, timeout, request.data))
            path = request.full_url.split("192.168.1.137", 1)[-1]
            if request.get_method() == "GET" and path == "/api/system-info":
                return _Response(_json({
                    "project_name": "esp32-photoframe",
                    "device_id": "aabbccddeeff",
                    "version": "2.18.0",
                    "board_name": "Waveshare 7.3 7-Color",
                    "width": 800,
                    "height": 480,
                }))
            if request.get_method() == "GET" and path == "/api/config":
                return _Response(_json(saved))
            if request.get_method() == "PATCH" and path == "/api/config":
                saved.update(json.loads(request.data.decode("utf-8")))
                return _Response(_json({"status": "success"}))
            if request.get_method() == "POST" and path == "/api/rotate":
                return _Response(b"")
            raise AssertionError((request.get_method(), request.full_url))

        result = PhotoFrameProvisioner(opener=opener, timeout_seconds=3).provision(
            "http://192.168.1.137",
            image_url=desired_image,
            rotation_cron=desired_cron,
            display_orientation="landscape",
            native_size=(800, 480),
            expected_profile_id="waveshare_photopainter_73",
        )
        self.assertEqual(
            [(method, url.split("192.168.1.137", 1)[-1]) for method, url, _timeout, _data in calls],
            [
                ("GET", "/api/system-info"),
                ("GET", "/api/config"),
                ("PATCH", "/api/config"),
                ("GET", "/api/config"),
                ("POST", "/api/rotate"),
            ],
        )
        patch_body = json.loads(calls[2][3].decode("utf-8"))
        self.assertEqual(patch_body["image_url"], desired_image)
        self.assertTrue(patch_body["auto_rotate"])
        self.assertEqual(patch_body["rotation_mode"], "url")
        self.assertEqual(patch_body["display_rotation_deg"], 0)
        self.assertFalse(patch_body["deep_sleep_enabled"])
        self.assertFalse(patch_body["save_downloaded_images"])
        self.assertIsNone(calls[4][3])
        self.assertEqual(result.rotate_status, "requested")
        self.assertEqual(result.device_hardware_id, "aabbccddeeff")

    def test_config_readback_requires_rotation_sleep_and_download_storage_settings(self):
        expected = {
            "auto_rotate": True,
            "rotate_cron": ["*/30 * *"],
            "rotation_mode": "url",
            "image_url": "http://192.168.1.135:7860/api/devices/demo/photoframe",
            "display_orientation": "landscape",
            "display_rotation_deg": 0,
            "deep_sleep_enabled": False,
            "save_downloaded_images": False,
        }
        cases = (
            ("display_rotation_deg", 180, "display_rotation_deg=0"),
            ("deep_sleep_enabled", True, "deep_sleep_enabled=false"),
            ("save_downloaded_images", True, "save_downloaded_images=false"),
        )
        for key, actual, message in cases:
            with self.subTest(key=key):
                readback = dict(expected)
                readback[key] = actual
                with self.assertRaisesRegex(ProvisionError, message):
                    PhotoFrameProvisioner._validate_config(readback, expected)

    def test_config_patch_requires_explicit_success_and_restores_previous_fields(self):
        saved = {
            "auto_rotate": False,
            "rotate_cron": ["0 */12 *"],
            "rotation_mode": "storage",
            "image_url": "http://example.invalid/old.jpg",
            "display_orientation": "landscape",
            "display_rotation_deg": 180,
            "deep_sleep_enabled": True,
            "save_downloaded_images": True,
        }
        patch_payloads = []

        def opener(request: Request, timeout: float):
            path = request.full_url.split("192.168.1.137", 1)[-1]
            if request.get_method() == "GET" and path == "/api/system-info":
                return _Response(_json({
                    "project_name": "esp32-photoframe",
                    "width": 800,
                    "height": 480,
                }))
            if request.get_method() == "GET" and path == "/api/config":
                return _Response(_json(saved))
            if request.get_method() == "PATCH" and path == "/api/config":
                patch_payloads.append(json.loads(request.data.decode("utf-8")))
                if len(patch_payloads) == 1:
                    return _Response(_json({"status": "error", "message": "settings locked"}))
                return _Response(_json({"status": "success"}))
            raise AssertionError((request.get_method(), request.full_url))

        with self.assertRaisesRegex(ProvisionError, "did not confirm status=success"):
            PhotoFrameProvisioner(opener=opener).provision(
                "http://192.168.1.137",
                image_url="http://192.168.1.135:7860/api/devices/demo/photoframe",
                rotation_cron=["*/30 * *"],
                display_orientation="landscape",
                native_size=(800, 480),
            )
        self.assertEqual(len(patch_payloads), 2)
        self.assertTrue(patch_payloads[0]["auto_rotate"])
        self.assertEqual(patch_payloads[1], saved)

    def test_identity_mismatch_stops_before_config_write(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.get_method() + " " + request.full_url)
            return _Response(_json({"project_name": "other", "width": 800, "height": 480}))

        with self.assertRaisesRegex(ProvisionError, "not official"):
            PhotoFrameProvisioner(opener=opener).provision(
                "http://192.168.1.137",
                image_url="http://192.168.1.135:7860/api/devices/demo/photoframe",
                rotation_cron=["*/30 * *"],
                display_orientation="landscape",
                native_size=(800, 480),
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("GET "))

    def test_product_identity_mismatch_stops_before_config_write(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.get_method() + " " + request.full_url)
            return _Response(_json({
                "project_name": "esp32-photoframe",
                "board_name": "Seeed Studio reTerminal E1002",
                "width": 800,
                "height": 480,
            }))

        with self.assertRaisesRegex(ProvisionError, "does not match profile"):
            PhotoFrameProvisioner(opener=opener).provision(
                "http://192.168.1.137",
                image_url="http://192.168.1.135:7860/api/devices/demo/photoframe",
                rotation_cron=["*/30 * *"],
                display_orientation="landscape",
                native_size=(800, 480),
                expected_profile_id="waveshare_photopainter_73",
            )
        self.assertEqual(len(calls), 1)

    def test_rotate_transport_failure_is_returned_as_timed_out_after_verified_config(self):
        saved = {
            "auto_rotate": True,
            "rotate_cron": ["*/30 * *"],
            "rotation_mode": "url",
            "image_url": "http://192.168.1.135:7860/api/devices/demo/photoframe",
            "display_orientation": "landscape",
            "display_rotation_deg": 0,
            "deep_sleep_enabled": False,
            "save_downloaded_images": False,
        }

        def opener(request, timeout):
            path = request.full_url.split("192.168.1.137", 1)[-1]
            if path == "/api/system-info":
                return _Response(_json({"project_name": "esp32-photoframe", "width": 800, "height": 480}))
            if request.get_method() == "PATCH":
                return _Response(_json({"status": "success"}))
            if request.get_method() == "POST":
                raise URLError("sleeping")
            return _Response(_json(saved))

        result = PhotoFrameProvisioner(opener=opener).provision(
            "http://192.168.1.137",
            image_url=saved["image_url"],
            rotation_cron=["*/30 * *"],
            display_orientation="landscape",
            native_size=(800, 480),
        )
        self.assertEqual(result.rotate_status, "timed_out")
        self.assertIn("unreachable", result.rotate_error)

    def test_rotate_explicit_error_response_is_failed_not_timed_out(self):
        saved = {
            "auto_rotate": True,
            "rotate_cron": ["*/30 * *"],
            "rotation_mode": "url",
            "image_url": "http://192.168.1.135:7860/api/devices/demo/photoframe",
            "display_orientation": "landscape",
            "display_rotation_deg": 0,
            "deep_sleep_enabled": False,
            "save_downloaded_images": False,
        }

        def opener(request, timeout):
            path = request.full_url.split("192.168.1.137", 1)[-1]
            if path == "/api/system-info":
                return _Response(_json({"project_name": "esp32-photoframe", "width": 800, "height": 480}))
            if request.get_method() == "PATCH":
                return _Response(_json({"status": "success"}))
            if request.get_method() == "POST":
                self.assertIsNone(request.data)
                return _Response(_json({"status": "error", "message": "device busy"}))
            return _Response(_json(saved))

        result = PhotoFrameProvisioner(opener=opener).provision(
            "http://192.168.1.137",
            image_url=saved["image_url"],
            rotation_cron=["*/30 * *"],
            display_orientation="landscape",
            native_size=(800, 480),
        )
        self.assertEqual(result.rotate_status, "failed")
        self.assertEqual(result.rotate_http_status, 200)
        self.assertIn("did not confirm status=success", result.rotate_error)

    def test_redirect_is_not_followed(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 302, "redirect", {}, io.BytesIO(b"redirect"))

        with self.assertRaisesRegex(ProvisionError, "HTTP 302"):
            PhotoFrameProvisioner(opener=opener).provision(
                "http://192.168.1.137",
                image_url="http://192.168.1.135:7860/api/devices/demo/photoframe",
                rotation_cron=["*/30 * *"],
                display_orientation="landscape",
                native_size=(800, 480),
            )


if __name__ == "__main__":
    unittest.main()
