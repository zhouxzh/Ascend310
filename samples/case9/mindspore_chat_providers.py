"""MindSpore/MindNLP chat-provider adapters for Case9.

The module deliberately has no import-time dependency on MindSpore or
MindNLP.  This is important on the controller (and for protocol tests), and
also keeps the adapter from accidentally importing a Torch based backend.
The board launcher loads this module in the already provisioned ``base``
environment and :class:`MindSporeChatProvider.load` performs the imports.

Providers expose a deliberately small interface used by
``mindspore_chat_service``:

``load()`` / ``close()`` / ``cancel()`` / ``status()``
``count_tokens(messages)``
``complete(messages, max_tokens)``
``stream(messages, max_tokens)``

``stream`` yields cumulative text snapshots as ``(text, token_count)``.  The
HTTP layer converts those snapshots to prefix-only SSE deltas, so tokenizer
chunking or UTF-8 boundaries never result in repeated output.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.metadata
import logging
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import sys
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


LOGGER = logging.getLogger("case9.mindspore_chat.providers")

DEFAULT_CONTEXT_LENGTH = 1024
DEFAULT_MAX_TOKENS = 32
# Keep the MindSpore candidate contract aligned with the ACL/Qwen service.
# The context check below still enforces prompt_tokens + max_tokens <= 1024.
MAX_MAX_TOKENS = 80
_NPU_SOC_RE = re.compile(r"\b(?:Ascend\s*)?(310B[0-9A-Za-z]+)\b", re.IGNORECASE)


class ProviderError(RuntimeError):
    """Base class for provider loading and generation failures."""


class ProviderUnavailable(ProviderError):
    """The provider is not ready or has entered fail-closed state."""


class ProviderRequestError(ProviderError):
    """A request cannot be represented by the selected tokenizer/model."""


class ProviderTimeout(ProviderError):
    """Generation exceeded the provider's configured watchdog."""


class ProviderBusy(ProviderError):
    """A provider is already serving another request."""


@dataclass(frozen=True)
class GenerationResult:
    """Normalized non-streaming generation result."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field from either a profile dataclass or a plain mapping."""

    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _profile_id(profile: Any) -> str:
    value = _value(profile, "id", "profile_id", "name", default="unknown")
    return str(value)


def _model_source(profile: Any) -> str:
    value = _value(
        profile,
        "model_id",
        "source_model",
        "repository",
        "model",
        "source",
        default=None,
    )
    if not isinstance(value, str) or not value.strip():
        raise ProviderUnavailable("chat model profile does not define a source model")
    source = value.strip()
    if "\x00" in source or "\\" in source or any(part in {".", ".."} for part in Path(source).parts):
        raise ProviderUnavailable("chat model source contains an unsafe path")
    return source


def _revision(profile: Any) -> Optional[str]:
    value = _value(profile, "revision", "model_revision", default=None)
    return str(value) if isinstance(value, str) and value.strip() else None


def _mirror(profile: Any) -> Optional[str]:
    value = _value(profile, "mirror", "download_mirror", default=None)
    return str(value) if isinstance(value, str) and value.strip() else None


def _context_length(profile: Any) -> int:
    value = _value(
        profile,
        "context_length",
        "context_window",
        "max_context_tokens",
        default=DEFAULT_CONTEXT_LENGTH,
    )
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = DEFAULT_CONTEXT_LENGTH
    if result < 1:
        raise ProviderUnavailable("profile context length must be positive")
    return result


def _model_path(profile: Any) -> Optional[str]:
    value = _value(profile, "model_path", "weights_path", "local_model_path", default=None)
    if value is None:
        return None
    return _resolve_local_profile_path(value, "model_path")


def _tokenizer_path(profile: Any) -> Optional[str]:
    value = _value(profile, "tokenizer_path", "tokenizer_dir", default=None)
    if value is None:
        return None
    return _resolve_local_profile_path(value, "tokenizer_path")


def _model_root() -> Path:
    """Return the explicit root allowed to contain profile model artifacts."""

    raw = os.environ.get("CASE9_MODEL_ROOT", os.getcwd())
    if not raw or "\x00" in raw:
        raise ProviderUnavailable("CASE9_MODEL_ROOT is empty or contains NUL")
    return Path(raw).expanduser().resolve()


def _resolve_local_profile_path(value: Any, name: str) -> Optional[str]:
    """Resolve a profile local path strictly below ``CASE9_MODEL_ROOT``.

    Profile files normally restrict paths to relative POSIX components.  The
    extra check here also protects lightweight test mappings.  An absolute
    value is accepted only when it remains below the explicitly selected model
    root; a profile cannot point a model loader at an arbitrary board path.
    """

    raw = str(value).strip()
    if not raw or "\x00" in raw or "\\" in raw:
        raise ProviderUnavailable("%s is empty or contains NUL" % name)
    input_path = Path(raw).expanduser()
    if any(part in {".", ".."} for part in input_path.parts):
        raise ProviderUnavailable("%s must not contain traversal components" % name)
    root = _model_root()
    candidate = input_path.resolve() if input_path.is_absolute() else (root / input_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProviderUnavailable("%s escapes CASE9_MODEL_ROOT" % name) from exc
    return str(candidate)


def _cache_directory(profile: Any) -> Optional[Path]:
    """Resolve ``profile.cache_dir`` below an explicit model root.

    ``CASE9_MODEL_ROOT`` is set by the board launcher to the deployment root;
    using the current working directory as the default keeps local tests and
    ad-hoc board invocations deterministic.  Absolute cache paths are rejected
    because they could escape the deployment root.
    """

    value = _value(profile, "cache_dir", default=None)
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or "\x00" in raw or "\\" in raw:
        raise ProviderUnavailable("profile cache_dir is empty or contains NUL")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise ProviderUnavailable("profile cache_dir must be a safe relative path")
    root = _model_root()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProviderUnavailable("profile cache_dir escapes model root") from exc
    return candidate


def _probe_npu_model() -> Optional[str]:
    """Read the visible 310B model without importing an optional runtime."""

    executable = shutil.which("npu-smi")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    values = sorted({match.group(1).upper() for match in _NPU_SOC_RE.finditer(completed.stdout or "")})
    return values[0] if len(values) == 1 else None


def _cann_version() -> Optional[str]:
    """Return a best-effort CANN version from the sourced toolkit tree."""

    roots: List[Path] = []
    for name in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME_PATH", "ASCEND_INSTALL_PATH"):
        raw = os.environ.get(name, "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path not in roots:
                roots.append(path)
    roots.extend(path for path in (Path("/usr/local/Ascend/ascend-toolkit"), Path("/usr/local/Ascend/latest")) if path not in roots)
    for root in roots:
        for relative in ("version.cfg", "latest/version.cfg", "version.info"):
            path = root / relative
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # CANN 8.0 installations commonly expose a wrapper
            # ``version.cfg`` whose first field is ``# version: 1.0``;
            # prefer the actual toolkit version in ``version_dir`` or
            # the bracketed ``*_running_version`` values.
            match = re.search(r"(?:version_dir)\s*[=:]\s*([0-9][^\s\r\n]*)", content)
            if match:
                return match.group(1).strip('"\'')
            match = re.search(r"running_version\s*=\s*\[[^:]+:([0-9][^\],\s]*)", content)
            if match:
                return match.group(1).strip('"\'')
            match = re.search(r"(?:version|Version)\s*[=:]\s*([0-9][^\s\r\n]*)", content)
            if match:
                return match.group(1).strip('"\'')
    return None


def _as_int(value: Any) -> int:
    """Convert a MindSpore tensor/NumPy scalar/list element to ``int``."""

    if hasattr(value, "asnumpy"):
        value = value.asnumpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    while isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("empty token value")
        value = value[0]
    return int(value)


def _to_list(value: Any) -> List[int]:
    if hasattr(value, "asnumpy"):
        value = value.asnumpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list) and value and isinstance(value[0], list):
        # ``input_ids`` is normally [batch, sequence].
        value = value[0]
    if not isinstance(value, (list, tuple)):
        try:
            return [int(value)]
        except (TypeError, ValueError) as exc:
            raise ProviderError("model output did not contain token ids") from exc
    return [int(item) for item in value]


def _tensor_length(value: Any) -> int:
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            if len(shape) >= 1:
                return int(shape[-1])
        except (TypeError, ValueError):
            pass
    return len(_to_list(value))


def _decode(tokenizer: Any, token_ids: Sequence[int]) -> str:
    try:
        result = tokenizer.decode(list(token_ids), skip_special_tokens=True)
    except TypeError:
        result = tokenizer.decode(list(token_ids))
    if result is None:
        return ""
    return str(result)


class MindSporeChatProvider:
    """Generic MindNLP causal-LM provider shared by the three profiles.

    ``model`` and ``tokenizer`` can be supplied for tests or for a board-side
    preloaded worker.  With neither supplied they are loaded lazily from the
    profile on :meth:`load`.
    """

    provider_name = "mindspore"

    def __init__(
        self,
        profile: Any,
        *,
        model: Any = None,
        tokenizer: Any = None,
        model_loader: Any = None,
        tokenizer_loader: Any = None,
        generation_timeout: float = 300.0,
    ) -> None:
        self.profile = profile
        self.profile_id = _profile_id(profile)
        self.source_model = _model_source(profile)
        self.revision = _revision(profile)
        self.mirror = _mirror(profile)
        self.context_length = _context_length(profile)
        self.generation_timeout = float(generation_timeout)
        if self.generation_timeout <= 0:
            raise ValueError("generation_timeout must be positive")
        self.model = model
        self.tokenizer = tokenizer
        self._model_loader = model_loader
        self._tokenizer_loader = tokenizer_loader
        self._mindspore: Any = None
        self._device_target: Optional[str] = None
        self._device_id: Optional[int] = None
        self._npu_model: Optional[str] = None
        self._cann_version: Optional[str] = None
        self._loaded = model is not None and tokenizer is not None
        self._healthy = True
        self._busy = False
        self._cancel_event = threading.Event()
        self._generation_thread: Optional[threading.Thread] = None
        # Keep installation, teardown, and health reconciliation of the
        # generation pointer atomic. A thread is not ``alive`` until
        # ``start()`` returns, so unsynchronised polls can otherwise race the
        # start/close path and lose ownership of a live model call.
        self._generation_state_lock = threading.Lock()
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_finish_reason = "stop"
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0

        if self._loaded:
            self._validate_tokenizer()

    @property
    def ready(self) -> bool:
        return bool(self._loaded and self._healthy and self.model is not None and self.tokenizer is not None)

    @property
    def healthy(self) -> bool:
        """Expose the provider health latch for service/watchdog callers.

        ``ready`` also depends on loaded model objects; callers that need to
        distinguish a failed-closed provider from an unloaded one should use
        this read-only health flag.  Keeping the value behind the existing
        private latch prevents external code from re-enabling a failed worker.
        """

        return bool(self._healthy)

    @property
    def model_id(self) -> str:
        # The service uses ``case9-active`` as its stable upstream id.  This
        # property is still useful to callers that inspect a provider.
        value = _value(self.profile, "runtime_model_id", "model_runtime_id", default=None)
        return str(value) if value else "case9-active"

    @property
    def eos_token_id(self) -> Optional[int]:
        value = getattr(self.tokenizer, "eos_token_id", None)
        if value is None:
            value = getattr(self.tokenizer, "eos_token", None)
        try:
            return int(value) if value is not None and not isinstance(value, str) else None
        except (TypeError, ValueError):
            return None

    def load(self) -> None:
        """Load MindSpore/MindNLP lazily and fail closed on errors."""

        if self._loaded:
            self._validate_tokenizer()
            return
        try:
            if self._model_loader is not None or self._tokenizer_loader is not None:
                self.tokenizer = self._load_with_callback(self._tokenizer_loader, "tokenizer")
                self.model = self._load_with_callback(self._model_loader, "model")
            else:
                # These imports intentionally happen only after the service is
                # started in the board's explicitly selected environment.
                self._mindspore = importlib.import_module("mindspore")
                self._configure_ascend_context()
                transformers = importlib.import_module("mindnlp.transformers")
                auto_tokenizer = getattr(transformers, "AutoTokenizer")
                auto_model = getattr(transformers, "AutoModelForCausalLM")
                self.tokenizer = self._from_pretrained(auto_tokenizer, "tokenizer")
                self.model = self._from_pretrained(auto_model, "model")
            if self.model is None or self.tokenizer is None:
                raise ProviderUnavailable("MindNLP loaders returned no model/tokenizer")
            set_train = getattr(self.model, "set_train", None)
            if callable(set_train):
                set_train(False)
            eval_method = getattr(self.model, "eval", None)
            if callable(eval_method):
                eval_method()
            self._validate_tokenizer()
            self._loaded = True
            self._healthy = True
            self._last_error = None
        except ProviderError:
            self._healthy = False
            raise
        except Exception as exc:
            self._healthy = False
            self._last_error = str(exc)
            raise ProviderUnavailable("failed to load MindSpore chat model: %s" % exc) from exc

    def _configure_ascend_context(self) -> None:
        """Require an Ascend context; never silently run the model on CPU."""

        mindspore = self._mindspore
        if mindspore is None:
            raise ProviderUnavailable("MindSpore module is unavailable")
        set_context = getattr(mindspore, "set_context", None)
        get_context = getattr(mindspore, "get_context", None)
        if not callable(set_context):
            raise ProviderUnavailable("MindSpore does not expose set_context")
        target = os.environ.get("CASE9_DEVICE_TARGET", "Ascend").strip() or "Ascend"
        if target.lower() != "ascend":
            raise ProviderUnavailable("CASE9_DEVICE_TARGET must be Ascend; CPU fallback is disabled")
        raw_device_id = os.environ.get("CASE9_DEVICE_ID", "0").strip() or "0"
        try:
            device_id = int(raw_device_id)
        except ValueError as exc:
            raise ProviderUnavailable("CASE9_DEVICE_ID must be an integer") from exc
        try:
            set_context(device_target="Ascend", device_id=device_id)
            actual = get_context("device_target") if callable(get_context) else "Ascend"
        except Exception as exc:
            raise ProviderUnavailable("could not initialize MindSpore Ascend context: %s" % exc) from exc
        if str(actual).lower() != "ascend":
            raise ProviderUnavailable("MindSpore selected %s instead of Ascend" % actual)
        observed_soc = _probe_npu_model()
        if not observed_soc:
            raise ProviderUnavailable("npu-smi did not report a visible Ascend 310B device")
        expected_raw = _value(self.profile, "board_soc", default=None)
        if expected_raw is None and isinstance(self.profile, Mapping):
            board = self.profile.get("board")
            if isinstance(board, Mapping):
                expected_raw = board.get("soc")
        expected_soc = str(expected_raw or "").upper().replace("ASCEND", "")
        if expected_soc and observed_soc != expected_soc:
            raise ProviderUnavailable(
                "profile expects Ascend%s but npu-smi reported %s" % (expected_soc, observed_soc)
            )
        self._device_target = str(actual)
        self._device_id = device_id
        self._npu_model = "Ascend" + observed_soc
        self._cann_version = _cann_version()

    def _load_with_callback(self, callback: Any, kind: str) -> Any:
        if callback is None:
            raise ProviderUnavailable("%s loader is not configured" % kind)
        try:
            return callback(self.profile)
        except TypeError:
            # A small convenience for tests that use a zero-argument lambda.
            return callback()

    def _from_pretrained(self, loader: Any, kind: str) -> Any:
        explicit_path = _model_path(self.profile) if kind == "model" else _tokenizer_path(self.profile)
        cache_dir = _cache_directory(self.profile)
        source = explicit_path or self.source_model
        uses_local_artifact = explicit_path is not None
        # A synchronized local profile directory takes precedence over a
        # remote identifier only when it contains a recognizable model file.
        # Otherwise retain the repository identifier and let MindNLP populate
        # the controlled cache directory.
        if explicit_path is None and cache_dir is not None and cache_dir.is_dir():
            if kind == "model":
                # Treat a cache as a local model only when both its structure
                # and at least one supported weight file are present.  A
                # tokenizer-only directory must not make model loading skip
                # the remote artifact resolution step.
                local_complete = (cache_dir / "config.json").is_file() and any(
                    (cache_dir / name).is_file()
                    for name in ("model.safetensors", "pytorch_model.bin", "mindspore.ckpt")
                )
            else:
                local_complete = any(
                    (cache_dir / name).is_file()
                    for name in ("tokenizer.json", "tokenizer.model")
                )
            if local_complete:
                source = str(cache_dir)
                uses_local_artifact = True
        kwargs: Dict[str, Any] = {}
        # A local, hash-verified directory has already selected its revision.
        # Passing a remote revision to older MindNLP releases can make them
        # treat the local path as a Hub identifier and redownload the model.
        if self.revision and not uses_local_artifact:
            kwargs["revision"] = self.revision
        if self.mirror and not uses_local_artifact:
            kwargs["mirror"] = self.mirror
        if cache_dir is not None and explicit_path is None and not uses_local_artifact:
            kwargs["cache_dir"] = str(cache_dir)
        if kind == "model" and self._mindspore is not None:
            dtype = getattr(self._mindspore, "float16", None)
            if dtype is not None:
                kwargs["ms_dtype"] = dtype
        # MindNLP releases differ slightly in optional mirror/cache keyword
        # support.  Retry only by dropping those optional transport hints;
        # never drop a pinned revision or dtype and accidentally load a
        # different artifact/backend.
        try:
            return loader.from_pretrained(source, **kwargs)
        except TypeError:
            for optional in ("mirror", "cache_dir"):
                if optional in kwargs:
                    retry = dict(kwargs)
                    retry.pop(optional)
                    try:
                        return loader.from_pretrained(source, **retry)
                    except TypeError:
                        kwargs = retry
            return loader.from_pretrained(source, **kwargs)

    def _validate_tokenizer(self) -> None:
        if self.tokenizer is None:
            raise ProviderUnavailable("tokenizer is unavailable")
        vocab = getattr(self.tokenizer, "vocab_size", None)
        if callable(vocab):
            try:
                vocab = vocab()
            except TypeError:
                vocab = None
        if vocab is not None:
            try:
                if int(vocab) <= 0:
                    raise ProviderUnavailable("tokenizer vocabulary is empty")
            except (TypeError, ValueError) as exc:
                raise ProviderUnavailable("tokenizer vocabulary is invalid") from exc

    def _render_messages(self, messages: Sequence[Mapping[str, str]]) -> Any:
        tokenizer = self.tokenizer
        if tokenizer is None:
            raise ProviderUnavailable("tokenizer is not loaded")
        template = getattr(tokenizer, "apply_chat_template", None)
        if callable(template):
            try:
                return template(
                    list(messages),
                    add_generation_prompt=True,
                    return_tensors="ms",
                    tokenize=True,
                )
            except (TypeError, ValueError, AttributeError):
                # Some older MindNLP tokenizers expose the method but do not
                # support ``return_tensors``.  Fall through to regular call.
                try:
                    rendered = template(list(messages), add_generation_prompt=True, tokenize=False)
                except Exception:
                    rendered = None
                if rendered is not None:
                    return self._tokenize_text(str(rendered))

        # TinyLlama's old MindNLP tokenizer has no chat template.  Use its
        # declared special-token strings instead of hard-coded token ids.
        parts: List[str] = []
        bos = getattr(tokenizer, "bos_token", None) or ""
        eos = getattr(tokenizer, "eos_token", None) or ""
        if bos:
            parts.append(str(bos))
        for message in messages:
            role = str(message["role"])
            content = str(message["content"])
            parts.append("<|%s|>\n%s" % (role, content))
            if eos:
                parts.append(str(eos))
        parts.append("<|assistant|>\n")
        return self._tokenize_text("\n".join(parts))

    def _tokenize_text(self, text: str) -> Any:
        tokenizer = self.tokenizer
        call = getattr(tokenizer, "__call__", None)
        if not callable(call):
            raise ProviderUnavailable("tokenizer cannot encode text")
        try:
            encoded = call(text, return_tensors="ms", add_special_tokens=False)
        except TypeError:
            encoded = call([text], return_tensors="ms", add_special_tokens=False)
        if isinstance(encoded, Mapping):
            return encoded.get("input_ids", encoded)
        return encoded

    def _input_ids(self, messages: Sequence[Mapping[str, str]]) -> Any:
        rendered = self._render_messages(messages)
        if isinstance(rendered, Mapping):
            rendered = rendered.get("input_ids")
        if rendered is None:
            raise ProviderRequestError("tokenizer returned no input_ids")
        return rendered

    def count_tokens(self, messages: Sequence[Mapping[str, str]]) -> int:
        if not self.ready:
            raise ProviderUnavailable("MindSpore chat model is not ready")
        return _tensor_length(self._input_ids(messages))

    def _check_budget(self, messages: Sequence[Mapping[str, str]], max_tokens: int) -> Tuple[Any, int]:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise ProviderRequestError("max_tokens must be an integer")
        if max_tokens < 1 or max_tokens > MAX_MAX_TOKENS:
            raise ProviderRequestError("max_tokens must be between 1 and %d" % MAX_MAX_TOKENS)
        input_ids = self._input_ids(messages)
        prompt_tokens = _tensor_length(input_ids)
        if prompt_tokens + max_tokens > self.context_length:
            raise ProviderRequestError(
                "prompt plus max_tokens exceeds the %d-token context limit" % self.context_length
            )
        return input_ids, prompt_tokens

    def _attention_mask(self, input_ids: Any) -> Optional[Any]:
        """Build an explicit prefix mask for MindNLP ``generate``.

        Recent MindNLP releases warn (and may spend extra work inferring the
        mask) when it is omitted.  Keep this optional for injected unit-test
        models and older controller environments: only a loaded MindSpore
        runtime gets a tensor mask, and construction failures are surfaced by
        the normal model call rather than importing a second backend.
        """

        mindspore = self._mindspore
        if mindspore is None:
            return None
        shape = getattr(input_ids, "shape", None)
        if shape is None:
            return None
        try:
            batch = int(shape[0])
            sequence = int(shape[-1])
        except (IndexError, TypeError, ValueError):
            return None
        if batch < 1 or sequence < 1:
            return None
        try:
            numpy = importlib.import_module("numpy")
            values = numpy.ones((batch, sequence), dtype=numpy.int64)
            tensor = getattr(mindspore, "Tensor", None)
            dtype = getattr(mindspore, "int64", None)
            if not callable(tensor) or dtype is None:
                return None
            return tensor(values, dtype=dtype)
        except Exception as exc:
            LOGGER.debug("could not construct attention_mask: %s", exc)
            return None

    def _generate_kwargs(self, input_ids: Any, max_tokens: int, streamer: Any = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "input_ids": input_ids,
            "max_new_tokens": int(max_tokens),
            "do_sample": False,
            "num_beams": 1,
            "top_p": 1.0,
        }
        attention_mask = self._attention_mask(input_ids)
        if attention_mask is not None:
            kwargs["attention_mask"] = attention_mask
        if streamer is not None:
            kwargs["streamer"] = streamer
        return kwargs

    def _run_generation(self, kwargs: Mapping[str, Any], output_queue: Optional[queue.Queue] = None) -> Any:
        try:
            result = self.model.generate(**dict(kwargs))
            if output_queue is not None:
                output_queue.put(("result", result))
            return result
        except BaseException as exc:  # propagate model errors to caller thread
            if output_queue is not None:
                output_queue.put(("error", exc))
            return None

    def complete(self, messages: Sequence[Mapping[str, str]], max_tokens: int) -> GenerationResult:
        if not self.ready:
            raise ProviderUnavailable("MindSpore chat model is not ready")
        input_ids, prompt_tokens = self._check_budget(messages, max_tokens)
        with self._generation_guard():
            output_queue: queue.Queue = queue.Queue(maxsize=1)
            thread = threading.Thread(
                target=self._run_generation,
                args=(self._generate_kwargs(input_ids, max_tokens), output_queue),
                name="case9-ms-generate",
                daemon=True,
            )
            self._start_generation_thread(thread)
            try:
                kind, value = output_queue.get(timeout=self.generation_timeout)
            except queue.Empty as exc:
                self._fail_closed("MindSpore generation watchdog expired")
                self.cancel()
                self._terminate_after_watchdog()
                raise ProviderTimeout("MindSpore generation timed out") from exc
            finally:
                thread.join(timeout=0.2)
                # Keep the pointer while an uninterruptible model call is
                # still running.  Clearing it would make health() report an
                # idle worker while the device is still occupied.  The
                # identity check avoids clearing a newer generation thread if
                # a caller has already replaced this one during teardown.
                with self._generation_state_lock:
                    if not thread.is_alive() and self._generation_thread is thread:
                        self._generation_thread = None
            if kind == "error":
                self._fail_closed(str(value))
                if isinstance(value, ProviderError):
                    raise value
                raise ProviderUnavailable("MindSpore generation failed: %s" % value) from value
            sequences = _extract_sequences(value)
            all_ids = _to_list(sequences)
            generated_ids = all_ids[prompt_tokens:] if len(all_ids) >= prompt_tokens else []
            eos = self.eos_token_id
            if eos is not None and eos in generated_ids:
                generated_ids = generated_ids[: generated_ids.index(eos) + 1]
            text = _decode(self.tokenizer, generated_ids)
            completion_tokens = len(generated_ids)
            finish_reason = "stop" if eos is not None and generated_ids and generated_ids[-1] == eos else "length"
            self._last_prompt_tokens = prompt_tokens
            self._last_completion_tokens = completion_tokens
            self._last_finish_reason = finish_reason
            return GenerationResult(text, prompt_tokens, completion_tokens, finish_reason)

    def stream(self, messages: Sequence[Mapping[str, str]], max_tokens: int) -> Iterable[Tuple[str, int]]:
        if not self.ready:
            raise ProviderUnavailable("MindSpore chat model is not ready")
        input_ids, prompt_tokens = self._check_budget(messages, max_tokens)
        transformers = importlib.import_module("mindnlp.transformers")
        streamer_cls = getattr(transformers, "TextIteratorStreamer", None)
        if streamer_cls is None:
            # A minimal fallback still produces one cumulative snapshot and
            # keeps the API usable with older MindNLP builds.  Falling back to
            # the bounded complete path is important: an iterator without a
            # timeout could otherwise block the worker forever in ``next``.
            result = self.complete(messages, max_tokens)
            yield result.text, result.completion_tokens
            return
        try:
            streamer = streamer_cls(
                self.tokenizer,
                timeout=min(300.0, self.generation_timeout),
                skip_prompt=True,
                skip_special_tokens=True,
            )
        except TypeError:
            LOGGER.warning(
                "MindNLP TextIteratorStreamer has no bounded timeout; "
                "using the guarded complete path"
            )
            # Do not construct a timeout-less streamer. ``complete``
            # supervises ``model.generate`` with a bounded queue and can
            # fail closed if the model call stalls.
            result = self.complete(messages, max_tokens)
            yield result.text, result.completion_tokens
            return
        with self._generation_guard():
            kwargs = self._generate_kwargs(input_ids, max_tokens, streamer)
            stream_queue: queue.Queue = queue.Queue(maxsize=1)
            thread = threading.Thread(
                target=self._run_generation,
                args=(kwargs, stream_queue),
                name="case9-ms-stream-generate",
                daemon=True,
            )
            self._start_generation_thread(thread)
            accumulated = ""
            count = 0
            started = time.monotonic()
            try:
                iterator = iter(streamer)
                while True:
                    if self._cancel_event.is_set():
                        raise ProviderUnavailable("generation cancelled")
                    if time.monotonic() - started > self.generation_timeout:
                        self._fail_closed("MindSpore generation watchdog expired")
                        self.cancel()
                        self._terminate_after_watchdog()
                        raise ProviderTimeout("MindSpore generation timed out")
                    try:
                        piece = next(iterator)
                    except StopIteration:
                        break
                    except (queue.Empty, TimeoutError) as exc:
                        self._fail_closed("MindSpore streamer watchdog expired")
                        self.cancel()
                        self._terminate_after_watchdog()
                        raise ProviderTimeout("MindSpore generation timed out") from exc
                    if piece is None:
                        continue
                    accumulated += str(piece)
                    count += 1
                    self._last_prompt_tokens = prompt_tokens
                    self._last_completion_tokens = count
                    yield accumulated, count
                # The streamer may end before the generation thread has
                # returned its result; join briefly to surface model errors.
                thread.join(timeout=0.5)
                if thread.is_alive():
                    self._fail_closed("MindSpore generation thread did not stop")
                    raise ProviderTimeout("MindSpore generation did not stop")
                # ``TextIteratorStreamer`` can finish after a model exception
                # on some MindNLP releases.  Inspect the worker result so a
                # failed generation is never reported as a clean stop.
                try:
                    result_kind, result_value = stream_queue.get_nowait()
                except queue.Empty:
                    result_kind, result_value = "result", None
                if result_kind == "error":
                    self._fail_closed(str(result_value))
                    if isinstance(result_value, ProviderError):
                        raise result_value
                    raise ProviderUnavailable("MindSpore streaming failed: %s" % result_value)
                # TextIteratorStreamer yields text fragments, not token IDs.
                # Prefer the completed sequence for usage and finish_reason;
                # retain the fragment count only for old builds that return no
                # sequence after streaming.
                if result_kind == "result" and result_value is not None:
                    try:
                        sequence = _extract_sequences(result_value)
                        total_tokens = _tensor_length(sequence)
                        count = max(0, total_tokens - prompt_tokens)
                        generated = _to_list(sequence)[prompt_tokens:]
                        eos = self.eos_token_id
                        if eos is not None and eos in generated:
                            self._last_finish_reason = "stop"
                        else:
                            self._last_finish_reason = "length" if count >= max_tokens else "stop"
                    except Exception:
                        self._last_finish_reason = "length" if count >= max_tokens else "stop"
                else:
                    self._last_finish_reason = "length" if count >= max_tokens else "stop"
                self._last_completion_tokens = count
            except ProviderError:
                self.cancel()
                raise
            except (RuntimeError, OSError) as exc:
                self._fail_closed(str(exc))
                self.cancel()
                raise ProviderUnavailable("MindSpore streaming failed: %s" % exc) from exc
            finally:
                # A timeout/cancellation can leave MindNLP's generation call
                # alive after the iterator has unwound.  Retain its pointer
                # until the thread has actually exited.
                thread.join(timeout=0.2)
                with self._generation_state_lock:
                    if not thread.is_alive() and self._generation_thread is thread:
                        self._generation_thread = None

    class _GenerationGuard:
        def __init__(self, owner: "MindSporeChatProvider") -> None:
            self.owner = owner

        def __enter__(self) -> None:
            if not self.owner._lock.acquire(blocking=False):
                raise ProviderBusy("MindSpore model is busy")
            if not self.owner.ready:
                self.owner._lock.release()
                raise ProviderUnavailable("MindSpore chat model is not ready")
            self.owner._busy = True
            self.owner._cancel_event.clear()

        def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
            self.owner._busy = False
            self.owner._lock.release()

    def _generation_guard(self) -> "MindSporeChatProvider._GenerationGuard":
        return MindSporeChatProvider._GenerationGuard(self)

    def _start_generation_thread(self, thread: threading.Thread) -> None:
        """Install and start a generation thread as one observable action."""

        with self._generation_state_lock:
            self._generation_thread = thread
            try:
                thread.start()
            except BaseException:
                if self._generation_thread is thread:
                    self._generation_thread = None
                raise

    def cancel(self) -> None:
        self._cancel_event.set()
        # MindNLP does not expose a universal cancellation API.  If a model
        # does provide one, use it, but never tear down another request's
        # buffers from this thread.
        cancel = getattr(self.model, "cancel_generation", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                LOGGER.debug("model cancellation hook failed", exc_info=True)

    def cancel_and_watchdog(self, wait_seconds: float = 0.5) -> bool:
        """Cancel an in-flight generation and fail closed if it will not stop.

        MindNLP does not guarantee that ``generate`` is interruptible.  The
        HTTP layer calls this method after a client disconnect so a detached
        generation cannot silently retain the device.  A normal shutdown may
        still use :meth:`close`; this stronger path is reserved for an
        abandoned request.
        """

        self.cancel()
        with self._generation_state_lock:
            thread = self._generation_thread
        if thread is None or not thread.is_alive():
            if thread is not None:
                with self._generation_state_lock:
                    if self._generation_thread is thread and not thread.is_alive():
                        self._generation_thread = None
            return True
        try:
            timeout = max(0.0, min(float(wait_seconds), 5.0))
        except (TypeError, ValueError):
            timeout = 0.5
        thread.join(timeout=timeout)
        with self._generation_state_lock:
            if not thread.is_alive() and self._generation_thread is thread:
                self._generation_thread = None
        if thread.is_alive():
            self._fail_closed("MindSpore generation did not stop after client disconnect")
            self._terminate_after_watchdog()
            return False
        return True

    def _fail_closed(self, message: str) -> None:
        self._healthy = False
        self._last_error = str(message)

    def _terminate_after_watchdog(self) -> None:
        """Terminate the real board worker after an uninterruptible stall.

        Both flags are set only by the board launcher.  This keeps injected
        providers and local protocol tests safe while allowing modelctl to
        observe a dead worker and roll back/fail-closed after a hard timeout.
        """

        if (
            os.environ.get("CASE9_PROCESS_WATCHDOG", "0") != "1"
            or os.environ.get("CASE9_WORKER_MAIN", "0") != "1"
        ):
            return

        def terminate() -> None:
            time.sleep(0.2)
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:
                pass

        threading.Thread(
            target=terminate,
            name="case9-ms-watchdog-terminate",
            daemon=True,
        ).start()

    def close(self) -> None:
        self.cancel()
        with self._generation_state_lock:
            thread = self._generation_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        thread_alive = bool(thread is not None and thread.is_alive())
        with self._generation_state_lock:
            if thread is not None and not thread_alive and self._generation_thread is thread:
                self._generation_thread = None
        if thread_alive:
            # A Python thread cannot be force-killed safely.  Do not release
            # or invalidate the model object while it may still touch device
            # memory; the board launcher watchdog will terminate this worker
            # after the fail-closed transition.
            self._fail_closed("MindSpore generation thread did not stop during close")
            self._terminate_after_watchdog()
            return
        model = self.model
        close = getattr(model, "close", None) if model is not None else None
        if callable(close):
            try:
                close()
            except Exception:
                LOGGER.debug("model close hook failed", exc_info=True)
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._busy = False

    def status(self) -> Dict[str, Any]:
        # A timed-out thread may finish asynchronously after its caller has
        # unwound.  Reconcile the pointer opportunistically, but never clear a
        # live thread or a pointer belonging to a newer generation.
        with self._generation_state_lock:
            generation_thread = self._generation_thread
            if generation_thread is not None and not generation_thread.is_alive():
                if self._generation_thread is generation_thread:
                    self._generation_thread = None
                generation_thread = self._generation_thread
        generation_alive = bool(generation_thread is not None and generation_thread.is_alive())
        busy = bool(self._busy or generation_alive)
        env = environment_fingerprint()
        profile_status = _value(self.profile, "status", default="experimental_dirty_base")
        admission = _value(self.profile, "admission_eligible", "admitted", default=False)
        return {
            "ready": self.ready,
            "healthy": bool(self._healthy),
            "provider": self.provider_name,
            "profile": self.profile_id,
            "profile_id": self.profile_id,
            "model_revision": self.revision,
            "source_model": self.source_model,
            "context_length": self.context_length,
            "busy": busy,
            "cache_cleanup": "in_progress" if busy else "idle",
            "cache_cleared": not busy,
            "last_error": self._last_error,
            "last_finish_reason": self._last_finish_reason,
            "last_prompt_tokens": self._last_prompt_tokens,
            "last_completion_tokens": self._last_completion_tokens,
            "environment": env,
            "environment_fingerprint": env.get("fingerprint"),
            "npu_model": self._npu_model or os.environ.get("ASCEND_SOC_VERSION") or os.environ.get("CASE9_NPU_MODEL") or "unknown",
            "cann_version": self._cann_version or _cann_version() or "unknown",
            "device_target": self._device_target,
            "device_id": self._device_id,
            "admission": "admitted" if admission else str(profile_status),
            "admission_status": str(profile_status),
        }


class Qwen15MindSporeProvider(MindSporeChatProvider):
    provider_name = "mindspore-qwen1.5"


class TinyLlamaMindSporeProvider(MindSporeChatProvider):
    provider_name = "mindspore-tinyllama"


class DeepSeekMindSporeProvider(MindSporeChatProvider):
    provider_name = "mindspore-deepseek"


def provider_class_for_profile(profile: Any) -> Any:
    profile_id = _profile_id(profile).lower()
    runtime_provider = _value(profile, "runtime_provider", "provider", default="mindspore")
    if str(runtime_provider).strip().lower() != "mindspore":
        raise ProviderUnavailable(
            "profile %s requests unsupported runtime provider %r"
            % (profile_id, runtime_provider)
        )
    providers = {
        "qwen1.5-0.5b-mindspore": Qwen15MindSporeProvider,
        "tinyllama-1.1b-mindspore": TinyLlamaMindSporeProvider,
        "deepseek-r1-qwen-1.5b-mindspore": DeepSeekMindSporeProvider,
    }
    try:
        return providers[profile_id]
    except KeyError as exc:
        raise ProviderUnavailable("unsupported chat model profile: %s" % profile_id) from exc


def create_provider(profile: Any, **kwargs: Any) -> MindSporeChatProvider:
    """Create the profile-specific adapter without importing MindSpore."""

    return provider_class_for_profile(profile)(profile, **kwargs)


def _extract_sequences(output: Any) -> Any:
    """Extract generated token IDs from common MindNLP return shapes."""

    if isinstance(output, Mapping):
        for key in ("sequences", "output_ids", "input_ids"):
            if key in output:
                return output[key]
    # Generate*Output is attribute-based in some MindNLP releases rather than
    # a Mapping.  Keep this optional and duck-typed to avoid importing the
    # runtime at module import time.
    for key in ("sequences", "output_ids", "input_ids"):
        candidate = getattr(output, key, None)
        if candidate is not None:
            return candidate
    if isinstance(output, (tuple, list)):
        if len(output) == 1:
            return output[0]
        # A list of IDs is itself a valid sequence; nested output is not.
        if output and isinstance(output[0], (int, float)):
            return output
        return output[0]
    return output


def environment_fingerprint() -> Dict[str, Any]:
    """Collect a side-effect-free environment/version fingerprint."""

    versions: Dict[str, Optional[str]] = {}
    for distribution in ("mindspore", "mindnlp", "numpy"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
        except Exception:
            versions[distribution] = None
    payload = {
        "python": "%d.%d.%d" % tuple(sys.version_info[:3]),
        "versions": versions,
        "conda_prefix": os.environ.get("CONDA_PREFIX"),
    }
    try:
        import hashlib
        import json

        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    except Exception:
        payload["fingerprint"] = None
    return payload


__all__ = [
    "DEFAULT_CONTEXT_LENGTH",
    "DEFAULT_MAX_TOKENS",
    "MAX_MAX_TOKENS",
    "GenerationResult",
    "ProviderBusy",
    "ProviderError",
    "ProviderRequestError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "MindSporeChatProvider",
    "Qwen15MindSporeProvider",
    "TinyLlamaMindSporeProvider",
    "DeepSeekMindSporeProvider",
    "create_provider",
    "provider_class_for_profile",
    "environment_fingerprint",
]
