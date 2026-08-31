"""Pure-Python contract checks for the Case 1 directory layout."""

import sys
import unittest
from pathlib import Path


CASE_ROOT = Path(__file__).resolve().parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from face_attendance.config import DATA_DIR, DB_PATH, MODEL_DIR, UPLOAD_DIR


class LayoutContractTest(unittest.TestCase):
    def test_runtime_paths_are_under_sample_root(self):
        for path in (DATA_DIR, DB_PATH, MODEL_DIR, UPLOAD_DIR):
            self.assertTrue(path.is_relative_to(CASE_ROOT))

    def test_expected_roles_are_present(self):
        for relative in ("app.py", "face_attendance", "scripts", "tests", "docs", "frontend", "models"):
            self.assertTrue((CASE_ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
