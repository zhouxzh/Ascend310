"""Torch-free TinyLlama tokenizer and prompt formatter.

The board artifact contains a ``tokenizer.json`` generated from the Llama
SentencePiece vocabulary.  ``tokenizers`` is imported only when a tokenizer is
constructed, so controller-side contract and service tests do not require the
Rust wheel (and never pull in Transformers or Torch).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union


class TokenizerError(ValueError):
    """Raised for missing, malformed, or incompatible tokenizer artifacts."""


class TinyLlamaTokenizer:
    """A small adapter around the Rust ``tokenizers`` package."""

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
                    "TinyLlama requires the Rust 'tokenizers' package; "
                    "do not install Transformers or Torch"
                ) from exc
            try:
                implementation = Tokenizer.from_file(str(path))
            except Exception as exc:
                raise TokenizerError(f"cannot load tokenizer: {path}") from exc
        self._tokenizer = implementation
        self.tokenizer_path = path
        self.config_path = Path(config_path).expanduser() if config_path else None
        self.config = self._read_config(self.config_path)

        self.unk_token_id = self._find_token_id("<unk>", 0)
        self.bos_token_id = self._find_token_id("<s>", None)
        self.eos_token_id = self._find_token_id("</s>", None)
        if self.bos_token_id is None or self.eos_token_id is None:
            raise TokenizerError("tokenizer must define <s> and </s> special token IDs")
        # LlamaTokenizer has no pad token.  The static graph still requires a
        # deterministic value for unused cache slots; <unk> (0) is the least
        # surprising choice and is never emitted as visible text.
        configured_pad = self.config.get("pad_token_id")
        if isinstance(configured_pad, int) and not isinstance(configured_pad, bool):
            self.pad_token_id = configured_pad
        else:
            self.pad_token_id = self._find_token_id("<pad>", self.unk_token_id)
        self.add_bos = bool(
            self.config.get("add_bos_token", True) if add_bos is None else add_bos
        )
        self._validate_ids()

    @staticmethod
    def _read_config(path: Optional[Path]) -> Mapping[str, Any]:
        if path is None:
            return {}
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

    def _config_int(self, key: str, default: int) -> int:
        value = self.config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TokenizerError(f"tokenizer config {key} must be a non-negative integer")
        return int(value)

    def _find_token_id(self, token: str, default: Optional[int]) -> Optional[int]:
        getter = getattr(self._tokenizer, "token_to_id", None)
        if callable(getter):
            try:
                value = getter(token)
            except Exception:
                value = None
            if value is not None:
                return int(value)
        return int(default) if default is not None else None

    def _validate_ids(self) -> None:
        vocab = self.vocab_size
        for name, value in (
            ("unk_token_id", self.unk_token_id),
            ("bos_token_id", self.bos_token_id),
            ("eos_token_id", self.eos_token_id),
            ("pad_token_id", self.pad_token_id),
        ):
            if value < 0 or value >= vocab:
                raise TokenizerError(f"{name}={value} is outside vocabulary size {vocab}")

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

    def encode_text(self, text: str, *, add_bos: Optional[bool] = None) -> List[int]:
        if not isinstance(text, str) or not text:
            raise TokenizerError("text must be non-empty")
        ids = self._encode(text)
        use_bos = self.add_bos if add_bos is None else bool(add_bos)
        if use_bos and (not ids or ids[0] != self.bos_token_id):
            ids.insert(0, self.bos_token_id)
        if not ids:
            raise TokenizerError("text produced no token IDs")
        return ids

    def encode_messages(self, messages: Sequence[Mapping[str, Any]]) -> List[int]:
        """Render the TinyLlama chat format and return prompt token IDs."""
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
            raise TokenizerError("at least one message is required")
        rendered: List[str] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise TokenizerError(f"message {index} must be an object")
            role, content = message.get("role"), message.get("content")
            if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
                raise TokenizerError(f"unsupported message role at index {index}")
            if not isinstance(content, str) or not content:
                raise TokenizerError(f"message {index} content must be non-empty text")
            rendered.append(f"<|{role}|>\n{content}</s>\n")
        if messages[-1].get("role") != "assistant":
            rendered.append("<|assistant|>")
        prompt = "".join(rendered)
        ids = self._encode(prompt)
        if self.add_bos and (not ids or ids[0] != self.bos_token_id):
            ids.insert(0, self.bos_token_id)
        if not ids:
            raise TokenizerError("chat prompt produced no token IDs")
        return ids

    def decode(self, token_ids: Iterable[int]) -> str:
        values: List[int] = []
        for token_id in token_ids:
            if isinstance(token_id, bool) or not isinstance(token_id, int):
                raise TokenizerError("token IDs must be integers")
            if token_id < 0 or token_id >= self.vocab_size:
                raise TokenizerError(f"token ID {token_id} is outside the vocabulary")
            values.append(int(token_id))
        try:
            return str(self._tokenizer.decode(values, skip_special_tokens=True))
        except TypeError:
            try:
                return str(self._tokenizer.decode(values))
            except Exception as exc:
                raise TokenizerError("token decoding failed") from exc
        except Exception as exc:
            raise TokenizerError("token decoding failed") from exc

    def _encode(self, text: str) -> List[int]:
        try:
            encoded = self._tokenizer.encode(text, add_special_tokens=False)
        except TypeError:
            encoded = self._tokenizer.encode(text)
        except Exception as exc:
            raise TokenizerError("text tokenization failed") from exc
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
        return ids


__all__ = ["TinyLlamaTokenizer", "TokenizerError"]
