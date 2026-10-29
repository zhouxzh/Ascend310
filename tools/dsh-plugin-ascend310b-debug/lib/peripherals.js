/**
 * Fixed command builders, validators and parsers for Ascend 310B peripheral
 * tests. User values are serialized into a Python literal and never become
 * shell fragments.
 */

export const PERIPHERAL_OUTPUT_ROOT = '/tmp/ascend310b-debug/peripherals';
export const CAMERA_DEVICE = /^\/dev\/video\d{1,2}$/u;
export const ALSA_DEVICE = /^(?:default|(?:plug)?hw:\d+,\d+|(?:plug)?hw:CARD=[A-Za-z0-9_.-]+,DEV=\d+)$/u;
export const PULSE_DEVICE = /^[A-Za-z0-9_.:@-]{1,160}$/u;
export const RECORDED_AUDIO_PATH = /^\/tmp\/ascend310b-debug\/peripherals\/microphone-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z\.wav$/u;
export const CAMERA_IMAGE_PATH = /^\/tmp\/ascend310b-debug\/peripherals\/camera-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z\.jpg$/u;

function numberInRange(value, fallback, min, max, label, integer = true) {
	const number = Number(value ?? fallback);
	if (!Number.isFinite(number) || number < min || number > max || (integer && !Number.isInteger(number))) {
		throw new Error(`${label}必须在 ${min} 到 ${max} 之间${integer ? '且为整数' : ''}。`);
	}
	return number;
}

function choose(value, allowed, fallback, label) {
	const selected = String(value ?? fallback);
	if (!allowed.includes(selected)) throw new Error(`${label}必须是 ${allowed.join('、')} 之一。`);
	return selected;
}

/** Detect the intended audio backend from a device selector. */
export function detectAudioBackend(backend, device) {
	const requested = String(backend ?? 'auto');
	if (!['auto', 'alsa', 'pulse'].includes(requested)) throw new Error('backend 必须是 auto、alsa 或 pulse。');
	if (requested !== 'auto') return requested;
	const selector = String(device ?? '').trim();
	return /^(?:default|(?:plug)?hw:)/u.test(selector) ? 'alsa' : 'pulse';
}

/** Return the fixed artifact path for one operation. */
export function peripheralOutputPath(kind, now = Date.now()) {
	const names = { camera: 'camera', microphone: 'microphone', speaker: 'speaker' };
	const extensions = { camera: 'jpg', microphone: 'wav', speaker: 'wav' };
	if (!(kind in names)) throw new Error('未知外设输出类型。');
	const stamp = new Date(now).toISOString().replace(/[:.]/gu, '-');
	return `${PERIPHERAL_OUTPUT_ROOT}/${names[kind]}-${stamp}.${extensions[kind]}`;
}

/** Validate camera arguments. */
export function normalizeCameraOptions(options = {}, now = Date.now()) {
	const device = String(options.device ?? '');
	if (!CAMERA_DEVICE.test(device)) throw new Error('device 必须是 /dev/videoN。');
	const width = numberInRange(options.width, 640, 160, 1920, 'width');
	const height = numberInRange(options.height, 480, 120, 1080, 'height');
	const pixelFormat = choose(String(options.pixelFormat ?? 'MJPG').toUpperCase(), ['MJPG', 'YUYV'], 'MJPG', 'pixelFormat');
	const frameCount = numberInRange(options.frameCount, 1, 1, 10, 'frameCount');
	const fps = numberInRange(options.fps, 30, 1, 120, 'fps');
	return { device, width, height, pixelFormat, frameCount, fps, output: peripheralOutputPath('camera', now) };
}

/** Validate microphone arguments and resolve auto backend. */
export function normalizeMicrophoneOptions(options = {}, now = Date.now()) {
	const requestedDevice = typeof options.device === 'string' && options.device.trim() !== '' ? options.device.trim() : null;
	const backend = detectAudioBackend(options.backend, requestedDevice);
	const device = requestedDevice ?? (backend === 'alsa' ? 'default' : '@DEFAULT_SOURCE@');
	if (backend === 'alsa' && !ALSA_DEVICE.test(device)) {
		throw new Error('ALSA device 必须是 default、hw:N,M 或 plughw:N,M。');
	}
	if (backend === 'pulse') {
		if (!PULSE_DEVICE.test(device)) throw new Error('Pulse source 名称包含不允许的字符。');
		if (device.endsWith('.monitor') || device === '@DEFAULT_MONITOR@') {
			throw new Error('Pulse source 是输出监视源 .monitor，不能作为麦克风输入。');
		}
	}
	const sampleRate = numberInRange(options.sampleRate, 16000, 8000, 48000, 'sampleRate');
	const channels = numberInRange(options.channels, 1, 1, 2, 'channels');
	const durationSec = numberInRange(options.durationSec, 3, 1, 10, 'durationSec');
	return {
		backend,
		device,
		sampleRate,
		channels,
		durationSec,
		output: peripheralOutputPath('microphone', now)
	};
}

/** Resolve and validate one playback backend/device pair. */
function normalizePlaybackRoute(options = {}) {
	const requestedDevice = typeof options.device === 'string' && options.device.trim() !== '' ? options.device.trim() : null;
	const backend = detectAudioBackend(options.backend, requestedDevice);
	const device = requestedDevice ?? (backend === 'alsa' ? 'default' : '@DEFAULT_SINK@');
	if (backend === 'alsa' && !ALSA_DEVICE.test(device)) {
		throw new Error('ALSA sink 必须是 default、hw:N,M 或 plughw:N,M。');
	}
	if (backend === 'pulse' && !PULSE_DEVICE.test(device)) {
		throw new Error('Pulse sink 名称包含不允许的字符。');
	}
	return { backend, device };
}

/** Validate speaker arguments and resolve auto backend. */
export function normalizeSpeakerOptions(options = {}, now = Date.now()) {
	const { backend, device } = normalizePlaybackRoute(options);
	const frequency = numberInRange(options.frequency, 440, 200, 2000, 'frequency', false);
	const durationSec = numberInRange(options.durationSec, 1, 0.25, 5, 'durationSec', false);
	const sampleRate = numberInRange(options.sampleRate, 48000, 8000, 48000, 'sampleRate');
	const channels = numberInRange(options.channels, 1, 1, 2, 'channels');
	return {
		backend,
		device,
		frequency,
		durationSec,
		sampleRate,
		channels,
		output: peripheralOutputPath('speaker', now)
	};
}

/** Validate playback of a previously recorded microphone WAV. */
export function normalizeRecordedPlaybackOptions(options = {}) {
	const recording = String(options.recording ?? '').trim();
	if (!RECORDED_AUDIO_PATH.test(recording)) {
		throw new Error('recording 必须是插件固定录音目录中的 microphone-<timestamp>.wav。');
	}
	const { backend, device } = normalizePlaybackRoute(options);
	return { backend, device, recording, output: recording };
}

const CAMERA_SCRIPT = String.raw`
import hashlib, json, os, subprocess, sys

try:
    import cv2
except Exception as cv_error:
    cv2 = None
    cv2_error = type(cv_error).__name__ + ": " + str(cv_error)

cfg = CONFIG
cap = None
try:
    os.makedirs(os.path.dirname(cfg["output"]), exist_ok=True)
    if cv2 is not None:
        capture_backend = "opencv"
        cap = cv2.VideoCapture(cfg["device"], cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(cfg["device"])
        if not cap.isOpened():
            raise RuntimeError("VideoCapture 无法打开设备")
        code = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        requested_code = cv2.VideoWriter_fourcc(*cfg["pixelFormat"])
        cap.set(cv2.CAP_PROP_FOURCC, requested_code)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
        frame_ok = 0
        failures = 0
        frame = None
        for _ in range(cfg["frameCount"]):
            ok, candidate = cap.read()
            if ok and candidate is not None and getattr(candidate, "size", 0) > 0:
                frame_ok += 1
                frame = candidate
            else:
                failures += 1
        if frame is None:
            raise RuntimeError("frame.read 未取得非空帧")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        actual_code = int(cap.get(cv2.CAP_PROP_FOURCC) or code)
        fourcc = "".join(chr((actual_code >> (8 * i)) & 0xFF) for i in range(4))
        actual_width = int(frame.shape[1])
        actual_height = int(frame.shape[0])
        actual_fps = round(float(cap.get(cv2.CAP_PROP_FPS) or 0), 3)
        mean_luminance = round(float(gray.mean()), 6)
        laplacian_variance = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 6)
        saved = bool(cv2.imwrite(cfg["output"], frame))
        if not saved:
            raise RuntimeError("cv2.imwrite 写入 JPEG 失败")
    else:
        from PIL import Image, ImageFilter, ImageStat
        capture_backend = "ffmpeg+Pillow"
        input_format = "mjpeg" if cfg["pixelFormat"] == "MJPG" else "yuyv422"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "v4l2", "-input_format", input_format,
            "-video_size", str(cfg["width"]) + "x" + str(cfg["height"]),
            "-i", cfg["device"], "-frames:v", "1", cfg["output"]
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg 采集失败: " + proc.stderr[-2000:])
        image = Image.open(cfg["output"])
        image.load()
        gray_image = image.convert("L")
        actual_width = image.width
        actual_height = image.height
        actual_fps = None
        fourcc = cfg["pixelFormat"]
        frame_ok = 1
        failures = 0
        mean_luminance = round(float(ImageStat.Stat(gray_image).mean[0]), 6)
        edge_kernel = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128)
        laplacian_variance = round(float(ImageStat.Stat(gray_image.filter(edge_kernel)).var[0]), 6)
    with open(cfg["output"], "rb") as fh:
        data = fh.read()
    result = {
        "ok": True,
        "device": cfg["device"],
        "requested": cfg,
        "captureBackend": capture_backend,
        "actualWidth": actual_width,
        "actualHeight": actual_height,
        "actualFps": actual_fps,
        "fourcc": fourcc,
        "frameCount": frame_ok,
        "readFailures": failures,
        "meanLuminance": mean_luminance,
        "laplacianVariance": laplacian_variance,
        "output": cfg["output"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "cv2Error": cv2_error if cv2 is None else None
    }
    if cap is not None:
        cap.release()
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "device": cfg.get("device")}, ensure_ascii=False))
    sys.exit(2)
`;

const MICROPHONE_SCRIPT = String.raw`
import array, hashlib, json, math, os, signal, subprocess, sys, time, wave

cfg = CONFIG
cmd = None
resolved_device = cfg["device"]
capture_started = None
capture_elapsed_sec = None
try:
    os.makedirs(os.path.dirname(cfg["output"]), exist_ok=True)
    capture_started = time.monotonic()
    if cfg["backend"] == "alsa":
        cmd = ["arecord", "-D", cfg["device"], "-f", "S16_LE", "-r", str(cfg["sampleRate"]), "-c", str(cfg["channels"]), "-d", str(cfg["durationSec"]), cfg["output"]]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=cfg["durationSec"] + 4)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()[-2000:]
            raise RuntimeError("ALSA 输入设备无法打开或录音失败（device=" + cfg["device"] + "）: " + detail)
    else:
        if cfg["device"].endswith(".monitor") or cfg["device"] == "@DEFAULT_MONITOR@":
            raise RuntimeError("拒绝 Pulse .monitor source")
        source = cfg["device"]
        if source == "@DEFAULT_SOURCE@":
            default_source = subprocess.run(["pactl", "get-default-source"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            source = default_source.stdout.strip() if default_source.returncode == 0 else ""
            if not source:
                raise RuntimeError("没有可用的 PulseAudio 默认输入源")
        if source.endswith(".monitor") or source == "@DEFAULT_MONITOR@":
            raise RuntimeError("PulseAudio 默认输入是输出监视源，不能作为麦克风（source=" + source + "）")
        resolved_device = source
        cmd = ["parecord", "--device=" + source, "--format=s16le", "--rate=" + str(cfg["sampleRate"]), "--channels=" + str(cfg["channels"]), "--file-format=wav", cfg["output"]]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            _, stderr = proc.communicate(timeout=cfg["durationSec"])
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGINT)
            try:
                _, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate(timeout=2)
                raise RuntimeError("PulseAudio 录音在达到请求时长后仍无法停止")
        # parecord finalizes the WAV in response to SIGINT; that signal status is expected.
        if proc.returncode not in (0, -signal.SIGINT, -signal.SIGTERM):
            raise RuntimeError("PulseAudio 录音失败: " + (stderr or "")[-2000:])
    capture_elapsed_sec = round(time.monotonic() - capture_started, 3)
    with open(cfg["output"], "rb") as fh:
        data = fh.read()
    with wave.open(cfg["output"], "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)
    if sample_width != 2 or not raw:
        raise RuntimeError("录音不是非空 PCM16 WAV")
    actual_duration = frame_count / max(sample_rate, 1)
    if actual_duration < max(0.5, cfg["durationSec"] - 0.75) or actual_duration > cfg["durationSec"] + 1.25:
        raise RuntimeError("录音时长异常: 请求 " + str(cfg["durationSec"]) + " 秒，WAV 实际 " + str(round(actual_duration, 3)) + " 秒")
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(float(value) * float(value) for value in samples) / len(samples))
    zero_ratio = sum(1 for value in samples if value == 0) / len(samples)
    clipping_ratio = sum(1 for value in samples if abs(value) >= 32760) / len(samples)
    result = {
        "ok": True,
        "evidence": "waveform-analysis",
        "backend": cfg["backend"],
        "device": resolved_device,
        "output": cfg["output"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "requestedDurationSec": cfg["durationSec"],
        "captureElapsedSec": capture_elapsed_sec,
        "durationSec": round(actual_duration, 3),
        "sampleRate": sample_rate,
        "channels": channels,
        "rms": round(rms, 3),
        "peak": peak,
        "zeroRatio": round(zero_ratio, 6),
        "clippingRatio": round(clipping_ratio, 6),
        "signalDetected": rms >= 1.0 and clipping_ratio < 0.25,
        "qualityPass": None,
        "humanConfirmationRequired": True,
        "command": cmd
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    capture_elapsed_sec = round(time.monotonic() - capture_started, 3) if capture_started is not None else None
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "captureElapsedSec": capture_elapsed_sec, "command": cmd, "requested": cfg}, ensure_ascii=False))
    sys.exit(2)
`;

const SPEAKER_SCRIPT = String.raw`
import hashlib, json, math, os, struct, subprocess, sys, wave

cfg = CONFIG
cmd = None
resolved_device = cfg["device"]
try:
    os.makedirs(os.path.dirname(cfg["output"]), exist_ok=True)
    total = max(1, int(cfg["sampleRate"] * cfg["durationSec"]))
    with wave.open(cfg["output"], "wb") as wav:
        wav.setnchannels(cfg["channels"])
        wav.setsampwidth(2)
        wav.setframerate(cfg["sampleRate"])
        frames = bytearray()
        for index in range(total):
            value = int(0.25 * 32767 * math.sin(2.0 * math.pi * cfg["frequency"] * index / cfg["sampleRate"]))
            frames.extend(struct.pack("<h", value) * cfg["channels"])
        wav.writeframes(bytes(frames))
    if cfg["backend"] == "alsa":
        cmd = ["aplay", "-D", cfg["device"], cfg["output"]]
    else:
        sink = cfg["device"]
        if sink == "@DEFAULT_SINK@":
            default_sink = subprocess.run(["pactl", "get-default-sink"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            sink = default_sink.stdout.strip() if default_sink.returncode == 0 else ""
            if not sink:
                raise RuntimeError("没有可用的 PulseAudio 默认输出设备")
        resolved_device = sink
        cmd = ["paplay", "--device=" + sink, cfg["output"]]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=cfg["durationSec"] + 10)
    if proc.returncode != 0:
        raise RuntimeError("播放命令失败: " + proc.stderr[-2000:])
    output_route = ("pulse:" + resolved_device) if cfg["backend"] == "pulse" else ("alsa:" + resolved_device)
    with open(cfg["output"], "rb") as fh:
        data = fh.read()
    result = {
        "ok": True,
        "evidence": "playback-command",
        "backend": cfg["backend"],
        "device": resolved_device,
        "outputRoute": output_route,
        "output": cfg["output"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "frequency": cfg["frequency"],
        "durationSec": cfg["durationSec"],
        "heard": None,
        "operatorConfirmationRequired": True,
        "volumeChanged": False,
        "command": cmd
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "command": cmd, "requested": cfg}, ensure_ascii=False))
    sys.exit(2)
`;

const RECORDED_PLAYBACK_SCRIPT = String.raw`
import hashlib, json, os, subprocess, sys, wave

cfg = CONFIG
cmd = None
resolved_device = cfg["device"]
try:
    recording = cfg["recording"]
    if not os.path.isfile(recording):
        raise RuntimeError("录音文件不存在")
    if os.path.getsize(recording) > 200 * 1024 * 1024:
        raise RuntimeError("录音文件超过 200 MB 安全上限")
    with wave.open(recording, "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
        compression = wav.getcomptype()
    if channels < 1 or sample_rate < 1 or sample_width < 1 or frame_count < 1 or compression != "NONE":
        raise RuntimeError("录音不是可播放的非空 PCM WAV")
    duration_sec = round(frame_count / sample_rate, 3)
    with open(recording, "rb") as fh:
        data = fh.read()
    if cfg["backend"] == "alsa":
        cmd = ["aplay", "-D", cfg["device"], recording]
    else:
        sink = cfg["device"]
        if sink == "@DEFAULT_SINK@":
            default_sink = subprocess.run(["pactl", "get-default-sink"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            sink = default_sink.stdout.strip() if default_sink.returncode == 0 else ""
            if not sink:
                raise RuntimeError("没有可用的 PulseAudio 默认输出设备")
        resolved_device = sink
        cmd = ["paplay", "--device=" + sink, recording]
    play_timeout = max(10.0, min(duration_sec + 10.0, 120.0))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=play_timeout)
    if proc.returncode != 0:
        raise RuntimeError("录音播放失败: " + proc.stderr[-2000:])
    output_route = ("pulse:" + resolved_device) if cfg["backend"] == "pulse" else ("alsa:" + resolved_device)
    result = {
        "ok": True,
        "evidence": "playback-command",
        "backend": cfg["backend"],
        "device": resolved_device,
        "outputRoute": output_route,
        "recording": recording,
        "sourceRecording": recording,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sampleRate": sample_rate,
        "channels": channels,
        "sampleWidth": sample_width,
        "frameCount": frame_count,
        "durationSec": duration_sec,
        "heard": None,
        "operatorConfirmationRequired": True,
        "volumeChanged": False,
        "command": cmd
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "command": cmd, "requested": cfg}, ensure_ascii=False))
    sys.exit(2)
`;

function pythonCommand(script, config) {
	const payloadLiteral = JSON.stringify(JSON.stringify(config)).replace(/'/gu, '\\u0027');
	const source = `import json\nCONFIG = json.loads(${payloadLiteral})\n${script}`;
	if (source.includes("'")) throw new Error('外设脚本包含不允许的单引号。');
	return `python -c '${source}'`;
}

export function buildCameraCommand(config) {
	return pythonCommand(CAMERA_SCRIPT, config);
}

export function buildMicrophoneCommand(config) {
	return pythonCommand(MICROPHONE_SCRIPT, config);
}

export function buildSpeakerCommand(config) {
	return pythonCommand(SPEAKER_SCRIPT, config);
}

export function buildRecordedPlaybackCommand(config) {
	return pythonCommand(RECORDED_PLAYBACK_SCRIPT, config);
}

/** Parse the final JSON line emitted by a peripheral script. */
export function parsePeripheralResult(stdout) {
	const lines = String(stdout ?? '').trim().split(/\r?\n/u);
	for (let index = lines.length - 1; index >= 0; index -= 1) {
		const line = lines[index].trim();
		if (!line.startsWith('{') || !line.endsWith('}')) continue;
		try {
			return JSON.parse(line);
		} catch {
			continue;
		}
	}
	return null;
}
