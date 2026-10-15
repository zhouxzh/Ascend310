from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from qwen25_legacy_compat_service import CompatibilityError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_qwen25_legacy_compat.py"
SPEC = importlib.util.spec_from_file_location("serve_qwen25_legacy_compat", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LegacyCompatibilityLauncherTests(unittest.TestCase):
    def test_requires_documented_base_environment_before_any_acl_preflight(self) -> None:
        with patch.object(MODULE.sys, "version_info", (3, 9, 0)):
            with patch.dict(MODULE.os.environ, {"CONDA_DEFAULT_ENV": "other"}, clear=True):
                with self.assertRaisesRegex(CompatibilityError, "documented base environment"):
                    MODULE._check_board_environment(allow_dirty_base=True)

    def test_uses_non_importing_acl_visibility_preflight(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('importlib.util.find_spec("acl")', source)
        self.assertNotIn("import acl  # type: ignore", source)


if __name__ == "__main__":
    unittest.main()
