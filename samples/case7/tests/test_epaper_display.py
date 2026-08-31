import unittest
from unittest import mock

from PIL import Image

from epaper_display import (
    EpaperConfig,
    EpaperDisplay,
    EpaperError,
    crop_to_panel,
    pack_palette_indices,
    prepare_frame,
)
from epaper_album import _parser


class EpaperFrameTests(unittest.TestCase):
    def test_portrait_photo_is_kept_upright_by_default_and_can_match_panel(self):
        portrait = Image.new("RGB", (300, 600), (255, 0, 0))
        kept = crop_to_panel(portrait, (80, 48), orientation_mode="auto")
        matched = crop_to_panel(portrait, (80, 48), orientation_mode="match_display")
        self.assertEqual(kept.size, (80, 48))
        self.assertEqual(matched.size, (80, 48))
        # Both paths must remain a valid fixed-size E6 frame; the opt-in mode
        # changes the source orientation before cropping, not the wire shape.
        self.assertEqual(prepare_frame(portrait, dither=False, orientation_mode="match_display").image.size, (800, 480))

    def test_prepare_frame_has_panel_shape_and_wire_size(self):
        frame = prepare_frame(Image.new("RGB", (1200, 700), (255, 255, 0)), dither=False)
        self.assertEqual(frame.image.size, (800, 480))
        self.assertEqual(len(frame.packed), 192000)

    def test_e6_visible_colors_keep_official_sparse_codes(self):
        expected = {
            (255, 255, 255): 0x01,
            (255, 255, 0): 0x02,
            (255, 0, 0): 0x03,
            (0, 0, 255): 0x05,
            (0, 255, 0): 0x06,
        }
        for color, code in expected.items():
            frame = prepare_frame(Image.new("RGB", (800, 480), color), dither=False)
            self.assertEqual(frame.packed[0] >> 4, code)

    def test_pack_uses_high_nibble_for_first_pixel(self):
        self.assertEqual(pack_palette_indices(bytes([0x02, 0x06]), 2, 1), bytes([0x26]))

    def test_pack_rejects_wrong_frame_length(self):
        with self.assertRaises(EpaperError):
            pack_palette_indices(bytes([0x00]), 2, 1)

    def test_hardware_backend_requires_explicit_gpio_offsets(self):
        display = EpaperDisplay(EpaperConfig(backend="orangepi"))
        with self.assertRaises(EpaperError):
            display.show(Image.new("RGB", (800, 480), (0, 0, 0)), preview_path=None, frame_path=None)

    def test_e6_init_uses_official_command_order(self):
        display = EpaperDisplay(EpaperConfig(backend="dry-run"))
        commands = []
        display._reset = mock.Mock()
        display._wait_idle = mock.Mock()
        display._command = lambda value: commands.append(value)
        display._data = mock.Mock()
        with mock.patch("epaper_display.time.sleep"):
            display._init_panel()
        self.assertEqual(
            commands,
            [0xAA, 0x01, 0x00, 0x03, 0x05, 0x06, 0x08, 0x30, 0x50, 0x60, 0x61, 0x84, 0xE3, 0x04],
        )

    def test_busy_timeout_is_bounded(self):
        class Busy:
            @staticmethod
            def read():
                return False

        display = EpaperDisplay(EpaperConfig(backend="orangepi", busy_timeout_s=0.0))
        display._transport = type("Transport", (), {"busy": Busy()})()
        with self.assertRaises(EpaperError):
            display._wait_idle()

    def test_epaper_cli_accepts_orientation_controls(self):
        args = _parser().parse_args(
            ["--photo", "example.jpg", "--orientation-mode", "match_display", "--rotation", "270"]
        )
        self.assertEqual(args.orientation_mode, "match_display")
        self.assertEqual(args.rotation, 270)


if __name__ == "__main__":
    unittest.main()
