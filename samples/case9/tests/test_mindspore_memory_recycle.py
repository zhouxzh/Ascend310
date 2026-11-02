from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from ascend_edge_chat.backends.mindspore_qwen import MindSporeChatProvider


def _provider(mindspore):
    provider = object.__new__(MindSporeChatProvider)
    provider._mindspore = mindspore
    provider._memory_recycle_status = "not_run"
    return provider


def test_recycle_runtime_memory_calls_available_public_hooks() -> None:
    recycle = Mock()
    empty_cache = Mock()
    provider = _provider(
        SimpleNamespace(ms_memory_recycle=recycle, hal=SimpleNamespace(empty_cache=empty_cache))
    )
    with patch("ascend_edge_chat.backends.mindspore_qwen.gc.collect") as collect:
        provider._recycle_runtime_memory()
    recycle.assert_called_once_with()
    empty_cache.assert_called_once_with()
    assert collect.call_count == 2
    assert provider._memory_recycle_status == "ms_memory_recycle+hal.empty_cache"


def test_recycle_runtime_memory_reports_unavailable_hooks() -> None:
    provider = _provider(SimpleNamespace())
    provider._recycle_runtime_memory()
    assert provider._memory_recycle_status == "unavailable"


def test_recycle_runtime_memory_without_framework_is_not_applicable() -> None:
    provider = _provider(None)
    provider._recycle_runtime_memory()
    assert provider._memory_recycle_status == "not_applicable"
