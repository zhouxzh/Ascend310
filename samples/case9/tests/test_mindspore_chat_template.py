from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
import unittest

from ascend_edge_chat.backends.mindspore_qwen import (
    MindSporeChatProvider,
    Qwen2MindSporeProvider,
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


if __name__ == "__main__":
    unittest.main()
