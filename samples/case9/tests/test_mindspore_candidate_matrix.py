from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mindspore_candidate_matrix.py"


def load_module():
    spec = importlib.util.spec_from_file_location("mindspore_candidate_matrix_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load candidate matrix helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATRIX = load_module()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


_FIXTURE_REVISION = "1" * 40
_FIXTURE_SHA256 = "0" * 64


def _validation_record(soc: str, tier: str, status: str) -> dict:
    complete = status in {"experimental_dirty_base", "admitted"}
    return {
        "status": status,
        "reason": "fixture evidence" if complete else "fixture has not run",
        "report_paths": ["reports/fixture/acceptance.json"] if complete else [],
        "updated_at": "2026-09-02T00:00:00Z" if complete else None,
        "run_id": "fixture-run" if complete else None,
        "soc": soc,
        "tier": tier,
        "environment_fingerprint": _FIXTURE_SHA256 if complete else None,
        "artifact_hashes": {"model": _FIXTURE_SHA256} if complete else {},
        "quality": {"human_review": "pending"} if complete else {},
        "performance": {"total_ms": 1} if complete else {},
        "metrics": {"machine_gates_passed": 9, "machine_gates_total": 9} if complete else {},
    }


def _v2_profile(profile_id: str, display_name: str, model_id: str, *, conditional: bool = False) -> dict:
    targets = [
        {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
        {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
    ]
    status = "blocked" if conditional else "experimental_dirty_base"
    validation_status = "blocked" if conditional else "experimental_dirty_base"
    return {
        "id": profile_id,
        "display_name": display_name,
        "model_id": model_id,
        "repository": model_id,
        "source": "fixture",
        "license": "Apache-2.0",
        "revision": _FIXTURE_REVISION,
        "tokenizer_revision": _FIXTURE_REVISION,
        "revision_pinned": True,
        "mirror": None,
        "board": targets[0],
        "runtime": {
            "provider": "mindspore",
            "context_length": 1024,
            "default_max_tokens": 32,
            "max_tokens": 64,
            "temperature": 0.0,
            "top_p": 1.0,
        },
        "cache_dir": "artifacts/fixture",
        "artifacts": [{
            "name": "model",
            "kind": "weights",
            "filename": "model.bin",
            "url": "https://example.invalid/model.bin",
            "expected_bytes": 1,
            "sha256": _FIXTURE_SHA256,
        }],
        "status": status,
        "admission": {"eligible": False, "reason": "fixture is experimental"},
        "notes": "fixture profile",
        "candidate_kind": "conditional" if conditional else "native_mindspore",
        "architecture": "causal_lm",
        "languages": ["en"],
        "weight_format": "safetensors",
        "board_targets": targets,
        "validation": {
            "Ascend310B4": _validation_record("Ascend310B4", "8T", validation_status),
            "Ascend310B1": _validation_record("Ascend310B1", "20T", validation_status),
        },
        "quality": {"reviewed": False},
        "performance": {},
    }


def registry_payload() -> dict:
    return {
        "schema_version": 2,
        "profiles": [
            _v2_profile("fixture-native", "Fixture Native", "fixture/native"),
            _v2_profile("fixture-conditional", "Fixture Conditional", "fixture/conditional", conditional=True),
        ],
    }


def legacy_registry_payload() -> dict:
    """Schema v1 fixture for a deliberately single-board target."""

    profile = _v2_profile("fixture-20t-only", "Fixture 20T Only", "fixture/20t-only")
    profile["board"] = {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"}
    profile["status"] = "not-run"
    base_keys = {
        "id", "display_name", "model_id", "repository", "source", "revision",
        "tokenizer_revision", "revision_pinned", "mirror", "board", "runtime",
        "cache_dir", "artifacts", "status", "admission", "notes",
    }
    return {
        "schema_version": 1,
        "profiles": [{key: profile[key] for key in base_keys}],
    }


def passing_acceptance(soc: str = "Ascend310B4", timestamp: str = "2026-09-02T00:00:00Z") -> dict:
    return {
        "recorded_at_utc": timestamp,
        "run_id": timestamp.replace(":", ""),
        "profile": {
            "id": "fixture-native",
            "board_soc": soc,
            "board_tier": "8T" if soc == "Ascend310B4" else "20T",
        },
        "service": {"base_url": "http://127.0.0.1:8090"},
        "gates": {
            "health": True,
            "models": True,
            "json": True,
            "sse": True,
            "errors": True,
            "protocol": True,
            "long_output": True,
            "stability": True,
            "quality_machine": True,
            "performance": True,
        },
        "quality": {"human_review": "pending"},
        "status": "passed",
    }


class CandidateMatrixTests(unittest.TestCase):
    def test_board_aliases_are_explicit_and_ordered(self) -> None:
        self.assertEqual(MATRIX.normalize_board("192.168.1.90")["soc"], "Ascend310B4")
        self.assertEqual(MATRIX.normalize_board("192.168.11.14")["host"], "192.168.1.90")
        self.assertEqual(MATRIX.normalize_board("192.168.8.178")["host"], "192.168.1.90")
        self.assertEqual(MATRIX.normalize_board("20T")["key"], "board20t")
        selected = MATRIX.select_boards("20t,8t,20t")
        self.assertEqual([item["key"] for item in selected], ["board8t", "board20t"])
        with self.assertRaises(MATRIX.MatrixError):
            MATRIX.normalize_board("127.0.0.1")
        self.assertEqual(
            MATRIX._board_from_mapping({"npu_model": "Ascend310B4 / 8T"})["key"],
            "board8t",
        )

    def test_acceptance_coverage_distinguishes_quality_from_required_machine_gates(self) -> None:
        coverage = MATRIX.acceptance_coverage()
        self.assertEqual(coverage["status"], "available")
        self.assertTrue(coverage["implemented"]["G6_quality"])
        self.assertFalse(coverage["required"]["G6_quality"])
        self.assertNotIn("quality_machine", coverage["required_gates"])

    def test_string_gate_values_are_normalized(self) -> None:
        gates = MATRIX._acceptance_gates({
            "gates": {
                "health": "passed",
                "models": "passed",
                "json": "passed",
                "sse": "passed",
                "errors": "passed",
                "protocol": "passed",
                "long_output": "passed",
                "stability": "passed",
                "quality_machine": "passed",
                "performance": "passed",
            },
            "quality": {"human_review": "reviewed"},
        })
        self.assertEqual(gates["G2_load"], "passed")
        self.assertEqual(gates["G3_api"], "passed")
        self.assertEqual(gates["G6_quality"]["human"], "passed")

    def test_machine_quality_failure_blocks_experimental_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            report = passing_acceptance()
            report["gates"]["quality_machine"] = False
            path = reports / "board8t" / "fixture-native" / "acceptance.json"
            write_json(path, report)
            write_json(path.parent / "environment.json", {
                "status": "passed",
                "profile_id": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            })
            write_json(path.parent / "artifact-verification.json", {
                "status": "passed",
                "artifact_verified": True,
                "profile": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            })
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("8t"),
            )
            row = payload["rows"][0]
            self.assertEqual(row["gates"]["G6_quality"]["machine"], "failed")
            self.assertFalse(row["technical_pass"])
            self.assertFalse(row["experimental_switchable"])

    def test_matrix_aggregates_gates_and_keeps_conditional_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            acceptance = reports / "board8t" / "fixture-native" / "new" / "acceptance.json"
            write_json(acceptance, passing_acceptance())
            write_json(acceptance.parent / "environment.json", {
                "status": "passed",
                "profile_id": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            })
            write_json(acceptance.parent / "artifact-verification.json", {
                "status": "passed",
                "artifact_verified": True,
                "profile": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            })
            write_json(acceptance.parent / "chain-summary.json", {"status": "passed"})
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native", "fixture-conditional"},
                MATRIX.select_boards("both"),
            )
            rows = {(row["profile"], row["board"]["key"]): row for row in payload["rows"]}
            native = rows[("fixture-native", "board8t")]
            self.assertEqual(native["gates"]["G0_environment"], "passed")
            self.assertEqual(native["gates"]["G1_artifacts"], "passed")
            self.assertEqual(native["gates"]["G2_load"], "passed")
            self.assertEqual(native["gates"]["G3_api"], "passed")
            self.assertTrue(native["technical_pass"])
            self.assertTrue(native["experimental_switchable"])
            self.assertEqual(native["quality"]["human"], "pending")
            self.assertEqual(rows[("fixture-native", "board20t")]["overall_status"], "incomplete")
            conditional = rows[("fixture-conditional", "board8t")]
            self.assertEqual(conditional["overall_status"], "blocked")
            self.assertFalse(conditional["experimental_switchable"])

    def test_explicit_soc_wins_over_a_stale_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            path = reports / "board8t" / "fixture-native" / "acceptance.json"
            write_json(path, passing_acceptance("Ascend310B1"))
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("both"),
            )
            rows = {(row["profile"], row["board"]["key"]): row for row in payload["rows"]}
            self.assertIsNone(rows[("fixture-native", "board8t")]["selected_report"])
            self.assertEqual(rows[("fixture-native", "board20t")]["report_count"], 1)

    def test_unscoped_profile_report_is_rejected_for_every_board(self) -> None:
        """A profile-only report must not be reused as evidence for both boards."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            # Deliberately omit board_soc/tier/host and place the report below
            # a neutral directory, so there is no permissible identity hint.
            path = reports / "unscoped" / "fixture-native" / "acceptance.json"
            report = passing_acceptance()
            report["profile"] = {"id": "fixture-native"}
            write_json(path, report)

            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("both"),
            )
            rows = {(row["profile"], row["board"]["key"]): row for row in payload["rows"]}
            for board_key in ("board8t", "board20t"):
                row = rows[("fixture-native", board_key)]
                self.assertIsNone(row["selected_report"])
                self.assertEqual(row["report_count"], 0)
                self.assertEqual(row["rejected_reports"][0]["reason"], "missing_board_identity")

    def test_report_without_profile_identity_is_never_attributed_by_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            report = passing_acceptance()
            report.pop("profile")
            report["board"] = {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"}
            path = reports / "board8t" / "fixture-native" / "acceptance.json"
            write_json(path, report)
            payload = MATRIX.build_matrix(
                registry, reports, {"fixture-native"}, MATRIX.select_boards("8t")
            )
            row = payload["rows"][0]
            self.assertIsNone(row["selected_report"])
            self.assertEqual(row["rejected_reports"][0]["reason"], "missing_profile_identity")

    def test_g0_failure_stops_all_downstream_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            report_dir = reports / "board8t" / "fixture-native" / "run"
            write_json(report_dir / "acceptance.json", passing_acceptance())
            write_json(report_dir / "environment.json", {
                "status": "failed",
                "profile_id": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
                "failures": ["device reset"],
            })
            write_json(report_dir / "artifact-verification.json", {
                "status": "passed",
                "artifact_verified": True,
                "profile": "fixture-native",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            })
            payload = MATRIX.build_matrix(
                registry, reports, {"fixture-native"}, MATRIX.select_boards("8t")
            )
            row = payload["rows"][0]
            self.assertEqual(row["gates"]["G0_environment"], "failed")
            self.assertEqual(row["evidence"]["stopped_at"], "G0_environment")
            for gate in ("G2_load", "G3_api", "G4_long_output", "G5_stability", "G7_performance", "G8_candidate_chain"):
                self.assertEqual(row["gates"][gate], "not-run")
            self.assertEqual(row["gates"]["G6_quality"]["machine"], "not-run")

    def test_formal_port_report_is_rejected_and_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            write_json(registry, registry_payload())
            report = passing_acceptance()
            report["service"]["base_url"] = "http://127.0.0.1:8080/v1"
            path = reports / "board8t" / "fixture-native" / "acceptance.json"
            write_json(path, report)
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("8t"),
            )
            row = payload["rows"][0]
            self.assertIsNone(row["selected_report"])
            self.assertEqual(row["report_count"], 0)
            self.assertEqual(row["rejected_reports"][0]["formal_ports"], [8080])

    def test_non_target_board_remains_visible_as_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            write_json(registry, legacy_registry_payload())
            payload = MATRIX.build_matrix(
                registry,
                root / "reports",
                {"fixture-20t-only"},
                MATRIX.select_boards("both"),
            )
            rows = {(row["profile"], row["board"]["key"]): row for row in payload["rows"]}
            self.assertFalse(rows[("fixture-20t-only", "board8t")]["targeted"])
            self.assertEqual(rows[("fixture-20t-only", "board8t")]["overall_status"], "not-run")
            self.assertTrue(rows[("fixture-20t-only", "board20t")]["targeted"])

    def test_registry_blocked_status_prevents_implicit_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            document = registry_payload()
            document["profiles"][0]["status"] = "blocked"
            document["profiles"][0]["validation"]["Ascend310B4"]["status"] = "blocked"
            write_json(registry, document)
            path = reports / "board8t" / "fixture-native" / "acceptance.json"
            write_json(path, passing_acceptance())
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("8t"),
            )
            row = payload["rows"][0]
            self.assertEqual(row["overall_status"], "blocked")
            self.assertFalse(row["experimental_switchable"])

    def test_per_soc_status_is_independent_of_aggregate_b4_block(self) -> None:
        """A B4 denial must not erase an independently verified B1 row."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            document = registry_payload()
            document["profiles"][0]["status"] = "blocked"
            document["profiles"][0]["validation"]["Ascend310B4"]["status"] = "blocked"
            document["profiles"][0]["validation"]["Ascend310B1"]["status"] = "experimental_dirty_base"
            write_json(registry, document)
            report_dir = reports / "board20t" / "fixture-native" / "verified"
            write_json(report_dir / "acceptance.json", passing_acceptance("Ascend310B1"))
            write_json(report_dir / "environment.json", {
                "status": "passed",
                "profile_id": "fixture-native",
                "board": {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
            })
            write_json(report_dir / "artifact-verification.json", {
                "status": "passed",
                "artifact_verified": True,
                "profile": "fixture-native",
                "board": {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
            })
            write_json(report_dir / "chain-summary.json", {"status": "passed"})
            payload = MATRIX.build_matrix(
                registry,
                reports,
                {"fixture-native"},
                MATRIX.select_boards("20t"),
            )
            row = payload["rows"][0]
            self.assertEqual(row["registry_status"], "experimental_dirty_base")
            self.assertEqual(row["overall_status"], "passed")
            self.assertTrue(row["technical_pass"])
            self.assertTrue(row["experimental_switchable"])

    def test_registry_not_run_status_is_preserved_even_with_passing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            document = registry_payload()
            document["profiles"][0]["status"] = "not-run"
            document["profiles"][0]["validation"]["Ascend310B4"]["status"] = "not-run"
            write_json(registry, document)
            # A stale/all-passing acceptance file must not promote an
            # operator-controlled not-run row into a usable candidate.
            write_json(
                root / "reports" / "board8t" / "fixture-native" / "acceptance.json",
                passing_acceptance(),
            )
            payload = MATRIX.build_matrix(
                registry,
                root / "reports",
                {"fixture-native"},
                MATRIX.select_boards("8t"),
            )
            self.assertEqual(payload["rows"][0]["overall_status"], "not-run")
            self.assertFalse(payload["rows"][0]["experimental_switchable"])

    def test_admitted_row_requires_manual_quality_approval(self) -> None:
        """A stale schema-v1 admitted flag cannot bypass the quality guard."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            document = legacy_registry_payload()
            profile = document["profiles"][0]
            profile["status"] = "admitted"
            profile["admission"] = {
                "eligible": True,
                "reason": "operator flag",
            }
            write_json(registry, document)
            write_json(
                root / "reports" / "board20t" / "fixture-20t-only" / "acceptance.json",
                passing_acceptance("Ascend310B1"),
            )
            payload = MATRIX.build_matrix(
                registry,
                root / "reports",
                {"fixture-20t-only"},
                MATRIX.select_boards("20t"),
            )
            row = payload["rows"][0]
            self.assertEqual(row["registry_status"], "admitted")
            self.assertFalse(row["experimental_switchable"])
            self.assertFalse(row["admission_quality"]["approved"])
            self.assertIn("quality", row["admission_quality"]["reason"])

    def test_schema_v2_missing_profile_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = registry_payload()
            registry["profiles"][0].pop("languages")
            path = root / "registry.json"
            write_json(path, registry)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.build_matrix(path, root / "reports", None, MATRIX.select_boards("8t"))

    def test_schema_v2_requires_dual_board_validation_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = registry_payload()
            profile = registry["profiles"][0]
            profile["board_targets"] = profile["board_targets"][:1]
            profile["validation"].pop("Ascend310B1")
            path = root / "registry.json"
            write_json(path, registry)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.build_matrix(path, root / "reports", None, MATRIX.select_boards("8t"))

    def test_schema_v2_validation_record_shape_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = registry_payload()
            registry["profiles"][0]["validation"]["Ascend310B4"].pop("metrics")
            path = root / "registry.json"
            write_json(path, registry)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.build_matrix(path, root / "reports", None, MATRIX.select_boards("8t"))

    def test_cli_dry_run_never_creates_output_or_requires_reports_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "matrix.json"
            code = MATRIX.main([
                "--registry", str(ROOT / "configs" / "chat_model_profiles.json"),
                "--reports-root", str(Path(directory) / "missing"),
                "--profile", "qwen1.5-0.5b-mindspore",
                "--board", "8t",
                "--output", str(output),
                "--dry-run",
            ])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())

    def test_output_report_is_written_only_when_explicitly_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            reports = root / "reports"
            output = root / "out" / "matrix.json"
            write_json(registry, registry_payload())
            code = MATRIX.main([
                "--registry", str(registry),
                "--reports-root", str(reports),
                "--profile", "fixture-native",
                "--board", "8t",
                "--output", str(output),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(stored["row_count"], 1)
            self.assertTrue(stored["policy"]["formal_ports_untouched"])

    def test_registry_schema_and_candidate_kind_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            document = registry_payload()
            document["schema_version"] = 9
            write_json(registry, document)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.build_matrix(registry, root / "reports", None, MATRIX.select_boards("8t"))
            document["schema_version"] = 2
            document["profiles"][0]["candidate_kind"] = "torch"
            write_json(registry, document)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.build_matrix(registry, root / "reports", None, MATRIX.select_boards("8t"))


if __name__ == "__main__":
    unittest.main()
