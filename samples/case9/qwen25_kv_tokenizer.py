"""Torch-free tokenizer and chat-template adapter for Qwen2.5.

Only the Rust ``tokenizers`` package is loaded, and it is imported lazily so
contract/service validation can run on a controller without ML frameworks.
The template is the no-tools branch published with Qwen2.5-Instruct; tool
calling is intentionally outside this first ACL service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union

from qwen25_kv_acl_contract import (
    QWEN25_BOS_TOKEN_ID,
    QWEN25_EOS_TOKEN_ID,
    QWEN25_PAD_TOKEN_ID,
    QWEN25_VOCABULARY_SIZE,
)


class TokenizerError(ValueError):
    """Raised when the Qwen2.5 tokenizer cannot satisfy the ACL contract."""


QWEN25_IM_START = "<|im_start|>"
QWEN25_IM_END = "<|im_end|>"
QWEN25_DEFAULT_SYSTEM_PROMPT = (
    "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
)


class Qwen25Tokenizer:
    """Small facade around ``tokenizers.Tokenizer`` with no Transformers import."""

    def __init__(
        self,
        tokenizer_path: Union[str, Path],
        config_path: Optional[Union[str, Path]] = None,
        *,
        implementation: Any = None,
        add_bos: Optional[bool] = None,
        # Qwen2.5 has 151,936 model rows but tokenizer.json exposes 151,665
        # regular/added entries; the remaining IDs are reserved model tokens.
        require_qwen_vocab: bool = False,
    ) -> None:
        path = Path(tokenizer_path).expanduser()
        if not path.is_file():
            raise TokenizerError(f"tokenizer file does not exist: {path}")
        if implementation is None:
            try:
                from tokenizers import Tokenizer  # type: ignore
            except ImportError as exc:
                raise TokenizerError(
                    "Qwen2.5 requires the Rust tokenizers package; do not install Torch"
                ) from exc
            try:
                implementation = Tokenizer.from_file(str(path))
            except Exception as exc:
                raise TokenizerError(f"cannot load tokenizer: {path}") from exc
        self._tokenizer = implementation
        self.tokenizer_path = path
        self.config_path = Path(config_path).expanduser() if config_path else None
        self.config = self._read_config(self.config_path)
        model_type = self.config.get("tokenizer_class")
        if model_type is not None and model_type not in {"Qwen2Tokenizer", "QwenTokenizer"}:
            raise TokenizerError(f"unsupported tokenizer class: {model_type!r}")

        self.im_start_id = self._token_id(QWEN25_IM_START)
        self.im_end_id = self._token_id(QWEN25_IM_END)
        if self.im_start_id is None or self.im_end_id is None:
            raise TokenizerError("tokenizer must define <|im_start|> and <|im_end|>")
        self.eos_token_id = self._configured_token_id(
            "eos_token", QWEN25_EOS_TOKEN_ID, fallback_token=QWEN25_IM_END
        )
        self.pad_token_id = self._configured_token_id(
            "pad_token", QWEN25_PAD_TOKEN_ID, fallback_token="<|endoftext|>"
        )
        configured_bos = self.config.get("bos_token")
        self.bos_token_id = (
            self._token_id(str(configured_bos)) if isinstance(configured_bos, str) else QWEN25_BOS_TOKEN_ID
        )
        default_add_bos = bool(self.config.get("add_bos_token", False))
        self.add_bos = default_add_bos if add_bos is None else bool(add_bos)
        if require_qwen_vocab and self.vocab_size != QWEN25_VOCABULARY_SIZE:
            raise TokenizerError(
                f"tokenizer vocabulary must be {QWEN25_VOCABULARY_SIZE}, got {self.vocab_size}"
            )
        if self.vocab_size > QWEN25_VOCABULARY_SIZE:
            raise TokenizerError(
                f"tokenizer vocabulary {self.vocab_size} exceeds model vocabulary {QWEN25_VOCABULARY_SIZE}"
            )
        self._validate_special_ids()

    @staticmethod
    def _read_config(path: Optional[Path]) -> Mapping[str, Any]:
        if path is None:
            return {}
        if not path.is_file():
            raise TokenizerError(f"tokenizer config does not exist: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TokenizerError(f"cannot read tokenizer config: {path}") from exc
        except json.JSONDecodeError as exc:
            raise TokenizerError(f"tokenizer config is not valid JSON: {path}") from exc
        if not isinstance(raw, Mapping):
            raise TokenizerError("tokenizer config must be a JSON object")
        return raw

    def _token_id(self, token: str) -> Optional[int]:
        getter = getattr(self._tokenizer, "token_to_id", None)
        if not callable(getter):
            raise TokenizerError("tokenizer implementation lacks token_to_id")
        try:
            value = getter(token)
        except Exception as exc:
            raise TokenizerError(f"cannot resolve special token {token!r}") from exc
        return int(value) if value is not None else None

    def _configured_token_id(
        self,
        config_key: str,
        fallback_id: int,
        *,
        fallback_token: str,
    ) -> int:
        configured = self.config.get(config_key)
        if isinstance(configured, str):
            value = self._token_id(configured)
            if value is not None:
                return value
        elif isinstance(configured, int) and not isinstance(configured, bool):
            return int(configured)
        value = self._token_id(fallback_token)
        return fallback_id if value is None else value

    def _validate_special_ids(self) -> None:
        for name, value in (
            ("im_start_id", self.im_start_id),
            ("im_end_id", self.im_end_id),
            ("eos_token_id", self.eos_token_id),
            ("pad_token_id", self.pad_token_id),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < self.vocab_size:
                raise TokenizerError(f"{name}={value!r} is outside tokenizer vocabulary")
        if self.bos_token_id is not None and (
            isinstance(self.bos_token_id, bool)
            or not isinstance(self.bos_token_id, int)
            or not 0 <= self.bos_token_id < self.vocab_size
        ):
            raise TokenizerError("bos_token_id is outside tokenizer vocabulary")

    @property
    def vocab_size(self) -> int:
        getter = getattr(self._tokenizer, "get_vocab_size", None)
        if not callable(getter):
            raise TokenizerError("tokenizer implementation lacks get_vocab_size")
        try:
            return int(getter(with_added_tokens=True))
        except TypeError:
            return int(getter())
        except Exception as exc:
            raise TokenizerError("cannot read tokenizer vocabulary size") from exc

    def encode_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        add_generation_prompt: bool = True,
        add_default_system: bool = True,
    ) -> List[int]:
        """Render supported roles using Qwen2.5's no-tools chat template."""
        if (
            not isinstance(messages, Sequence)
            or isinstance(messages, (str, bytes))
            or not messages
        ):
            raise TokenizerError("at least one message is required")
        normalized: List[Mapping[str, Any]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise TokenizerError(f"message {index} must be an object")
            role, content = message.get("role"), message.get("content")
            if role not in {"system", "user", "assistant"}:
                raise TokenizerError(f"unsupported message role at index {index}")
            if not isinstance(content, str) or not content:
                raise TokenizerError(f"message {index} content must be non-empty text")
            normalized.append({"role": role, "content": content})

        rendered: List[str] = []
        has_system = normalized[0].get("role") == "system"
        if add_default_system and not has_system:
            rendered.append(
                f"{QWEN25_IM_START}system\n{QWEN25_DEFAULT_SYSTEM_PROMPT}{QWEN25_IM_END}\n"
            )
        for message in normalized:
            rendered.append(
                f"{QWEN25_IM_START}{message['role']}\n{message['content']}"
                f"{QWEN25_IM_END}\n"
            )
        if add_generation_prompt:
            rendered.append(f"{QWEN25_IM_START}assistant\n")
        return self._encode("".join(rendered), add_bos=self.add_bos)

    def encode_text(self, text: str, *, add_bos: Optional[bool] = None) -> List[int]:
        if not isinstance(text, str) or not text:
            raise TokenizerError("text must be non-empty")
        return self._encode(text, add_bos=self.add_bos if add_bos is None else bool(add_bos))

    def decode(self, token_ids: Iterable[int]) -> str:
        values: List[int] = []
        for token_id in token_ids:
            if isinstance(token_id, bool) or not isinstance(token_id, int):
                raise TokenizerError("token IDs must be integers")
            if token_id < 0 or token_id >= self.vocab_size:
                raise TokenizerError(f"token ID {token_id} is outside vocabulary")
            values.append(int(token_id))
        decoder = getattr(self._tokenizer, "decode", None)
        if not callable(decoder):
            raise TokenizerError("tokenizer implementation lacks decode")
        try:
            return str(decoder(values, skip_special_tokens=True))
        except TypeError:
            try:
                return str(decoder(values))
            except Exception as exc:
                raise TokenizerError("token decoding failed") from exc
        except Exception as exc:
            raise TokenizerError("token decoding failed") from exc

    def _encode(self, text: str, *, add_bos: bool) -> List[int]:
        encoder = getattr(self._tokenizer, "encode", None)
        if not callable(encoder):
            raise TokenizerError("tokenizer implementation lacks encode")
        try:
            try:
                encoded = encoder(text, add_special_tokens=False)
            except TypeError:
                encoded = encoder(text)
        except Exception as exc:
            raise TokenizerError("Qwen2.5 text tokenization failed") from exc
        raw_ids = getattr(encoded, "ids", encoded)
        if not isinstance(raw_ids, (list, tuple)):
            raise TokenizerError("tokenizer returned an invalid ID sequence")
        ids: List[int] = []
        for value in raw_ids:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TokenizerError("tokenizer returned a non-integer token ID")
            if value < 0 or value >= self.vocab_size:
                raise TokenizerError(f"tokenizer returned out-of-range token ID {value}")
            ids.append(int(value))
        if add_bos and self.bos_token_id is not None and (not ids or ids[0] != self.bos_token_id):
            ids.insert(0, self.bos_token_id)
        if not ids:
            raise TokenizerError("text produced no token IDs")
        return ids


# A concise alias is useful to callers while keeping the qwen25 namespace
# explicit and avoiding accidental imports from the legacy Qwen1 adapter.
Qwen25TokenizerAdapter = Qwen25Tokenizer


__all__ = [
    "TokenizerError",
    "Qwen25Tokenizer",
    "Qwen25TokenizerAdapter",
    "QWEN25_IM_START",
    "QWEN25_IM_END",
    "QWEN25_DEFAULT_SYSTEM_PROMPT",
]
