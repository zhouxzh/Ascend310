import pytest

from time_frequency_dashboard.benchmark_volk_npu import (
    aggregate_repeat_timings,
    timing_summary,
    validate_volk_implementation_record,
)


def test_aggregate_repeat_timings_uses_all_measurements():
    repeats = [
        {"timings_ms": [1.0, 2.0], "process_cpu_ms": 3.0},
        {"timings_ms": [100.0, 101.0], "process_cpu_ms": 4.0},
    ]
    actual = aggregate_repeat_timings(repeats)
    expected = timing_summary([1.0, 2.0, 100.0, 101.0], 7.0)

    assert actual == expected
    assert actual["p95_ms"] == pytest.approx(100.85)


def test_aggregate_repeat_timings_requires_raw_measurements():
    with pytest.raises(ValueError, match="no timings"):
        aggregate_repeat_timings([{"process_cpu_ms": 1.0}])


def test_volk_implementation_record_rejects_unproven_manual_kernel():
    with pytest.raises(RuntimeError, match="does not prove"):
        validate_volk_implementation_record(
            {"available_implementations": ["generic"]}, implementation="neon"
        )

    validate_volk_implementation_record(
        {"available_implementations": ["generic", "neon"]}, implementation="neon"
    )
    validate_volk_implementation_record(
        {"dispatcher_machine": "neon", "available_implementations": ["generic"]},
        implementation="dispatcher",
    )
