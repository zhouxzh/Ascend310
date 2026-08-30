from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from case9_model_profiles import (
    DEFAULT_REGISTRY_PATH,
    ProfileError,
    clear_active_state,
    load_profiles,
    public_profiles,
    read_active_state,
    resolve_safe_path,
    safe_relative_path,
    write_active_state,
)


class ChatModelProfileTests(unittest.TestCase):
    def test_checked_in_registry_contains_three_ordered_profiles(self) -> None:
        registry = load_profiles(DEFAULT_REGISTRY_PATH)
        self.assertEqual(
            [item.id for item in registry],
            [
                "qwen1.5-0.5b-mindspore",
                "tinyllama-1.1b-mindspore",
                "deepseek-r1-qwen-1.5b-mindspore",
            ],
        )
        self.assertEqual(registry.get("qwen1.5-0.5b-mindspore").board_soc, "Ascend310B4")
        self.assertEqual(registry.get("deepseek-r1-qwen-1.5b-mindspore").board_tier, "20T")

    def test_profile_limits_and_artifacts_are_strict(self) -> None:
        registry = load_profiles()
        for profile in registry:
            self.assertEqual(profile.context_length, 1024)
            self.assertEqual(profile.default_max_tokens, 32)
            self.assertEqual(profile.max_tokens, 80)
            self.assertEqual(profile.temperature, 0.0)
            self.assertEqual(profile.top_p, 1.0)
            self.assertGreaterEqual(len(profile.artifacts), 3)
            self.assertTrue(all("/" not in artifact.filename and "\\" not in artifact.filename for artifact in profile.artifacts))

    def test_non_finite_numeric_runtime_values_are_rejected(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["runtime"]["temperature"] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            # json.dumps emits NaN by default; load_profiles must reject it
            # instead of allowing it through range comparisons.
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

    def test_public_view_contains_no_paths_urls_or_hashes(self) -> None:
        items = public_profiles()
        self.assertEqual(len(items), 3)
        forbidden = {"cache_dir", "artifacts", "url", "sha256", "expected_bytes", "notes"}
        for item in items:
            self.assertTrue(forbidden.isdisjoint(item))
            self.assertIn("status", item)
            self.assertIn("admission_reason", item)

    def test_unknown_keys_and_duplicate_json_keys_are_rejected(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

            path.write_text('{"schema_version":1,"schema_version":1,"profiles":[]}', encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

    def test_path_helpers_reject_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(safe_relative_path("models/a.bin").as_posix(), "models/a.bin")
            for value in ("../outside", "models/../outside", "C:/outside", "models\\outside", ""):
                with self.assertRaises(ProfileError):
                    safe_relative_path(value)
            with self.assertRaises(ProfileError):
                safe_relative_path(None)  # type: ignore[arg-type]
            with self.assertRaises(ProfileError):
                resolve_safe_path(root.parent / "outside", root)

            target = root / "target"
            target.write_text("x", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            with self.assertRaises(ProfileError):
                resolve_safe_path("link", root)

            with self.assertRaises(ProfileError):
                # Explicit state locations are allowed, but public state APIs
                # may not traverse a symlink before touching them.
                write_active_state(link, "qwen1.5-0.5b-mindspore")

    def test_active_state_is_atomic_validated_and_clearable(self) -> None:
        registry = load_profiles()
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "run" / "active-model.json"
            state = write_active_state(
                state_path,
                registry.profiles[0].id,
                registry=registry,
                worker_pid=123,
                cache_cleared=True,
            )
            loaded = read_active_state(state_path, registry=registry)
            self.assertEqual(loaded, state)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema_version"], 1)
            self.assertTrue(clear_active_state(state_path))
            self.assertIsNone(read_active_state(state_path))
            self.assertFalse(clear_active_state(state_path))

    def test_relative_cli_paths_work_on_windows_and_posix(self) -> None:
        # The CLI receives a Path after argparse normalization on Windows;
        # validating its POSIX representation must not reject a safe nested
        # state path.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "run" / "mindspore-chat" / "active-model.json"
            write_active_state(
                state_path,
                "qwen1.5-0.5b-mindspore",
                registry=load_profiles(),
                worker_pid=123,
                cache_cleared=True,
            )
            self.assertEqual(read_active_state(state_path).worker_pid, 123)

    def test_deepseek_revision_is_pinned_while_profile_stays_blocked(self) -> None:
        profile = load_profiles().get("deepseek-r1-qwen-1.5b-mindspore")
        self.assertTrue(profile.revision_pinned)
        self.assertEqual(profile.revision, "0a28897fe71fdd30de350b667ae588601a85990f")
        self.assertEqual(profile.status, "blocked")
        self.assertFalse(profile.admission_eligible)

    def test_tinyllama_failed_quality_gate_is_blocked(self) -> None:
        profile = load_profiles().get("tinyllama-1.1b-mindspore")
        self.assertEqual(profile.status, "blocked")
        self.assertFalse(profile.admission_eligible)


if __name__ == "__main__":
    unittest.main()
