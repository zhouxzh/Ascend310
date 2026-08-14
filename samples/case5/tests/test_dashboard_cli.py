from pathlib import Path

from time_frequency_dashboard.main import parse_args


def test_dashboard_cli_defaults_keep_runtime_artifacts_under_project_root():
    args = parse_args([])

    assert args.sessions == Path("data/hantek_sessions")
    assert args.sdr_models_dir == Path("models/generated/inference")
    assert args.sdr_output_root == Path("data/rtl_sdr_npu_inference")


def test_dashboard_cli_accepts_explicit_sdr_runtime_roots():
    args = parse_args(
        [
            "--sessions",
            "custom/hantek",
            "--sdr-models-dir",
            "custom/models",
            "--sdr-output-root",
            "custom/sdr",
            "--sdr-developer-sources",
        ]
    )

    assert args.sessions == Path("custom/hantek")
    assert args.sdr_models_dir == Path("custom/models")
    assert args.sdr_output_root == Path("custom/sdr")
    assert args.sdr_developer_sources is True
