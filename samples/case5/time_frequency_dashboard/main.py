"""CLI entry point for the Case 5 PySide6 dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import Case5Config
from .controller import Case5Controller
from .instrument_coordinator import InstrumentCoordinator
from .rtl_sdr_service import RtlSdrService


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--om",
        type=Path,
        default=Path("models/generated/npu_dft_1ms_10000_20khz.om"),
        help="NPU OM model; missing model remains visibly unavailable",
    )
    parser.add_argument(
        "--sigrok-bridge",
        type=Path,
        default=Path("build/sigrok_capture_bridge"),
        help="compiled libsigrok capture bridge",
    )
    parser.add_argument(
        "--sessions",
        type=Path,
        default=Path("data/hantek_sessions"),
        help="root directory for Hantek raw/analysis session artifacts",
    )
    parser.add_argument("--ch1-volts-div", type=float, default=1.0)
    parser.add_argument("--ch2-volts-div", type=float, default=0.25)
    parser.add_argument("--ch1-probe-ratio", type=float, default=1.0)
    parser.add_argument("--ch2-probe-ratio", type=float, default=1.0)
    parser.add_argument("--sigrok-callback-ms", type=int, default=40)
    parser.add_argument("--allow-simulation", action="store_true")
    parser.add_argument(
        "--sdr-models-dir",
        type=Path,
        default=Path("models/generated/inference"),
        help="directory scanned for accepted, hash-verified RTL-SDR manifests",
    )
    parser.add_argument(
        "--sdr-output-root",
        type=Path,
        default=Path("data/rtl_sdr_npu_inference"),
        help="root directory for RTL-SDR CU8 and JSONL run artifacts",
    )
    parser.add_argument(
        "--sdr-developer-sources",
        action="store_true",
        help="show CU8 replay and synthetic SDR inputs; not hardware acceptance",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    config = Case5Config(
        session_root=args.sessions,
        ch1_volts_per_division=args.ch1_volts_div,
        ch2_volts_per_division=args.ch2_volts_div,
        ch1_probe_ratio=args.ch1_probe_ratio,
        ch2_probe_ratio=args.ch2_probe_ratio,
        sigrok_callback_msec=args.sigrok_callback_ms,
    )
    config.validate()
    controller = Case5Controller(config, args.om)
    rtl_sdr_service = RtlSdrService()
    coordinator = InstrumentCoordinator(controller, rtl_sdr_service)
    from .ui.main_window import run_dashboard

    return run_dashboard(
        controller,
        args.sigrok_bridge,
        args.allow_simulation,
        rtl_sdr_service=rtl_sdr_service,
        coordinator=coordinator,
        sdr_models_dir=args.sdr_models_dir,
        sdr_output_root=args.sdr_output_root,
        sdr_developer_sources=args.sdr_developer_sources,
    )


if __name__ == "__main__":
    raise SystemExit(main())
