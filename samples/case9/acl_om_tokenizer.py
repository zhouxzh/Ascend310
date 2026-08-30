"""Torch-free Qwen tokenizer and chat-template helper.

The board receives a ``tokenizer.json`` produced by the model publisher.  The
Rust ``tokenizers`` package is loaded lazily so importing the service on a
controller does not require any ML runtime.  Qwen 1.5 uses a deliberately
small, documented chat template; implementing that template here avoids the
Transformers dependency (and its optional Torch integration).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union


class TokenizerError(ValueError):
    """Raised when the board tokenizer cannot satisfy the Qwen contract."""


QWEN_SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>")


class QwenTokenizer:
    """Minimal Qwen 1.5 tokenizer facade using ``tokenizers.Tokenizer``."""

    def __init__(
        self,
        tokenizer_path: Union[str, Path],
        config_path: Optional[Union[str, Path]] = None,
    ):
        path = Path(tokenizer_path).expanduser()
        if not path.is_file():
            raise TokenizerError(f"tokenizer file does not exist: {path}")
        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as exc:
            raise TokenizerError(
                "the no-Torch runtime requires the Rust 'tokenizers' package"
            ) from exc
        try:
            self._tokenizer = Tokenizer.from_file(str(path))
        except Exception as exc:
            raise TokenizerError(f"cannot load tokenizer: {path}") from exc

        self.tokenizer_path = path
        self.config_path = Path(config_path).expanduser() if config_path else None
        self.chat_template = self._read_chat_template(self.config_path)
        self.im_start_id = self._token_id(QWEN_SPECIAL_TOKENS[0])
        self.im_end_id = self._token_id(QWEN_SPECIAL_TOKENS[1])
        if self.im_start_id is None or self.im_end_id is None:
            raise TokenizerError(
                "tokenizer must define <|im_start|> and <|im_end|> special tokens"
            )

    def _token_id(self, token: str) -> Optional[int]:
        value = self._tokenizer.token_to_id(token)
        return int(value) if value is not None else None

    @staticmethod
    def _read_chat_template(path: Optional[Path]) -> Optional[str]:
        if path is None or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TokenizerError(f"cannot read tokenizer config: {path}") from exc
        template = raw.get("chat_template") if isinstance(raw, Mapping) else None
        if template is not None and not isinstance(template, str):
            raise TokenizerError("tokenizer_config.chat_template must be a string")
        return template

    @property
    def vocab_size(self) -> int:
        return int(self._tokenizer.get_vocab_size(with_added_tokens=True))

    def encode_messages(self, messages: Sequence[Mapping[str, Any]]) -> List[int]:
        """Render Qwen's role-delimited template and return token IDs.

        We intentionally support only the Qwen 1.5 template shape.  A model
        with a different template must be exported and inspected as a separate
        candidate rather than silently receiving a malformed prompt.
        """
        if not messages:
            raise TokenizerError("at least one message is required")
        rendered: List[str] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise TokenizerError(f"message {index} must be an object")
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"}:
                raise TokenizerError(f"unsupported message role at index {index}")
            if not isinstance(content, str) or not content:
                raise TokenizerError(f"message {index} content must be non-empty text")
            rendered.append(
                "<|im_start|>" + role + "\n" + content + "<|im_end|>\n"
            )
        rendered.append("<|im_start|>assistant\n")
        prompt = "".join(rendered)
        try:
            encoded = self._tokenizer.encode(prompt, add_special_tokens=False)
            ids = [int(value) for value in encoded.ids]
        except Exception as exc:
            raise TokenizerError("Qwen prompt tokenization failed") from exc
        if not ids:
            raise TokenizerError("Qwen prompt produced no token IDs")
        return ids

    def decode(self, token_ids: Iterable[int]) -> str:
        try:
            return self._tokenizer.decode(
                [int(token_id) for token_id in token_ids], skip_special_tokens=True
            )
        except Exception as exc:
            raise TokenizerError("Qwen token decoding failed") from exc

    def encode_text(self, text: str) -> List[int]:
        if not isinstance(text, str) or not text:
            raise TokenizerError("text must be non-empty")
        try:
            return [int(value) for value in self._tokenizer.encode(text, add_special_tokens=False).ids]
        except Exception as exc:
            raise TokenizerError("text tokenization failed") from exc
