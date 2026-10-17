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
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
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

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

/** Collected tool definitions, keyed by name. */
const registeredTools = new Map();

// --- registration ---------------------------------------------------------
const routes = [];
const effects = [];
const config = {
	repoRoot: repoRoot ?? undefined,
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

	for (const name of ['ascend_probe', 'ascend_check', 'ascend_debug_plan', 'ascend_sample_index', 'ascend_board_exec']) {
		check(registeredTools.has(name), `tool ${name} must be registered`);
	}
	check(routes.length === 2, `expected two panel routes, got ${routes.length}`);
	check(routes.every((route) => route.kind === 'exact'), 'panel routes must be exact matches');
	check(effects.length === 2, 'each route must be registered through an effect');
	check(
		routes.map((route) => route.path).join(',') === '/ascend310b-debug/report.json,/ascend310b-debug/probe',
		'routes must expose the report and probe endpoints'
	);

	// --- target resolution ------------------------------------------------
	const resolved = host.resolveTarget(config);
	check(resolved.id === 'board-8t', 'the configured defaultTarget must win');
	check(host.resolveTarget(config, 'board-20t').host === '198.51.100.11', 'a target id must select that board');
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
	check(probeResult.text.includes('Ascend310B4'), 'the probe tool must print the board from the transcript');
	check(probeResult.text.includes('8.0.0'), 'the probe tool must print the probed CANN version');
	check(probeResult.text.includes('observed-match'), 'the probe tool must report the recorded version pairing');

	const planTool = registeredTools.get('ascend_debug_plan');
	const planResult = await planTool.execute({ symptom: 'import acl 失败', rawText: PROBE_8T_CANN80, repoRoot }, {});
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
	} else {
		// With no configured repository root the tool must refuse clearly instead
		// of silently indexing whatever directory the process happens to be in.
		const refusal = await indexTool.execute({ limit: 3 }, {});
		check(refusal.text.includes('未配置'), 'an unconfigured repoRoot must produce an explicit refusal');
	}

	// --- read-only command gate -------------------------------------------
	const execTool = registeredTools.get('ascend_board_exec');
	const preview = await execTool.execute({ command: 'npu-smi info', run: false }, {});
	check(preview.text.includes('npu-smi info'), 'a preview must echo the command');
	check(preview.text.includes('未执行') || preview.text.includes('read-only'), 'a preview must state that nothing ran');

	let rejected = await execTool.execute({ command: 'rm -rf /home/HwHiAiUser/Documents', run: false }, {});
	check(rejected.allowed === false, 'a mutating command must be refused');
	check(rejected.executed === false, 'a refused command must not execute');
	check(rejected.text.includes('命令被拒绝'), 'a refusal must say so in its text');
	check(String(rejected.reason).length > 0, 'a refusal must carry a reason');

	let chained = await execTool.execute({ command: 'npu-smi info && reboot', run: false }, {});
	check(chained.allowed === false, 'a chained command must be refused');
	check(chained.executed === false, 'a chained command must not execute');

	let redirected = await execTool.execute({ command: 'npu-smi info > /tmp/x', run: false }, {});
	check(redirected.allowed === false, 'a redirecting command must be refused');

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
		async *[Symbol.asyncIterator]() {
			yield JSON.stringify({ target: 'board-8t', rawText: PROBE_8T_CANN80 });
		}
	};
	await probeRoute.handler(request, response);
	check(response.status === 200, `the probe route must answer 200, got ${response.status}: ${response.body.slice(0, 400)}`);
	const payload = JSON.parse(response.body);
	check(payload.ok === true, `the probe route must report ok (${payload.error ?? 'no error field'})`);
	check(payload.board?.soc === 'Ascend310B4', 'the probe route must return the parsed board');
	check(Array.isArray(payload.targets) && payload.targets.length === 2, 'the probe route must return both configured targets');
	check(Array.isArray(payload.knowledge) && payload.knowledge.length > 0, 'the probe route must return the knowledge router');
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
	check(foreignPlan.scope?.applicable === false, 'the planner scope must retain the boolean machine-readable decision');
	check(foreignPlan.matched.length === 0, 'the planner must not return 310B procedures for another chip');
	check(foreignPlan.text.includes('不适用范围'), 'the planner must explain why it refused');

	const checkTool = registeredTools.get('ascend_check');
	const foreignCheck = await checkTool.execute(
		{
			repoRoot: repoRoot ?? process.cwd(),
			rawText: '===ABS_BEGIN:npu_smi_info===\n| 0       310P3             | OK            | 12.8         45                0    / 0              |\n===ABS_END:npu_smi_info==='
		},
		{}
	);
	check(foreignCheck.verdict === 'out-of-scope', 'the check tool must report out-of-scope');
	check(foreignCheck.upgradeOrder.length === 0, 'the check tool must withhold the 310B upgrade order');
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
	rmSync(stubRoot, { recursive: true, force: true });
}
