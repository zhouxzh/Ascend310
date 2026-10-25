from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "artifacts" / "qwen25-kv-1024" / "board" / "qwen25-static-kv-1024-v2.om.lock.json"
CONTRACT_PATH = ROOT / "contracts" / "qwen25-kv-1024" / "board" / "qwen25-static-kv-1024-v2-om-contract.json"


class Qwen25AclOmContractTests(unittest.TestCase):
    def test_lock_has_executable_atc_shape_and_matches_contract_artifact(self) -> None:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        expected_shape = ";".join(
            "%s:%s" % (item["name"], ",".join(str(value) for value in item["shape"]))
            for item in contract["acl_om"]["inputs"]
        )
        self.assertEqual(lock["atc_input_shape"], expected_shape)
        self.assertNotIn("derived-from", lock["atc_command"])
        self.assertIn('--input_shape="%s"' % expected_shape, lock["atc_command"])
        self.assertEqual(lock["om_path"], contract["om_artifact"]["path"])
        self.assertEqual(lock["bytes"], contract["om_artifact"]["bytes"])
        self.assertEqual(lock["sha256"], contract["om_artifact"]["sha256"])


if __name__ == "__main__":
    unittest.main()
