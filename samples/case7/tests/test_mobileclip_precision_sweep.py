import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from scripts import run_mobileclip_precision_sweep as sweep


class PrecisionSweepConfigTests(unittest.TestCase):
    def test_candidates_are_versioned_and_nested(self):
        configs = sweep.candidate_configs()
        self.assertEqual(tuple(configs), sweep.CANDIDATE_IDS)
        self.assertEqual(configs["C0"]["keep_dtype_nodes"], [])
        self.assertIn(
            "/image_encoder/model/network.5/proj/proj.0/lkb_reparam/Conv",
            configs["C1"]["keep_dtype_nodes"],
        )
        self.assertTrue(
            set(configs["C2"]["keep_dtype_nodes"]).issubset(
                set(configs["C4"]["keep_dtype_nodes"])
            )
        )
        for value in configs.values():
            expected = hashlib.sha256(Path(value["keep_dtype_path"]).read_bytes()).hexdigest()
            self.assertEqual(value["keep_dtype_sha256"], expected)
            self.assertEqual(value["keep_dtype_node_count"], len(value["keep_dtype_nodes"]))

    def test_serial_environment_disables_worker_pools(self):
        environment = sweep.serial_environment({"OMP_NUM_THREADS": "32", "PYTHONHASHSEED": "123"})
        self.assertEqual(environment["OMP_NUM_THREADS"], "1")
        self.assertEqual(environment["PYTHONHASHSEED"], "0")
        self.assertEqual(environment["MAX_COMPILE_CORE_NUMBER"], "1")
        self.assertEqual(environment["MULTI_THREAD_COMPILE"], "0")
        self.assertEqual(environment["TBE_PARALLEL_COMPILER"], "0")
        self.assertEqual(environment["TE_PARALLEL_COMPILER"], "1")
        self.assertEqual(environment["MAKEFLAGS"], "-j1")


class PrecisionSweepCommandTests(unittest.TestCase):
    def setUp(self):
        self.configs = sweep.candidate_configs()
        self.production_om = Path("models/om/mobileclip_s0_image.om")

    def test_c0_omits_keep_dtype_and_uses_isolated_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            command = sweep._conversion_command(
                "C0", Path(directory) / "C0", self.configs["C0"], "Ascend310B4", "python"
            )
            self.assertIn("--without-keep-dtype", command)
            self.assertNotIn("--keep-dtype-file", command)
            self.assertIn("--precision-mode", command)
            self.assertIn("allow_fp32_to_fp16", command)
            self.assertIn("--op-select-implmode", command)
            self.assertIn("high_precision_for_all", command)
            self.assertIn("--allow-low-memory-single-thread", command)
            # prepare_models.py appends --op_compiler_cache_mode=disable to
            # the ATC invocation; the sweep records that invariant separately.
            self.assertTrue(sweep.SERIAL_ENV["MAX_COMPILE_CORE_NUMBER"] == "1")

    def test_dry_run_never_invokes_subprocess_or_writes_om(self):
        with tempfile.TemporaryDirectory() as directory:
            result = sweep.run_conversion(
                "C1",
                Path(directory) / "C1",
                self.configs["C1"],
                self.production_om,
                dry_run=True,
            )
            self.assertEqual(result["status"], "dry_run")
            self.assertFalse(Path(result["om_path"]).exists())

    def test_existing_candidate_om_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate_dir = Path(directory) / "C1"
            om = candidate_dir / "om" / self.production_om.name
            om.parent.mkdir(parents=True)
            om.write_bytes(b"stale")
            result = sweep.run_conversion(
                "C1", candidate_dir, self.configs["C1"], self.production_om
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("reuse existing", result["error"])

    def test_production_paths_are_refused(self):
        with self.assertRaisesRegex(sweep.SweepError, "protected production"):
            sweep._assert_isolated(
                sweep.ROOT / "data" / "precision-candidate", self.production_om
            )


class PrecisionSweepSelectionTests(unittest.TestCase):
    def test_selects_fewer_nodes_then_faster_p50(self):
        candidates = {
            "C2": {
                "candidate_id": "C2",
                "keep_dtype_node_count": 7,
                "performance": {"p50_ms": 10},
                "passed": True,
            },
            "C3": {
                "candidate_id": "C3",
                "keep_dtype_node_count": 6,
                "performance": {"p50_ms": 20},
                "passed": True,
            },
        }
        self.assertEqual(sweep.choose_candidate(candidates), "C3")

    def test_no_candidate_pass_returns_none(self):
        self.assertIsNone(sweep.choose_candidate({"C1": {"passed": False}}))

    def test_fixture_records_sort_by_image_id(self):
        payload = {
            "records": [{"image_id": "b"}, {"image_id": "a"}, {"image_id": "c"}]
        }
        records, seeds = sweep.fixture_records(payload, 2)
        self.assertEqual([item["image_id"] for item in records], ["a", "b"])
        self.assertEqual(seeds, list(sweep.RANDOM_SEEDS))

    def test_fixture_count_is_fixed_for_production_cli(self):
        args = sweep.parser().parse_args(["--fixture-count", "32"])
        self.assertEqual(args.fixture_count, sweep.FIXTURE_IMAGE_COUNT)
        # main() applies the protocol gate before any board or filesystem work.
        self.assertEqual(sweep.main(["--fixture-count", "31"]), 2)

    def test_seed_fixture_metadata_declares_production_preprocess(self):
        import scripts.run_mobileclip_precision_sweep as module

        with mock.patch.object(module, "_onnx_session", return_value=(None, "stub")), mock.patch.object(
            module, "_onnx_output", side_effect=lambda _s, _n, value: value.reshape(-1)[:512]
        ), mock.patch.object(module, "_preprocess", side_effect=lambda _r, image: image.transpose(2, 0, 1)[None].astype("float32") / 255.0):
            record = mock.Mock()
            component = mock.Mock()
            component.input_shape = (1, 3, 4, 4)
            component.input_dtype = "float32"
            component.input_name = "image"
            record.components = {"image": component}
            with tempfile.TemporaryDirectory() as directory:
                fixture = module.write_fixture_references(
                    record, {"records": [{"image_id": "a", "path": "missing"}]}, Path(directory), fixture_count=0, seeds=(310,)
                )
                self.assertEqual(fixture["random_distribution"], "uint8_uniform_bgr_then_production_preprocess")
                self.assertEqual(fixture["random"][0]["kind"], "synthetic_image_seed")
                self.assertEqual(fixture["random"][0]["outside_production_range_fraction"], 0.0)

    def test_numerical_gate_counts_references_returned_by_helper(self):
        # The pipeline helper exposes ``references`` but does not need to add
        # a redundant count.  The sweep must derive it before applying its
        # fixed image-plus-seed fixture gate.
        import prepare_models

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "validation.json"
            references = [{"passed": True} for _ in range(36)]
            helper_result = {"passed": True, "references": references}
            fake_record = object()
            with mock.patch.object(
                prepare_models,
                "validate_candidate",
                return_value=helper_result,
            ):
                result = sweep.run_numerical_gate(
                    Path(directory) / "candidate.om",
                    report,
                    Path(directory),
                    record=fake_record,
                )
            self.assertTrue(result["passed"])
            self.assertEqual(result["reference_count"], 36)


if __name__ == "__main__":
    unittest.main()
