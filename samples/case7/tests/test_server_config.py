import tempfile
import unittest
from pathlib import Path

from server_config import ConfigError, ConfigStore


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_update_is_persistent_and_revisioned(self):
        store = ConfigStore(self.path)
        first = store.get()
        changed = store.update({"device": {"jpeg_quality": 71}}, expected_revision=first["revision"])
        self.assertEqual(changed["device"]["jpeg_quality"], 71)
        self.assertEqual(changed["revision"], first["revision"] + 1)
        self.assertEqual(ConfigStore(self.path).get()["revision"], changed["revision"])
        with self.assertRaises(ConfigError):
            store.update({"timezone": "UTC"}, expected_revision=first["revision"])

    def test_display_cadences_are_independent(self):
        store = ConfigStore(self.path)
        value = store.get()
        self.assertEqual(value["display"]["touchscreen_interval_seconds"], 60)
        self.assertEqual(value["display"]["remote_refresh_seconds"], 30)
        self.assertEqual(value["epaper"]["rotation_interval_seconds"], 1800)
        changed = store.update(
            {
                "display": {
                    "touchscreen_interval_seconds": 45,
                    "remote_refresh_seconds": 15,
                },
                "epaper": {"rotation_interval_seconds": 600},
            },
            expected_revision=value["revision"],
        )
        self.assertEqual(changed["display"]["touchscreen_interval_seconds"], 45)
        self.assertEqual(changed["display"]["remote_refresh_seconds"], 15)
        self.assertEqual(changed["epaper"]["rotation_interval_seconds"], 600)
        with self.assertRaises(ConfigError):
            store.update({"epaper": {"rotation_interval_seconds": 61}})

    def test_touchscreen_device_switch_defaults_and_persists(self):
        store = ConfigStore(self.path)
        self.assertTrue(store.get()["display"]["touchscreen_enabled"])
        changed = store.update({"display": {"touchscreen_enabled": False}})
        self.assertFalse(changed["display"]["touchscreen_enabled"])
        self.assertFalse(ConfigStore(self.path).get()["display"]["touchscreen_enabled"])
        with self.assertRaises(ConfigError):
            store.update({"display": {"touchscreen_enabled": "false"}})

    def test_legacy_hourly_default_migrates_to_fast_touchscreen(self):
        self.path.write_text(
            '{"schema_version":1,"revision":4,"display":{"interval_seconds":3600}}',
            encoding="utf-8",
        )
        value = ConfigStore(self.path).get()
        self.assertEqual(value["display"]["interval_seconds"], 3600)
        self.assertEqual(value["display"]["touchscreen_interval_seconds"], 60)

    def test_e6_paths_and_line_offsets_are_validated(self):
        store = ConfigStore(self.path)
        value = store.update({"epaper": {"dc_line": 12, "rst_line": 13, "busy_line": 14}})
        self.assertEqual(value["epaper"]["dc_line"], 12)
        with self.assertRaises(ConfigError):
            store.update({"epaper": {"spi_device": "relative-device"}})
        with self.assertRaises(ConfigError):
            store.update({"shell_command": "rm -rf /"})

    def test_filename_watermark_setting_is_persistent_and_boolean(self):
        store = ConfigStore(self.path)
        changed = store.update({"display": {"show_filename": False}})
        self.assertFalse(changed["display"]["show_filename"])
        self.assertFalse(ConfigStore(self.path).get()["display"]["show_filename"])
        with self.assertRaises(ConfigError):
            store.update({"display": {"show_filename": "false"}})

    def test_display_orientation_settings_are_persistent_and_validated(self):
        store = ConfigStore(self.path)
        changed = store.update({"display": {"orientation_mode": "match_display", "rotation": 90}})
        self.assertEqual(changed["display"]["orientation_mode"], "match_display")
        self.assertEqual(changed["display"]["rotation"], 90)
        restored = ConfigStore(self.path).get()
        self.assertEqual(restored["display"]["orientation_mode"], "match_display")
        self.assertEqual(restored["display"]["rotation"], 90)
        with self.assertRaises(ConfigError):
            store.update({"display": {"orientation_mode": "sideways"}})
        with self.assertRaises(ConfigError):
            store.update({"display": {"rotation": 45}})

    def test_epaper_orientation_is_independent_from_touchscreen(self):
        store = ConfigStore(self.path)
        changed = store.update(
            {
                "display": {"orientation_mode": "match_display", "rotation": 90},
                "epaper": {"orientation_mode": "auto", "rotation": 270, "e6_dither": False},
            }
        )
        self.assertEqual(changed["display"]["rotation"], 90)
        self.assertEqual(changed["epaper"]["rotation"], 270)
        self.assertFalse(changed["epaper"]["e6_dither"])
        restored = ConfigStore(self.path).get()
        self.assertEqual(restored["epaper"]["orientation_mode"], "auto")
        self.assertEqual(restored["epaper"]["rotation"], 270)
        with self.assertRaises(ConfigError):
            store.update({"epaper": {"orientation_mode": "sideways"}})
        with self.assertRaises(ConfigError):
            store.update({"epaper": {"rotation": 45}})
        with self.assertRaises(ConfigError):
            store.update({"epaper": {"e6_dither": "false"}})


if __name__ == "__main__":
    unittest.main()
