/**
 * Render test for the browser panel.
 *
 * The bundle is mounted with a React stand-in whose `useState` is pre-seeded
 * with a real route payload, then the returned element tree is drawn to text.
 * This catches a panel that throws, reads an undefined field, or silently falls
 * back to placeholder text after data has arrived.
 *
 * Usage: node test/client-render.mjs [repoRoot]
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PROBE_8T_CANN80 } from './fixtures.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, '..');
const repoRoot = process.argv[2] ?? process.env.DSH_ASCEND310B_ROOT ?? null;
const bundlePath = join(pluginRoot, 'lib', 'client.js');
const bundleSource = readFileSync(bundlePath, 'utf8');

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

globalThis.window = { __ModuleLoader__: { load: (entry) => (loaded.push(entry)) } };
globalThis.document = {
	querySelector: () => null,
	createElement: () => ({ dataset: {}, textContent: '' }),
	head: { appendChild: () => {} }
};
const loaded = [];

/** Build the report payload the same way the HTTP route would. */
const stubRoot = join(pluginRoot, 'node_modules');
const stubDir = join(stubRoot, '@deepseek-ai', 'dsh-tools');
const { existsSync, mkdirSync, rmdirSync, rmSync, writeFileSync } = await import('node:fs');
if (existsSync(stubDir)) {
	console.error(`client-render test refuses to overwrite existing ${stubDir}`);
	process.exit(1);
}
mkdirSync(stubDir, { recursive: true });
writeFileSync(
	join(stubDir, 'package.json'),
	JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-stub', type: 'module', main: './index.js' }),
	'utf8'
);
writeFileSync(
	join(stubDir, 'index.js'),
	'export function defineTool(options) {\n\treturn options;\n}\n',
	'utf8'
);

let host;
try {
	host = await import(new URL('../lib/index.js', import.meta.url).href);
} finally {
	rmSync(stubDir, { recursive: true, force: true });
	try {
		rmdirSync(join(stubRoot, '@deepseek-ai'));
	} catch {
		// A real @deepseek-ai dependency tree is not test-owned.
	}
	try {
		rmdirSync(stubRoot);
	} catch {
		// Keep a real dependency tree if one existed or another dependency remains.
	}
}

const routes = [];
const inventoryPath = join(pluginRoot, 'test', '.client-render-inventory.json');
rmSync(inventoryPath, { force: true });
host.apply(
	{
		tools: { register: () => {} },
		inject: (services, callback) => {
			if (!services.includes('webServer')) return;
			callback({
				webServer: { register: (route) => (routes.push(route), () => {}) },
				effect: (factory) => factory()
			});
		}
	},
	{
		repoRoot: repoRoot ?? undefined,
		inventoryPath,
		defaultTarget: 'board-8t',
		targets: [
			{ id: 'board-8t', label: '8T 板', host: '198.51.100.10', user: 'HwHiAiUser' },
			{ id: 'board-20t', label: '20T 板', host: '198.51.100.11', user: 'HwHiAiUser' }
		]
	}
);

const response = {
	status: null,
	headers: null,
	body: '',
	writeHead(status, headers) {
		this.status = status;
		this.headers = headers;
	},
	end(chunk) {
		this.body = chunk ?? '';
	}
};
const request = {
	method: 'POST',
	headers: { 'content-type': 'application/json' },
	async *[Symbol.asyncIterator]() {
		yield JSON.stringify({ target: 'board-8t', rawText: PROBE_8T_CANN80 });
	}
};
await routes.find((route) => route.path === '/ascend310b-debug/probe').handler(request, response);
check(response.status === 200, `route must answer 200, got ${response.status}`);
let payload = JSON.parse(response.body);

// --- mount the bundle with the payload already loaded --------------------
let seeded = 0;
let seededKind = 'ready';
let seededSelected = 'board-8t';
let seededTab = 'overview';
let seededPeripheralMode = 'camera';
let seededPeripheralState = null;
let seededSpeakerConfirmed;
let seededRecordingPlayback;
let seededPlaybackConfirmed;
const reactStub = {
	createElement(type, props, ...children) {
		// Function components are invoked like React would, so the rendered text
		// below comes from the real components rather than from their tags.
		if (typeof type === 'function') return type({ ...(props ?? {}), children });
		return { type, props: props ?? {}, children };
	},
	useState: (initial) => {
		seeded += 1;
		// First state call is the load state; fifth is the active tab.
		if (seeded === 1) return [{ kind: seededKind, payload }, () => {}];
		if (seeded === 2) return [seededSelected, () => {}];
		if (seeded === 4) return [seededTab, () => {}];
		if (seeded === 8) return [seededPeripheralMode, () => {}];
		if (seeded === 10 && seededPeripheralState !== null) return [seededPeripheralState, () => {}];
		if (seeded === 12 && seededSpeakerConfirmed !== undefined) return [seededSpeakerConfirmed, () => {}];
		if (seeded === 13 && seededRecordingPlayback !== undefined) return [seededRecordingPlayback, () => {}];
		if (seeded === 14 && seededPlaybackConfirmed !== undefined) return [seededPlaybackConfirmed, () => {}];
		return [initial, () => {}];
	},
	useEffect: () => {},
	useCallback: (fn) => fn,
	useRef: (initial) => ({ current: initial })
};

new Function(bundleSource)();
const face = loaded[0].factory((name) => {
	if (name === 'react') return reactStub;
	throw new Error(`unexpected require("${name}")`);
});

let panel = null;
face.apply({
	slots: {
		inject: (_name, callback) => {
			const produced = callback();
			if (produced !== undefined && typeof produced.next === 'function') for (const _ of produced) void _;
		},
		register: (options, component) => {
			if (options.name === 'main') panel = component;
			return () => {};
		}
	}
});
check(typeof panel === 'function', 'the main panel component must be registered');

/** Flatten an element tree into searchable text, the way React renders it. */
function draw(node, out = []) {
	// null, undefined, false and empty strings are non-rendering children.
	if (node === null || node === undefined || node === false || node === '') return out;
	if (typeof node === 'string' || typeof node === 'number') {
		out.push(String(node));
		return out;
	}
	if (Array.isArray(node)) {
		for (const child of node) draw(child, out);
		return out;
	}
	for (const child of node.children ?? []) draw(child, out);
	return out;
}
function findNode(node, predicate, out = []) {
	if (node === null || node === undefined || node === false) return out;
	if (Array.isArray(node)) {
		for (const child of node) findNode(child, predicate, out);
		return out;
	}
	if (typeof node !== 'object') return out;
	if (predicate(node)) out.push(node);
	for (const child of node.children ?? []) findNode(child, predicate, out);
	return out;
}

let text = '';
try {
	text = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render with a loaded payload: ${error?.message ?? error}`);
}

check(text.includes('Ascend310B4'), 'the panel must show the probed SoC');
check(text.includes('observed-match'), 'the panel must show the pairing verdict');
check(text.includes('8.0.0'), 'the panel must show the probed CANN version');
check(text.includes('25.2.0'), 'the panel must show the probed driver version');
check(/\d+\.\d GB \/ 7\.4 GB/.test(text), 'the panel must show NPU memory used/total in GB');
check(text.includes('Orange Pi AI Pro 8T · 7.4 GB'), 'the fixed memory capacity must appear in the device name in GB');
// Firmware is deliberately not a default probe field: the current 310B
// images do not expose a readable, trustworthy firmware version.
// `undefined` appears legitimately inside a knowledge-entry title (the C++
// "undefined symbol" symptom), so the check targets React artifacts instead.
check(!text.includes('>undefined<') && !text.includes('undefined undefined'), 'the panel must not render raw undefined values');
check(text.includes('昇腾310B调试'), 'the panel title must name the chip family');
check(text.includes('检查在线'), 'the primary action must be an online check');
check(text.includes('更新系统信息'), 'overview must expose a system-only refresh action');
seededKind = 'loading';
seeded = 0;
let switchingText = '';
try {
	switchingText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must keep the last report while switching targets: ${error?.message ?? error}`);
}
check(switchingText.includes('Ascend310B4'), 'target switching must not replace the current workbench with a loading page');
check(!switchingText.includes('正在读取插件配置'), 'switching a saved report must not remount the whole panel');
seededKind = 'ready';
check(!text.includes('更新设备信息'), 'the UI must not expose a global all-in-one refresh action');
check(!text.includes('每 60 秒刷新') && !text.includes('自动探测'), 'the panel must not run automatic full probes');
check(text.includes('串口发现'), 'the panel must expose a serial-discovery control');
check(text.includes('局域网扫描'), 'the panel must expose a LAN scan control');
check(text.includes('开发板记录'), 'the panel must expose a board-record tab');
check(text.includes('摄像头') && text.includes('音频接口'), 'the panel must expose dedicated camera and audio tabs');
check(!text.includes('310B 调试'), 'the old title without 昇腾 must be gone');
check(text.includes('已确认是昇腾 310B'), 'an in-scope board must say so explicitly');
check(
	bundleSource.includes('void load(true, targetId)') && !bundleSource.includes('void runProbe(targetId)'),
	'selecting a historical board must switch reports without starting a blocking probe'
);
check(bundleSource.includes('viewGeneration'), 'target actions must share one view generation');
check(bundleSource.includes('abortAll'), 'panel actions must share one abort coordinator');
check(bundleSource.includes('mountedRef'), 'panel requests must stop committing after unmount');
check(bundleSource.includes('staleError'), 'probe failures must preserve the last successful payload');
check(bundleSource.includes('aria-label'), 'interactive query and binding controls need accessible labels');
check(
	bundleSource.includes('row.online ? " ad-online" : row.offline ? " ad-offline" : row.changed ? " ad-changed"'),
	'live SSH state must take precedence over offline and historical-change colors'
);
check(!bundleSource.includes('LIVENESS_INTERVAL_MS') && !bundleSource.includes('LIVE_INTERVAL_MS'), 'the panel must not schedule automatic board probes or liveness checks');
check(bundleSource.includes('本次检测 SSH 不可达'), 'inventory rows must distinguish current offline state from history');
check(
	!bundleSource.includes('ad-activity-dock') && !bundleSource.includes('正在探测当前开发板'),
	'probing must not add any transient banner or floating status message'
);

const probedPayload = payload;
payload = {
	...payload,
	probedAt: null,
	board: null,
	transport: null,
	facts: {},
	missingCore: []
};
seeded = 0;
let emptyText = '';
try {
	emptyText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render a stable unprobed scaffold: ${error?.message ?? error}`);
}
check(emptyText.includes('尚未获取系统信息'), 'an unprobed panel must explain what system refresh will collect');
check(emptyText.includes('更新系统信息'), 'the unprobed state must expose system-only refresh');
check(!emptyText.includes('未采集的关键项'), 'an unprobed panel must not duplicate a missing-fields banner');
check(emptyText.includes('SoC / 档位'), 'the overview scaffold must stay mounted before and after the first system refresh');
payload = probedPayload;

const cameraPayload = JSON.parse(JSON.stringify(probedPayload));
cameraPayload.facts.videoDevices = {
	value: [
		'vin-media_mainpath (platform:media:isp-vir0):',
		'\t/dev/video2',
		'\t/dev/video3',
		'USB Composite Device: USB Camera (usb-xhci-hcd.3.auto-1):',
		'\t/dev/video0',
		'\t/dev/video1'
	].join('\n'),
	source: 'video_devices'
};
cameraPayload.facts.videoDetails = {
	value: [
		'DEVICE=/dev/video0',
		'Driver Info:',
		'\tDriver name      : uvcvideo',
		'\tCard type        : USB Composite Device: USB Camera',
		'\tBus info         : usb-xhci-hcd.3.auto-1',
		'Device Caps      : 0x04200001',
		'\tVideo Capture',
		'\tStreaming',
		'DEVICE=/dev/video1',
		'Driver Info:',
		'\tDriver name      : uvcvideo',
		'\tCard type        : USB Composite Device: USB Camera',
		'\tBus info         : usb-xhci-hcd.3.auto-1',
		'Device Caps      : 0x04a00000',
		'\tMetadata Capture',
		'\tStreaming',
		'DEVICE=/dev/video2',
		'Driver Info:',
		'\tDriver name      : vin-media',
		'\tCard type        : vin-media_mainpath',
		'\tBus info         : platform:media:isp-vir0',
		'Device Caps      : 0x04201000',
		'\tVideo Capture Multiplanar',
		'\tStreaming',
		'DEVICE=/dev/video3',
		'Driver Info:',
		'\tDriver name      : vin-media',
		'\tCard type        : vin-media_selfpath',
		'\tBus info         : platform:media:isp-vir0',
		'Device Caps      : 0x04201000',
		'\tVideo Capture Multiplanar',
		'\tStreaming'
	].join('\n'),
	source: 'video_details'
};
cameraPayload.facts.videoFormats = {
	value: [
		'DEVICE=/dev/video0',
		"ioctl: VIDIOC_ENUM_FMT",
		"\t[0]: 'MJPG' (Motion-JPEG, compressed)",
		'\t\tSize: Discrete 640x480',
		'\t\t\tInterval: Discrete 0.033s (30.000 fps)',
		'\t\tSize: Discrete 1280x720',
		'\t\t\tInterval: Discrete 0.033s (30.000 fps)',
		"\t[1]: 'YUYV' (YUYV 4:2:2)",
		'\t\tSize: Discrete 640x480',
		'\t\t\tInterval: Discrete 0.100s (10.000 fps)',
		'DEVICE=/dev/video2',
		"\t[0]: 'YUYV' (YUYV 4:2:2)",
		'\t\tSize: Stepwise 320x240 - 1920x1080 with step 1x1',
		'\t\t\tInterval: Stepwise 0.033s (30.000 fps) - 0.500s (2.000 fps)'
	].join('\n'),
	source: 'video_formats'
};
payload = cameraPayload;
seededTab = 'camera';
seededPeripheralMode = 'camera';
seeded = 0;
let cameraText = '';
try {
	cameraText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render the camera page: ${error?.message ?? error}`);
}
check(cameraText.includes('摄像头采集测试'), 'the camera tab must provide a dedicated capture workbench');
check(cameraText.includes('摄像头 / V4L2'), 'the camera tab must show V4L2 devices');
check(cameraText.includes('USB 外设'), 'the camera tab must show USB peripheral context');
check(cameraText.includes('采集一帧'), 'camera mode must expose a real single-frame capture action');
check(cameraText.includes('更新摄像头信息'), 'camera refresh action must be labeled for camera data only');
check(cameraText.includes('摄像头数据：更新时间未记录'), 'camera data must expose category freshness');
check(cameraText.includes('640 × 480') && cameraText.includes('1280 × 720'), 'camera resolutions must come from v4l2-ctl output');
check(cameraText.includes('Motion-JPEG, compressed') && cameraText.includes('YUYV 4:2:2'), 'camera formats must show actual FourCC descriptions');
check(cameraText.includes('30 fps') && cameraText.includes('10 fps'), 'camera format details must show actual frame rates');
check(cameraText.includes('当前节点支持的全部格式'), 'camera page must show the full reported format capability list');
check(bundleSource.includes('CAMERA_REFRESH_URL'), 'camera refresh must use the lightweight V4L2-only endpoint');
check(bundleSource.includes('CAMERA_PREVIEW_URL'), 'camera capture must use the restricted inline preview endpoint');
check(bundleSource.includes('AUDIO_REFRESH_URL') && bundleSource.includes('SYSTEM_REFRESH_URL'), 'system, camera and audio refresh endpoints must stay separate');
check(!bundleSource.includes('onClick: () => runProbe()'), 'the UI must not expose a global all-in-one device refresh');
check(!bundleSource.includes('const runProbe'), 'the frontend must not retain a dead all-in-one probe path');
check(bundleSource.includes('更新中 ${refreshElapsedSec}s…'), 'camera refresh must show elapsed progress instead of a silent wait');
check(bundleSource.includes('ad-global-status') && bundleSource.includes('ad-global-detail'), 'async status messages must use an expandable bounded layout slot');
check(bundleSource.includes('ad-playback-button'), 'recorded playback action must keep a fixed-width slot');
check(bundleSource.includes('probedAt: payload.probedAt'), 'system refresh must commit board metadata together with system facts');
check(bundleSource.includes('上次检测 SSH 可达（正在重新检查）'), 'liveness checks must label retained state as previous, not current');
check(cameraText.includes('USB Composite Device: USB Camera'), 'camera selectors must show the physical device name');
check(cameraText.includes('/dev/video0') && cameraText.includes('/dev/video1'), 'camera selectors must retain device nodes');
check(cameraText.includes('仅元数据，不能取帧'), 'metadata-only V4L2 nodes must be marked and disabled');
check(cameraText.includes('视频采集（多平面）'), 'MIPI multiplanar capture nodes must identify their capability');

seededPeripheralMode = 'camera';
seededPeripheralState = {
	phase: 'done',
	action: 'camera',
	inventory: null,
	result: {
		ok: true,
		executed: true,
		evidence: 'observed-pass',
		exitCode: 0,
		output: '/tmp/ascend310b-debug/peripherals/camera-2026-10-29T09-30-00-000Z.jpg',
		data: { sha256: 'cafebabe', actualWidth: 640, actualHeight: 480, fourcc: 'MJPG', frameCount: 1, bytes: 1234 },
		text: '摄像头采集完成'
	}
};
seeded = 0;
const cameraResultTree = panel({});
const previewImages = findNode(cameraResultTree, (node) => node.type === 'img' && node.props?.alt === '摄像头本次采集画面');
check(previewImages.length === 1, 'a successful camera capture must render exactly one inline preview image');
check(
	previewImages[0]?.props?.src?.includes('/ascend310b-debug/camera-preview?target=board-8t') &&
		previewImages[0]?.props?.src?.includes('camera-2026-10-29T09-30-00-000Z.jpg'),
	'camera preview must call the restricted preview route with the captured fixed-path JPEG'
);
seededPeripheralState = null;

const audioPayload = JSON.parse(JSON.stringify(probedPayload));
audioPayload.facts.audioCapture = {
	value: [
		'**** List of CAPTURE Hardware Devices ****',
		'card 0: ascend310b [ascend310b], device 1: ascend310b-capture [ascend310b-hifi-1]',
		'\tSubdevices: 1/1',
		'card 2: U1080P [UGREEN Camera 1080P], device 0: USB Audio [USB Audio]',
		'\tSubdevices: 1/1'
	].join('\n'),
	source: 'audio_capture'
};
audioPayload.facts.audioSources = {
	value: '0\talsa_input.usb-Jieli_USB_Audio-00.mono-fallback\tmodule-alsa-card.c\ts16le 1ch 44100Hz\tSUSPENDED',
	source: 'audio_sources'
};
audioPayload.facts.audioSinks = {
	value: '0\talsa_output.platform-sound.stereo-fallback\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tSUSPENDED',
	source: 'audio_sinks'
};
payload = audioPayload;
seededTab = 'audio';
seededPeripheralMode = 'microphone';
seeded = 0;
let audioPageText = '';
try {
	audioPageText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render the audio page: ${error?.message ?? error}`);
}
check(audioPageText.includes('音频接口检测'), 'the audio tab must provide a dedicated interface workbench');
check(audioPageText.includes('麦克风输入') && audioPageText.includes('扬声器输出'), 'the audio tab must show microphone and speaker together');
check(audioPageText.includes('录音回放') && audioPageText.includes('使用右侧当前扬声器'), 'recorded playback must share the visible speaker selection');
check(audioPageText.includes('录制 3 秒'), 'microphone recording must show its configured duration');
check(audioPageText.includes('更新音频接口信息'), 'audio refresh action must be labeled for audio data only');
check(audioPageText.includes('音频数据：更新时间未记录'), 'audio data must expose category freshness');
check(!audioPageText.includes('先完成扬声器测试'), 'microphone must not be blocked by speaker confirmation');
check(audioPageText.includes('输入 Source') && audioPageText.includes('输出 Sink'), 'the audio tab must show source and sink selectors');
check(audioPageText.includes('USB Audio'), 'audio selectors must show the ALSA device name');
check(audioPageText.includes('alsa_input.usb-Jieli_USB_Audio-00.mono-fallback'), 'audio selectors must show the Pulse source name');
check(audioPageText.includes('alsa_output.platform-sound.stereo-fallback'), 'audio selectors must show the Pulse sink name');
seeded = 0;
const audioTree = panel({});
const microphoneSourceSelect = findNode(audioTree, (node) => node.type === 'select' && node.props?.id === 'ad-audio-microphone-source')[0];
const microphoneSourceOptions = draw(microphoneSourceSelect).join(' ');
check(
	microphoneSourceOptions.indexOf('USB/外接') >= 0 &&
		microphoneSourceOptions.indexOf('USB/外接') < microphoneSourceOptions.indexOf('· 板载'),
	'usable USB microphones must be listed before onboard capture devices'
);
check(
	bundleSource.includes('CAMERA_CAPTURE_URL') &&
		bundleSource.includes('MICROPHONE_RECORD_URL') &&
		bundleSource.includes('SPEAKER_TEST_URL') &&
		bundleSource.includes('PLAY_RECORDING_URL') &&
		bundleSource.includes('allowStateChange: true'),
	'peripheral actions must call the fixed explicit-execution routes'
);
check(bundleSource.includes('人工确认通过'), 'speaker playback must distinguish command success from human confirmation');
check(bundleSource.includes('Number(right.external) - Number(left.external)'), 'microphone inventory must prefer USB/external capture devices');
check(
	bundleSource.includes('sampleRate: 16000') && bundleSource.includes('durationSec: 3'),
	'peripheral forms must submit the backend sampleRate/durationSec contract'
);
check(
	!bundleSource.includes('durationSeconds:') && !bundleSource.includes('rate: 16000'),
	'peripheral forms must not submit obsolete durationSeconds/rate fields'
);
check(bundleSource.includes('/tmp/ascend310b-debug/peripherals'), 'camera guidance must name the fixed artifact root');
check(
	bundleSource.includes('audioSources') && bundleSource.includes('audioSinks'),
	'the peripheral UI must consume connected PulseAudio/PipeWire sources and sinks'
);
check(
	bundleSource.includes('bluetoothDevices') && bundleSource.includes('pulseCardDetails'),
	'the peripheral UI must surface read-only BlueZ and Pulse card/profile evidence'
);

seededPeripheralMode = 'microphone';
seededSpeakerConfirmed = false;
seededPeripheralState = {
	phase: 'done',
	action: 'microphone',
	inventory: null,
	result: {
		ok: true,
		executed: true,
		evidence: 'observed-pass',
		exitCode: 0,
		durationMs: 5210,
		text: '麦克风采集完成，检测到非零输入信号。',
		command: 'fixed microphone command',
		output: '/tmp/ascend310b-debug/peripherals/microphone.wav',
		data: {
			device: 'plughw:0,0',
			signalDetected: true,
			rms: 123.4,
			peak: 2048,
			zeroRatio: 0.1,
			clippingRatio: 0,
			sampleRate: 16000,
			channels: 1,
			requestedDurationSec: 3,
			captureElapsedSec: 3.02,
			durationSec: 3.01,
			sha256: 'abc123'
		}
	}
};
seeded = 0;
let microphoneResultText = '';
try {
	microphoneResultText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render microphone evidence: ${error?.message ?? error}`);
}
check(microphoneResultText.includes('检测到信号'), 'microphone evidence must show whether real input was detected');
check(microphoneResultText.includes('RMS'), 'microphone evidence must render level metrics');
check(microphoneResultText.includes('请求 WAV') && microphoneResultText.includes('实际 WAV'), 'microphone evidence must distinguish requested and actual WAV duration');
check(microphoneResultText.includes('采集耗时') && microphoneResultText.includes('操作总耗时'), 'microphone evidence must separate capture and total operation time');
check(microphoneResultText.includes('16000 Hz / 1 ch'), 'microphone evidence must show the actual WAV contract');
check(microphoneResultText.includes('回放最近一次录音'), 'a successful recording must be playable beside the visible speaker controls');

seededRecordingPlayback = {
	phase: 'done',
	message: '',
	result: {
		ok: true,
		data: {
			sourceRecording: '/tmp/ascend310b-debug/peripherals/microphone.wav',
			device: 'plughw:0,0',
			outputRoute: 'alsa:plughw:0,0',
			durationSec: 3,
			sha256: 'abc123'
		}
	}
};
seededPlaybackConfirmed = true;
seeded = 0;
let playbackResultText = '';
try {
	playbackResultText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render recording playback evidence: ${error?.message ?? error}`);
}
check(playbackResultText.includes('录音回放'), 'microphone verification must include recorded playback');
check(playbackResultText.includes('输出设备：alsa:plughw:0,0'), 'recorded playback must show the current shared speaker selection');
check(playbackResultText.includes('alsa:plughw:0,0'), 'recorded playback must show the actual output route');
check(playbackResultText.includes('录音回放确认通过'), 'recorded playback must require explicit human confirmation');

seededPeripheralMode = 'camera';
seededPeripheralState = null;
seededSpeakerConfirmed = undefined;
seededRecordingPlayback = undefined;
seededPlaybackConfirmed = undefined;
seededTab = 'overview';

seededTab = 'debug';
seeded = 0;
let debugText = '';
try {
	debugText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render the debug dependencies: ${error?.message ?? error}`);
}
check(debugText.includes('案例系统程序'), 'the debug panel must show per-case system-program requirements');
check(debugText.includes('ascend_apt_install'), 'the debug panel must show the explicit apt dry-run hint');
seededTab = 'overview';

// The same panel must refuse to imply a 310B workflow for another accelerator.
const savedPayload = payload;
payload = {
	...savedPayload,
	board: { soc: 'Ascend310P3', chip: '310P3', tier: 'unknown', health: 'OK' },
	scope: {
		applicable: false,
		family: 'Ascend310P3',
		soc: 'Ascend310P3',
		chip: '310P3',
		supportedSocs: ['Ascend310B1', 'Ascend310B4'],
		message: '检测到 Ascend310P3，不是昇腾 310B1/310B4。本插件的调试程序、样例索引和版本配套表不适用于该芯片。'
	}
};
seeded = 0;
let foreignText = '';
try {
	foreignText = draw(panel({})).join(' ');
} catch (error) {
	failures.push(`the panel must render an out-of-scope payload: ${error?.message ?? error}`);
}
check(foreignText.includes('不是昇腾 310B1/310B4'), 'an out-of-scope board must be named as such');
check(foreignText.includes('不适用于该芯片'), 'an out-of-scope board must say the procedures do not apply');
payload = savedPayload;
rmSync(inventoryPath, { force: true });

if (failures.length > 0) {
	console.error('client render test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('client render test passed');
console.log(`  rendered characters : ${text.length}`);
console.log(`  pairing shown       : ${payload.pairing.verdict}`);
console.log(`  board shown         : ${payload.board.soc} / ${payload.board.tier}`);
console.log('  out-of-scope board  : rendered the not-310B warning');
