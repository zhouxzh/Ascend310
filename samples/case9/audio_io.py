"""Audio capture/playback and offline speech runtime adapters.

The local chat service deliberately keeps audio in memory.  PulseAudio is
invoked with argument lists (never through a shell) and receives/returns raw
signed 16-bit PCM.  The speech adapters are lazy: importing this module does
not require sherpa-onnx or Piper, which keeps the gateway's normal test suite
usable on a development machine.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import signal
import struct
import threading
from array import array
from dataclasses import dataclass
from typing import Any, Optional, Protocol


DEFAULT_AUDIO_SOURCE = (
    "alsa_input.usb-046d_C922_Pro_Stream_Webcam_B7E0139F-02.analog-stereo"
)
DEFAULT_AUDIO_SINK = (
    "alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344637353804-00.analog-stereo"
)


class AudioError(RuntimeError):
    """Base error for capture and playback failures."""


class AudioBusyError(AudioError):
    """The single process-wide audio operation is already in use."""


class SpeechRuntimeError(RuntimeError):
    """An ASR/TTS runtime or model is unavailable or failed."""


@dataclass(frozen=True)
class AudioSettings:
    """PulseAudio and PCM settings for one local-chat process."""

    source: str = DEFAULT_AUDIO_SOURCE
    sink: str = DEFAULT_AUDIO_SINK
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2
    max_duration_seconds: float = 30.0
    playback_sample_rate: int = 22_050
    parec_binary: str = "parec"
    paplay_binary: str = "paplay"

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.sink.strip():
            raise AudioError("audio source and sink must not be empty")
        if self.sample_rate != 16_000:
            raise AudioError("capture sample rate is fixed at 16000 for Zipformer")
        if self.channels != 1:
            raise AudioError("audio channels are fixed at 1 for local Chinese chat")
        if self.sample_width != 2:
            raise AudioError("PCM sample width is fixed at 2 bytes")
        if (
            not math.isfinite(self.max_duration_seconds)
            or not 0.1 <= self.max_duration_seconds <= 30.0
        ):
            raise AudioError("capture duration must be between 0.1 and 30 seconds")
        if self.playback_sample_rate != 22_050:
            raise AudioError("playback sample rate is fixed at 22050 for Huayan VITS")
        if not self.parec_binary.strip() or not self.paplay_binary.strip():
            raise AudioError("audio command names must not be empty")

    @classmethod
    def from_environ(cls) -> "AudioSettings":
        def integer(name: str, default: int, minimum: int) -> int:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise AudioError(f"{name} must be an integer") from exc
            if value < minimum:
                raise AudioError(f"{name} must be at least {minimum}")
            return value

        def decimal(name: str, default: float, minimum: float) -> float:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise AudioError(f"{name} must be a number") from exc
            if not math.isfinite(value):
                raise AudioError(f"{name} must be finite")
            if value < minimum:
                raise AudioError(f"{name} must be at least {minimum}")
            return value

        source = os.environ.get(
            "LOCAL_CHAT_PULSE_SOURCE",
            os.environ.get("AUDIO_SOURCE", DEFAULT_AUDIO_SOURCE),
        ).strip()
        sink = os.environ.get(
            "LOCAL_CHAT_PULSE_SINK",
            os.environ.get("AUDIO_SINK", DEFAULT_AUDIO_SINK),
        ).strip()
        if not source or not sink:
            raise AudioError("AUDIO_SOURCE and AUDIO_SINK must not be empty")
        channels = integer("AUDIO_CHANNELS", 1, 1)
        if channels != 1:
            raise AudioError("AUDIO_CHANNELS is fixed at 1 for local Chinese chat")
        max_duration = decimal("AUDIO_MAX_DURATION_SECONDS", 30.0, 0.1)
        if max_duration > 30.0:
            raise AudioError("AUDIO_MAX_DURATION_SECONDS must not exceed 30")
        sample_rate = integer("AUDIO_SAMPLE_RATE", 16_000, 8_000)
        if sample_rate != 16_000:
            raise AudioError("AUDIO_SAMPLE_RATE is fixed at 16000 for Zipformer")
        playback_sample_rate = integer("AUDIO_PLAYBACK_SAMPLE_RATE", 22_050, 8_000)
        if playback_sample_rate != 22_050:
            raise AudioError("AUDIO_PLAYBACK_SAMPLE_RATE is fixed at 22050 for Huayan VITS")
        return cls(
            source=source,
            sink=sink,
            sample_rate=sample_rate,
            channels=channels,
            sample_width=2,
            max_duration_seconds=max_duration,
            playback_sample_rate=playback_sample_rate,
            parec_binary=os.environ.get("PAREC_BINARY", "parec").strip() or "parec",
            paplay_binary=os.environ.get("PAPLAY_BINARY", "paplay").strip() or "paplay",
        )


class CaptureHandle(Protocol):
    async def stop(self) -> bytes:
        """Stop capture, release the global operation, and return PCM bytes."""


class AudioBackend(Protocol):
    async def start_capture(self) -> CaptureHandle:
        """Start one raw PCM capture operation."""

    async def play_pcm(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        """Play raw PCM through the configured sink."""


class _PulseCapture:
    def __init__(self, owner: "PulseAudioBackend", process: asyncio.subprocess.Process):
        self._owner = owner
        self._process = process
        self._buffer = bytearray()
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stopped = False

    async def _read_stdout(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        try:
            while True:
                chunk = await stdout.read(64 * 1024)
                if not chunk:
                    return
                self._buffer.extend(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The process exit status is reported by stop(); do not leak a
            # traceback from a normal device disconnect.
            return

    async def stop(self) -> bytes:
        if self._stopped:
            return bytes(self._buffer)
        self._stopped = True
        try:
            if self._process.returncode is None:
                self._process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=1.5)
                    except asyncio.TimeoutError:
                        self._process.kill()
                        await self._process.wait()
            await asyncio.wait_for(self._reader_task, timeout=2.0)
            if self._process.returncode not in (0, -signal.SIGINT, -signal.SIGTERM):
                raise AudioError(
                    f"parec exited with status {self._process.returncode}; "
                    "check the PulseAudio source and device permissions"
                )
            return bytes(self._buffer)
        finally:
            if not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
            await self._owner._release()


class PulseAudioBackend:
    """Raw PulseAudio backend with one process-wide operation at a time."""

    def __init__(self, settings: Optional[AudioSettings] = None):
        self.settings = settings or AudioSettings.from_environ()
        # The operation flag is only inspected/updated in short synchronous
        # sections.  A thread lock avoids binding the backend to an event loop
        # at construction time, which is required by Python 3.9 on the board.
        self._operation_state_lock = threading.Lock()
        self._operation_in_use = False
        self._active_capture: Optional[_PulseCapture] = None

    def _require_binary(self, binary: str) -> str:
        resolved = shutil.which(binary)
        if resolved is None:
            raise AudioError(
                f"Required audio command '{binary}' was not found; "
                "install PulseAudio client tools on the board"
            )
        return resolved

    async def _acquire(self) -> None:
        with self._operation_state_lock:
            if self._operation_in_use:
                raise AudioBusyError("The microphone or speaker is already in use")
            self._operation_in_use = True

    async def _release(self) -> None:
        with self._operation_state_lock:
            if not self._operation_in_use:
                return
            self._operation_in_use = False
            self._active_capture = None

    async def start_capture(self) -> CaptureHandle:
        await self._acquire()
        try:
            binary = self._require_binary(self.settings.parec_binary)
            command = [
                binary,
                "--device",
                self.settings.source,
                "--rate",
                str(self.settings.sample_rate),
                "--channels",
                str(self.settings.channels),
                "--format",
                "s16le",
                "--raw",
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            capture = _PulseCapture(self, process)
            self._active_capture = capture
            await asyncio.sleep(0)
            if process.returncode is not None and process.returncode != 0:
                await capture.stop()
                raise AudioError(
                    f"parec exited immediately with status {process.returncode}"
                )
            return capture
        except Exception:
            await self._release()
            raise

    async def play_pcm(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        if not pcm:
            return
        if len(pcm) % self.settings.sample_width:
            raise AudioError("PCM payload is not aligned to the configured sample width")
        # Validate the caller-provided format before probing paplay.  This
        # keeps contract errors deterministic on development hosts where the
        # board-only PulseAudio client is intentionally unavailable.
        rate = self.settings.playback_sample_rate if sample_rate is None else sample_rate
        if rate != self.settings.playback_sample_rate:
            raise AudioError(
                f"Playback sample rate must be {self.settings.playback_sample_rate} Hz"
            )
        await self._acquire()
        process: Optional[asyncio.subprocess.Process] = None
        try:
            binary = self._require_binary(self.settings.paplay_binary)
            command = [
                binary,
                "--device",
                self.settings.sink,
                "--rate",
                str(rate),
                "--channels",
                str(self.settings.channels),
                "--format",
                "s16le",
                "--raw",
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdin = process.stdin
            if stdin is None:
                raise AudioError("paplay did not expose stdin")
            stdin.write(pcm)
            try:
                await asyncio.wait_for(stdin.drain(), timeout=30.0)
            except asyncio.TimeoutError as exc:
                raise AudioError("paplay stopped accepting audio data") from exc
            stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=30.0)
            except asyncio.TimeoutError as exc:
                raise AudioError("paplay playback timed out") from exc
            if process.returncode != 0:
                raise AudioError(
                    f"paplay exited with status {process.returncode}; "
                    "check the PulseAudio sink and device permissions"
                )
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await self._release()


class SpeechRecognizer(Protocol):
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Return recognized text from signed 16-bit little-endian PCM."""


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return raw signed 16-bit little-endian PCM."""

    @property
    def sample_rate(self) -> int:
        """Sample rate of synthesize() output."""


class UnavailableSpeechRecognizer:
    """Explicit failure used when the board ASR runtime is not configured."""

    def __init__(self, reason: str):
        self.reason = reason

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise SpeechRuntimeError(self.reason)


class UnavailableSpeechSynthesizer:
    """Explicit failure used when the board TTS runtime is not configured."""

    sample_rate = 22_050

    def __init__(self, reason: str):
        self.reason = reason

    async def synthesize(self, text: str) -> bytes:
        raise SpeechRuntimeError(self.reason)


def _pcm16_to_float(pcm: bytes) -> list[float]:
    if len(pcm) % 2:
        raise SpeechRuntimeError("ASR received an unaligned PCM payload")
    values = array("h")
    values.frombytes(pcm)
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        values.byteswap()
    return [value / 32768.0 for value in values]


class SherpaOnnxRecognizer:
    """Lazy sherpa-onnx online Zipformer recognizer.

    Set ``SHERPA_MODEL_DIR`` or the four individual ``SHERPA_*`` model
    variables.  The directory form accepts the standard encoder/decoder/
    joiner/tokens names emitted by the sherpa-onnx model downloads.
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        num_threads: int = 2,
        provider: str = "cpu",
    ):
        self.model_dir = model_dir or os.environ.get("SHERPA_MODEL_DIR", "").strip()
        self.num_threads = num_threads
        self.provider = provider
        self._recognizer: Any = None

    def _model_paths(self) -> tuple[str, str, str, str]:
        directory = self.model_dir
        names = (
            os.environ.get("SHERPA_ENCODER", "encoder-epoch-99-avg-1.int8.onnx"),
            os.environ.get("SHERPA_DECODER", "decoder-epoch-99-avg-1.int8.onnx"),
            os.environ.get("SHERPA_JOINER", "joiner-epoch-99-avg-1.int8.onnx"),
            os.environ.get("SHERPA_TOKENS", "tokens.txt"),
        )
        if directory:
            paths = tuple(
                name if os.path.isabs(name) else os.path.join(directory, name)
                for name in names
            )
        else:
            configured = [os.environ.get(key, "").strip() for key in (
                "SHERPA_ENCODER", "SHERPA_DECODER", "SHERPA_JOINER", "SHERPA_TOKENS"
            )]
            if not all(configured):
                raise SpeechRuntimeError(
                    "SHERPA_MODEL_DIR or all SHERPA_ENCODER/DECODER/JOINER/TOKENS "
                    "paths must be configured before local speech recognition"
                )
            paths = tuple(os.path.abspath(path) for path in configured)
        missing = [path for path in paths if not os.path.isfile(path)]
        if missing:
            raise SpeechRuntimeError(
                "sherpa-onnx ASR model files are missing: " + ", ".join(missing)
            )
        return paths  # type: ignore[return-value]

    def _load(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        try:
            import sherpa_onnx  # type: ignore
        except ImportError as exc:
            raise SpeechRuntimeError(
                "sherpa_onnx is not installed in the selected Python environment"
            ) from exc
        encoder, decoder, joiner, tokens = self._model_paths()
        try:
            # ``from_transducer`` is the stable public constructor used by
            # sherpa-onnx's online ASR examples.  Keep a configuration-object
            # fallback for older aarch64 wheels already used on some boards.
            factory = getattr(sherpa_onnx.OnlineRecognizer, "from_transducer", None)
            if callable(factory):
                self._recognizer = factory(
                    tokens=tokens,
                    encoder=encoder,
                    decoder=decoder,
                    joiner=joiner,
                    num_threads=self.num_threads,
                    sample_rate=16_000,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    provider=self.provider,
                )
            else:
                model_config = sherpa_onnx.OnlineTransducerModelConfig(
                    encoder=encoder,
                    decoder=decoder,
                    joiner=joiner,
                    tokens=tokens,
                    num_threads=self.num_threads,
                    provider=self.provider,
                )
                config = sherpa_onnx.OnlineRecognizerConfig(model_config=model_config)
                self._recognizer = sherpa_onnx.OnlineRecognizer(config)
        except Exception as exc:
            raise SpeechRuntimeError(f"could not initialize sherpa-onnx ASR: {exc}") from exc
        return self._recognizer

    def _transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if sample_rate != 16_000:
            raise SpeechRuntimeError("ASR sample rate must be 16000 Hz")
        recognizer = self._load()
        stream = recognizer.create_stream()
        samples = _pcm16_to_float(pcm)
        stream.accept_waveform(sample_rate, samples)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        get_result = getattr(recognizer, "get_result_all", None)
        if not callable(get_result):
            get_result = getattr(recognizer, "get_result", None)
        if not callable(get_result):
            raise SpeechRuntimeError("sherpa-onnx runtime does not expose a result method")
        result = get_result(stream)
        return str(getattr(result, "text", result) or "").strip()

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not pcm:
            return ""
        return await asyncio.to_thread(self._transcribe, pcm, sample_rate)


class SherpaOnnxSynthesizer:
    """Lazy sherpa-onnx VITS/Piper adapter returning in-memory PCM."""

    def __init__(self, model_dir: Optional[str] = None, num_threads: int = 2, provider: str = "cpu"):
        self.model_dir = model_dir or os.environ.get("SHERPA_TTS_MODEL_DIR", "").strip()
        self.num_threads = num_threads
        self.provider = provider
        self._tts: Any = None
        self._sample_rate = 22_050

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _paths(self) -> tuple[str, str, str]:
        if not self.model_dir:
            raise SpeechRuntimeError(
                "SHERPA_TTS_MODEL_DIR must point to the Huayan VITS model directory"
            )
        model_name = os.environ.get("SHERPA_TTS_MODEL", "zh_CN-huayan-medium.onnx")
        tokens_name = os.environ.get("SHERPA_TTS_TOKENS", "tokens.txt")
        data_name = os.environ.get("SHERPA_TTS_DATA_DIR", "espeak-ng-data")
        model = model_name if os.path.isabs(model_name) else os.path.join(self.model_dir, model_name)
        tokens = tokens_name if os.path.isabs(tokens_name) else os.path.join(self.model_dir, tokens_name)
        data_dir = data_name if os.path.isabs(data_name) else os.path.join(self.model_dir, data_name)
        missing = [path for path in (model, tokens, data_dir) if not os.path.exists(path)]
        if missing:
            raise SpeechRuntimeError("sherpa-onnx TTS model files are missing: " + ", ".join(missing))
        return model, tokens, data_dir

    def _load(self) -> Any:
        if self._tts is not None:
            return self._tts
        try:
            import sherpa_onnx  # type: ignore
        except ImportError as exc:
            raise SpeechRuntimeError(
                "sherpa_onnx is not installed in the selected Python environment"
            ) from exc
        model, tokens, data_dir = self._paths()
        try:
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=model, lexicon="", tokens=tokens, data_dir=data_dir
                    ),
                    num_threads=self.num_threads,
                    provider=self.provider,
                ),
                rule_fsts="",
                max_num_sentences=1,
            )
            if not config.validate():
                raise SpeechRuntimeError("sherpa-onnx rejected the configured TTS model")
            self._tts = sherpa_onnx.OfflineTts(config)
        except SpeechRuntimeError:
            raise
        except Exception as exc:
            raise SpeechRuntimeError(f"could not initialize sherpa-onnx TTS: {exc}") from exc
        return self._tts

    @staticmethod
    def _to_pcm16(samples: Any) -> bytes:
        values = array("h")
        for sample in samples:
            scaled = int(round(float(sample) * 32767.0))
            values.append(max(-32768, min(32767, scaled)))
        if struct.pack("=h", 1) != struct.pack("<h", 1):
            values.byteswap()
        return values.tobytes()

    def _synthesize(self, text: str) -> bytes:
        generated = self._load().generate(text)
        sample_rate = int(getattr(generated, "sample_rate", self._sample_rate))
        if sample_rate != 22_050:
            raise SpeechRuntimeError(
                "sherpa-onnx TTS must return 22050 Hz PCM for the configured speaker path"
            )
        self._sample_rate = 22_050
        return self._to_pcm16(getattr(generated, "samples", ()))

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            return b""
        return await asyncio.to_thread(self._synthesize, text)


# Backwards-compatible name for callers that injected a fake Piper adapter in
# early experiments; production construction uses SherpaOnnxSynthesizer.
PiperSynthesizer = SherpaOnnxSynthesizer
