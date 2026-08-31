import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import prepare_models
from model_registry import load_candidates


MODEL_ID = "mobileclip_s0__npu__mixed_fp16"


class CandidateConversionTests(unittest.TestCase):
    def setUp(self):
        self.record = next(record for record in load_candidates() if record.model_id == MODEL_ID)

    def test_parser_accepts_isolated_candidate_paths(self):
        args = prepare_models.parser().parse_args(
            [
                "convert",
                "--model",
                MODEL_ID,
                "--component",
                "image",
                "--output-om-dir",
                "reports/precision_sweep/c1/om",
                "--keep-dtype-file",
                "atc_configs/mobileclip_s0_image_keep_dtype.cfg",
                "--report-dir",
                "reports/precision_sweep/c1",
            ]
        )
        self.assertEqual(args.output_om_dir, "reports/precision_sweep/c1/om")
        self.assertEqual(args.keep_dtype_file, "atc_configs/mobileclip_s0_image_keep_dtype.cfg")
        self.assertEqual(args.report_dir, "reports/precision_sweep/c1")

    def test_parser_accepts_alternate_onnx_path(self):
        args = prepare_models.parser().parse_args(
            [
                "convert",
                "--model",
                MODEL_ID,
                "--component",
                "image",
                "--onnx-path",
                "models/onnx/group-rewrite.onnx",
            ]
        )
        self.assertEqual(args.onnx_path, "models/onnx/group-rewrite.onnx")

    def test_explicit_canonical_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("prepare_models.shutil.which", return_value="atc"), mock.patch(
                "prepare_models._atc_parallel_option", return_value=(None, "default-disabled")
            ), mock.patch("prepare_models._host_memory_snapshot", return_value=None):
                with self.assertRaisesRegex(prepare_models.PipelineError, "canonical OM"):
                    prepare_models.convert(
                        [self.record],
                        "Ascend310B4",
                        component_kind="image",
                        output_om_dir=prepare_models.OM_DIR,
                        report_dir=Path(temporary) / "reports",
                    )

    def test_candidate_report_cannot_use_production_report_tree(self):
        with self.assertRaisesRegex(prepare_models.PipelineError, "production model report directory"):
            prepare_models.convert(
                [self.record],
                "Ascend310B4",
                component_kind="image",
                output_om_dir="reports/precision_sweep/canonical-check/om",
                report_dir=prepare_models.REPORT_DIR / "candidate-overwrite",
            )

    def test_candidate_conversion_records_isolated_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "om"
            report_dir = root / "reports"
            keep_dtype = root / "candidate.cfg"
            keep_dtype.write_text("/image_encoder/model/network.5/proj/proj.0/lkb_reparam/Conv\n")

            def fake_run(command, cwd=None, env=None, log_path=None):
                output = next(value for value in command if value.startswith("--output="))
                Path(output.split("=", 1)[1] + ".om").write_bytes(b"candidate-om")
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).write_text("synthetic ATC success\n")

            with mock.patch("prepare_models.shutil.which", return_value="atc"), mock.patch(
                "prepare_models._atc_parallel_option", return_value=(None, "default-disabled")
            ), mock.patch("prepare_models._host_memory_snapshot", return_value=None), mock.patch(
                "prepare_models._atc_lock", return_value=contextlib.nullcontext()
            ), mock.patch("prepare_models._run", side_effect=fake_run):
                prepare_models.convert(
                    [self.record],
                    "Ascend310B4",
                    component_kind="image",
                    precision_mode="allow_fp32_to_fp16",
                    output_om_dir=output_dir,
                    keep_dtype_file=keep_dtype,
                    report_dir=report_dir,
                )

            candidate = output_dir / self.record.components["image"].om_path.name
            report = json.loads((report_dir / "atc_conversion.json").read_text(encoding="utf-8"))
            component = report["models"][MODEL_ID]["components"]["image"]
            self.assertTrue(candidate.is_file())
            self.assertNotEqual(candidate.resolve(), self.record.components["image"].om_path.resolve())
            self.assertEqual(Path(component["output_om"]).name, candidate.name)
            self.assertEqual(Path(component["log"]).name, f"{MODEL_ID}__image.log")
            self.assertEqual(Path(component["keep_dtype"]).name, "candidate.cfg")

    def test_alternate_onnx_is_recorded_and_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "rewritten.onnx"
            source.write_bytes(b"alternate-onnx")
            output_dir = root / "om"
            report_dir = root / "reports"

            def fake_run(command, cwd=None, env=None, log_path=None):
                self.assertIn(f"--model={source}", command)
                output = next(value for value in command if value.startswith("--output="))
                Path(output.split("=", 1)[1] + ".om").write_bytes(b"candidate-om")
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).write_text("synthetic ATC success\n")

            with mock.patch("prepare_models.shutil.which", return_value="atc"), mock.patch(
                "prepare_models._atc_parallel_option", return_value=(None, "default-disabled")
            ), mock.patch("prepare_models._host_memory_snapshot", return_value=None), mock.patch(
                "prepare_models._atc_lock", return_value=contextlib.nullcontext()
            ), mock.patch("prepare_models._run", side_effect=fake_run):
                prepare_models.convert(
                    [self.record],
                    "Ascend310B4",
                    component_kind="image",
                    precision_mode="allow_fp32_to_fp16",
                    output_om_dir=output_dir,
                    report_dir=report_dir,
                    onnx_path=source,
                    use_keep_dtype=False,
                )

            report = json.loads((report_dir / "atc_conversion.json").read_text(encoding="utf-8"))
            component = report["models"][MODEL_ID]["components"]["image"]
            self.assertEqual(Path(component["onnx"]).name, source.name)
            self.assertEqual(component["onnx_sha256"], prepare_models.sha256_file(source))


class CandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.record = next(record for record in load_candidates() if record.model_id == MODEL_ID)
        self.component = self.record.components["image"]

    def test_validation_writes_evidence_without_registry_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_om = root / "candidate.om"
            candidate_om.write_bytes(b"candidate")
            reference_dir = root / "references"
            reference_dir.mkdir()
            output = np.arange(self.record.embedding_dim, dtype=np.float32) + 1.0
            np.savez_compressed(
                reference_dir / f"{MODEL_ID}__image.npz",
                input=np.zeros(self.component.input_shape, dtype=np.float32),
                output=output,
            )
            report_path = root / "candidate_validation.json"
            registry_before = prepare_models.DEFAULT_REGISTRY.read_bytes()

            class FakeResource:
                def release(self):
                    pass

            class FakeModel:
                def __init__(self, resource, path):
                    self.path = Path(path)

                def output_contracts(self):
                    return ({"size": self_record.embedding_dim * 4, "acl_dtype": 0},)

                def execute(self, values):
                    return [self_output.astype(np.float32).tobytes()]

                def release(self):
                    pass

            self_record = self.record
            self_output = output
            with mock.patch("prepare_models.AscendResource", FakeResource), mock.patch(
                "prepare_models.AclModel", FakeModel
            ):
                result = prepare_models.validate_candidate(
                    [self.record],
                    component_kind="image",
                    om_path=candidate_om,
                    report_path=report_path,
                    reference_dir=reference_dir,
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(result["passed"])
            self.assertTrue(report["passed"])
            self.assertEqual(report["candidate_om_sha256"], prepare_models.sha256_file(candidate_om))
            self.assertEqual(len(report["references"]), 1)
            self.assertTrue(report["references"][0]["passed"])
            self.assertEqual(registry_before, prepare_models.DEFAULT_REGISTRY.read_bytes())

    def test_parser_requires_candidate_report_and_om(self):
        with self.assertRaises(SystemExit):
            prepare_models.parser().parse_args(
                ["validate-candidate", "--model", MODEL_ID, "--component", "image"]
            )


if __name__ == "__main__":
    unittest.main()
