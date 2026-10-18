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
const { mkdirSync, rmSync, writeFileSync } = await import('node:fs');
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
	rmSync(stubRoot, { recursive: true, force: true });
}

const routes = [];
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
	async *[Symbol.asyncIterator]() {
		yield JSON.stringify({ target: 'board-8t', rawText: PROBE_8T_CANN80 });
	}
};
await routes.find((route) => route.path === '/ascend310b-debug/probe').handler(request, response);
check(response.status === 200, `route must answer 200, got ${response.status}`);
let payload = JSON.parse(response.body);

// --- mount the bundle with the payload already loaded --------------------
let seeded = 0;
const reactStub = {
	createElement(type, props, ...children) {
		// Function components are invoked like React would, so the rendered text
		// below comes from the real components rather than from their tags.
		if (typeof type === 'function') return type({ ...(props ?? {}), children });
		return { type, props: props ?? {}, children };
	},
	useState: (initial) => {
		seeded += 1;
		// First state call is the load state, second is the target selector, third
		// is the symptom query. Seed the panel as if the first fetch had resolved.
		if (seeded === 1) return [{ kind: 'ready', payload }, () => {}];
		return [initial, () => {}];
	},
	useEffect: () => {},
	useCallback: (fn) => fn
};

new Function(readFileSync(join(pluginRoot, 'lib', 'client.js'), 'utf8'))();
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
// Firmware is deliberately not a default probe field: the current 310B
// images do not expose a readable, trustworthy firmware version.
// `undefined` appears legitimately inside a knowledge-entry title (the C++
// "undefined symbol" symptom), so the check targets React artifacts instead.
check(!text.includes('>undefined<') && !text.includes('undefined undefined'), 'the panel must not render raw undefined values');
check(text.includes('昇腾310B调试'), 'the panel title must name the chip family');
check(text.includes('实时监控'), 'the panel must expose a live-monitor control');
check(text.includes('发现开发板'), 'the panel must expose a discovery control');
check(text.includes('开发板资产'), 'the panel must expose a MAC-keyed asset tab');
check(!text.includes('310B 调试'), 'the old title without 昇腾 must be gone');
check(text.includes('已确认是昇腾 310B'), 'an in-scope board must say so explicitly');

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
