/**
 * Fixed command builders, validators and parsers for Ascend 310B peripheral
 * tests. User values are serialized into a Python literal and never become
 * shell fragments.
 */

import { randomBytes } from 'node:crypto';

export const PERIPHERAL_OUTPUT_ROOT = '/tmp/ascend310b-debug/peripherals';
export const CAMERA_DEVICE = /^\/dev\/video\d{1,2}$/u;
export const ALSA_DEVICE = /^(?:default|(?:plug)?hw:\d+,\d+|(?:plug)?hw:CARD=[A-Za-z0-9_.-]+,DEV=\d+)$/u;
export const PULSE_DEVICE = /^[A-Za-z0-9_.:@-]{1,160}$/u;
const OUTPUT_NONCE = /-[0-9a-f]{12}/u;
export const RECORDED_AUDIO_PATH = new RegExp(
	`^/tmp/ascend310b-debug/peripherals/microphone-\\d{4}-\\d{2}-\\d{2}T\\d{2}-\\d{2}-\\d{2}-\\d{3}Z(?:${OUTPUT_NONCE.source})?\\.wav$`,
	'u'
);
export const CAMERA_IMAGE_PATH = new RegExp(
	`^/tmp/ascend310b-debug/peripherals/camera-\\d{4}-\\d{2}-\\d{2}T\\d{2}-\\d{2}-\\d{2}-\\d{3}Z(?:${OUTPUT_NONCE.source})?\\.jpg$`,
	'u'
);

const activePeripheralLocks = new Set();

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

/** Return a collision-resistant artifact path for one operation. */
export function peripheralOutputPath(kind, now = Date.now(), nonce = randomBytes(6).toString('hex')) {
	const names = { camera: 'camera', microphone: 'microphone', speaker: 'speaker' };
	const extensions = { camera: 'jpg', microphone: 'wav', speaker: 'wav' };
	if (!(kind in names)) throw new Error('未知外设输出类型。');
	if (!/^[0-9a-f]{12}$/u.test(nonce)) throw new Error('外设输出 nonce 必须是 12 位小写十六进制。');
	const stamp = new Date(now).toISOString().replace(/[:.]/gu, '-');
	return `${PERIPHERAL_OUTPUT_ROOT}/${names[kind]}-${stamp}-${nonce}.${extensions[kind]}`;
}

/** Build a lock identity from the resolved target and physical/logical device. */
export function peripheralLockKey(targetId, normalized) {
	const target = String(targetId ?? '').trim();
	const device = String(normalized?.device ?? '').trim();
	if (target === '' || device === '') throw new Error('外设锁需要 target 和 device。');
	return JSON.stringify([target, normalized?.backend ?? 'device', device]);
}

/** Reject concurrent use of the same device on the same target and always unlock. */
export async function withPeripheralLock(key, operation) {
	if (activePeripheralLocks.has(key)) {
		const error = new Error('同一目标上的该外设正在执行另一个操作，请等待完成后重试。');
		error.code = 'PERIPHERAL_BUSY';
		throw error;
	}
	activePeripheralLocks.add(key);
	try {
		return await operation();
	} finally {
		activePeripheralLocks.delete(key);
	}
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

const OUTPUT_MAINTENANCE_SCRIPT = String.raw`
def cleanup_outputs(current):
    root = os.path.dirname(current)
    os.makedirs(root, exist_ok=True)
    current_real = os.path.realpath(current)
    retained_count = 1
    retained_bytes = os.path.getsize(current) if os.path.isfile(current) else 0
    entries = []
    for entry in os.scandir(root):
        if not entry.is_file(follow_symlinks=False) or os.path.realpath(entry.path) == current_real:
            continue
        if not entry.name.startswith(("camera-", "microphone-", "speaker-")) or not entry.name.endswith((".jpg", ".wav")):
            continue
        stat = entry.stat(follow_symlinks=False)
        entries.append((stat.st_mtime, stat.st_size, entry.path))
    entries.sort(reverse=True)
    for _, size, path in entries:
        if retained_count < 32 and retained_bytes + size <= 256 * 1024 * 1024:
            retained_count += 1
            retained_bytes += size
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

def remove_failed_output(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
`;

const CAMERA_SCRIPT = String.raw`
import hashlib, json, os, re, subprocess, sys
${OUTPUT_MAINTENANCE_SCRIPT}

def jpeg_size(data):
    # Parse SOF markers without importing a decoder library.
    if data[:2] != b"\xff\xd8":
        raise RuntimeError("采集输出不是 JPEG")
    index = 2
    while index + 9 <= len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9):
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 7:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += length
    raise RuntimeError("无法解析 JPEG 尺寸")

def yuyv_to_rgb(data):
    rgb = bytearray((len(data) // 2) * 3)
    destination = 0
    for source in range(0, len(data), 4):
        y0, cb, y1, cr = data[source:source + 4]
        chroma_blue = cb - 128
        chroma_red = cr - 128
        for luma in (y0, y1):
            scaled_luma = max(0, luma - 16)
            red = min(255, max(0, (298 * scaled_luma + 409 * chroma_red + 128) >> 8))
            green = min(255, max(0, (298 * scaled_luma - 100 * chroma_blue - 208 * chroma_red + 128) >> 8))
            blue = min(255, max(0, (298 * scaled_luma + 516 * chroma_blue + 128) >> 8))
            rgb[destination:destination + 3] = bytes((red, green, blue))
            destination += 3
    return bytes(rgb)

def actual_format(device):
    proc = subprocess.run(["v4l2-ctl", "-d", device, "--get-fmt-video"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError("无法读取V4L2实际格式: " + proc.stderr[-2000:])
    size = re.search(r"Width/Height\s*:\s*(\d+)\s*/\s*(\d+)", proc.stdout)
    format_line = next((line for line in proc.stdout.splitlines() if "Pixel Format" in line), "")
    if size is None or ":" not in format_line:
        raise RuntimeError("V4L2未返回实际尺寸或像素格式")
    fourcc = format_line.rsplit(":", 1)[1].strip().split()[0].strip(chr(39))
    if not fourcc:
        raise RuntimeError("V4L2未返回实际像素格式")
    return int(size.group(1)), int(size.group(2)), fourcc

cfg = CONFIG
raw_output = cfg["output"] + ".raw"
try:
    cleanup_outputs(cfg["output"])
    cmd = [
        "v4l2-ctl", "-d", cfg["device"],
        "--set-fmt-video=width=" + str(cfg["width"]) + ",height=" + str(cfg["height"]) + ",pixelformat=" + cfg["pixelFormat"],
        "--stream-mmap", "--stream-count=" + str(cfg["frameCount"]),
        "--stream-to=" + raw_output
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError("V4L2采集失败: " + proc.stderr[-2000:])
    actual_width, actual_height, fourcc = actual_format(cfg["device"])
    if fourcc != cfg["pixelFormat"]:
        raise RuntimeError("V4L2实际格式不匹配: 请求 " + cfg["pixelFormat"] + "，实际 " + fourcc)
    with open(raw_output, "rb") as fh:
        stream = fh.read()
    if fourcc == "MJPG":
        end = stream.rfind(b"\xff\xd9")
        start = stream.rfind(b"\xff\xd8", 0, end)
        if start < 0 or end < start:
            raise RuntimeError("V4L2未返回完整MJPG帧")
        data = stream[start:end + 2]
        actual_width, actual_height = jpeg_size(data)
        with open(cfg["output"], "wb") as fh:
            fh.write(data)
        frame_ok = max(1, stream.count(b"\xff\xd8"))
        conversion_backend = "none"
    elif fourcc == "YUYV":
        expected_bytes = actual_width * actual_height * 2
        frame_ok = len(stream) // expected_bytes
        if frame_ok < 1:
            raise RuntimeError("V4L2未返回完整YUYV帧: 期望至少 " + str(expected_bytes) + " 字节，实际 " + str(len(stream)) + " 字节")
        raw_frame = stream[-expected_bytes:]
        try:
            from PIL import Image
        except Exception as pil_error:
            raise RuntimeError("已读取YUYV原始帧，但Pillow不可用，无法生成预览JPEG: " + type(pil_error).__name__ + ": " + str(pil_error)) from pil_error
        rgb_frame = yuyv_to_rgb(raw_frame)
        image = Image.frombytes("RGB", (actual_width, actual_height), rgb_frame)
        image.save(cfg["output"], format="JPEG", quality=90)
        with open(cfg["output"], "rb") as fh:
            data = fh.read()
        if data[:2] != b"\xff\xd8":
            raise RuntimeError("Pillow未生成有效JPEG")
        conversion_backend = "Pillow"
    else:
        raise RuntimeError("只支持MJPG或YUYV实际格式，收到 " + fourcc)
    try:
        os.remove(raw_output)
    except OSError:
        pass
    result = {
        "ok": True,
        "device": cfg["device"],
        "requested": cfg,
        "captureBackend": "v4l2-mmap",
        "conversionBackend": conversion_backend,
        "actualWidth": actual_width,
        "actualHeight": actual_height,
        "actualFps": None,
        "fourcc": fourcc,
        "frameCount": frame_ok,
        "readFailures": max(0, cfg["frameCount"] - frame_ok),
        "meanLuminance": None,
        "laplacianVariance": None,
        "output": cfg["output"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()
    }
    cleanup_outputs(cfg["output"])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    try:
        if os.path.isfile(raw_output):
            os.remove(raw_output)
    except OSError:
        pass
    remove_failed_output(cfg["output"])
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "device": cfg.get("device")}, ensure_ascii=False))
    sys.exit(2)
`;

const MICROPHONE_SCRIPT = String.raw`
import array, hashlib, json, math, os, signal, subprocess, sys, time, wave
${OUTPUT_MAINTENANCE_SCRIPT}

cfg = CONFIG
cmd = None
proc = None
resolved_device = cfg["device"]
capture_started = None
capture_elapsed_sec = None
try:
    cleanup_outputs(cfg["output"])
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
    cleanup_outputs(cfg["output"])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    capture_elapsed_sec = round(time.monotonic() - capture_started, 3) if capture_started is not None else None
    remove_failed_output(cfg["output"])
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "captureElapsedSec": capture_elapsed_sec, "command": cmd, "requested": cfg}, ensure_ascii=False))
    sys.exit(2)
finally:
    if proc is not None and hasattr(proc, "poll") and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
`;

const SPEAKER_SCRIPT = String.raw`
import hashlib, json, math, os, struct, subprocess, sys, wave
${OUTPUT_MAINTENANCE_SCRIPT}

cfg = CONFIG
cmd = None
resolved_device = cfg["device"]
try:
    cleanup_outputs(cfg["output"])
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
    cleanup_outputs(cfg["output"])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    remove_failed_output(cfg["output"])
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "command": cmd, "requested": cfg}, ensure_ascii=False))
    sys.exit(2)
`;

const RECORDED_PLAYBACK_SCRIPT = String.raw`
import hashlib, json, os, subprocess, sys, wave
${OUTPUT_MAINTENANCE_SCRIPT}

cfg = CONFIG
cmd = None
resolved_device = cfg["device"]
try:
    recording = cfg["recording"]
    cleanup_outputs(recording)
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
