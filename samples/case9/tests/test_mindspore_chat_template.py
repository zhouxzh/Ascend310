from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import patch

from ascend_edge_chat.backends.mindspore_qwen import (
    HbmPreflightError,
    MindSporeChatProvider,
    Qwen2MindSporeProvider,
    _check_hbm_headroom,
    provider_class_for_profile,
)
from ascend_edge_chat.backends.model_profiles import load_profiles


class _TemplateTokenizer:
    vocab_size = 32

    def __init__(self) -> None:
        self.options = None

    def apply_chat_template(self, messages, **options):
        self.options = options
        return [[1, 2, 3]]


def _profile(template: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="qwen2-1.5b-instruct-mindspore",
        model_id="Qwen/Qwen2-1.5B-Instruct",
        revision="b139ca2b8d7ba579ed727ffcd7060075666a808b",
        mirror="https://hf-mirror.com",
        context_length=1024,
        chat_template=template,
    )


class MindSporeChatTemplateTests(unittest.TestCase):
    def test_profile_template_is_passed_to_tokenizer(self) -> None:
        tokenizer = _TemplateTokenizer()
        template = "{{ '<|im_start|>assistant\\n' }}"
        provider = MindSporeChatProvider(_profile(template), model=object(), tokenizer=tokenizer)

        self.assertEqual(provider._render_messages([{"role": "user", "content": "你好"}]), [[1, 2, 3]])
        self.assertEqual(tokenizer.options["chat_template"], template)
        self.assertTrue(tokenizer.options["add_generation_prompt"])

    def test_qwen2_profiles_ship_reviewed_chatml_template(self) -> None:
        profiles = {item.id: item for item in load_profiles("configs/tutorial-models.json")}
        for profile_id in (
            "qwen2-0.5b-instruct-mindspore",
            "qwen2-1.5b-instruct-mindspore",
            "qwen2.5-0.5b-mindspore",
            "qwen2.5-1.5b-mindspore",
        ):
            template = profiles[profile_id].chat_template
            self.assertIsNotNone(template)
            self.assertIn("<|im_start|>", template)
            self.assertIn("<|im_end|>", template)

    def test_qwen2_profile_cannot_fall_back_to_qwen15_template(self) -> None:
        self.assertIs(provider_class_for_profile(_profile()), Qwen2MindSporeProvider)
        provider = Qwen2MindSporeProvider(_profile(), model=object(), tokenizer=object())
        with self.assertRaisesRegex(Exception, "no usable chat template"):
            provider._render_messages([{"role": "user", "content": "你好"}])

    def test_hbm_preflight_records_20t_device_accounting_without_claiming_allocator_free(self) -> None:
        snapshot = """\
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
| 0       310B1                 | Alarm           | 0.0          70                15    / 15            |
| 0       0                     | NA              | 0            10000/ 23673                            |
"""
        with patch(
            "ascend_edge_chat.backends.mindspore_qwen._npu_smi_info",
            return_value=snapshot,
        ), patch.dict(
            "os.environ",
            {"ASCEND_EDGE_CHAT_MIN_ACCOUNTED_FREE_HBM_MB_20T": "8192"},
        ):
            report = _check_hbm_headroom("310B1", 0)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["tier"], "20T")
        self.assertEqual(report["accounted_free_mb"], 13673)
        self.assertFalse(report["allocator_free_observed"])
        self.assertIsNone(report["allocator_free_mb"])

    def test_hbm_preflight_rejects_8t_qwen_before_known_allocator_failure(self) -> None:
        snapshot = """\
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
| 0       310B4                 | Alarm           | 0.0          45                15    / 15            |
| 0       0                     | NA              | 0            2225 / 7545                             |
"""
        with patch(
            "ascend_edge_chat.backends.mindspore_qwen._npu_smi_info",
            return_value=snapshot,
        ), self.assertRaises(HbmPreflightError) as captured:
            _check_hbm_headroom("310B4", 0)

        self.assertEqual(captured.exception.details["code"], "hbm_accounted_headroom_insufficient")
        self.assertEqual(captured.exception.details["tier"], "8T")
        self.assertEqual(captured.exception.details["accounted_free_mb"], 5320)
        self.assertEqual(captured.exception.details["minimum_accounted_free_mb"], 6144)

    def test_hbm_preflight_rejects_low_20t_device_headroom_before_model_load(self) -> None:
        snapshot = """\
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
| 0       310B1                 | Alarm           | 0.0          70                15    / 15            |
| 0       0                     | NA              | 0            16000/ 23673                            |
"""
        with patch(
            "ascend_edge_chat.backends.mindspore_qwen._npu_smi_info",
            return_value=snapshot,
        ), patch.dict(
            "os.environ",
            {"ASCEND_EDGE_CHAT_MIN_ACCOUNTED_FREE_HBM_MB_20T": "8192"},
        ), self.assertRaises(HbmPreflightError) as captured:
            _check_hbm_headroom("310B1", 0)

        self.assertEqual(captured.exception.details["code"], "hbm_accounted_headroom_insufficient")
        self.assertEqual(captured.exception.details["tier"], "20T")
        self.assertEqual(captured.exception.details["accounted_free_mb"], 7673)


if __name__ == "__main__":
    unittest.main()
