import os
import tempfile
import unittest
from pathlib import Path

from admin_auth import AdminTokenStore


class AdminAuthTests(unittest.TestCase):
    def test_token_is_persistent_and_wrong_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets" / "admin.token"
            first = AdminTokenStore(path)
            second = AdminTokenStore(path)
            self.assertTrue(first.verify(second.reveal_for_cli()))
            self.assertFalse(first.verify("wrong"))
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
