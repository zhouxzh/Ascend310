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
    quality_admission_check,
    read_active_state,
    resolve_safe_path,
    safe_relative_path,
    write_active_state,
)


class ChatModelProfileTests(unittest.TestCase):
    @staticmethod
    def _admitted_registry_source() -> dict:
        """Return a complete schema-v2 fixture with explicit quality approval."""

        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        profile = source["profiles"][0]
        profile["status"] = "admitted"
        profile["admission"] = {
            "eligible": True,
            "reason": "human quality review approved",
        }
        profile["quality"] = {
            "reviewed": True,
            "human_review": "approved",
            "human_passed_count": 8,
            "human_sample_count": 10,
            "languages": {"zh": "passed", "en": "passed"},
        }
        for record in profile["validation"].values():
            record["status"] = "admitted"
            record["quality"] = {
                "reviewed": True,
                "human_review": "approved",
                "human_passed_count": 8,
                "human_sample_count": 10,
                "languages": {"zh": "passed", "en": "passed"},
            }
        return source

    def test_quality_admission_requires_explicit_human_approval_and_eight_of_ten(self) -> None:
        self.assertEqual(
            quality_admission_check(
                {
                    "reviewed": True,
                    "human_review": "pending",
                    "human_passed_count": 10,
                    "human_sample_count": 10,
                    "languages": {"zh": "passed"},
                },
                languages=("zh",),
            )[0],
            False,
        )
        approved, reason = quality_admission_check(
            {
                "reviewed": True,
                "human_review": "approved",
                "human_passed_count": 7,
                "human_sample_count": 10,
                "languages": {"zh": "passed"},
            },
            languages=("zh",),
        )
        self.assertFalse(approved)
        self.assertIn("8 passed", reason)

    def test_schema_v2_rejects_admitted_without_human_quality_evidence(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        profile = source["profiles"][0]
        profile["status"] = "admitted"
        profile["admission"]["eligible"] = True
        for record in profile["validation"].values():
            record["status"] = "admitted"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "human quality"):
                load_profiles(path)

    def test_schema_v2_accepts_admitted_only_with_approved_quality(self) -> None:
        source = self._admitted_registry_source()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            profile = load_profiles(path).profiles[0]
            self.assertEqual(profile.status, "admitted")
            self.assertTrue(profile.admission_eligible)
            self.assertEqual(profile.quality_admission_for_soc("Ascend310B4"), (True, ""))
            public = profile.to_public_dict()
            self.assertTrue(public["admitted"])
            self.assertTrue(public["switchable"])
    def test_checked_in_registry_contains_native_and_conditional_profiles(self) -> None:
        registry = load_profiles(DEFAULT_REGISTRY_PATH)
        expected_ids = {
            "qwen1.5-0.5b-mindspore",
            "tinyllama-1.1b-mindspore",
            "deepseek-r1-qwen-1.5b-mindspore",
            "qwen2.5-0.5b-mindspore",
            "qwen2.5-1.5b-mindspore",
            "qwen3-0.6b-mindspore",
            "qwen3-1.7b-mindspore",
            "minicpm3-4b-mindspore",
            "openpangu-embedded-1b-conditional",
            "ee-model-1.5b-conditional",
            "telechat-1b-conditional",
            "bitcpm-cann-1b-conditional",
            "minicpm5-1b-ascend-conditional",
            "minicpm4-0.5b-conditional",
            "minimind3-64m-conditional",
            "haidass-143m-conditional",
            "bloomz-560m-research-only",
            "qwen2-0.5b-instruct-mindspore",
            "qwen2-1.5b-instruct-mindspore",
            "qwen2-math-1.5b-instruct-mindspore",
            "qwen2.5-coder-0.5b-instruct-mindspore",
            "qwen2.5-math-1.5b-instruct-mindspore",
            "llama3.2-1b-instruct-mindspore",
        }
        self.assertEqual({item.id for item in registry}, expected_ids)
        self.assertEqual(len(registry), len(expected_ids))
        ids = [item.id for item in registry]
        self.assertEqual(ids[:8], [
            "qwen1.5-0.5b-mindspore",
            "tinyllama-1.1b-mindspore",
            "deepseek-r1-qwen-1.5b-mindspore",
            "qwen2.5-0.5b-mindspore",
            "qwen2.5-1.5b-mindspore",
            "qwen3-0.6b-mindspore",
            "qwen3-1.7b-mindspore",
            "minicpm3-4b-mindspore",
        ])
        self.assertEqual(sum(not item.is_conditional for item in registry), 8)
        self.assertEqual(sum(item.is_conditional for item in registry), 15)
        expected_socs = {"Ascend310B4", "Ascend310B1"}
        for item in registry:
            self.assertEqual({target["soc"] for target in item.board_targets}, expected_socs)
            self.assertEqual(set(item.validation), expected_socs)
        self.assertEqual(registry.get("qwen1.5-0.5b-mindspore").board_soc, "Ascend310B4")
        qwen15 = registry.get("qwen1.5-0.5b-mindspore")
        self.assertEqual(qwen15.status_for_soc("Ascend310B4"), "blocked")
        self.assertEqual(qwen15.status_for_soc("Ascend310B1"), "experimental_dirty_base")
        self.assertEqual(registry.get("deepseek-r1-qwen-1.5b-mindspore").board_tier, "20T")
        self.assertTrue(registry.get("qwen2.5-0.5b-mindspore").supports_soc("Ascend310B1"))
        self.assertEqual(registry.get("minicpm3-4b-mindspore").status_for_soc("Ascend310B1"), "not-run")

    def test_profile_limits_and_artifacts_are_strict(self) -> None:
        registry = load_profiles()
        for profile in registry:
            self.assertEqual(profile.context_length, 1024)
            self.assertEqual(profile.default_max_tokens, 32)
            self.assertEqual(profile.max_tokens, 64)
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
        self.assertEqual(len(items), 23)
        forbidden = {"cache_dir", "artifacts", "url", "sha256", "expected_bytes", "notes"}
        for item in items:
            self.assertTrue(forbidden.isdisjoint(item))
            self.assertIn("status", item)
            self.assertIn("admission_reason", item)
            self.assertIn("candidate_kind", item)
            self.assertIn("board_targets", item)
            self.assertIn("validation", item)
        conditional = next(item for item in items if item["id"] == "openpangu-embedded-1b-conditional")
        self.assertTrue(conditional["conditional"])
        self.assertFalse(conditional["switchable"])
        deepseek = next(item for item in items if item["id"] == "deepseek-r1-qwen-1.5b-mindspore")
        self.assertFalse(deepseek["switchable"])
        self.assertTrue(deepseek["switchable_by_soc"]["Ascend310B4"])
        self.assertFalse(deepseek["switchable_by_soc"]["Ascend310B1"])
        qwen15 = next(item for item in items if item["id"] == "qwen1.5-0.5b-mindspore")
        self.assertFalse(qwen15["switchable"])
        self.assertFalse(qwen15["switchable_by_soc"]["Ascend310B4"])
        self.assertTrue(qwen15["switchable_by_soc"]["Ascend310B1"])

    def test_schema_v2_validation_is_explicit_for_each_board(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        required = {
            "status", "reason", "report_paths", "updated_at", "run_id",
            "soc", "tier", "environment_fingerprint", "artifact_hashes",
            "quality", "performance", "metrics",
        }
        optional = {"notes"}
        for profile in source["profiles"]:
            targets = {item["soc"] for item in profile["board_targets"]}
            self.assertEqual(set(profile["validation"]), targets)
            for soc, record in profile["validation"].items():
                self.assertTrue(required.issubset(set(record)), (profile["id"], soc))
                self.assertTrue(set(record).issubset(required | optional), (profile["id"], soc))
                if "notes" in record:
                    self.assertIsInstance(record["notes"], str, (profile["id"], soc))

    def test_schema_v2_rejects_malformed_validation_timestamp_or_soc(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["validation"]["Ascend310B4"]["updated_at"] = "yesterday"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["validation"]["Ascend310B4"]["soc"] = "Ascend310B1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

    def test_schema_v2_rejects_terminal_validation_without_evidence(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        record = source["profiles"][0]["validation"]["Ascend310B4"]
        record["status"] = "experimental_dirty_base"
        record["report_paths"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profiles(path)

    def test_schema_v2_requires_all_candidate_metadata_fields(self) -> None:
        required_fields = {
            "license",
            "candidate_kind",
            "architecture",
            "languages",
            "weight_format",
            "board_targets",
            "validation",
            "quality",
            "performance",
        }
        for field_name in sorted(required_fields):
            source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
            source["profiles"][0].pop(field_name)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profiles.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                with self.assertRaisesRegex(ProfileError, field_name):
                    load_profiles(path)

    def test_schema_v2_requires_both_supported_board_socs(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        profile = source["profiles"][0]
        profile["board_targets"] = [profile["board_targets"][0]]
        profile["validation"] = {
            "Ascend310B4": profile["validation"]["Ascend310B4"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "Ascend310B1"):
                load_profiles(path)

    def test_schema_v2_rejects_case_insensitive_duplicate_board_soc(self) -> None:
        source = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["board_targets"][1]["soc"] = "Ascend310b4"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "duplicate SoC"):
                load_profiles(path)

    def test_public_view_exposes_quality_label_and_block_reason(self) -> None:
        items = {item["id"]: item for item in public_profiles()}
        self.assertEqual(items["qwen1.5-0.5b-mindspore"]["quality_status"], "质量待审核")
        self.assertEqual(items["tinyllama-1.1b-mindspore"]["quality_status"], "质量未通过")
        self.assertTrue(items["tinyllama-1.1b-mindspore"]["blocked_reason"])

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
