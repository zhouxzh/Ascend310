"""Torch-free tokenizer adapter for the Qwen2.5 static graph.

Special-token IDs are read from the actual tokenizer configuration and
``tokenizer.json``.  No numeric pad/eos assumption is made: different Qwen2.5
exports have used different padding special-token entries, while the static
model's embedding vocabulary is a separate descriptor value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union


class TokenizerError(ValueError):
    """Raised when the supplied Qwen2.5 tokenizer is incomplete or invalid."""


QWEN25_IM_START = "<|im_start|>"
QWEN25_IM_END = "<|im_end|>"
QWEN25_DEFAULT_SYSTEM_PROMPT = (
    "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
)


class Qwen25Tokenizer:
    """Minimal Rust-tokenizers facade; Transformers and Torch are forbidden."""

    def __init__(
        self,
        tokenizer_path: Union[str, Path],
        config_path: Optional[Union[str, Path]] = None,
        *,
        implementation: Any = None,
        add_bos: Optional[bool] = None,
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
        tokenizer_class = self.config.get("tokenizer_class")
        if tokenizer_class is not None and tokenizer_class not in {"Qwen2Tokenizer", "QwenTokenizer"}:
            raise TokenizerError(f"unsupported tokenizer class: {tokenizer_class!r}")
        self.im_start_id = self._required_token_id(QWEN25_IM_START)
        self.im_end_id = self._required_token_id(QWEN25_IM_END)
        # These IDs are resolved from config names first, then from the exact
        # token strings in tokenizer.json.  There is no hard-coded numeric
        # fallback, because model exports differ in their PAD token choice.
        self.eos_token_id = self._configured_id("eos_token", QWEN25_IM_END)
        self.pad_token_id = self._configured_id("pad_token", None)
        if self.pad_token_id is None:
            raise TokenizerError("tokenizer config must define a resolvable pad_token")
        bos_name = self.config.get("bos_token")
        self.bos_token_id = (
            self._token_id(str(bos_name)) if isinstance(bos_name, str) else None
        )
        configured_add_bos = self.config.get("add_bos_token", False)
        self.add_bos = bool(configured_add_bos) if add_bos is None else bool(add_bos)
        self._validate_ids()

    @staticmethod
    def _read_config(path: Optional[Path]) -> Mapping[str, Any]:
        if path is None:
            raise TokenizerError("Qwen2.5 tokenizer_config.json is required for token IDs")
        if not path.is_file():
            raise TokenizerError(f"tokenizer config does not exist: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TokenizerError(f"cannot read tokenizer config: {path}") from exc
        except json.JSONDecodeError as exc:
            raise TokenizerError(f"tokenizer config is not valid JSON: {path}") from exc
        if not isinstance(value, Mapping):
            raise TokenizerError("tokenizer config must be a JSON object")
        return value

    def _token_id(self, token: str) -> Optional[int]:
        getter = getattr(self._tokenizer, "token_to_id", None)
        if not callable(getter):
            raise TokenizerError("tokenizer implementation lacks token_to_id")
        try:
            value = getter(token)
        except Exception as exc:
            raise TokenizerError(f"cannot resolve token {token!r}") from exc
        return int(value) if value is not None else None

    def _required_token_id(self, token: str) -> int:
        value = self._token_id(token)
        if value is None:
            raise TokenizerError(f"tokenizer does not define {token}")
        return value

    def _configured_id(self, key: str, fallback_token: Optional[str]) -> Optional[int]:
        configured = self.config.get(key)
        if isinstance(configured, str):
            value = self._token_id(configured)
            if value is not None:
                return value
            raise TokenizerError(f"tokenizer {key} {configured!r} is not in tokenizer.json")
        if isinstance(configured, int) and not isinstance(configured, bool) and configured >= 0:
            # Numeric IDs are accepted only when the config explicitly binds
            # them; the adapter never invents one.
            return int(configured)
        if fallback_token is not None:
            return self._token_id(fallback_token)
        return None

    def _validate_ids(self) -> None:
        vocab = self.vocab_size
        for name, value in (
            ("im_start_id", self.im_start_id),
            ("im_end_id", self.im_end_id),
            ("eos_token_id", self.eos_token_id),
            ("pad_token_id", self.pad_token_id),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < vocab:
                raise TokenizerError(f"{name}={value!r} is outside tokenizer vocabulary")
        if self.bos_token_id is not None and (
            isinstance(self.bos_token_id, bool)
            or not isinstance(self.bos_token_id, int)
            or not 0 <= self.bos_token_id < vocab
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
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
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
        if add_default_system and normalized[0].get("role") != "system":
            rendered.append(
                f"{QWEN25_IM_START}system\n{QWEN25_DEFAULT_SYSTEM_PROMPT}{QWEN25_IM_END}\n"
            )
        for item in normalized:
            rendered.append(f"{QWEN25_IM_START}{item['role']}\n{item['content']}{QWEN25_IM_END}\n")
        if add_generation_prompt:
            rendered.append(f"{QWEN25_IM_START}assistant\n")
        return self._encode("".join(rendered), add_bos=self.add_bos)

    def encode_text(self, text: str, *, add_bos: Optional[bool] = None) -> List[int]:
        if not isinstance(text, str) or not text:
            raise TokenizerError("text must be non-empty")
        return self._encode(text, add_bos=self.add_bos if add_bos is None else bool(add_bos))

    def decode(self, token_ids: Iterable[int]) -> str:
        values: List[int] = []
        for value in token_ids:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < self.vocab_size:
                raise TokenizerError("token IDs must be integers inside tokenizer vocabulary")
            values.append(int(value))
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
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= self.vocab_size:
                raise TokenizerError("tokenizer returned an out-of-range token ID")
            ids.append(int(value))
        if add_bos and self.bos_token_id is not None and (not ids or ids[0] != self.bos_token_id):
            ids.insert(0, self.bos_token_id)
        if not ids:
            raise TokenizerError("text produced no token IDs")
        return ids


Qwen25TokenizerAdapter = Qwen25Tokenizer


__all__ = [
    "TokenizerError",
    "Qwen25Tokenizer",
    "Qwen25TokenizerAdapter",
    "QWEN25_IM_START",
    "QWEN25_IM_END",
    "QWEN25_DEFAULT_SYSTEM_PROMPT",
]
