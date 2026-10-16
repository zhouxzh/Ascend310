from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest

import numpy as np

from palmprint_workbench.domain.templates import TemplateStore


class TemplateStoreTests(unittest.TestCase):
    def test_automatic_person_ids_are_monotonic_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateStore(root)
            self.assertEqual(store.allocate_person_id(), "P-000001")
            self.assertEqual(store.allocate_person_id(), "P-000002")
            self.assertEqual(TemplateStore(root).allocate_person_id(), "P-000003")

    def test_automatic_person_id_skips_existing_template_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TemplateStore(Path(directory))
            store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([1.0, 0.0], dtype=np.float32)] * 5,
                person_id="P-000017",
                display_name=None,
                palm_side="left",
            )
            self.assertEqual(store.allocate_person_id(), "P-000018")

    def test_stable_palm_id_replaces_only_the_same_palm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TemplateStore(Path(directory))
            first = store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([1.0, 0.0], dtype=np.float32)] * 5,
                person_id="P-001",
                display_name="Alice",
                palm_side="left",
            )
            right = store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([0.0, 1.0], dtype=np.float32)] * 5,
                person_id="P-001",
                display_name="Alice",
                palm_side="right",
            )
            replacement = store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([0.5, 0.5], dtype=np.float32)] * 5,
                person_id="P-001",
                display_name="Alice Updated",
                palm_side="left",
            )

            self.assertEqual(first["palm_id"], "P-001:left")
            self.assertEqual(right["palm_id"], "P-001:right")
            self.assertEqual(replacement["replaced_samples"], 5)
            users = store.users("ccnet__npu__mixed_fp16")
            self.assertEqual([item["palm_id"] for item in users], ["P-001:left", "P-001:right"])
            self.assertEqual([item["samples"] for item in users], [5, 5])
            self.assertEqual(store.summary("ccnet__npu__mixed_fp16")["person_count"], 1)

    def test_invalid_replacement_keeps_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TemplateStore(Path(directory))
            store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([1.0, 0.0], dtype=np.float32)] * 5,
                person_id="P-001",
                display_name="Alice",
                palm_side="left",
            )
            with self.assertRaises(ValueError):
                store.replace_palm(
                    "ccnet__npu__mixed_fp16",
                    [],
                    person_id="P-001",
                    display_name="Alice",
                    palm_side="left",
                )
            self.assertEqual(store.users("ccnet__npu__mixed_fp16")[0]["samples"], 5)

    def test_roi_pipeline_isolation_excludes_old_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TemplateStore(Path(directory))
            store.replace_palm(
                "ccnet__npu__mixed_fp16",
                [np.array([1.0, 0.0], dtype=np.float32)],
                person_id="P-001",
                display_name="Alice",
                palm_side="left",
                roi_pipeline_id="next_pipeline",
            )
            self.assertEqual(store.users("ccnet__npu__mixed_fp16"), [])
            self.assertEqual(store.summary("ccnet__npu__mixed_fp16")["palm_count"], 0)

    def test_combined_store_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateStore(root)
            store.enroll(
                "ccnet",
                [np.array([1.0, 0.0], dtype=np.float32)] * 3,
                "Alice",
                "left",
            )

            path = root / "ccnet.pstore"
            self.assertTrue(path.is_file())
            codes, metadata = store.load("ccnet")
            self.assertEqual(codes.shape, (3, 2))
            self.assertEqual(metadata[0]["user_name"], "Alice")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_manual_test_store_ignores_encryption_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_path = root / "template.key"
            key_path.write_bytes(b"k" * 32)
            if os.name != "nt":
                os.chmod(key_path, 0o600)
            store = TemplateStore(root / "templates", key_file=key_path, require_encryption=True)
            store.enroll(
                "ccnet",
                [np.array([1.0, 0.0], dtype=np.float32)] * 3,
                "Alice",
                "left",
            )

            payload = (root / "templates" / "ccnet.pstore").read_bytes()
            self.assertTrue(payload.startswith(TemplateStore._PLAIN_MAGIC))
            self.assertIn(b"Alice", payload)
            codes, metadata = store.load("ccnet")
            self.assertEqual(codes.shape, (3, 2))
            self.assertEqual(metadata[0]["user_name"], "Alice")

    def test_readiness_reports_plaintext_manual_test_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateStore(root, require_encryption=True, defer_key_error=True)
            status = store.readiness()
            self.assertTrue(status["ready"])
            self.assertFalse(status["encryption_required"])
            self.assertFalse(status["key_configured"])

            (root / "ccnet.npz").write_bytes(b"legacy")
            status = store.readiness()
            self.assertTrue(status["legacy_plaintext_present"])
            self.assertTrue(status["ready"])

    def test_corrupt_active_generation_recovers_last_good_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateStore(root)
            store.enroll(
                "ccnet",
                [np.array([1.0, 0.0], dtype=np.float32)],
                "Alice",
                "left",
                user_id="alice",
            )
            store.enroll(
                "ccnet",
                [np.array([0.0, 1.0], dtype=np.float32)],
                "Bob",
                "right",
                user_id="bob",
            )

            active = root / "ccnet.pstore"
            backup = root / "ccnet.pstore.bak"
            previous = root / "ccnet.pstore.previous"
            self.assertTrue(backup.is_file())
            self.assertTrue(previous.is_file())
            active.write_bytes(b"truncated generation")

            codes, metadata = store.load("ccnet")
            self.assertEqual(codes.shape, (2, 2))
            self.assertEqual(
                [item["user_id"] for item in metadata], ["alice", "bob"]
            )
            self.assertEqual(active.read_bytes(), backup.read_bytes())
            version = json.loads(
                (root / "ccnet.pstore.version.json").read_text(encoding="utf-8")
            )
            self.assertEqual(version["sha256"], hashlib.sha256(active.read_bytes()).hexdigest())

            # If the newest recovery copy is also damaged, the previous
            # generation remains available as a deliberate second-level
            # fallback.
            active.write_bytes(b"corrupt again")
            backup.write_bytes(b"corrupt backup")
            codes, metadata = store.load("ccnet")
            self.assertEqual([item["user_id"] for item in metadata], ["alice"])

    def test_concurrent_enrollment_is_serialized_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateStore(root)
            failures: list[BaseException] = []

            def enroll(index: int) -> None:
                try:
                    store.enroll(
                        "ccnet",
                        [np.array([float(index), 1.0], dtype=np.float32)],
                        f"User {index}",
                        "right",
                        user_id=f"user-{index}",
                    )
                except BaseException as exc:  # pragma: no cover - assertion below
                    failures.append(exc)

            threads = [threading.Thread(target=enroll, args=(index,)) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            codes, metadata = store.load("ccnet")
            self.assertEqual(codes.shape, (12, 2))
            self.assertEqual({item["user_id"] for item in metadata}, {
                f"user-{index}" for index in range(12)
            })
            version = json.loads(
                (root / "ccnet.pstore.version.json").read_text(encoding="utf-8")
            )
            self.assertEqual(version["generation"], 12)

    @unittest.skipUnless(os.name != "nt", "POSIX file locking is board-specific")
    def test_multiple_store_instances_use_transaction_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failures: list[BaseException] = []

            def enroll(index: int) -> None:
                try:
                    TemplateStore(root).enroll(
                        "ccnet",
                        [np.array([float(index), 1.0], dtype=np.float32)],
                        f"User {index}",
                        "right",
                        user_id=f"instance-{index}",
                    )
                except BaseException as exc:  # pragma: no cover - assertion below
                    failures.append(exc)

            threads = [threading.Thread(target=enroll, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            self.assertEqual(len(TemplateStore(root).users("ccnet")), 8)


if __name__ == "__main__":
    unittest.main()
