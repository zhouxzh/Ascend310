from __future__ import annotations

import unittest

import numpy as np

from tools.offline.calibrate_visible_light import calibrate


class VisibleLightCalibrationTests(unittest.TestCase):
    def test_calibration_treats_left_and_right_as_independent_palms(self) -> None:
        records = []
        for index in range(20):
            palm_id = f"P-{index // 2:03d}:{'left' if index % 2 == 0 else 'right'}"
            vector = np.zeros(24, dtype=np.float32)
            vector[index] = 1.0
            for sample in range(5):
                records.append({"palm_id": palm_id, "role": "enroll", "embedding": (vector + sample * 0.001).tolist()})
            for sample in range(10):
                records.append({"palm_id": palm_id, "role": "probe", "embedding": (vector + sample * 0.001).tolist()})
        report = calibrate({"records": records})
        self.assertEqual(report["identity_count"], 20)
        self.assertEqual(report["probe_count"], 200)
        self.assertEqual(report["top1"], 1.0)
        self.assertIn("histogram", report["score_distributions"]["genuine"])

    def test_calibration_requires_twenty_palms_and_full_protocol(self) -> None:
        with self.assertRaises(ValueError):
            calibrate({"records": []})


if __name__ == "__main__":
    unittest.main()
