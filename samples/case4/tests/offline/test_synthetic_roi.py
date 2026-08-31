from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from tools.offline.generate_synthetic_roi import ROI_SIZE, synthetic_roi, write_pgm


class SyntheticRoiTests(unittest.TestCase):
    def test_pattern_is_deterministic_and_non_empty(self):
        first = synthetic_roi()
        second = synthetic_roi()
        self.assertEqual(first.shape, (ROI_SIZE, ROI_SIZE))
        self.assertEqual(first.dtype, np.uint8)
        self.assertGreater(int(first.max()), int(first.min()))
        np.testing.assert_array_equal(first, second)

    def test_pgm_writer_keeps_exact_pixel_payload(self):
        image = synthetic_roi()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "synthetic.pgm"
            write_pgm(output, image)
            payload = output.read_bytes()
        self.assertTrue(payload.startswith(b"P5\n128 128\n255\n"))
        self.assertEqual(payload.split(b"255\n", 1)[1], image.tobytes())


if __name__ == "__main__":
    unittest.main()
