import tempfile
import unittest
from pathlib import Path

from device_registry import LOCAL_TOUCHSCREEN_ID, DeviceError, DeviceRegistry, photo_frame_profile


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = DeviceRegistry(Path(self.temp.name) / "devices.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_lcd_handshake_token_and_revoke(self):
        value = self.registry.handshake({
            "name": "desk-lcd",
            "display": {"kind": "lcd", "width": 320, "height": 240, "codecs": ["jpeg"], "max_bytes": 200000},
        })
        self.assertTrue(value["token"])
        self.assertNotIn("token_hash", value)
        device = self.registry.authorize(value["device_id"], value["token"])
        self.assertEqual(device["name"], "desk-lcd")
        with self.assertRaises(DeviceError):
            self.registry.handshake({"device_id": value["device_id"], "display": {"kind": "lcd", "width": 320, "height": 240, "codecs": ["jpeg"], "max_bytes": 200000}})
        repeated = self.registry.handshake({"device_id": value["device_id"], "token": value["token"], "display": {"kind": "lcd", "width": 320, "height": 240, "codecs": ["jpeg"], "max_bytes": 200000}})
        self.assertIsNone(repeated["token"])
        self.assertNotIn("token", self.registry.list()[0])
        self.assertNotIn("token_hash", self.registry.list()[0])
        self.registry.revoke(value["device_id"])
        # Revoke is intentionally a soft operation: it invalidates the
        # device token while retaining the registration for audit/recovery.
        revoked = self.registry.get(value["device_id"])
        self.assertFalse(revoked["enabled"])
        self.assertEqual(revoked["device_id"], value["device_id"])
        with self.assertRaises(DeviceError):
            self.registry.authorize(value["device_id"], value["token"])

    def test_delete_removes_remote_registration_and_survives_reload(self):
        value = self.registry.handshake({
            "name": "remove-me",
            "display": {"kind": "lcd", "width": 320, "height": 240, "codecs": ["jpeg"], "max_bytes": 200000},
        })
        device_id = value["device_id"]
        self.assertTrue(any(item["device_id"] == device_id for item in self.registry.list()))

        removed = self.registry.delete(device_id)
        self.assertEqual(removed["device_id"], device_id)
        self.assertTrue(removed.get("deleted", True))
        self.assertFalse(any(item["device_id"] == device_id for item in self.registry.list()))
        with self.assertRaisesRegex(DeviceError, "unknown device"):
            self.registry.get(device_id)

        # The deletion is durable and cannot be resurrected by a process
        # restart; it also only touches the registry record, not photo data.
        restored = DeviceRegistry(Path(self.temp.name) / "devices.json")
        self.assertFalse(any(item["device_id"] == device_id for item in restored.list()))
        with self.assertRaisesRegex(DeviceError, "unknown device"):
            restored.delete(device_id)

    def test_delete_rejects_builtin_local_touchscreen(self):
        with self.assertRaisesRegex(DeviceError, "local touchscreen"):
            self.registry.delete(LOCAL_TOUCHSCREEN_ID)
        # A rejected delete must not create or alter the local registration.
        self.assertIsNone(self.registry._data.get("local_touchscreen"))

    def test_e6_requires_exact_capability(self):
        with self.assertRaises(DeviceError):
            self.registry.handshake({"display": {"kind": "epaper", "width": 640, "height": 480, "codecs": ["e6"]}})
        value = self.registry.handshake({"display": {"kind": "epaper", "width": 800, "height": 480, "codecs": ["e6"]}})
        self.assertEqual(value["display"]["kind"], "epaper")
        with self.assertRaises(DeviceError):
            self.registry.handshake({"display": {"kind": "lcd", "width": 320, "height": 240, "codecs": ["jpeg"], "max_bytes": 1}})

    def test_photoframe_is_a_jpeg_url_rotation_device(self):
        value = self.registry.handshake({"name": "waveshare", "profile_id": "waveshare_photopainter_73", "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]}})
        self.assertEqual(value["display"]["kind"], "photoframe")
        self.assertEqual(value["display"]["max_bytes"], 2 * 1024 * 1024)
        self.assertEqual(value["policy"]["rotation_cron"], ["*/30 * *"])
        self.assertEqual(value["display"]["orientation_mode"], "auto")
        updated = self.registry.update(value["device_id"], {"policy": {"crop_mode": "fit"}})
        self.assertEqual(updated["policy"]["crop_mode"], "fit")
        self.assertEqual(updated["policy_revision"], 2)

    def test_verified_photoframe_request_promotes_pull_state_and_persists(self):
        value = self.registry.handshake({
            "name": "verified-frame",
            "profile_id": "waveshare_photopainter_73",
            "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]},
        })
        device_id = value["device_id"]
        self.registry.mark_pull_provision(
            device_id,
            "awaiting_pull",
            device_url="http://192.168.1.137",
            configured_image_url="http://192.168.1.135:7860/api/devices/example/photoframe",
            successful=True,
        )

        # A browser-style request must not move the control-plane state.
        self.registry.mark_request(device_id, "ok")
        self.assertEqual(self.registry.get(device_id)["pull_provision"]["status"], "awaiting_pull")

        recorded = self.registry.mark_request(
            device_id,
            "ok",
            firmware_version="v2.18.0",
            display={"width": 800, "height": 480, "orientation": "landscape"},
        )
        self.assertEqual(recorded["pull_provision"]["status"], "pulled")
        self.assertIsNotNone(recorded["pull_provision"]["first_pull_at"])
        self.assertIsNotNone(recorded["pull_provision"]["last_pull_at"])

        restored = DeviceRegistry(Path(self.temp.name) / "devices.json").get(device_id)
        self.assertEqual(restored["pull_provision"]["status"], "pulled")
        self.assertEqual(restored["last_request_firmware"], "v2.18.0")

    def test_supported_product_profiles_fix_dimensions_and_orientation(self):
        waveshare = photo_frame_profile("waveshare_photopainter_73")
        self.assertEqual(waveshare["orientations"], ["landscape", "portrait"])
        self.assertEqual(waveshare["panel"], "E6")
        self.assertEqual(waveshare["color_count"], 6)
        self.assertEqual(waveshare["rotation_degrees"], [])
        with self.assertRaises(DeviceError):
            photo_frame_profile(None)
        value = self.registry.handshake({
            "profile_id": "waveshare_photopainter_73",
            "display": {"kind": "photoframe", "width": 480, "height": 800, "orientation": "portrait", "codecs": ["jpeg"]},
        })
        self.assertEqual(value["profile_id"], "waveshare_photopainter_73")
        self.assertEqual(value["display"]["orientation"], "portrait")
        profile_defaults = self.registry.handshake({
            "profile_id": "seeedstudio_reterminal_e1002",
            "display": {"kind": "photoframe"},
        })
        self.assertEqual(profile_defaults["display"]["width"], 800)
        self.assertEqual(profile_defaults["display"]["height"], 480)
        self.assertEqual(profile_defaults["display"]["codecs"], ["jpeg"])
        with self.assertRaises(DeviceError):
            self.registry.handshake({
                "profile_id": "seeedstudio_reterminal_e1002",
                "display": {"kind": "photoframe", "width": 480, "height": 800, "orientation": "portrait", "codecs": ["jpeg"]},
            })
        with self.assertRaises(DeviceError):
            self.registry.handshake({
                "profile_id": "waveshare_photopainter_73",
                "display": {"kind": "photoframe", "width": 800, "height": 480, "rotation": 90, "codecs": ["jpeg"]},
            })
        with self.assertRaisesRegex(DeviceError, "conflicts"):
            self.registry.handshake({
                "profile_id": "waveshare_photopainter_73",
                "display": {
                    "kind": "photoframe",
                    "profile_id": "seeedstudio_reterminal_e1002",
                    "width": 800,
                    "height": 480,
                    "codecs": ["jpeg"],
                },
            })
        with self.assertRaisesRegex(DeviceError, "profile_id is required"):
            self.registry.handshake({"display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]}})
        with self.assertRaisesRegex(DeviceError, "codecs must be exactly"):
            self.registry.handshake({
                "profile_id": "waveshare_photopainter_73",
                "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["e6"]},
            })
        with self.assertRaises(DeviceError):
            self.registry.handshake({
                "profile_id": "waveshare_photopainter_73",
                "display": {"kind": "photoframe", "width": 800, "height": 480, "rotation": 360, "codecs": ["jpeg"]},
            })
        value = self.registry.handshake({
            "profile_id": "waveshare_photopainter_73",
            "display": {
                "kind": "photoframe",
                "width": 800,
                "height": 480,
                "codecs": ["jpeg"],
                "panel": "untrusted-panel",
                "color_count": 99,
            },
        })
        self.assertNotIn("panel", value["display"])
        self.assertNotIn("color_count", value["display"])

    def test_handshake_rejects_non_object_payloads(self):
        with self.assertRaisesRegex(DeviceError, "handshake must be"):
            self.registry.handshake([])
        with self.assertRaisesRegex(DeviceError, "display must be"):
            self.registry.handshake({"display": "not-an-object"})

    def test_display_orientation_mode_is_validated(self):
        value = self.registry.handshake({
            "name": "portrait-lcd",
            "display": {
                "kind": "lcd",
                "width": 240,
                "height": 320,
                "codecs": ["jpeg"],
                "max_bytes": 200000,
                "orientation_mode": "match_display",
                "orientation": "portrait",
            },
        })
        self.assertEqual(value["display"]["orientation_mode"], "match_display")
        self.assertEqual(value["display"]["orientation"], "portrait")
        with self.assertRaises(DeviceError):
            self.registry.handshake({"display": {"kind": "lcd", "width": 240, "height": 320, "codecs": ["jpeg"], "max_bytes": 200000, "orientation_mode": "sideways"}})

    def test_mismatched_orientation_is_rejected_before_persistence(self):
        with self.assertRaises(DeviceError):
            self.registry.handshake(
                {
                    "name": "bad-orientation",
                    "display": {
                        "kind": "lcd",
                        "width": 80,
                        "height": 48,
                        "codecs": ["jpeg"],
                        "max_bytes": 20000,
                        "orientation": "portrait",
                    },
                }
            )
        self.assertEqual(self.registry.list(), [])

    def test_active_push_requires_explicit_base_url_and_persists_status(self):
        value = self.registry.handshake({"name": "push-frame", "profile_id": "seeedstudio_reterminal_e1002", "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]}})
        with self.assertRaises(DeviceError):
            self.registry.update(value["device_id"], {"push": {"enabled": True, "base_url": "ftp://e1002"}})
        with self.assertRaises(DeviceError):
            self.registry.update(value["device_id"], {"push": {"enabled": True, "base_url": "http://192.168.1.76:80"}})
        updated = self.registry.update(value["device_id"], {"push": {"enabled": True, "base_url": "http://192.168.1.76:80", "protocol": "photoframe_api"}})
        self.assertTrue(updated["push"]["enabled"])
        self.assertEqual(updated["push"]["base_url"], "http://192.168.1.76:80")
        self.assertEqual(updated["push"]["protocol"], "photoframe_api")
        self.assertEqual(updated["push"]["timeout_seconds"], 60)
        self.assertEqual(updated["push"]["attempts"], 1)
        marked = self.registry.mark_push(value["device_id"], "ok", photo_id=3, etag='"etag"', slot="2026-08-23-12-00:*/5 * *")
        self.assertEqual(marked["push"]["last_photo_id"], 3)
        self.assertEqual(marked["push"]["last_success_slot"], "2026-08-23-12-00:*/5 * *")
        restored = DeviceRegistry(Path(self.temp.name) / "devices.json").get(value["device_id"])
        self.assertEqual(restored["push"]["last_status"], "ok")
        self.assertEqual(restored["push"]["last_slot"], "2026-08-23-12-00:*/5 * *")

    def test_active_push_requires_explicit_supported_protocol(self):
        value = self.registry.handshake({"name": "protocol-frame", "profile_id": "waveshare_photopainter_73", "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]}})
        with self.assertRaises(DeviceError):
            self.registry.update(value["device_id"], {"push": {"enabled": True, "base_url": "http://192.168.1.77", "protocol": "auto"}})
        updated = self.registry.update(value["device_id"], {"push": {"enabled": True, "base_url": "http://192.168.1.77", "protocol": "waveshare_dataup"}})
        self.assertEqual(updated["push"]["protocol"], "waveshare_dataup")
        self.registry.update(value["device_id"], {"push": {"protocol": "case7_push"}})
        self.assertEqual(self.registry.get(value["device_id"])["push"]["protocol"], "case7_push")

    def test_old_incomplete_enabled_push_is_disabled_during_migration(self):
        path = Path(self.temp.name) / "legacy.json"
        path.write_text(
            '{"schema_version":1,"devices":{"legacy":{"display":{"kind":"photoframe"},"push":{"enabled":true}}}}',
            encoding="utf-8",
        )
        value = DeviceRegistry(path).get("legacy")
        self.assertFalse(value["push"]["enabled"])
        self.assertEqual(value["push"]["last_status"], "disabled")
        self.assertIn("base_url", value["push"]["last_error"])
        self.assertEqual(value["push"]["timeout_seconds"], 60)
        self.assertEqual(value["push"]["attempts"], 1)

    def test_legacy_photoframe_without_profile_is_not_guessed_as_waveshare(self):
        path = Path(self.temp.name) / "legacy-unidentified.json"
        path.write_text(
            '{"schema_version":1,"devices":{"legacy":{"name":"old-frame",'
            '"display":{"kind":"photoframe","width":1024,"height":600,"codecs":["jpeg"]}}}}',
            encoding="utf-8",
        )
        registry = DeviceRegistry(path)
        value = registry.get("legacy")
        self.assertNotIn("profile_id", value)
        self.assertNotIn("profile_id", value["display"])
        self.assertEqual(value["display"]["width"], 1024)
        self.assertEqual(value["display"]["height"], 600)
        self.assertTrue(value["profile_required"])
        self.assertIn("register it as", value["profile_error"])

    def test_legacy_photoframe_can_be_explicitly_reidentified(self):
        path = Path(self.temp.name) / "legacy-reidentify.json"
        path.write_text(
            '{"schema_version":1,"devices":{"legacy":{"display":{"kind":"photoframe",'
            '"width":1024,"height":600,"codecs":["jpeg"]}}}}',
            encoding="utf-8",
        )
        registry = DeviceRegistry(path)
        value = registry.update("legacy", {"profile_id": "seeedstudio_reterminal_e1002"})
        self.assertEqual(value["profile_id"], "seeedstudio_reterminal_e1002")
        self.assertFalse(value.get("profile_required", False))
        self.assertEqual((value["display"]["width"], value["display"]["height"]), (800, 480))

    def test_malformed_unprofiled_record_cannot_update_policy_or_push(self):
        path = Path(self.temp.name) / "malformed-unprofiled.json"
        path.write_text(
            '{"schema_version":3,"devices":{"legacy":{"display":{"kind":"photoframe",'
            '"width":800,"height":480,"codecs":["jpeg"]}}}}',
            encoding="utf-8",
        )
        registry = DeviceRegistry(path)
        registry._data["devices"]["legacy"].pop("profile_required", None)
        with self.assertRaisesRegex(DeviceError, "model is not identified"):
            registry.update("legacy", {"policy": {"crop_mode": "fit"}})
        with self.assertRaisesRegex(DeviceError, "model is not identified"):
            registry.update("legacy", {"push": {"enabled": False}})

    def test_legacy_success_slot_is_restored_for_idempotence(self):
        path = Path(self.temp.name) / "legacy-success.json"
        path.write_text(
            '{"schema_version":1,"devices":{"legacy":{"display":{"kind":"photoframe"},"push":{"enabled":false,"base_url":"http://e1002","protocol":"photoframe_api","last_status":"ok","last_slot":"2026-08-23-22-20:*/5 * *"}}}}',
            encoding="utf-8",
        )
        value = DeviceRegistry(path).get("legacy")
        self.assertEqual(value["push"]["last_success_slot"], "2026-08-23-22-20:*/5 * *")

    def test_local_touchscreen_is_persisted_but_not_a_remote_protocol_device(self):
        # Existing device protocol callers must continue to see an empty
        # registry until an ESP32/LCD/E6 device has actually registered.
        self.assertEqual(self.registry.list(), [])
        local = self.registry.local_touchscreen()
        self.assertEqual(local["device_id"], LOCAL_TOUCHSCREEN_ID)
        self.assertTrue(local["is_local"])
        self.assertEqual(local["device_type"], "touchscreen")
        self.assertEqual(local["display"]["kind"], "touchscreen")
        self.assertFalse(local["push"]["enabled"])
        self.assertEqual(self.registry.list(), [])

        changed = self.registry.update_local_touchscreen(
            {
                "name": "客厅控制屏",
                "enabled": False,
                "display": {"width": 1280, "height": 800, "rotation": 90},
            }
        )
        self.assertEqual(changed["name"], "客厅控制屏")
        self.assertFalse(changed["enabled"])
        self.assertEqual(changed["display"]["width"], 1280)
        self.assertEqual(changed["display"]["rotation"], 90)
        restored = DeviceRegistry(Path(self.temp.name) / "devices.json").local_touchscreen()
        self.assertEqual(restored["name"], "客厅控制屏")
        self.assertFalse(restored["enabled"])

    def test_local_touchscreen_rejects_network_push_and_delete_operations(self):
        with self.assertRaises(DeviceError):
            self.registry.update_local_touchscreen({"push": {"enabled": True}})
        with self.assertRaises(DeviceError):
            self.registry.revoke(LOCAL_TOUCHSCREEN_ID)


if __name__ == "__main__":
    unittest.main()
