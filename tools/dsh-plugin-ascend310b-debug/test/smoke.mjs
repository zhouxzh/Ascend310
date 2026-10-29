/**
 * Host-half smoke test.
 *
 * `@deepseek-ai/dsh-tools` only exists inside the DSH installation, so this test
 * creates a temporary stub with the same shape in the plugin's own
 * `node_modules`, imports the host half for real, and drives `apply()` against a
 * fake Cordis context. That exercises the registration contract, the target
 * resolver, every tool's argument handling, the read-only command gate, and both
 * HTTP routes. The stub is removed again before the process exits.
 *
 * Usage: node test/smoke.mjs [repoRoot]
 */

import assert from 'node:assert/strict';
import { existsSync, mkdirSync, rmdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PROBE_8T_CANN80 } from './fixtures.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, '..');
const repoRoot = process.argv[2] ?? process.env.DSH_ASCEND310B_ROOT ?? null;

// A real checkout of this plugin has no node_modules of its own; the temporary
// one exists only for the duration of this test.
const stubRoot = join(pluginRoot, 'node_modules');
const stubDir = join(stubRoot, '@deepseek-ai', 'dsh-tools');
const stubExistedBefore = existsSync(stubDir);
if (stubExistedBefore) {
	console.error('smoke test refuses to run: ' + stubDir + ' already exists');
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
	[
		'export function defineTool(options) {',
		"\tfor (const key of ['name', 'description', 'parameters', 'output', 'execute']) {",
		'\t\tif (options?.[key] === undefined) throw new Error(`defineTool: missing ${key}`);',
		'\t}',
		"\tif (typeof options.output?.schema !== 'object' || typeof options.output?.render !== 'function') {",
		'\t\tthrow new Error(`defineTool: ${options.name} must declare output { schema, render }`);',
		'\t}',
		'\treturn options;',
		'}',
		''
	].join('\n'),
	'utf8'
);

const host = await import(new URL('../lib/index.js', import.meta.url).href);
const ascendTask = host.internals.prepareTaskCommand('python -V', 'ascend');
assert.match(ascendTask, /conda activate ascend/);
assert.match(ascendTask, /PYTHONNOUSERSITE=1/);
assert.match(ascendTask, /ascend-toolkit\/set_env\.sh/);
assert.doesNotMatch(ascendTask, /conda activate base/);
assert.equal(host.internals.prepareTaskCommand('hostname', 'none'), 'hostname');
assert.match(host.internals.prepareTaskCommand('hostname', 'base'), /conda activate base/);

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
check(host.internals.downloadIntegrity({ executed: true, exitCode: 0 }, 'a'.repeat(64)) === 'verified', 'successful hashed download must be verified');
check(host.internals.downloadIntegrity({ executed: true, exitCode: 1 }, 'a'.repeat(64)) === 'not-verified', 'failed hashed download must not be verified');
check(host.internals.downloadIntegrity({ executed: false, exitCode: 0 }, 'a'.repeat(64)) === 'not-verified', 'dry-run download must not be verified');

/**
 * Minimal validator for the subset of the tool output schema this plugin uses.
 * The real Host validates `defineTool.output.schema` before the model sees a
 * result; the stub in this test cannot, so this catches shape regressions such
 * as a null optional field or an undeclared property.
 */
function validateOutput(name, value) {
	const schema = registeredTools.get(name)?.output?.schema;
	if (schema === undefined) {
		failures.push(`${name}: missing output schema`);
		return;
	}
	const findUndefined = (node, path) => {
		if (node === undefined) return;
		if (node === null || typeof node !== 'object') return;
		if (Array.isArray(node)) {
			node.forEach((item, index) => findUndefined(item, `${path}[${index}]`));
			return;
		}
		for (const [key, child] of Object.entries(node)) {
			if (child === undefined) failures.push(`${path}.${key} is an own undefined property in ${name}`);
			else findUndefined(child, `${path}.${key}`);
		}
	};
	findUndefined(value, name);
	const visit = (node, shape, path) => {
		if (node === undefined) return;
		if (shape === null || typeof shape !== 'object') return;
		const label = (key) => `${path}.${key}`;
		if (shape.type === 'object') {
			if (node === null || typeof node !== 'object' || Array.isArray(node)) {
				failures.push(`${label('')} must be an object for ${name}`);
				return;
			}
			const required = new Set(Array.isArray(shape.required) ? shape.required : []);
			for (const [key, child] of Object.entries(shape.properties ?? {})) {
				if (child?.required === true) required.add(key);
				if (node[key] === undefined) {
					if (required.has(key)) failures.push(`${label(key)} is required by ${name}`);
					continue;
				}
				visit(node[key], child, label(key));
			}
			if (shape.additionalProperties === false) {
				for (const key of Object.keys(node)) {
					if (!(key in (shape.properties ?? {}))) failures.push(`${label(key)} is not declared by ${name}`);
				}
			}
			return;
		}
		if (shape.type === 'array') {
			if (!Array.isArray(node)) {
				failures.push(`${label('')} must be an array for ${name}`);
				return;
			}
			node.forEach((item, index) => visit(item, shape.items, `${path}[${index}]`));
			return;
		}
		if (shape.type === 'integer' && !Number.isInteger(node)) failures.push(`${label('')} must be an integer for ${name}`);
		else if (shape.type === 'number' && (typeof node !== 'number' || !Number.isFinite(node))) failures.push(`${label('')} must be a number for ${name}`);
		else if (shape.type === 'boolean' && typeof node !== 'boolean') failures.push(`${label('')} must be a boolean for ${name}`);
		else if (shape.type === 'string' && typeof node !== 'string') failures.push(`${label('')} must be a string for ${name}`);
	};
	visit(value, schema, name);
}

/** Collected tool definitions, keyed by name. */
const registeredTools = new Map();

// --- registration ---------------------------------------------------------
const routes = [];
const effects = [];
const config = {
	repoRoot: repoRoot ?? undefined,
	inventoryPath: join(pluginRoot, 'test', '.no-inventory.json'),
	defaultTarget: 'board-8t',
	targets: [
		{ id: 'board-8t', label: '8T 板', host: '198.51.100.10', user: 'HwHiAiUser', note: '8T/310B4' },
		{ id: 'board-20t', label: '20T 板', host: '198.51.100.11', user: 'HwHiAiUser', note: '20T/310B1' }
	]
};

const fakeCtx = {
	tools: { register: (tool) => registeredTools.set(tool.name, tool) },
	inject: (services, callback) => {
		if (!services.includes('webServer')) return;
		callback({
			webServer: {
				register: (route) => {
					routes.push(route);
					return () => {};
				}
			},
			effect: (factory, label) => effects.push({ disposer: factory(), label })
		});
	}
};

try {
	assert.equal(host.name, 'dsh-plugin-ascend310b-debug', 'plugin name must match the bundle id');
	assert.deepEqual(host.inject, ['tools'], 'the host half needs the tools service');

	host.apply(fakeCtx, config);

	for (const name of ['ascend_probe', 'ascend_camera_capture', 'ascend_microphone_record', 'ascend_speaker_test', 'ascend_play_recording', 'ascend_check', 'ascend_debug_plan', 'ascend_sample_index', 'ascend_board_exec', 'ascend_board_run', 'ascend_apt_install', 'ascend_model_download', 'ascend_ui_capture', 'ascend_discover', 'ascend_serial_probe', 'ascend_serial_exec']) {
		check(registeredTools.has(name), `tool ${name} must be registered`);
	}
	check(routes.length === 14, `expected fourteen panel routes, got ${routes.length}`);
	check(routes.every((route) => route.kind === 'exact'), 'panel routes must be exact matches');
	check(effects.length === 14, 'each route must be registered through an effect');
	check(
		routes.map((route) => route.path).join(',') ===
			'/ascend310b-debug/report.json,/ascend310b-debug/probe,/ascend310b-debug/discover,/ascend310b-debug/liveness,/ascend310b-debug/camera-refresh,/ascend310b-debug/audio-refresh,/ascend310b-debug/system-refresh,/ascend310b-debug/camera-preview,/ascend310b-debug/camera-capture,/ascend310b-debug/microphone-record,/ascend310b-debug/speaker-test,/ascend310b-debug/play-recording,/ascend310b-debug/bind,/ascend310b-debug/serial-probe',
		'routes must expose report, probe, discovery, liveness, category refreshes, camera preview/capture, audio and serial/bind endpoints'
	);

	// --- target resolution ------------------------------------------------
	const resolved = host.resolveTarget(config);
	check(resolved.id === 'board-8t', 'the configured defaultTarget must win');
	check(host.resolveTarget(config, 'board-20t').host === '198.51.100.11', 'a target id must select that board');

	const mac = 'c0:74:2b:fd:dd:7a';
	const discoveredId = 'ascend-c0742bfddd7a';
	try {
		writeFileSync(
			config.inventoryPath,
			JSON.stringify({
				version: 1,
				boards: {
					[mac]: {
						id: discoveredId,
						identity: mac,
						mac,
						user: 'HwHiAiUser',
						lastIp: '198.51.100.10',
						hostname: 'orangepiaipro',
						system: { hostname: 'orangepiaipro', soc: 'Ascend310B4', tier: '8T' },
						changes: [],
						history: []
					}
				}
			}),
			'utf8'
		);
		const merged = host.resolveTarget(config, 'board-8t');
		check(merged.mac === mac, 'a configured target must inherit the discovered MAC');
		let duplicate = false;
		try {
			host.resolveTarget(config, discoveredId);
		} catch {
			duplicate = true;
		}
		check(duplicate, 'a configured target and its discovered MAC target must render as one board');
	} finally {
		rmSync(config.inventoryPath, { force: true });
	}

	const discoverTool = registeredTools.get('ascend_discover');
	const rejectedScan = await discoverTool.execute({ cidrs: ['8.8.8.0/24'] }, {});
	validateOutput('ascend_discover', rejectedScan);
	check(rejectedScan.scanned === 0 && rejectedScan.errors.length > 0, 'a public CIDR must be refused without scanning');
	check(host.resolveTarget(config, 'HwHiAiUser@198.51.100.11').id === 'board-20t', 'user@host must select that board');
	check(host.resolveTarget(config, 'local').transport === 'local', 'the "local" selector must remain available');
	let ambiguous = null;
	try {
		host.resolveTarget({ targets: config.targets });
	} catch (error) {
		ambiguous = error;
	}
	check(ambiguous !== null, 'two boards without a default must be reported as ambiguous');
	check(
		ambiguous?.message.includes('board-8t') && ambiguous?.message.includes('board-20t'),
		'the ambiguity error must name both candidates'
	);

	// --- probe with a saved transcript ------------------------------------
	const probeTool = registeredTools.get('ascend_probe');
	const probeResult = await probeTool.execute({ rawText: PROBE_8T_CANN80, repoRoot }, {});
	validateOutput('ascend_probe', probeResult);
	check(probeResult.text.includes('Ascend310B4'), 'the probe tool must print the board from the transcript');
	check(probeResult.text.includes('8.0.0'), 'the probe tool must print the probed CANN version');
	check(probeResult.text.includes('observed-match'), 'the probe tool must report the recorded version pairing');

	const offlineProbe = host.buildProbeOutput({
		result: {
			target: { id: 'board-8t', label: '开发板 1（8T）', transport: 'ssh', host: 'orangepiaipro.local', user: 'HwHiAiUser' },
			source: 'ssh',
			environment: { facts: {}, missingCore: ['SoC', 'CANN', '驱动'], scope: null },
			rawText: '',
			transport: { ok: false, reason: 'SSH 退出码 255：Could not resolve hostname' }
		},
		report: {
			board: null,
			text: '',
			scope: { applicable: 'unknown', message: '未识别到 SoC。' },
			pairing: { verdict: 'incomplete', evidence: 'untested', reasons: [] }
		}
	});
	validateOutput('ascend_probe', offlineProbe);
	check(offlineProbe.ok === false, 'an unreachable target must return ok=false');
	check(!Object.hasOwn(offlineProbe, 'board'), 'an unreachable target must omit board rather than return undefined');
	check(offlineProbe.text.includes('offline/unreachable'), 'an unreachable target must identify itself as offline/unreachable');
	check(offlineProbe.text.includes('不表示'), 'an offline target must not become an unsupported-hardware claim');

	const planTool = registeredTools.get('ascend_debug_plan');
	const planResult = await planTool.execute({ symptom: 'import acl 失败', rawText: PROBE_8T_CANN80, repoRoot }, {});
	validateOutput('ascend_debug_plan', planResult);
	check(planResult.text.includes('停止条件'), 'the plan tool must render stop conditions');
	check(typeof planResult.applicable === 'string', 'the plan applicability must be serializable as a schema string');
	for (const sample of planResult.samples) {
		check(typeof sample.id === 'string' && typeof sample.title === 'string' && typeof sample.readme === 'string',
			'every planned sample must carry string identity fields');
		for (const key of ['archetype', 'archetypeLabel', 'entry', 'test']) {
			check(sample[key] === undefined || typeof sample[key] === 'string',
				`planned sample field ${key} must be omitted when it is not a string`);
		}
	}

	const indexTool = registeredTools.get('ascend_sample_index');
	if (repoRoot !== null) {
		const indexResult = await indexTool.execute({ repoRoot, limit: 3 }, {});
		validateOutput('ascend_sample_index', indexResult);
		check(indexResult.text.length > 0, 'the sample index tool must render matching samples');
		for (const sample of indexResult.matched) {
			for (const key of ['archetype', 'archetypeLabel', 'entry', 'test']) {
				check(sample[key] === undefined || typeof sample[key] === 'string',
					`indexed sample field ${key} must be omitted when it is not a string`);
			}
		}
		const pyaclResult = await indexTool.execute({ repoRoot, archetype: 'pyacl-inference', limit: 5 }, {});
		check(
			pyaclResult.matched.some((sample) => sample.id === 'chapter4'),
			'the PyACL filter must expose the canonical chapter-4 inference sample'
		);
		const cameraResult = await indexTool.execute({ repoRoot, query: '摄像头', limit: 8 }, {});
		check(
			cameraResult.matched.some((sample) => ['case2', 'case4', 'case8', 'chapter6'].includes(sample.id)),
			'a Chinese camera symptom must reach camera/VENC samples'
		);
		const vencResult = await indexTool.execute({ repoRoot, query: 'VENC', limit: 8 }, {});
		check(vencResult.matched.some((sample) => sample.id === 'chapter6'), 'the VENC query must reach the DVPP sample');
		const case4 = await indexTool.execute({ repoRoot, query: '掌纹', limit: 8 }, {});
		check(
			case4.matched.some((sample) => sample.id === 'case4' && String(sample.entry ?? '').includes('palmprint_workbench.api')),
			'case4 must expose its service entry rather than a compileall check'
		);
	} else {
		// A copied installation has no checkout but still ships the generated
		// snapshot; search must work instead of refusing.
		const fallback = await indexTool.execute({ limit: 3 }, {});
		validateOutput('ascend_sample_index', fallback);
		check(fallback.matched.length > 0, 'the packaged sample snapshot must be searchable without a checkout');
		check(fallback.text.includes('包内快照'), 'a snapshot-only search must say where the samples came from');
	}

	// --- read-only command gate -------------------------------------------
	const execTool = registeredTools.get('ascend_board_exec');
	const preview = await execTool.execute({ command: 'npu-smi info', run: false }, {});
	validateOutput('ascend_board_exec', preview);
	check(preview.text.includes('npu-smi info'), 'a preview must echo the command');
	check(preview.text.includes('未执行') || preview.text.includes('read-only'), 'a preview must state that nothing ran');

	let rejected = await execTool.execute({ command: 'rm -rf /home/HwHiAiUser/Documents', run: false }, {});
	validateOutput('ascend_board_exec', rejected);
	check(rejected.allowed === false, 'a mutating command must be refused');
	check(rejected.executed === false, 'a refused command must not execute');
	check(rejected.text.includes('命令被拒绝'), 'a refusal must say so in its text');
	check(String(rejected.reason).length > 0, 'a refusal must carry a reason');

	let chained = await execTool.execute({ command: 'npu-smi info && reboot', run: false }, {});
	validateOutput('ascend_board_exec', chained);
	check(chained.allowed === false, 'a chained command must be refused');
	check(chained.executed === false, 'a chained command must not execute');

	let redirected = await execTool.execute({ command: 'npu-smi info > /tmp/x', run: false }, {});
	validateOutput('ascend_board_exec', redirected);
	check(redirected.allowed === false, 'a redirecting command must be refused');

	// --- peripheral action contracts -------------------------------------
	const cameraTool = registeredTools.get('ascend_camera_capture');
	const cameraPreview = await cameraTool.execute(
		{ target: 'board-8t', device: '/dev/video0', allowStateChange: true, dryRun: true },
		{}
	);
	validateOutput('ascend_camera_capture', cameraPreview);
	check(cameraPreview.dryRun === true && cameraPreview.executed === false, 'camera dry-run must not open the device');
	check(cameraPreview.output.startsWith('/tmp/ascend310b-debug/peripherals/'), 'camera evidence must stay in the fixed temp root');
	const cameraRefusal = await cameraTool.execute({ target: 'board-8t', device: '/dev/video0' }, {});
	validateOutput('ascend_camera_capture', cameraRefusal);
	check(cameraRefusal.ok === false && cameraRefusal.executed === false, 'camera capture without allowStateChange must be refused');

	const microphoneTool = registeredTools.get('ascend_microphone_record');
	const microphonePreview = await microphoneTool.execute(
		{ target: 'board-8t', backend: 'pulse', device: '@DEFAULT_SOURCE@', allowStateChange: true, dryRun: true },
		{}
	);
	validateOutput('ascend_microphone_record', microphonePreview);
	check(microphonePreview.dryRun === true, 'microphone dry-run must not record');
	check(microphonePreview.output.endsWith('.wav'), 'microphone evidence must be a WAV');

	const speakerTool = registeredTools.get('ascend_speaker_test');
	const speakerPreview = await speakerTool.execute(
		{ target: 'board-8t', backend: 'pulse', device: '@DEFAULT_SINK@', allowStateChange: true, dryRun: true },
		{}
	);
	validateOutput('ascend_speaker_test', speakerPreview);
	check(speakerPreview.dryRun === true, 'speaker dry-run must not play');
	check(speakerPreview.evidence === 'untested', 'speaker command success cannot claim audible evidence');

	const recordingPath = '/tmp/ascend310b-debug/peripherals/microphone-2026-10-26T08-00-00-000Z.wav';
	const recordingTool = registeredTools.get('ascend_play_recording');
	const recordingPreview = await recordingTool.execute(
		{ target: 'board-8t', backend: 'pulse', device: '@DEFAULT_SINK@', recording: recordingPath, allowStateChange: true, dryRun: true },
		{}
	);
	validateOutput('ascend_play_recording', recordingPreview);
	check(recordingPreview.action === 'play-recording', 'recorded playback must retain its action name');
	check(recordingPreview.dryRun === true && recordingPreview.executed === false, 'recorded playback dry-run must not play');
	check(recordingPreview.output === recordingPath, 'recorded playback must preserve the source recording path');
	check(recordingPreview.evidence === 'untested', 'recorded playback cannot claim audible success');
	const recordingRefusal = await recordingTool.execute(
		{ target: 'board-8t', recording: '/tmp/arbitrary.wav', allowStateChange: true, dryRun: true },
		{}
	);
	validateOutput('ascend_play_recording', recordingRefusal);
	check(recordingRefusal.ok === false && recordingRefusal.executed === false, 'arbitrary recorded playback paths must be refused');

	// --- explicit task and download execution ----------------------------
	const runTool = registeredTools.get('ascend_board_run');
	const taskPreview = await runTool.execute(
		{
			target: 'board-8t',
			command: 'python samples/chapter5/check_mindspore_env.py',
			cwd: '/home/HwHiAiUser/Ascend310',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_board_run', taskPreview);
	check(taskPreview.allowed === true && taskPreview.executed === false, 'an approved dry-run task must be previewed');
	check(taskPreview.text.includes('python samples/chapter5/check_mindspore_env.py'), 'task preview must echo the command');
	const taskRefusal = await runTool.execute(
		{ target: 'board-8t', command: 'wget -O /tmp/x https://example.com/x', cwd: '/tmp' },
		{}
	);
	validateOutput('ascend_board_run', taskRefusal);
	check(taskRefusal.allowed === false, 'a task without allowStateChange must be refused');

	const downloadTool = registeredTools.get('ascend_model_download');
	const hfPreview = await downloadTool.execute(
		{
			target: 'board-8t',
			source: 'hf',
			repoId: 'org/model',
			filename: 'config.json',
			destination: '/tmp/model-config.json',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_model_download', hfPreview);
	check(hfPreview.resolvedUrl.startsWith('https://hf-mirror.com/'), 'HF downloads must use the mirror');
	const sensitiveDownload = await downloadTool.execute(
		{
			target: 'board-8t',
			source: 'hf',
			repoId: 'org/model',
			filename: 'config.json',
			cwd: '/home/HwHiAiUser/.ssh',
			destination: 'authorized_keys',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_model_download', sensitiveDownload);
	check(sensitiveDownload.allowed === false, 'model downloads must reject sensitive home directories');
	const githubPreview = await downloadTool.execute(
		{
			target: 'board-8t',
			source: 'github',
			url: 'https://github.com/example/repo/raw/main/README.md',
			destination: '/tmp/github-readme.md',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_model_download', githubPreview);
	check(githubPreview.resolvedUrl.startsWith('https://gh-proxy.com/https://github.com/'), 'GitHub downloads must use the web proxy');
	const huaweiPreview = await downloadTool.execute(
		{
			target: 'board-8t',
			source: 'huawei',
			url: 'https://obs.example.myhuaweicloud.com/model.onnx',
			destination: '/tmp/model.onnx',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_model_download', huaweiPreview);
	check(huaweiPreview.resolvedUrl.includes('myhuaweicloud.com'), 'Huawei cloud downloads must stay direct');
	const badHuawei = await downloadTool.execute(
		{
			target: 'board-8t',
			source: 'huawei',
			url: 'https://example.com/model.onnx',
			destination: '/tmp/model.onnx',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_model_download', badHuawei);
	check(badHuawei.allowed === false, 'Huawei source policy must reject non-Huawei hosts');
	const badTask = await runTool.execute(
		{
			target: 'board-8t',
			command: 'npu-smi set -t health -i 0 -c 0',
			cwd: '/home/HwHiAiUser/Ascend310',
			allowStateChange: true,
			dryRun: true
		},
		{}
	);
	validateOutput('ascend_board_run', badTask);
	check(badTask.allowed === false, 'the task runner must still reject hardware mutation');

	const captureTool = registeredTools.get('ascend_ui_capture');
	const captureRefusal = await captureTool.execute(
		{ url: 'http://127.0.0.1:8080/', output: 'screenshots/case.png' },
		{}
	);
	validateOutput('ascend_ui_capture', captureRefusal);
	check(captureRefusal.allowed === false, 'screenshots must require explicit allowStateChange');
	const captureOutside = await captureTool.execute(
		{ url: 'http://127.0.0.1:8080/', output: '../outside.png', allowStateChange: true },
		{}
	);
	validateOutput('ascend_ui_capture', captureOutside);
	check(captureOutside.allowed === false, 'screenshots must reject paths outside artifactRoot');

	// --- HTTP route with a saved board transcript -------------------------
	const probeRoute = routes.find((route) => route.path === '/ascend310b-debug/probe');
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
	const routeResponse = () => ({
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
	});
	const reportRoute = routes.find((route) => route.path === '/ascend310b-debug/report.json');
	const wrongReportMethod = routeResponse();
	await reportRoute.handler({ method: 'POST', headers: { 'content-type': 'application/json' } }, wrongReportMethod);
	check(wrongReportMethod.status === 405, 'report route must reject POST with 405');
	const wrongProbeMethod = routeResponse();
	await probeRoute.handler({ method: 'GET', headers: {} }, wrongProbeMethod);
	check(wrongProbeMethod.status === 405, 'probe route must reject GET with 405');
	const wrongContentType = routeResponse();
	await probeRoute.handler({ method: 'POST', headers: {} }, wrongContentType);
	check(wrongContentType.status === 415, 'probe route must require application/json');
	const wrongOrigin = routeResponse();
	await probeRoute.handler(
		{ method: 'POST', headers: { 'content-type': 'application/json', host: '127.0.0.1', origin: 'https://evil.example' } },
		wrongOrigin
	);
	check(wrongOrigin.status === 403, 'probe route must reject a cross-origin POST');
	await probeRoute.handler(request, response);
	check(response.status === 200, `the probe route must answer 200, got ${response.status}: ${response.body.slice(0, 400)}`);
	const payload = JSON.parse(response.body);
	check(payload.ok === true, `the probe route must report ok (${payload.error ?? 'no error field'})`);
	check(payload.board?.soc === 'Ascend310B4', 'the probe route must return the parsed board');
	check(Array.isArray(payload.targets) && payload.targets.length === 2, 'the probe route must return both configured targets');
	check(Array.isArray(payload.knowledge) && payload.knowledge.length > 0, 'the probe route must return the knowledge router');
	check(Array.isArray(payload.caseDependencies) && payload.caseDependencies.length === 9, 'the panel must return all 9 case dependency records');
	check(
		Array.isArray(payload.samples) && payload.samples.every((sample) => typeof sample.title === 'string'),
		'the probe route must return sample entries'
	);
	check(
		Array.isArray(payload.observedStacks) &&
			payload.observedStacks.every((stack) => typeof stack.soc === 'string' && typeof stack.status === 'string'),
		'every recorded stack must carry a SoC and an evidence label'
	);
	check(payload.scope?.applicable === true, 'the probe route must mark a 310B board as in scope');
	check(payload.scope?.supportedSocs?.includes('Ascend310B4'), 'the scope must list the SoCs the plugin supports');

	const discoverRoute = routes.find((route) => route.path === '/ascend310b-debug/discover');
	const discoverResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const discoverRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield JSON.stringify({ cidrs: ['8.8.8.0/24'] });
		}
	};
	await discoverRoute.handler(discoverRequest, discoverResponse);
	check(discoverResponse.status === 500, 'the discover route must reject a public CIDR without scanning');
	check(String(JSON.parse(discoverResponse.body).error).includes('私有'), 'the discover route must explain the CIDR refusal');

	const livenessRoute = routes.find((route) => route.path === '/ascend310b-debug/liveness');
	const livenessResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const livenessRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield JSON.stringify({ targets: ['missing-target'] });
		}
	};
	await livenessRoute.handler(livenessRequest, livenessResponse);
	const livenessPayload = JSON.parse(livenessResponse.body);
	check(livenessResponse.status === 200 && livenessPayload.ok === true, 'the liveness route must return a transport status payload');
	check(Array.isArray(livenessPayload.targets) && livenessPayload.targets.length === 0, 'an unknown liveness target must not trigger a network probe');

	const previewRoute = routes.find((candidate) => candidate.path === '/ascend310b-debug/camera-preview');
	const previewResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	await previewRoute.handler(
		{ method: 'GET', headers: {}, url: '/ascend310b-debug/camera-preview?target=board-8t&file=/etc/passwd' },
		previewResponse
	);
	check(previewResponse.status === 422, 'camera preview must reject arbitrary files before SSH access');

	const peripheralRoutes = [
		{
			path: '/ascend310b-debug/camera-capture',
			body: { target: 'board-8t', device: '/dev/video0', allowStateChange: true, dryRun: true }
		},
		{
			path: '/ascend310b-debug/microphone-record',
			body: { target: 'board-8t', backend: 'pulse', device: '@DEFAULT_SOURCE@', allowStateChange: true, dryRun: true }
		},
		{
			path: '/ascend310b-debug/speaker-test',
			body: { target: 'board-8t', backend: 'pulse', device: '@DEFAULT_SINK@', allowStateChange: true, dryRun: true }
		},
		{
			path: '/ascend310b-debug/play-recording',
			body: {
				target: 'board-8t',
				backend: 'pulse',
				device: '@DEFAULT_SINK@',
				recording: '/tmp/ascend310b-debug/peripherals/microphone-2026-10-26T08-00-00-000Z.wav',
				allowStateChange: true,
				dryRun: true
			}
		}
	];
	for (const entry of peripheralRoutes) {
		const route = routes.find((candidate) => candidate.path === entry.path);
		const response = {
			status: null,
			body: '',
			writeHead(status) {
				this.status = status;
			},
			end(chunk) {
				this.body = chunk ?? '';
			}
		};
		const request = {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			async *[Symbol.asyncIterator]() {
				yield JSON.stringify(entry.body);
			}
		};
		await route.handler(request, response);
		const payload = JSON.parse(response.body);
		check(response.status === 200 && payload.ok === true, `${entry.path} must accept a validated dry-run`);
		check(payload.result?.dryRun === true && payload.result?.executed === false, `${entry.path} dry-run must not open hardware`);
	}

	const invalidRecordingRoute = routes.find((route) => route.path === '/ascend310b-debug/play-recording');
	const invalidRecordingResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const invalidRecordingRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield JSON.stringify({
				target: 'board-8t',
				recording: '/tmp/arbitrary.wav',
				allowStateChange: true,
				dryRun: true
			});
		}
	};
	await invalidRecordingRoute.handler(invalidRecordingRequest, invalidRecordingResponse);
	const invalidRecordingPayload = JSON.parse(invalidRecordingResponse.body);
	check(invalidRecordingResponse.status === 422, 'invalid recorded-audio paths must return HTTP 422');
	check(
		invalidRecordingPayload.ok === false && invalidRecordingPayload.result?.action === 'play-recording',
		'invalid recorded-audio routes must return the standard failure result'
	);

	const bindRoute = routes.find((route) => route.path === '/ascend310b-debug/bind');
	const bindResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const bindRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield '{}';
		}
	};
	await bindRoute.handler(bindRequest, bindResponse);
	check(bindResponse.status === 400, 'the bind route must require both target ids');

	const serialRoute = routes.find((route) => route.path === '/ascend310b-debug/serial-probe');
	const serialResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const serialRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield '{}';
		}
	};
	await serialRoute.handler(serialRequest, serialResponse);
	check(serialResponse.status === 400, 'the serial route must reject missing serial configuration');
	check(String(JSON.parse(serialResponse.body).error).includes('串口'), 'the serial route must explain invalid serial configuration');

	// The same route must refuse to imply its procedures apply to another chip.
	const foreignResponse = {
		status: null,
		body: '',
		writeHead(status) {
			this.status = status;
		},
		end(chunk) {
			this.body = chunk ?? '';
		}
	};
	const foreignRequest = {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		async *[Symbol.asyncIterator]() {
			yield JSON.stringify({
				rawText: '===ABS_BEGIN:npu_smi_info===\n| 0       310P3             | OK            | 12.8         45                0    / 0              |\n===ABS_END:npu_smi_info==='
			});
		}
	};
	await probeRoute.handler(foreignRequest, foreignResponse);
	check(foreignResponse.status === 200, 'a non-310B transcript must still parse successfully');
	const foreignPayload = JSON.parse(foreignResponse.body);
	check(foreignPayload.scope?.applicable === false, 'a non-310B SoC must be reported as out of scope');
	check(foreignPayload.scope?.soc === 'Ascend310P3', 'the out-of-scope SoC must be named');

	// The planner must not hand a 310B procedure to a different accelerator.
	const foreignPlan = await planTool.execute(
		{ symptom: 'import acl failed', rawText: '===ABS_BEGIN:npu_smi_info===\n| 0       310P3             | OK            | 12.8         45                0    / 0              |\n===ABS_END:npu_smi_info===' },
		{}
	);
	check(foreignPlan.applicable === 'no', 'the planner must mark an out-of-scope device as not applicable');
	validateOutput('ascend_debug_plan', foreignPlan);
	check(foreignPlan.scope?.applicable === false, 'the planner scope must retain the boolean machine-readable decision');
	check(foreignPlan.matched.length === 0, 'the planner must not return 310B procedures for another chip');
	check(foreignPlan.text.includes('不适用范围'), 'the planner must explain why it refused');

	const checkTool = registeredTools.get('ascend_check');
	const inScopeCheck = await checkTool.execute(
		{ repoRoot: repoRoot ?? process.cwd(), rawText: PROBE_8T_CANN80 },
		{}
	);
	validateOutput('ascend_check', inScopeCheck);
	check(inScopeCheck.verdict === 'observed-match', 'the check tool must report the recorded 8T stack');
	check(inScopeCheck.acceptanceGates.length === 5, 'the check tool must return the five acceptance gates');
	if (repoRoot === null) {
		const noRepoCheck = await checkTool.execute({ rawText: PROBE_8T_CANN80 }, {});
		validateOutput('ascend_check', noRepoCheck);
		check(noRepoCheck.verdict === 'observed-match', 'check must still judge versions without a checkout');
		check(noRepoCheck.text.includes('包内快照'), 'a checkout-less check must say samples came from the package');
	}
	const foreignCheck = await checkTool.execute(
		{
			repoRoot: repoRoot ?? process.cwd(),
			rawText: '===ABS_BEGIN:npu_smi_info===\n| 0       310P3             | OK            | 12.8         45                0    / 0              |\n===ABS_END:npu_smi_info==='
		},
		{}
	);
	check(foreignCheck.verdict === 'out-of-scope', 'the check tool must report out-of-scope');
	validateOutput('ascend_check', foreignCheck);
	check(foreignCheck.upgradeOrder.length === 0, 'the check tool must withhold the 310B upgrade order');
	check(foreignCheck.acceptanceGates.length === 0, 'the check tool must withhold acceptance gates out of scope');
	check(foreignCheck.text.includes('不适用范围'), 'the check tool must explain why it withheld advice');
	check(!foreignCheck.text.includes('### 升级顺序'), 'an out-of-scope board must not receive an upgrade sequence');

	if (failures.length > 0) {
		console.error('smoke test FAILED:');
		for (const failure of failures) console.error(`  - ${failure}`);
		process.exitCode = 1;
	} else {
		console.log('smoke test passed');
		console.log(`  tools      : ${[...registeredTools.keys()].join(', ')}`);
		console.log(`  routes     : ${routes.map((route) => route.path).join(', ')}`);
		console.log(`  board      : ${payload.board.soc} / ${payload.board.tier} / ${payload.board.health}`);
		console.log(`  pairing    : ${payload.pairing.verdict} (${payload.pairing.evidence})`);
		console.log(`  samples    : ${payload.samples.length}`);
		console.log(`  knowledge  : ${payload.knowledge.length}`);
	}
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
