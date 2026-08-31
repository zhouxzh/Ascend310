"""Model-agnostic embedding backends for admitted Ascend OM artifacts."""

from __future__ import annotations

import re
import string
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

from config import NPU_DEVICE_ID
from model_registry import ComponentContract, ModelRecord, ModelRegistry, RegistryError


MOBILECLIP_ID = "mobileclip_s0__npu__mixed_fp16"
CHINESE_CLIP_ID = "chinese_clip_rn50__npu__mixed_fp16"
RESNET50_ID = "resnet50_feature__npu__mixed_fp16"
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
NUMPY_DTYPES = {
    "float16": np.float16,
    "float32": np.float32,
    "int32": np.int32,
    "int64": np.int64,
}
# Values reported by ``acl.mdl.get_*_data_type`` in CANN 8.0's PyACL binding.
ACL_DTYPE_CODES = {
    "float32": 0,
    "float16": 1,
    "int32": 3,
    "int64": 9,
}


class EmbeddingError(RuntimeError):
    """Raised for unavailable models, invalid inputs, or ACL failures."""


def resolve_text_model(query: str, requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    return CHINESE_CLIP_ID if CJK_PATTERN.search(query or "") else MOBILECLIP_ID


def resolve_image_model(requested: str = "auto") -> str:
    return MOBILECLIP_ID if requested == "auto" else requested


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not np.isfinite(vector).all():
        raise EmbeddingError("model output contains NaN or infinity")
    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        raise EmbeddingError("model output has zero norm")
    return vector / norm


def _check_ret(ret, operation: str):
    """Accept PyACL's scalar and ``(value, ret)`` return conventions."""
    if isinstance(ret, tuple):
        if not ret:
            raise EmbeddingError(f"{operation} returned an empty ACL result")
        ret = ret[-1]
    if ret != 0:
        raise EmbeddingError(f"{operation} failed with ACL code {ret}")


class AscendResource:
    """Own one ACL device/context and release it deterministically."""

    def __init__(self, device_id: int = NPU_DEVICE_ID):
        try:
            import acl
        except ImportError as exc:
            raise EmbeddingError(
                "PyACL is unavailable; activate conda and source the CANN environment"
            ) from exc
        self.acl = acl
        self.device_id = int(device_id)
        self.context = None
        self._released = False
        _check_ret(acl.init(), "acl.init")
        try:
            _check_ret(acl.rt.set_device(self.device_id), "acl.rt.set_device")
            self.context, ret = acl.rt.create_context(self.device_id)
            _check_ret(ret, "acl.rt.create_context")
        except Exception:
            try:
                acl.finalize()
            finally:
                raise

    def activate(self):
        _check_ret(self.acl.rt.set_context(self.context), "acl.rt.set_context")

    def release(self):
        if self._released:
            return
        self._released = True
        acl = self.acl
        if self.context is not None:
            acl.rt.destroy_context(self.context)
            self.context = None
        acl.rt.reset_device(self.device_id)
        acl.finalize()


class AclModel:
    """Strict fixed-shape ACL model wrapper."""

    def __init__(self, resource: AscendResource, path: Path):
        self.resource = resource
        self.acl = resource.acl
        self.path = Path(path)
        if not self.path.is_file():
            raise EmbeddingError(f"OM model does not exist: {self.path}")
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers = []
        self.output_buffers = []
        self.resource.activate()
        self.model_id, ret = self.acl.mdl.load_from_file(str(self.path))
        _check_ret(ret, f"acl.mdl.load_from_file({self.path.name})")
        try:
            self._allocate()
        except Exception:
            self.release()
            raise

    def _allocate(self):
        acl = self.acl
        self.desc = acl.mdl.create_desc()
        _check_ret(acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
        self.input_dataset = acl.mdl.create_dataset()
        self.output_dataset = acl.mdl.create_dataset()
        for index in range(acl.mdl.get_num_inputs(self.desc)):
            size = int(acl.mdl.get_input_size_by_index(self.desc, index))
            ptr, ret = acl.rt.malloc(size, 2)
            _check_ret(ret, "acl.rt.malloc input")
            data_buffer = acl.create_data_buffer(ptr, size)
            _check_ret(acl.mdl.add_dataset_buffer(self.input_dataset, data_buffer), "add input buffer")
            self.input_buffers.append({"ptr": ptr, "size": size, "buffer": data_buffer})
        for index in range(acl.mdl.get_num_outputs(self.desc)):
            size = int(acl.mdl.get_output_size_by_index(self.desc, index))
            ptr, ret = acl.rt.malloc(size, 2)
            _check_ret(ret, "acl.rt.malloc output")
            data_buffer = acl.create_data_buffer(ptr, size)
            _check_ret(acl.mdl.add_dataset_buffer(self.output_dataset, data_buffer), "add output buffer")
            self.output_buffers.append({"ptr": ptr, "size": size, "buffer": data_buffer})

    def output_contracts(self):
        """Return the OM-described output sizes and data-type codes."""
        return tuple(
            {
                "size": int(self.acl.mdl.get_output_size_by_index(self.desc, index)),
                "acl_dtype": int(self.acl.mdl.get_output_data_type(self.desc, index)),
            }
            for index in range(self.acl.mdl.get_num_outputs(self.desc))
        )

    def execute(self, inputs):
        if len(inputs) != len(self.input_buffers):
            raise EmbeddingError(
                f"{self.path.name} expects {len(self.input_buffers)} inputs, got {len(inputs)}"
            )
        acl = self.acl
        self.resource.activate()
        for index, value in enumerate(inputs):
            array = np.ascontiguousarray(value)
            expected = self.input_buffers[index]["size"]
            if array.nbytes != expected:
                raise EmbeddingError(
                    f"{self.path.name} input {index} is {array.nbytes} bytes, expected {expected}"
                )
            source_ptr = acl.util.numpy_to_ptr(array)
            _check_ret(
                acl.rt.memcpy(self.input_buffers[index]["ptr"], expected, source_ptr, expected, 1),
                "acl.rt.memcpy host-to-device",
            )
        _check_ret(acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset), "acl.mdl.execute")
        outputs = []
        for value in self.output_buffers:
            host = np.empty(value["size"], dtype=np.uint8)
            host_ptr = acl.util.numpy_to_ptr(host)
            _check_ret(
                acl.rt.memcpy(host_ptr, value["size"], value["ptr"], value["size"], 2),
                "acl.rt.memcpy device-to-host",
            )
            outputs.append(host.tobytes())
        return outputs

    def release(self):
        acl = getattr(self, "acl", None)
        if acl is None:
            return
        for value in self.input_buffers + self.output_buffers:
            if value.get("buffer") is not None:
                acl.destroy_data_buffer(value["buffer"])
            if value.get("ptr") is not None:
                acl.rt.free(value["ptr"])
        self.input_buffers.clear()
        self.output_buffers.clear()
        if self.input_dataset is not None:
            acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None
        if self.output_dataset is not None:
            acl.mdl.destroy_dataset(self.output_dataset)
            self.output_dataset = None
        if self.model_id is not None:
            acl.mdl.unload(self.model_id)
            self.model_id = None
        if self.desc is not None:
            acl.mdl.destroy_desc(self.desc)
            self.desc = None


class _BertVocabTokenizer:
    """Small dependency-free BERT tokenizer matching Chinese-CLIP's contract."""

    def __init__(
        self,
        path: Path,
        pad_token: str = "[PAD]",
        unk_token: str = "[UNK]",
        cls_token: str = "[CLS]",
        sep_token: str = "[SEP]",
    ):
        # BERT token ids are physical file-line offsets. ``str.splitlines``
        # treats Unicode line-separator tokens as newlines, unlike the pinned
        # Chinese-CLIP tokenizer, so read the UTF-8 file line by line.
        with path.open(encoding="utf-8") as handle:
            self.vocab = {
                token.strip(): index
                for index, token in enumerate(handle)
            }
        required = {
            "pad": pad_token,
            "unknown": unk_token,
            "classification": cls_token,
            "separator": sep_token,
        }
        missing = [name for name, token in required.items() if token not in self.vocab]
        if missing:
            raise EmbeddingError(
                f"BERT vocabulary {path} is missing required token(s): {', '.join(missing)}"
            )
        self.pad_id = self.vocab[pad_token]
        self.unk_id = self.vocab[unk_token]
        self.cls_id = self.vocab[cls_token]
        self.sep_id = self.vocab[sep_token]

    @staticmethod
    def _clean(text: str) -> str:
        output = []
        for char in text:
            code = ord(char)
            if code in (0, 0xFFFD) or (0 <= code <= 31 and code not in (9, 10, 13)):
                continue
            output.append(" " if code == 0xA0 else char)
        return "".join(output).lower()

    @staticmethod
    def _is_cjk(char: str) -> bool:
        code = ord(char)
        return (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
            or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
        )

    @staticmethod
    def _is_punctuation(char: str) -> bool:
        return char in string.punctuation or unicodedata.category(char).startswith("P")

    def _basic_tokens(self, text: str):
        text = self._clean(text)
        spaced = []
        for char in text:
            if self._is_cjk(char):
                spaced.extend((" ", char, " "))
            else:
                spaced.append(char)
        tokens = "".join(spaced).strip().split()
        output = []
        for token in tokens:
            start = 0
            for index, char in enumerate(token):
                if self._is_punctuation(char):
                    if start < index:
                        output.append(token[start:index])
                    output.append(char)
                    start = index + 1
            if start < len(token):
                output.append(token[start:])
        return [token for token in output if token]

    def _wordpiece(self, token: str):
        if token in self.vocab:
            return [token]
        chars = list(token)
        pieces = []
        start = 0
        while start < len(chars):
            end = len(chars)
            found = None
            while start < end:
                piece = "".join(chars[start:end])
                if start:
                    piece = "##" + piece
                if piece in self.vocab:
                    found = piece
                    break
                end -= 1
            if found is None:
                return ["[UNK]"]
            pieces.append(found)
            start = end
        return pieces

    def encode(self, text: str, length: int) -> np.ndarray:
        pieces = []
        for token in self._basic_tokens(text):
            pieces.extend(self._wordpiece(token))
        ids = [self.cls_id]
        ids.extend(self.vocab.get(piece, self.unk_id) for piece in pieces[: max(0, length - 2)])
        ids.append(self.sep_id)
        ids.extend([self.pad_id] * (length - len(ids)))
        return np.asarray([ids[:length]], dtype=np.int64)


class TokenizerAdapter:
    """Use model metadata with a local tokenizer.json asset."""

    def __init__(self, model: ModelRecord):
        if not model.tokenizer:
            raise EmbeddingError(f"{model.model_id} has no text tokenizer contract")
        path = Path(str(model.tokenizer["path"]))
        if not path.is_absolute():
            path = (Path(__file__).resolve().parent / path).resolve()
        if not path.is_file():
            raise EmbeddingError(f"tokenizer asset does not exist: {path}")
        self.length = int(model.tokenizer["context_length"])
        self.pad_id = int(model.tokenizer.get("pad_token_id", 0))
        if model.tokenizer.get("kind") == "bert":
            self.tokenizer = _BertVocabTokenizer(
                path,
                pad_token=str(model.tokenizer.get("pad_token", "[PAD]")),
                unk_token=str(model.tokenizer.get("unk_token", "[UNK]")),
                cls_token=str(model.tokenizer.get("cls_token", "[CLS]")),
                sep_token=str(model.tokenizer.get("sep_token", "[SEP]")),
            )
            self.pad_id = self.tokenizer.pad_id
            self.kind = "bert"
        else:
            try:
                from tokenizers import Tokenizer
            except ImportError as exc:
                raise EmbeddingError("the tokenizers package is required for text search") from exc
            self.tokenizer = Tokenizer.from_file(str(path))
            self.kind = "tokenizers"

    def encode(self, text: str, dtype) -> np.ndarray:
        text = (text or "").strip()
        if not text:
            raise EmbeddingError("text query must not be empty")
        if self.kind == "bert":
            return self.tokenizer.encode(text, self.length).astype(dtype, copy=False)
        ids = self.tokenizer.encode(text, add_special_tokens=True).ids[: self.length]
        ids.extend([self.pad_id] * (self.length - len(ids)))
        return np.asarray([ids], dtype=dtype)


class EmbeddingBackend:
    @property
    def model_id(self) -> str:
        raise NotImplementedError

    @property
    def embedding_dim(self) -> int:
        raise NotImplementedError

    def encode_image(self, image_bgr: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def encode_text(self, text: str) -> np.ndarray:
        raise NotImplementedError

    def release(self):
        raise NotImplementedError


class NpuEmbeddingBackend(EmbeddingBackend):
    """Image/text encoder backed exclusively by production-admitted OM files."""

    def __init__(
        self,
        record: ModelRecord,
        resource: AscendResource,
        model_factory: Callable[[AscendResource, Path], AclModel] = AclModel,
    ):
        record.verify_artifacts(require_hashes=True)
        self.record = record
        self.resource = resource
        self._model_factory = model_factory
        self._models: Dict[str, AclModel] = {}
        self._tokenizer: Optional[TokenizerAdapter] = None
        self._lock = threading.RLock()

    @property
    def model_id(self) -> str:
        return self.record.model_id

    @property
    def embedding_dim(self) -> int:
        return self.record.embedding_dim

    def _model(self, kind: str) -> AclModel:
        contract = self.record.components.get(kind)
        if contract is None:
            raise EmbeddingError(f"{self.model_id} does not support {kind} encoding")
        if kind not in self._models:
            self._models[kind] = self._model_factory(self.resource, contract.om_path)
        return self._models[kind]

    def _decode(self, raw: bytes, contract: ComponentContract) -> np.ndarray:
        dtype = NUMPY_DTYPES[contract.output_dtype]
        expected = self.embedding_dim * np.dtype(dtype).itemsize
        if len(raw) != expected:
            raise EmbeddingError(
                f"{self.model_id}/{contract.kind} returned {len(raw)} bytes, expected {expected}"
            )
        return l2_normalize(np.frombuffer(raw, dtype=dtype).astype(np.float32))

    def preprocess_image(self, image_bgr: np.ndarray) -> np.ndarray:
        if not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
            raise EmbeddingError("image must be a BGR uint8 array with three channels")
        try:
            import cv2
        except ImportError as exc:
            raise EmbeddingError("OpenCV is required for image preprocessing") from exc
        config = self.record.image_preprocess
        size = int(config["size"])
        image = cv2.resize(image_bgr, (size, size), interpolation=cv2.INTER_CUBIC)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.asarray(config["mean"], dtype=np.float32)
        std = np.asarray(config["std"], dtype=np.float32)
        if np.any(std == 0):
            raise EmbeddingError(f"{self.model_id} has an invalid zero image std")
        image = (image - mean) / std
        return np.ascontiguousarray(image.transpose(2, 0, 1)[None, ...], dtype=np.float32)

    def encode_image(self, image_bgr: np.ndarray) -> np.ndarray:
        contract = self.record.components["image"]
        tensor = self.preprocess_image(image_bgr).astype(NUMPY_DTYPES[contract.input_dtype], copy=False)
        if tuple(tensor.shape) != contract.input_shape:
            raise EmbeddingError(f"image tensor shape {tensor.shape} does not match {contract.input_shape}")
        with self._lock:
            output = self._model("image").execute([tensor])[0]
        return self._decode(output, contract)

    def encode_text(self, text: str) -> np.ndarray:
        contract = self.record.components.get("text")
        if contract is None:
            raise EmbeddingError(f"{self.model_id} does not support text queries")
        if self._tokenizer is None:
            self._tokenizer = TokenizerAdapter(self.record)
        tokens = self._tokenizer.encode(text, NUMPY_DTYPES[contract.input_dtype])
        if tuple(tokens.shape) != contract.input_shape:
            raise EmbeddingError(f"text tensor shape {tokens.shape} does not match {contract.input_shape}")
        with self._lock:
            output = self._model("text").execute([tokens])[0]
        return self._decode(output, contract)

    def release(self):
        for model in self._models.values():
            model.release()
        self._models.clear()


class ModelManager:
    """Keep at most one admitted model active and serialize NPU access."""

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        resource_factory: Callable[[int], AscendResource] = AscendResource,
        backend_factory: Callable[[ModelRecord, AscendResource], EmbeddingBackend] = NpuEmbeddingBackend,
    ):
        self.registry = registry or ModelRegistry()
        self._resource_factory = resource_factory
        self._backend_factory = backend_factory
        self._resource: Optional[AscendResource] = None
        self._active: Optional[EmbeddingBackend] = None
        self._lock = threading.RLock()

    def get(self, model_id: str) -> EmbeddingBackend:
        with self._lock:
            record = self.registry.get(model_id)
            if self._active is not None and self._active.model_id == model_id:
                return self._active
            if self._active is not None:
                self._active.release()
                self._active = None
            if self._resource is None:
                self._resource = self._resource_factory(NPU_DEVICE_ID)
            try:
                self._active = self._backend_factory(record, self._resource)
            except (RegistryError, EmbeddingError) as exc:
                raise EmbeddingError(str(exc)) from exc
            return self._active

    def encode_image(self, model_id: str, image_bgr: np.ndarray) -> np.ndarray:
        """Execute while holding the same lock used for model switching."""
        with self._lock:
            return self.get(model_id).encode_image(image_bgr)

    def encode_text(self, model_id: str, text: str) -> np.ndarray:
        with self._lock:
            return self.get(model_id).encode_text(text)

    def release(self):
        with self._lock:
            if self._active is not None:
                self._active.release()
                self._active = None
            if self._resource is not None:
                self._resource.release()
                self._resource = None
