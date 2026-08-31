"""Focused tests for the controller-side MobileCLIP campaign aggregator."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Union

from scripts import aggregate_mobileclip_compatibility as aggregator


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class MobileclipCompatibilityAggregatorTests(unittest.TestCase):
    def _root(self, directory: str) -> Path:
        return Path(directory) / "campaign"

    def _environment(self, root: Path, role: str, soc: str) -> None:
        _write_json(
            root / "environment" / role / "environment.json",
            {
                "schema_version": 1,
                "role": role,
                "soc_detected": soc,
                "npu_model": soc,
                "runtime_role": role,
                "npu_smi": {
                    "software_version": "25.2.0",
                    "firmware_version_raw": "NA",
                },
                "cann": {"versions_detected": ["7.6.0.1.220:8.0.0"]},
                "driver_version_info": "Version=25.2.0",
                "firmware_version_raw": "NA",
                "production_mutation": False,
            },
        )

    def _conversion(self, root: Path, component: str) -> None:
        onnx = root / "artifacts" / "onnx" / f"mobileclip_s0_{component}.onnx"
        om = (
            root
            / "artifacts"
            / "om"
            / "8t-ascend310b4"
            / f"mobileclip_s0_{component}.om"
        )
        report = root / "artifacts" / "8t-310b4" / component / "atc-report.json"
        log = root / "stages" / "8t-310b4" / f"native-convert-{component}.log"
        policy = root / "environment" / "8t-310b4" / "compile-policy.txt"
        onnx.parent.mkdir(parents=True, exist_ok=True)
        om.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        policy.parent.mkdir(parents=True, exist_ok=True)
        onnx.write_bytes(b"fixed-onnx-" + component.encode("ascii"))
        om.write_bytes(b"fixed-om-" + component.encode("ascii"))
        report.write_text("{}\n", encoding="utf-8")
        shape = "image:1,3,256,256" if component == "image" else "text:1,77"
        log.write_text(
            "atc --model=mobileclip_s0_%s.onnx --framework=5 "
            "--soc_version=Ascend310B4 --input_shape=%s "
            "--precision_mode=allow_fp32_to_fp16 "
            "--op_select_implmode=high_precision_for_all "
            "--enable_graph_parallel=0 --op_compiler_cache_mode=disable\n"
            % (component, shape),
            encoding="utf-8",
        )
        policy.write_text(
            "policy=serial-no-cache-no-swap\n"
            "MAX_COMPILE_CORE_NUMBER=1\nMULTI_THREAD_COMPILE=0\n"
            "TBE_PARALLEL_COMPILER=0\n"
            "atc_op_compiler_cache_mode=disable\ncpu_fallback=false\n",
            encoding="utf-8",
        )
        om_sha = hashlib.sha256(om.read_bytes()).hexdigest()
        report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
        _write_json(
            root / "artifacts" / "8t-310b4" / component / "conversion_result.json",
            {
                "schema_version": 1,
                "component": component,
                "compiler_role": "8t-310b4",
                "status": "passed",
                "exit_code": 0,
                "production_mutation": False,
                "om": {
                    "path": str(om),
                    "exists": True,
                    "size": om.stat().st_size,
                    "sha256": om_sha,
                },
                "report": {
                    "path": str(report),
                    "exists": True,
                    "sha256": report_sha,
                },
                "atc_command": [
                    "atc",
                    f"--model=mobileclip_s0_{component}.onnx",
                    "--framework=5",
                    "--soc_version=Ascend310B4",
                    f"--input_shape={shape}",
                    "--precision_mode=allow_fp32_to_fp16",
                    "--op_select_implmode=high_precision_for_all",
                    "--enable_graph_parallel=0",
                    "--op_compiler_cache_mode=disable",
                ],
            },
        )

    def _cell(
        self,
        root: Path,
        component: str,
        compiler: str,
        runtime: str,
        *,
        status: str = "passed",
        classification: Optional[str] = None,
    ) -> None:
        value = {
            "schema_version": 1,
            "component": component,
            "compiler_role": compiler,
            "runtime_role": runtime,
            "status": status,
            "sample_count": aggregator.EXPECTED_SAMPLE_COUNTS[component],
            "passed_count": aggregator.EXPECTED_SAMPLE_COUNTS[component]
            if status == "passed"
            else 0,
            "min_cosine": 0.9999 if status == "passed" else None,
            "max_cosine": 1.0 if status == "passed" else None,
            "threshold": aggregator.NUMERICAL_THRESHOLD,
            "fixture_expected": aggregator.EXPECTED_SAMPLE_COUNTS[component],
            "production_mutation": False,
        }
        if status == "passed":
            shape = [1, 3, 256, 256] if component == "image" else [1, 77]
            value["references"] = [
                {
                    "passed": True,
                    "finite": True,
                    "output_dim": 512,
                    "input_shape": shape,
                    "cosine_similarity": 0.9999,
                }
                for _ in range(aggregator.EXPECTED_SAMPLE_COUNTS[component])
            ]
        if classification is not None:
            value["classification"] = classification
        _write_json(
            root
            / "cells"
            / component
            / f"{compiler}-on-{runtime}"
            / "result.json",
            value,
        )

    def _manifest(self, root: Path) -> None:
        artifact = root / "artifact.bin"
        artifact.write_bytes(b"fixture")
        files = [artifact]
        files.extend((root / "artifacts" / "onnx").glob("*.onnx"))
        files.extend((root / "artifacts" / "om" / "8t-ascend310b4").glob("*.om"))
        _write_json(
            root / "artifact_manifest.json",
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "path": str(path.relative_to(root)).replace("\\", "/"),
                        "size_bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in files
                ],
            },
        )

    def _complete_campaign(self, root: Path) -> None:
        self._environment(root, "8t-310b4", "Ascend310B4")
        self._environment(root, "20t-310b1", "Ascend310B1")
        self._conversion(root, "image")
        self._conversion(root, "text")
        for component in aggregator.EXPECTED_COMPONENTS:
            for compiler, runtime in aggregator.EXPECTED_CELLS:
                self._cell(root, component, compiler, runtime)
        self._manifest(root)

    def test_complete_matrix_and_hash_manifest_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            result = aggregator.aggregate(root)
            self.assertTrue(result["complete"])
            self.assertFalse(result["production_mutation"])
            self.assertEqual(result["missing_cells"], [])
            self.assertEqual(result["artifact_hash_errors"], [])
            self.assertEqual(
                result["matrix"]["image"]["8t-310b4-om-on-20t-310b1"]["status"],
                "passed",
            )

    def test_partial_campaign_uses_explicit_not_run_and_strict_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            root.mkdir(parents=True)
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["missing_cells"]), 8)
            self.assertEqual(
                result["matrix"]["text"]["20t-310b1-om-on-8t-310b4"]["status"],
                "not_run",
            )
            output = root / "compatibility_matrix.json"
            self.assertEqual(
                aggregator.main(["--campaign-root", str(root), "--strict"]),
                2,
            )
            self.assertTrue(output.is_file())

    def test_stage_failure_classification_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            root.mkdir(parents=True)
            _write_json(
                root / "summary.json",
                {"soc_version": "Ascend310B4"},
            )
            _write_json(
                root / "stages" / "validate-image.json",
                {
                    "stage_kind": "validation",
                    "component": "image",
                    "status": "failed",
                    "classification": "load_rejected",
                    "artifact_label": "20t-310b1",
                    "runtime_label": "8t-310b4",
                    "exit_code": 1,
                },
            )
            result = aggregator.aggregate(root)
            cell = result["matrix"]["image"]["20t-310b1-om-on-8t-310b4"]
            self.assertEqual(cell["status"], "load_rejected")
            self.assertEqual(cell["status_normalization"], "inferred from failure_class/classification")

    def test_runner_stage_and_stable_cell_are_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            root.mkdir(parents=True)
            self._environment(root, "8t-310b4", "Ascend310B4")
            _write_json(
                root / "stages" / "validate-image.json",
                {
                    "schema_version": 1,
                    "stage_kind": "validation",
                    "component": "image",
                    "status": "passed",
                    "classification": "passed",
                    "compiler_role": "8t-310b4",
                    "runtime_role": "8t-310b4",
                    "sample_count": 36,
                    "passed_count": 36,
                    "min_cosine": 0.999,
                    "max_cosine": 1.0,
                },
            )
            self._cell(root, "image", "8t-310b4", "8t-310b4")
            result = aggregator.aggregate(root)
            cell = result["matrix"]["image"]["8t-310b4-om-on-8t-310b4"]
            self.assertEqual(cell["evidence_path"].replace("\\", "/"),
                             "cells/image/8t-310b4-on-8t-310b4/result.json")

    def test_production_mutation_and_bad_hash_block_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            # Change one cell declaration after the fixture was assembled.
            cell_path = root / "cells" / "image" / "8t-310b4-on-8t-310b4" / "result.json"
            cell = json.loads(cell_path.read_text(encoding="utf-8"))
            cell["production_mutation"] = True
            cell_path.write_text(json.dumps(cell), encoding="utf-8")
            manifest_path = root / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertTrue(any("production_mutation=true" in error for error in result["errors"]))
            self.assertTrue(result["artifact_hash_errors"])

    def test_manifest_path_escape_is_reported_without_reading_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            root.mkdir(parents=True)
            _write_json(
                root / "artifact_manifest.json",
                {"artifacts": [{"path": "../outside.bin", "sha256": "0" * 64}]},
            )
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertTrue(any("escapes campaign root" in error for error in result["artifact_hash_errors"]))

    def test_passed_cell_requires_complete_sample_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            cell_path = root / "cells" / "image" / "8t-310b4-on-8t-310b4" / "result.json"
            cell = json.loads(cell_path.read_text(encoding="utf-8"))
            cell.pop("references")
            cell_path.write_text(json.dumps(cell), encoding="utf-8")
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertTrue(any("missing references" in error for error in result["errors"]))

    def test_missing_artifact_manifest_blocks_strict_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            (root / "artifact_manifest.json").unlink()
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertIn("missing artifact_manifest.json", result["errors"])

    def test_environment_role_must_match_observed_soc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            environment = root / "environment" / "20t-310b1" / "environment.json"
            value = json.loads(environment.read_text(encoding="utf-8"))
            value["soc_detected"] = "Ascend310B4"
            environment.write_text(json.dumps(value), encoding="utf-8")
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertTrue(any("conflicts with observed SoC" in error for error in result["errors"]))

    def test_conversion_requires_atc_log_and_artifact_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            self._complete_campaign(root)
            (root / "stages" / "8t-310b4" / "native-convert-image.log").unlink()
            result = aggregator.aggregate(root)
            self.assertFalse(result["complete"])
            self.assertTrue(any("missing local ATC log evidence for image" in error for error in result["errors"]))


class CampaignRunnerContractTests(unittest.TestCase):
    """Local checks for the board-only shell wrapper (no CANN is invoked)."""

    SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_mobileclip_cross_board_campaign.sh"

    @staticmethod
    def _posix_path(path: Union[Path, str]) -> str:
        """Translate a Windows path for the WSL bash shim used by CI."""
        value = str(path).replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            return "/mnt/" + value[0].lower() + value[2:]
        return value

    def test_runner_declares_serial_no_cache_and_acl_only_policy(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        for marker in (
            "set -euo pipefail",
            "MAX_COMPILE_CORE_NUMBER=1",
            "MULTI_THREAD_COMPILE=0",
            "TBE_PARALLEL_COMPILER=0",
            "TE_PARALLEL_COMPILER=1",
            "--framework=5",
            "--precision_mode=allow_fp32_to_fp16",
            "--op_select_implmode=high_precision_for_all",
            "--enable_graph_parallel=0",
            "--op_compiler_cache_mode=disable",
            "validate-candidate",
            "cpu_fallback=false",
            "__sample-*.npz",
            "__seed-*.npz",
            "__query-*.npz",
            "reference staging collision",
            "onnx_size",
            "artifact_manifest.json",
            "soc_for_role",
            "refusing cross-board ACL validation",
        ):
            self.assertIn(marker, text)
        for forbidden in ("rsync", "scp", "ssh ", "--delete"):
            self.assertNotIn(forbidden, text)

    def test_dry_run_does_not_create_campaign_root(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not installed on the controller")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "campaign"
            result = subprocess.run(
                [
                    bash,
                    self._posix_path(self.SCRIPT),
                    "--dry-run",
                    "--mode",
                    "preflight",
                    "--campaign-root",
                    self._posix_path(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(root.exists())
            self.assertIn("serial_atc=1", result.stdout)

    def test_dry_run_rejects_production_and_cell_path_escape(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not installed on the controller")
        production = subprocess.run(
            [
                bash,
                self._posix_path(self.SCRIPT),
                "--dry-run",
                "--campaign-root",
                "/home/HwHiAiUser/Documents/ai-album",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(production.returncode, 0)
        self.assertIn("production", production.stderr.lower())
        with tempfile.TemporaryDirectory() as directory:
            escaped = subprocess.run(
                [
                    bash,
                    self._posix_path(self.SCRIPT),
                    "--dry-run",
                    "--campaign-root",
                    self._posix_path(Path(directory) / "campaign"),
                    "--cell-id",
                    "../outside",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(escaped.returncode, 0)
            self.assertIn("cell-id", escaped.stderr.lower())


if __name__ == "__main__":
    unittest.main()
