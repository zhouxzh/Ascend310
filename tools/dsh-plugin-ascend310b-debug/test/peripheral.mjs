/**
 * Pure validation and command tests for peripheral tools.
 *
 * Usage: node test/peripheral.mjs
 */

import assert from 'node:assert/strict';

import { validateTaskCommand } from '../lib/shell.js';
import {
	CAMERA_IMAGE_PATH,
	PERIPHERAL_OUTPUT_ROOT,
	buildCameraCommand,
	buildMicrophoneCommand,
	buildRecordedPlaybackCommand,
	buildSpeakerCommand,
	detectAudioBackend,
	normalizeCameraOptions,
	normalizeMicrophoneOptions,
	normalizeRecordedPlaybackOptions,
	normalizeSpeakerOptions,
	parsePeripheralResult,
	peripheralOutputPath
} from '../lib/peripherals.js';

const failures = [];
const check = (name, run) => {
	try {
		run();
	} catch (error) {
		failures.push(`${name}: ${error?.message ?? error}`);
	}
};
const now = Date.UTC(2026, 9, 26, 8, 0, 0);

check('camera accepts the documented limits', () => {
	const low = normalizeCameraOptions({ device: '/dev/video0', width: 160, height: 120, frameCount: 1 }, now);
	const high = normalizeCameraOptions({ device: '/dev/video9', width: 1920, height: 1080, frameCount: 10 }, now);
	assert.equal(low.pixelFormat, 'MJPG');
	assert.equal(high.frameCount, 10);
	assert.ok(low.output.startsWith(`${PERIPHERAL_OUTPUT_ROOT}/camera-`));
});

check('camera rejects unsafe device and out-of-range values', () => {
	assert.throws(() => normalizeCameraOptions({ device: '/dev/video0/../x' }, now));
	assert.throws(() => normalizeCameraOptions({ device: '/dev/video0', width: 159 }, now));
	assert.throws(() => normalizeCameraOptions({ device: '/dev/video0', height: 1081 }, now));
	assert.throws(() => normalizeCameraOptions({ device: '/dev/video0', frameCount: 11 }, now));
	assert.throws(() => normalizeCameraOptions({ device: '/dev/video0', pixelFormat: 'RGB24' }, now));
	const fixed = normalizeCameraOptions({ device: '/dev/video0', output: '/tmp/arbitrary.jpg' }, now);
	assert.ok(fixed.output.startsWith(`${PERIPHERAL_OUTPUT_ROOT}/`));
	assert.notEqual(fixed.output, '/tmp/arbitrary.jpg');
});

check('microphone auto backend follows the selector', () => {
	assert.equal(detectAudioBackend('auto', 'plughw:0,1'), 'alsa');
	assert.equal(detectAudioBackend('auto', 'default'), 'alsa');
	assert.equal(detectAudioBackend('auto', 'bluez_input.00_11_22_33_44_55'), 'pulse');
	assert.equal(normalizeMicrophoneOptions({}, now).backend, 'pulse');
});

check('microphone rejects Pulse monitor sources', () => {
	assert.throws(() => normalizeMicrophoneOptions({ backend: 'pulse', device: 'alsa_output.foo.monitor' }, now));
	assert.throws(() => normalizeMicrophoneOptions({ backend: 'pulse', device: '@DEFAULT_MONITOR@' }, now));
});

check('microphone validates sample rate, channels and duration', () => {
	const value = normalizeMicrophoneOptions({ backend: 'pulse', sampleRate: 48000, channels: 2, durationSec: 10 }, now);
	assert.equal(value.sampleRate, 48000);
	assert.throws(() => normalizeMicrophoneOptions({ sampleRate: 7999 }, now));
	assert.throws(() => normalizeMicrophoneOptions({ channels: 3 }, now));
	assert.throws(() => normalizeMicrophoneOptions({ durationSec: 11 }, now));
});

check('speaker validates frequency and sub-second duration', () => {
	const value = normalizeSpeakerOptions({ backend: 'pulse', frequency: 2000, durationSec: 0.25 }, now);
	assert.equal(value.frequency, 2000);
	assert.equal(value.durationSec, 0.25);
	assert.throws(() => normalizeSpeakerOptions({ frequency: 199 }, now));
	assert.throws(() => normalizeSpeakerOptions({ durationSec: 5.01 }, now));
});

check('output paths are fixed and timestamped', () => {
	assert.equal(
		peripheralOutputPath('camera', now),
		'/tmp/ascend310b-debug/peripherals/camera-2026-10-26T08-00-00-000Z.jpg'
	);
	assert.equal(
		peripheralOutputPath('microphone', now),
		'/tmp/ascend310b-debug/peripherals/microphone-2026-10-26T08-00-00-000Z.wav'
	);
	assert.throws(() => peripheralOutputPath('other', now));
});

check('camera command is fixed and valid', () => {
	const command = buildCameraCommand(normalizeCameraOptions({ device: '/dev/video1', frameCount: 3 }, now));
	assert.match(command, /^python -c '/u);
	assert.match(command, /cv2\.VideoCapture/u);
	assert.match(command, /frame\.read/u);
	assert.match(command, /ffmpeg/u);
	assert.match(command, /from PIL import Image/u);
	assert.equal(command.includes('base64'), false);
	assert.equal(validateTaskCommand(command).ok, true);
});

check('camera preview paths are strictly limited to generated JPEGs', () => {
	assert.equal(CAMERA_IMAGE_PATH.test(`${PERIPHERAL_OUTPUT_ROOT}/camera-2026-10-29T09-30-00-000Z.jpg`), true);
	assert.equal(CAMERA_IMAGE_PATH.test(`${PERIPHERAL_OUTPUT_ROOT}/camera-../../etc/passwd.jpg`), false);
	assert.equal(CAMERA_IMAGE_PATH.test(`${PERIPHERAL_OUTPUT_ROOT}/camera-2026-10-29T09-30-00-000Z.wav`), false);
	assert.equal(CAMERA_IMAGE_PATH.test('/tmp/other/camera-2026-10-29T09-30-00-000Z.jpg'), false);
});

check('microphone commands select arecord or parecord', () => {
	const alsa = buildMicrophoneCommand(normalizeMicrophoneOptions({ backend: 'alsa', device: 'plughw:0,1' }, now));
	const pulse = buildMicrophoneCommand(normalizeMicrophoneOptions({ backend: 'pulse', device: 'bluez_input.foo' }, now));
	const pulseDefault = buildMicrophoneCommand(normalizeMicrophoneOptions({ backend: 'pulse', device: '@DEFAULT_SOURCE@' }, now));
	assert.match(alsa, /arecord/u);
	assert.match(pulse, /parecord/u);
	assert.match(pulse, /bluez_input\.foo/u);
	assert.match(pulseDefault, /get-default-source/u);
	assert.match(pulse, /communicate\(timeout=cfg\["durationSec"\]\)/u);
	assert.match(pulse, /send_signal\(signal\.SIGINT\)/u);
	assert.match(pulse, /录音时长异常/u);
	assert.match(alsa, /ALSA 输入设备无法打开或录音失败/u);
	assert.match(pulseDefault, /PulseAudio 默认输入是输出监视源/u);
	assert.equal(validateTaskCommand(alsa).ok, true);
	assert.equal(validateTaskCommand(pulse).ok, true);
});

check('speaker commands select aplay or paplay and keep heard null', () => {
	const alsa = buildSpeakerCommand(normalizeSpeakerOptions({ backend: 'alsa' }, now));
	const pulse = buildSpeakerCommand(normalizeSpeakerOptions({ backend: 'pulse', device: 'bluez_sink.00_11_22_33_44_55.a2dp_sink' }, now));
	const pulseDefault = buildSpeakerCommand(normalizeSpeakerOptions({ backend: 'pulse', device: '@DEFAULT_SINK@' }, now));
	assert.match(alsa, /aplay/u);
	assert.match(pulse, /paplay/u);
	assert.match(pulse, /bluez_sink\.00_11_22_33_44_55\.a2dp_sink/u);
	assert.match(pulseDefault, /get-default-sink/u);
	assert.match(pulse, /"heard": None/u);
	assert.equal(validateTaskCommand(alsa).ok, true);
	assert.equal(validateTaskCommand(pulse).ok, true);
});

check('recorded playback accepts only the fixed microphone WAV path', () => {
	const recording = peripheralOutputPath('microphone', now);
	const value = normalizeRecordedPlaybackOptions({ backend: 'alsa', device: 'plughw:0,0', recording });
	assert.equal(value.recording, recording);
	assert.equal(value.output, recording);
	for (const unsafe of [
		'/tmp/microphone-2026-10-26T08-00-00-000Z.wav',
		'/tmp/ascend310b-debug/peripherals/../microphone-2026-10-26T08-00-00-000Z.wav',
		'/tmp/ascend310b-debug/peripherals/speaker-2026-10-26T08-00-00-000Z.wav',
		'/tmp/ascend310b-debug/peripherals/microphone-../../etc/passwd.wav',
		'/tmp/ascend310b-debug/peripherals/microphone-2026-10-26T08:00:00.000Z.wav',
		'/tmp/ascend310b-debug/peripherals/microphone-2026-10-26T08-00-00-000Z.WAV'
	]) {
		assert.throws(() => normalizeRecordedPlaybackOptions({ recording: unsafe }), undefined, unsafe);
	}
});

check('recorded playback reuses speaker backend and device validation', () => {
	const recording = peripheralOutputPath('microphone', now);
	assert.equal(normalizeRecordedPlaybackOptions({ recording, device: 'plughw:0,0' }).backend, 'alsa');
	assert.equal(normalizeRecordedPlaybackOptions({ recording, device: 'bluez_sink.foo' }).backend, 'pulse');
	assert.throws(() => normalizeRecordedPlaybackOptions({ recording, backend: 'alsa', device: 'bluez_sink.foo' }));
	assert.throws(() => normalizeRecordedPlaybackOptions({ recording, backend: 'pulse', device: 'bad sink name' }));
});

check('recorded playback uses fixed aplay/paplay script and keeps audible result untested', () => {
	const recording = peripheralOutputPath('microphone', now);
	const alsa = buildRecordedPlaybackCommand(
		normalizeRecordedPlaybackOptions({ recording, backend: 'alsa', device: 'plughw:0,0' })
	);
	const pulse = buildRecordedPlaybackCommand(
		normalizeRecordedPlaybackOptions({ recording, backend: 'pulse', device: '@DEFAULT_SINK@' })
	);
	assert.match(alsa, /wave\.open/u);
	assert.match(alsa, /aplay/u);
	assert.match(pulse, /paplay/u);
	assert.match(pulse, /get-default-sink/u);
	assert.match(pulse, /"sourceRecording": recording/u);
	assert.match(pulse, /"heard": None/u);
	assert.match(pulse, /"volumeChanged": False/u);
	assert.equal(validateTaskCommand(alsa).ok, true);
	assert.equal(validateTaskCommand(pulse).ok, true);
});

check('peripheral JSON parses the last object', () => {
	assert.deepEqual(parsePeripheralResult('log\n{"ok":true,"rms":4}\n'), { ok: true, rms: 4 });
	assert.equal(parsePeripheralResult('not json'), null);
});

if (failures.length > 0) {
	console.error('peripheral tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('peripheral tests passed');
console.log('  camera   : fixed Python/OpenCV frame.read contract');
console.log('  microphone: fixed ALSA/Pulse WAV evidence');
console.log('  speaker  : fixed WAV playback with heard=null');
console.log('  recording: strict recorded-WAV path and playback contract');
