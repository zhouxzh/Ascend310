import unittest
from pathlib import Path
from unittest import mock

from model_registry import load_candidates
from prepare_models import SERIAL_ATC_ENV, _atc_parallel_option, parser


class SerialAtcContractTests(unittest.TestCase):
    def test_atc_environment_is_serial(self):
        self.assertEqual(SERIAL_ATC_ENV["MAX_COMPILE_CORE_NUMBER"], "1")
        self.assertEqual(SERIAL_ATC_ENV["MULTI_THREAD_COMPILE"], "0")
        self.assertEqual(SERIAL_ATC_ENV["TBE_PARALLEL_COMPILER"], "0")
        self.assertEqual(SERIAL_ATC_ENV["TE_PARALLEL_COMPILER"], "1")
        self.assertEqual(SERIAL_ATC_ENV["ASCENDC_PAR_COMPILE_JOB"], "0")
        self.assertEqual(SERIAL_ATC_ENV["TILINGKEY_PAR_COMPILE"], "0")
        self.assertEqual(SERIAL_ATC_ENV["OMP_NUM_THREADS"], "1")

    def test_parallel_graph_flag_is_rejected(self):
        with self.assertRaises(SystemExit):
            parser().parse_args(
                [
                    "convert",
                    "--model",
                    "resnet50_feature__npu__mixed_fp16",
                    "--enable-graph-parallel",
                    "1",
                ]
            )

    def test_high_precision_is_the_default_atc_operator_policy(self):
        args = parser().parse_args(["convert", "--model", "resnet50_feature__npu__mixed_fp16"])
        self.assertEqual(args.op_select_implmode, "high_precision")

    def test_mobileclip_image_uses_admitted_c0_precision_strategy(self):
        records = {record.model_id: record for record in load_candidates()}
        record = records["mobileclip_s0__npu__mixed_fp16"]
        config = record.components["image"].atc_keep_dtype
        # C0 is the admitted empty whitelist.  The versioned file is consumed
        # by the isolated sweep; production metadata records the strategy.
        self.assertIsNone(config)
        self.assertEqual(record.components["image"].precision_mode, "allow_fp32_to_fp16")
        self.assertEqual(record.precision_strategy["candidate_id"], "C0")
        self.assertIsNone(record.components["text"].precision_mode)
        self.assertEqual(record.effective_precision_mode(record.components["text"]), "allow_fp32_to_fp16")

    def test_precision_override_is_explicit(self):
        args = parser().parse_args(
            ["convert", "--model", "resnet50_feature__npu__mixed_fp16", "--precision-mode", "force_fp32"]
        )
        self.assertEqual(args.precision_mode, "force_fp32")

    @mock.patch("prepare_models.shutil.which", return_value="/usr/bin/atc")
    @mock.patch("prepare_models.subprocess.run")
    def test_atc_83_uses_supported_serial_flag(self, run, _which):
        run.return_value = mock.Mock(stdout="--ac_parallel_enable  Enable engines\n")
        self.assertEqual(_atc_parallel_option(), ("--ac_parallel_enable=0", "ac_parallel_enable=0"))

    @mock.patch("prepare_models.shutil.which", return_value="/usr/bin/atc")
    @mock.patch("prepare_models.subprocess.run")
    def test_atc_80_keeps_legacy_serial_flag(self, run, _which):
        run.return_value = mock.Mock(stdout="--enable_graph_parallel  Enable graph parallel\n")
        self.assertEqual(_atc_parallel_option(), ("--enable_graph_parallel=0", "enable_graph_parallel=0"))


if __name__ == "__main__":
    unittest.main()
