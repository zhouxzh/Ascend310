from __future__ import annotations

import json
import hashlib

import pytest

from time_frequency_dashboard.rtl_sdr_run_report import (
    cu8_channel_statistics,
    main,
    summarize_rtl_sdr_run,
)


def _write_report(tmp_path):
    capture = tmp_path / "capture.cu8"
    capture.write_bytes(bytes([0, 255, 127, 128, 255, 0, 128, 127]))
    report = tmp_path / "inference.jsonl"
    records = [
        {
            "record_type": "run_metadata",
            "source": "rtl",
            "inference_backend": "NPU (Ascend 310B)",
            "capture_file": "capture.cu8",
            "rf_input_context": "antenna_connected",
            "center_frequency_hz": 100_000_000.0,
            "sample_rate_hz": 2_048_000.0,
            "batch_duration_ms": 2.0 / 2_048_000.0 * 1_000.0,
            "model_task": "iq_classification",
            "model_input_shape": [1, 2, 2],
        },
        {
            "record_type": "inference_batch",
            "sequence": 0,
            "source_sample_offset": 0,
            "input_shape": [1, 2, 2],
            "backend": "NPU (Ascend 310B)",
            "npu_inference_ms": 1.0,
            "post_capture_pipeline_ms": 2.0,
            "end_to_end_ms": 3.0,
            "queue_dropped_batches": 0,
            "detections": [{"label": "bpsk"}],
        },
        {
            "record_type": "inference_batch",
            "sequence": 1,
            "source_sample_offset": 2,
            "input_shape": [1, 2, 2],
            "backend": "NPU (Ascend 310B)",
            "npu_inference_ms": 3.0,
            "post_capture_pipeline_ms": 4.0,
            "end_to_end_ms": 5.0,
            "queue_dropped_batches": 0,
            "detections": [{"label": "qpsk"}, {"label": "bpsk"}],
        },
        {
            "record_type": "run_summary",
            "completion_status": "completed",
            "completed_batches": 2,
            "produced_batches": 2,
            "queue_dropped_batches": 0,
            "archive_failed_batches": 0,
            "inference_backend": "NPU (Ascend 310B)",
            "capture_bytes": capture.stat().st_size,
            "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        },
    ]
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return report, capture


def test_cu8_channel_statistics_reports_dc_and_clipping(tmp_path):
    _, capture = _write_report(tmp_path)
    summary = cu8_channel_statistics(capture, chunk_bytes=2)

    assert summary["capture_bytes"] == 8
    assert summary["complex_samples"] == 4
    assert summary["i"]["clip_fraction"] == pytest.approx(0.5)
    assert summary["q"]["clip_fraction"] == pytest.approx(0.5)
    assert summary["i"]["mean_cu8"] == pytest.approx(127.5)
    assert summary["q"]["mean_cu8"] == pytest.approx(127.5)


def test_summarize_rtl_sdr_run_uses_recorded_capture_and_timings(tmp_path):
    report, capture = _write_report(tmp_path)
    summary = summarize_rtl_sdr_run(report)

    assert summary["capture_path"] == str(capture.resolve())
    assert summary["capture_qc"]["capture_sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
    assert summary["batches"] == 2
    assert summary["timing"]["npu_inference_ms"]["p50_ms"] == pytest.approx(2.0)
    assert summary["timing"]["npu_inference_ms"]["samples"] == 2
    assert summary["timing"]["npu_inference_ms"]["missing_rows"] == 0
    assert summary["timing"]["post_capture_pipeline_ms"]["max_ms"] == pytest.approx(4.0)
    assert summary["detection_label_counts"] == {"bpsk": 2, "qpsk": 1}


def test_summarize_rtl_sdr_run_allows_validated_batch_shape_when_metadata_shape_is_missing(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[0].pop("model_input_shape")
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    assert summarize_rtl_sdr_run(report)["batches"] == 2


def test_summarize_rtl_sdr_run_validates_declared_complete_window_capture_plan(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    metadata, footer = records[0], records[-1]
    metadata.update(
        {
            "capture_plan_policy": "ceil_requested_duration_to_complete_fixed_windows_v1",
            "requested_duration_seconds": 3.5 / 2_048_000.0,
            "requested_capture_samples": 4,
            "planned_capture_duration_seconds": 4.0 / 2_048_000.0,
            "planned_capture_samples": 4,
            "planned_capture_batches": 2,
        }
    )
    footer.update(
        {
            "capture_plan_policy": "ceil_requested_duration_to_complete_fixed_windows_v1",
            "requested_duration_seconds": 3.5 / 2_048_000.0,
            "planned_capture_duration_seconds": 4.0 / 2_048_000.0,
            "planned_capture_samples": 4,
            "planned_capture_batches": 2,
        }
    )
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    assert summarize_rtl_sdr_run(report)["batches"] == 2

    footer["planned_capture_samples"] = 2
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match run_metadata"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_odd_footer_capture_bytes(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[-1]["capture_bytes"] = 7
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="must be even"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize(
    ("record_index", "field", "value", "message"),
    [
        (2, "sequence", 0, "sequence values must be strictly increasing"),
        (1, "sequence", 2, "equal sequence times"),
        (2, "sequence", True, "sequence must be a non-negative integer"),
        (2, "source_sample_offset", 0, "source_sample_offset values must be strictly increasing"),
        (2, "source_sample_offset", True, "source_sample_offset must be a non-negative integer"),
    ],
)
def test_summarize_rtl_sdr_run_rejects_invalid_batch_sequence_or_offset(
    tmp_path, record_index, field, value, message
):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[record_index][field] = value
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_out_of_order_or_overlapping_iq_offsets(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[1]["source_sample_offset"] = 1
    records[2]["source_sample_offset"] = 0
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="source_sample_offset"):
        summarize_rtl_sdr_run(report)

    records[1]["source_sample_offset"] = 0
    records[2]["source_sample_offset"] = 1
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    with pytest.raises(ValueError, match="equal sequence times"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_iq_offset_that_does_not_match_sequence(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[1]["source_sample_offset"] = 1
    records[2]["source_sample_offset"] = 3
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="equal sequence times"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_iq_window_beyond_cu8_capture(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    # Keep the sequence-to-window relation valid while making the archived
    # capture too short for the final fixed window.
    records[2]["sequence"] = 2
    records[2]["source_sample_offset"] = 4
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="extends beyond"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_non_rtl_or_incomplete_reports(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[0]["source"] = "synthetic"
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    with pytest.raises(ValueError, match="RTL-SDR"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("status", [None, "stopped", "failed"])
def test_summarize_rtl_sdr_run_rejects_noncompleted_run(tmp_path, status):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    if status is None:
        records[-1].pop("completion_status")
    else:
        records[-1]["completion_status"] = status
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="normally completed"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_zero_batch_report(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    report.write_text(
        json.dumps(records[0]) + "\n" + json.dumps(records[-1]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least one inference_batch"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("completed_batches", 1, "completed_batches does not match"),
        ("produced_batches", 3, "must equal"),
        ("queue_dropped_batches", 1, "must equal"),
        ("archive_failed_batches", 1, "archive failures"),
    ],
)
def test_summarize_rtl_sdr_run_rejects_inconsistent_completed_footer_counts(
    tmp_path, field, value, message
):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[-1][field] = value
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_completed_capture_with_unaccounted_windows(tmp_path):
    report, capture = _write_report(tmp_path)
    # Keep the JSONL rows superficially valid but append two unreported fixed
    # windows and place the second row at a non-produced sequence.
    capture.write_bytes(capture.read_bytes() + bytes([1, 2]) * 4)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[2]["sequence"] = 3
    records[2]["source_sample_offset"] = 6
    records[-1]["capture_bytes"] = capture.stat().st_size
    records[-1]["capture_sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="capture length|sequences"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("completion_status", ["stopped", "failed"])
def test_summarize_rtl_sdr_run_rejects_noncompleted_service_runs(tmp_path, completion_status):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[-1]["completion_status"] = completion_status
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="not a completed run"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("location", ["header", "batch", "footer"])
def test_summarize_rtl_sdr_run_rejects_non_npu_backend(tmp_path, location):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    index = {"header": 0, "batch": 1, "footer": -1}[location]
    field = "inference_backend" if location != "batch" else "backend"
    records[index][field] = "CPU"
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="Ascend 310B NPU backend"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_capture_not_bound_to_jsonl(tmp_path):
    report, _ = _write_report(tmp_path)
    other_capture = tmp_path / "other.cu8"
    other_capture.write_bytes(b"\x01\x02" * 4)

    with pytest.raises(ValueError, match="SHA256"):
        summarize_rtl_sdr_run(report, capture_path=other_capture)


@pytest.mark.parametrize("field", ["capture_bytes", "capture_sha256"])
def test_summarize_rtl_sdr_run_requires_footer_capture_binding(tmp_path, field):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[-1].pop(field)
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="must bind"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize(
    "field", ["npu_inference_ms", "post_capture_pipeline_ms", "end_to_end_ms"]
)
def test_summarize_rtl_sdr_run_requires_core_timing_on_every_batch(tmp_path, field):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[1].pop(field)
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match=f"required {field}"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("capture_file", [None, "", [], 0])
def test_summarize_rtl_sdr_run_requires_metadata_capture_binding(
    tmp_path, capture_file
):
    report, capture = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[0]["capture_file"] = capture_file
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata must bind"):
        summarize_rtl_sdr_run(report, capture_path=capture)


def test_summarize_rtl_sdr_run_rejects_duplicate_json_keys(tmp_path):
    report, _ = _write_report(tmp_path)
    records = report.read_text(encoding="utf-8").splitlines()
    duplicate_header = records[0].removesuffix("}") + ', "source": "rtl"}'
    report.write_text("\n".join([duplicate_header, *records[1:]]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_nonstandard_json_constants(tmp_path):
    report, _ = _write_report(tmp_path)
    raw = report.read_text(encoding="utf-8")
    report.write_text(raw.replace('"source": "rtl"', '"source": NaN', 1), encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard constant"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("record", ["[]", "1", '"not-an-object"'])
def test_summarize_rtl_sdr_run_rejects_non_object_jsonl_records(tmp_path, record):
    report, _ = _write_report(tmp_path)
    report.write_text(record + "\n" + report.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="must be an object"):
        summarize_rtl_sdr_run(report)


def test_summarize_rtl_sdr_run_rejects_malformed_detections(tmp_path):
    report, _ = _write_report(tmp_path)
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    records[1]["detections"] = {"label": "bpsk"}
    report.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    with pytest.raises(ValueError, match="detections must be a list"):
        summarize_rtl_sdr_run(report)


@pytest.mark.parametrize("target_name", ["inference.jsonl", "capture.cu8"])
def test_main_refuses_to_overwrite_run_inputs(monkeypatch, tmp_path, target_name):
    report, capture = _write_report(tmp_path)
    target = report if target_name == report.name else capture
    before = target.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "rtl_sdr_run_report",
            "--inference-jsonl",
            str(report),
            "--output",
            str(target),
        ],
    )

    with pytest.raises(ValueError, match="must not overwrite"):
        main()
    assert target.read_bytes() == before


def test_main_refuses_to_overwrite_an_existing_qc_output(monkeypatch, tmp_path):
    report, _ = _write_report(tmp_path)
    output = tmp_path / "existing_qc.json"
    output.write_bytes(b"keep-me")
    monkeypatch.setattr(
        "sys.argv",
        [
            "rtl_sdr_run_report",
            "--inference-jsonl",
            str(report),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        main()
    assert output.read_bytes() == b"keep-me"


def test_capture_only_mode_does_not_need_or_report_jsonl_timings(monkeypatch, tmp_path, capsys):
    _, capture = _write_report(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "rtl_sdr_run_report",
            "--capture-only",
            "--capture-cu8",
            str(capture),
        ],
    )

    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["capture_qc"]["complex_samples"] == 4
    assert "timing" not in summary
