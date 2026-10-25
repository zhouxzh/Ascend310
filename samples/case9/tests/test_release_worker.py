from __future__ import annotations

from contextlib import redirect_stderr
import io
import os
import unittest
from unittest.mock import patch

from ascend_edge_chat.backends import mindspore_worker
from ascend_edge_chat.backends.mindspore_worker import MODEL_ID, RequestError, _parse_request, _prefix_delta
from ascend_edge_chat.backends.runtime_preflight import RuntimePreflightError


class ReleaseWorkerTests(unittest.TestCase):
    def test_worker_accepts_only_internal_release_model(self) -> None:
        request = _parse_request(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "你好"}],
                "max_tokens": 32,
            }
        )
        self.assertEqual(request.model, MODEL_ID)
        self.assertEqual(request.max_tokens, 32)
        extended = _parse_request(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "请继续"}],
                "max_tokens": 256,
            }
        )
        self.assertEqual(extended.max_tokens, 256)
        with self.assertRaises(RequestError):
            _parse_request({"model": "other", "messages": [{"role": "user", "content": "你好"}]})

    def test_prefix_delta_never_repeats_prior_text(self) -> None:
        self.assertEqual(_prefix_delta("你好", "你好世界"), "世界")
        self.assertEqual(_prefix_delta("你好", "你好"), "")

    def test_worker_rejects_runtime_drift_before_loading_a_profile(self) -> None:
        failure = RuntimePreflightError(
            {
                "status": "failed",
                "code": "runtime_lock_failed",
                "errors": [{"field": "mindspore", "expected": "2.4.10", "observed": "2.4.9"}],
            }
        )
        with patch.dict(os.environ, {"ASCEND_EDGE_CHAT_LAUNCHER_VERIFIED": "1"}, clear=False), patch.object(
            mindspore_worker, "validate_runtime_lock", side_effect=failure
        ) as preflight, self.assertRaises(SystemExit) as captured, redirect_stderr(io.StringIO()):
            mindspore_worker._main(["--profile", "qwen1.5-0.5b-chat"])

        self.assertEqual(captured.exception.code, 2)
        preflight.assert_called_once_with(strict_target=True)
