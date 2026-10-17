from __future__ import annotations

import unittest

from ascend_edge_chat.backends.mindspore_worker import MODEL_ID, RequestError, _parse_request, _prefix_delta


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
        with self.assertRaises(RequestError):
            _parse_request({"model": "other", "messages": [{"role": "user", "content": "你好"}]})

    def test_prefix_delta_never_repeats_prior_text(self) -> None:
        self.assertEqual(_prefix_delta("你好", "你好世界"), "世界")
        self.assertEqual(_prefix_delta("你好", "你好"), "")
