import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from display_policy import (
    DisplayPolicyError,
    PhotoRenderer,
    cron_slot,
    orient_image,
    orientation_for_size,
    parse_cron,
    validate_policy,
)


class DisplayPolicyTests(unittest.TestCase):
    def test_cron_supports_lists_ranges_steps_and_sunday(self):
        minutes, hours, days = parse_cron("*/15 8-10/2 1,3-5")
        self.assertIn(30, minutes)
        self.assertEqual(hours, {8, 10})
        self.assertIn(4, days)
        self.assertEqual(cron_slot(datetime(2026, 8, 17, 8, 30), ["30 8 *"]), "2026-08-17-08-30:30 8 *")

    def test_invalid_cron_and_policy_are_rejected(self):
        with self.assertRaises(DisplayPolicyError):
            parse_cron("0 8")
        with self.assertRaises(DisplayPolicyError):
            validate_policy({"crop_mode": "stretch"})
        with self.assertRaises(DisplayPolicyError):
            validate_policy({"orientation_mode": "always"})

    def test_orientation_modes_preserve_or_match_source_aspect(self):
        portrait = Image.new("RGB", (30, 60), (220, 30, 30))
        self.assertEqual(orientation_for_size(portrait.size), "portrait")
        preserved = orient_image(portrait, (80, 48), mode="auto")
        self.assertEqual(preserved.size, (30, 60))
        matched = orient_image(portrait, (80, 48), mode="match_display")
        self.assertEqual(matched.size, (60, 30))
        rotated = orient_image(portrait, (80, 48), mode="auto", rotation=90)
        self.assertEqual(rotated.size, (60, 30))

    def test_match_display_keeps_final_orientation_after_mounting_rotation(self):
        landscape = Image.new("RGB", (60, 30), (220, 30, 30))
        # ``match_display`` is a postcondition on the encoded frame.  An odd
        # mounting correction is therefore followed by the compensating turn.
        result = orient_image(landscape, (80, 48), mode="match_display", rotation=90)
        self.assertEqual(orientation_for_size(result.size), "landscape")

    def test_square_target_is_an_internal_rendering_value(self):
        result = orient_image(Image.new("RGB", (40, 20)), (60, 60), target_orientation="square")
        self.assertEqual(result.size, (40, 20))

    def test_exif_orientation_is_normalized_before_rendering(self):
        image = Image.new("RGB", (20, 40), (20, 100, 220))
        exif = image.getexif()
        exif[274] = 6  # camera reports a clockwise 90-degree orientation
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "oriented.jpg"
            image.save(source, exif=exif.tobytes())
            with Image.open(source) as opened:
                normalized = orient_image(opened, (80, 48), mode="auto")
            self.assertEqual(normalized.size, (40, 20))

    def test_playlist_policy_defaults_and_validation(self):
        value = validate_policy()
        self.assertEqual(value["selection_mode"], "smart")
        self.assertEqual(value["playlist_photo_ids"], [])
        self.assertEqual(value["repeat_window"], 12)
        playlist = validate_policy({"selection_mode": "playlist", "playlist_photo_ids": [3, 8], "repeat_window": 20})
        self.assertEqual(playlist["playlist_photo_ids"], [3, 8])
        self.assertEqual(playlist["repeat_window"], 20)
        with self.assertRaises(DisplayPolicyError):
            validate_policy({"selection_mode": "random"})
        with self.assertRaises(DisplayPolicyError):
            validate_policy({"playlist_photo_ids": [3, 3]})
        with self.assertRaises(DisplayPolicyError):
            validate_policy({"playlist_photo_ids": [0]})

    def test_five_minute_cron_slot_is_stable(self):
        self.assertEqual(
            cron_slot(datetime(2026, 8, 23, 10, 15), ["*/5 * *"]),
            "2026-08-23-10-15:*/5 * *",
        )
        self.assertIsNone(cron_slot(datetime(2026, 8, 23, 10, 16), ["*/5 * *"]))

    def test_renderer_is_bounded_and_produces_panel_size(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            Image.new("RGB", (120, 60), (230, 20, 40)).save(source)
            body, width, height = PhotoRenderer().render(source, {"width": 80, "height": 48, "max_bytes": 50000}, "Asia/Shanghai", "晴天")
            self.assertEqual((width, height), (80, 48))
            self.assertLessEqual(len(body), 50000)
            self.assertTrue(body.startswith(b"\xff\xd8"))

    def test_renderer_applies_explicit_rotation_before_cover_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "landscape.jpg"
            image = Image.new("RGB", (120, 60), (230, 20, 40))
            image.save(source)
            policy = {"width": 80, "height": 48, "max_bytes": 50000, "rotation": 90}
            body, width, height = PhotoRenderer().render(source, policy, "Asia/Shanghai")
            self.assertEqual((width, height), (80, 48))
            self.assertTrue(body.startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
