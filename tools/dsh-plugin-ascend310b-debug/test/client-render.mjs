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
let seededLiveness;
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
		// First state call is the load state; third is a serial-only asset
		// selection; fifth is the active tab.
		if (seeded === 1) return [{ kind: seededKind, payload }, () => {}];
		if (seeded === 2) return [seededSelected, () => {}];
		if (seeded === 5) return [seededTab, () => {}];
		if (seeded === 8 && seededLiveness !== undefined) return [seededLiveness, () => {}];
		if (seeded === 9) return [seededPeripheralMode, () => {}];
		if (seeded === 11 && seededPeripheralState !== null) return [seededPeripheralState, () => {}];
		if (seeded === 13 && seededSpeakerConfirmed !== undefined) return [seededSpeakerConfirmed, () => {}];
		if (seeded === 14 && seededRecordingPlayback !== undefined) return [seededRecordingPlayback, () => {}];
		if (seeded === 15 && seededPlaybackConfirmed !== undefined) return [seededPlaybackConfirmed, () => {}];
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
	effect: (factory) => {
		factory();
		return () => {};
	},
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
check(text.includes('Orange Pi AI Pro 8T'), 'the canonical 8T device name must be visible');
check(!text.includes('Orange Pi AI Pro 8T · 7.4 GB'), 'NPU HBM capacity must not be presented as part of the physical device name');
// Firmware is deliberately not a default probe field: the current 310B
// images do not expose a readable, trustworthy firmware version.
// `undefined` appears legitimately inside a knowledge-entry title (the C++
// "undefined symbol" symptom), so the check targets React artifacts instead.
check(!text.includes('>undefined<') && !text.includes('undefined undefined'), 'the panel must not render raw undefined values');
check(text.includes('昇腾310B调试'), 'the panel title must name the chip family');
check(text.includes('检查在线'), 'the primary action must be an online check');
check(bundleSource.includes('ad-head-actions'), 'online check, serial discovery and LAN scan must share one action row');
check(text.includes('更新系统信息'), 'overview must expose a system-only refresh action');
seededSelected = 'board-20t';
seededLiveness = { kind: 'done', byTarget: { 'board-20t': true, 'board-8t': false }, checkedAt: '2026-10-30T00:00:00.000Z', message: '' };
seeded = 0;
const livenessTree = panel({});
const railNode = findNode(livenessTree, (node) => node.props?.className === 'ad-rail')[0];
const foldedRailText = draw(railNode).join(' ');
check(foldedRailText.includes('显示历史记录（1）'), 'offline devices must be folded into history after liveness completes');
check(!foldedRailText.includes('Orange Pi AI Pro 8T'), 'an offline non-selected device must not remain in the primary rail');
check(bundleSource.includes('A leftover configuredId is not identity'), 'stale inventory records must not inherit SSH liveness from a configured id');

const originalPayloadForStale = payload;
payload = {
	...originalPayloadForStale,
	boards: [
		{
			id: 'ascend-c0742bfdf696',
			mac: 'c0:74:2b:fd:f6:96',
			macs: ['c0:74:2b:fd:f6:96'],
			hostname: 'orangepiaipro',
			lastIp: '198.51.100.10',
			soc: 'Ascend310B4',
			tier: '8T',
			model: 'Orange Pi Ai Pro',
			configuredId: 'board-8t',
			lastSeen: '2026-10-30T07:54:28.768Z',
			changesCount: 0
		},
		{
			id: 'ascend-c0742bfddd7a',
			mac: 'c0:74:2b:fd:dd:7a',
			macs: ['c0:74:2b:fd:dd:7a'],
			hostname: 'orangepiaipro',
			lastIp: '172.17.0.1',
			soc: 'Ascend310B4',
			tier: '8T',
			model: 'Orange Pi Ai Pro 8T',
			configuredId: 'board-8t',
			lastSeen: '2026-10-18T13:50:49.955Z',
			changesCount: 0
		},
		{
			id: 'ascend-c0742bf81276',
			mac: 'c0:74:2b:f8:12:76',
			macs: ['c0:74:2b:f8:12:76'],
			hostname: 'orangepiaipro-20t',
			lastIp: '198.51.100.11',
			soc: 'Ascend310B1',
			tier: '20T',
			model: 'OPi Ai Pro 20T',
			configuredId: 'board-20t',
			lastSeen: '2026-10-30T07:54:28.768Z',
			changesCount: 0
		}
	],
	targets: [
		{ id: 'board-8t', label: '8T 板', host: '198.51.100.10', mac: 'c0:74:2b:fd:f6:96', macs: ['c0:74:2b:fd:f6:96'], discoveredId: 'ascend-c0742bfdf696', lastSeen: '2026-10-30T07:54:28.768Z' },
		{ id: 'board-20t', label: '20T 板', host: '198.51.100.11', mac: 'c0:74:2b:f8:12:76', macs: ['c0:74:2b:f8:12:76'], discoveredId: 'ascend-c0742bf81276', lastSeen: '2026-10-30T07:54:28.768Z' },
		{ id: 'ascend-c0742bfddd7a', label: 'stale 8T', host: '172.17.0.1', mac: 'c0:74:2b:fd:dd:7a', macs: ['c0:74:2b:fd:dd:7a'], lastSeen: '2026-10-18T13:50:49.955Z' }
	]
};
seededSelected = 'board-20t';
seededLiveness = { kind: 'done', byTarget: { 'board-20t': true, 'board-8t': true, 'ascend-c0742bfddd7a': false }, checkedAt: '2026-10-30T00:00:00.000Z', message: '' };
seeded = 0;
const staleRailText = draw(findNode(panel({}), (node) => node.props?.className === 'ad-rail')[0]).join(' ');
check(staleRailText.includes('显示历史记录（1）'), 'stale 8T NIC records that share configuredId must fold into history');
check(staleRailText.includes('c0:74:2b:fd:f6:96'), 'the currently reachable 8T board must remain in the primary rail');
check(!staleRailText.includes('c0:74:2b:fd:dd:7a'), 'an unreachable historical 8T MAC must not stay in the primary rail');

const stalePayloadForRail = payload;
payload = {
	...originalPayloadForStale,
	boards: [
		{
			id: 'ascend-c0742bfdf696',
			mac: 'c0:74:2b:fd:f6:96',
			macs: ['c0:74:2b:fd:f6:96'],
			hostname: 'orangepiaipro',
			lastIp: '198.51.100.212',
			soc: 'Ascend310B4',
			tier: '8T',
			model: 'Orange Pi Ai Pro 8T',
			configuredId: 'board-8t',
			lastSeen: '2026-10-30T08:13:09.843Z',
			changesCount: 0
		},
		{
			id: 'ascend-c0742bfdf74e',
			mac: 'c0:74:2b:fd:f7:4e',
			macs: ['c0:74:2b:fd:f7:4e'],
			hostname: 'orangepiaipro',
			lastIp: '198.51.100.107',
			soc: 'Ascend310B4',
			tier: '8T',
			model: 'Orange Pi Ai Pro 8T',
			configuredId: null,
			lastSeen: new Date().toISOString(),
			changesCount: 0
		},
		{
			id: 'ascend-c0742bf81276',
			mac: 'c0:74:2b:f8:12:76',
			macs: ['c0:74:2b:f8:12:76'],
			hostname: 'orangepiaipro-20t',
			lastIp: '198.51.100.101',
			soc: 'Ascend310B1',
			tier: '20T',
			model: 'OPi Ai Pro 20T',
			configuredId: 'board-20t',
			lastSeen: new Date().toISOString(),
			changesCount: 0
		}
	],
	targets: [
		{
			id: 'board-8t',
			label: '8T 板',
			host: 'orangepiaipro.local',
			mac: 'c0:74:2b:fd:f6:96',
			macs: ['c0:74:2b:fd:f6:96'],
			discoveredId: 'ascend-c0742bfdf696',
			lastSeen: '2026-10-30T08:13:09.843Z',
			isConfigured: true
		},
		{
			id: 'ascend-c0742bfdf74e',
			label: '发现 8T',
			host: '198.51.100.107',
			mac: 'c0:74:2b:fd:f7:4e',
			macs: ['c0:74:2b:fd:f7:4e'],
			lastSeen: new Date().toISOString(),
			isConfigured: false
		},
		{
			id: 'board-20t',
			label: '20T 板',
			host: '198.51.100.101',
			mac: 'c0:74:2b:f8:12:76',
			macs: ['c0:74:2b:f8:12:76'],
			discoveredId: 'ascend-c0742bf81276',
			lastSeen: new Date().toISOString(),
			isConfigured: true
		}
	]
};
seededSelected = 'board-20t';
seededLiveness = {
	kind: 'done',
	byTarget: { 'board-8t': true, 'ascend-c0742bfdf74e': true, 'board-20t': true },
	duplicateOf: { 'board-8t': 'ascend-c0742bfdf74e' },
	conflicts: [{ endpoint: '198.51.100.107:22', targetIds: ['board-8t', 'ascend-c0742bfdf74e'] }],
	checkedAt: new Date().toISOString(),
	message: ''
};
seeded = 0;
const duplicateEndpointTree = panel({});
const duplicateEndpointPanelText = draw(duplicateEndpointTree).join(' ');
const duplicateEndpointRailText = draw(findNode(duplicateEndpointTree, (node) => node.props?.className === 'ad-rail')[0]).join(' ');
check(duplicateEndpointRailText.includes('c0:74:2b:fd:f7:4e'), 'the observed MAC row must represent a shared SSH endpoint');
check(!duplicateEndpointRailText.includes('c0:74:2b:fd:f6:96'), 'a stale configured MAC row must not light up separately for the same endpoint');
check(duplicateEndpointPanelText.includes('同一 SSH 端点对应多个身份记录'), 'the panel must explain that duplicate endpoint identities require a binding');
payload = stalePayloadForRail;
seededSelected = 'board-8t';
seededLiveness = { kind: 'done', byTarget: { 'board-20t': true, 'board-8t': true, 'ascend-c0742bfddd7a': false }, checkedAt: '2026-10-30T00:00:00.000Z', message: '' };
seededTab = 'assets';
seeded = 0;
const assetsTree = panel({});
const activeAssetsPanel = findNode(assetsTree, (node) => node.props?.className === 'ad-panel ad-active')[0];
const assetsText = draw(activeAssetsPanel).join(' ');
check(assetsText.includes('c0:74:2b:fd:f6:96'), 'the assets page must show the target whose MAC/discovered id matches board-8t');
check(!assetsText.includes('c0:74:2b:fd:dd:7a'), 'the assets page must not select a stale board merely because configuredId matches');
seededSelected = 'board-20t';
seededTab = 'overview';

const serialSeen = new Date().toISOString();
payload = {
	...originalPayloadForStale,
	boards: [
		{
			id: 'ascend-c0742bfdf74e',
			mac: 'c0:74:2b:fd:f7:4e',
			macs: ['c0:74:2b:fd:f7:4e'],
			hostname: 'orangepiaipro',
			lastIp: '198.51.100.20',
			soc: 'Ascend310B4',
			tier: '8T',
			model: 'Orange Pi Ai Pro 8T',
			configuredId: null,
			lastSeen: serialSeen,
			changesCount: 0,
			serial: { status: 'reachable', port: 'COM6', user: 'HwHiAiUser', ip: '198.51.100.20', hostname: 'orangepiaipro', lastSeen: serialSeen }
		},
		{
			id: 'ascend-c0742bf81276',
			mac: 'c0:74:2b:f8:12:76',
			macs: ['c0:74:2b:f8:12:76'],
			hostname: 'orangepiaipro-20t',
			lastIp: '198.51.100.11',
			soc: 'Ascend310B1',
			tier: '20T',
			model: 'OPi Ai Pro 20T',
			configuredId: 'board-20t',
			lastSeen: '2026-10-30T07:54:28.768Z',
			changesCount: 0
		}
	],
	targets: [
		{ id: 'board-20t', label: '20T 板', host: '198.51.100.11', mac: 'c0:74:2b:f8:12:76', macs: ['c0:74:2b:f8:12:76'], discoveredId: 'ascend-c0742bf81276', lastSeen: '2026-10-30T07:54:28.768Z' },
		{ id: 'ascend-c0742bfdf74e', label: 'serial 8T', host: '198.51.100.20', mac: 'c0:74:2b:fd:f7:4e', macs: ['c0:74:2b:fd:f7:4e'], lastSeen: serialSeen }
	]
};
seededSelected = 'board-20t';
seededLiveness = { kind: 'done', byTarget: { 'board-20t': true, 'ascend-c0742bfdf74e': false }, checkedAt: serialSeen, message: '' };
seeded = 0;
const serialRailText = draw(findNode(panel({}), (node) => node.props?.className === 'ad-rail')[0]).join(' ');
check(serialRailText.includes('c0:74:2b:fd:f7:4e'), 'a recently serial-logged 8T must stay in the primary rail even when SSH is down');
check(serialRailText.includes('串口已登录，SSH 不可达'), 'the primary rail must say serial login succeeded when SSH is down');
payload = originalPayloadForStale;
check(bundleSource.includes('overflow:hidden;display:flex;flex-direction:column'), 'the panel shell must use a fixed flex layout instead of growing the page');
check(bundleSource.includes('className: "ad-panels"'), 'tab pages must stay mounted so switching does not remount the workbench');
check(bundleSource.includes('position:absolute;inset:0'), 'tab pages must occupy one shared rectangle instead of changing the page height');
check(!bundleSource.includes('hidden: tab !== id'), 'inactive tabs must not use hidden/display:none, which collapses the workbench');
check(bundleSource.includes('el.style.height'), 'the panel shell must pin itself to the remaining viewport height');
check(
	bundleSource.includes('.ad-workbench{flex:1 1 0;min-height:0;height:0;display:grid;grid-template-columns:248px minmax(0,1fr)'),
	'the workbench must keep a fixed navigation column independent of board content'
);
check(
	bundleSource.includes('.ad-overview-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))'),
	'the overview must use a fixed three-column board template'
);
check(!bundleSource.includes('grid-template-columns:repeat(auto-fit'), 'content must not choose its own column count through auto-fit grids');
check(bundleSource.includes('.ad-table{width:100%;table-layout:fixed'), 'all diagnostic tables must use fixed layout');
check(
	bundleSource.includes('function StorageTable') && bundleSource.includes('总容量') && bundleSource.includes('剩余可用'),
	'the overview must render storage medium, total capacity, used bytes and free bytes'
);
check(
	bundleSource.includes('未配置网络（仅串口已发现）'),
	'serial-only boards must be visible as discovered even before a network address is configured'
);
seeded = 0;
seededKind = 'ready';
seededSelected = 'board-8t';
seededTab = 'overview';
seededLiveness = undefined;
const fixedTableTree = panel({});
const fixedTables = findNode(fixedTableTree, (node) => node.type === 'table' && String(node.props?.className ?? '').includes('ad-table'));
const fixedTableGroups = findNode(fixedTableTree, (node) => node.type === 'colgroup');
check(
	fixedTables.length > 0 && fixedTableGroups.length === fixedTables.length &&
		fixedTableGroups.every((group) => findNode(group, (node) => node.type === 'col').length >= 2),
	`every rendered table must declare an explicit column-width template (${fixedTables.length} tables, ${fixedTableGroups.length} groups, ${fixedTableGroups.map((group) => findNode(group, (node) => node.type === 'col').length).join('/')})`
);
check(bundleSource.includes('没有历史记录'), 'the history toggle must keep a reserved slot even when no devices are folded');
check(!bundleSource.includes('ad-global-detail'), 'global status must not expand and push the workbench');
seededSelected = 'board-8t';
seededLiveness = undefined;
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
check(text.includes('串口记录'), 'the panel must expose a serial-records tab');
check(text.includes('摄像头') && text.includes('音频接口'), 'the panel must expose dedicated camera and audio tabs');
check(bundleSource.includes('/ascend310b-debug/serial-sessions'), 'serial records must use the serial sessions route');
check(bundleSource.includes('/ascend310b-debug/serial-sessions/clear'), 'serial records must use the clear route');
check(bundleSource.includes('window.confirm'), 'serial record clearing must require browser confirmation');
check(bundleSource.includes('串口会话记录') && bundleSource.includes('脱敏 Transcript'), 'serial records must render session details and redacted transcript');
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
	bundleSource.includes('row.online ? " ad-online" : row.serialOnline ? " ad-serial" : row.offline ? " ad-offline" : row.duplicate || row.changed ? " ad-changed"'),
	'live SSH state must take precedence over serial, offline and historical-change colors'
);
check(bundleSource.includes('foundId || selectedRef.current'), 'serial discovery must switch to the board it just found');
check(bundleSource.includes('const selectedBoard = boardForTarget(selectedTarget, payload.boards)'), 'assets must match a target to inventory by MAC/discovered identity, not stale configuredId');
check(bundleSource.includes('const currentBoard = boardForTarget(current, payload.boards)'), 'the header must use the same physical-board identity matcher as the rail');
check(bundleSource.includes('generation !== bindingGeneration.current || selectedRef.current !== discoveredTarget'), 'a binding response must not commit after the user switches boards');
check(bundleSource.includes('signal: controller.signal') && bundleSource.includes('bindingController'), 'binding requests must be abortable');
check(bundleSource.includes('h(TabBar, { active: tab, onChange: setTab, tabRefs })'), 'the tab bar must receive refs so arrow-key navigation can move focus');
check(bundleSource.includes('boardRailCount(payload.boards, payload.targets)'), 'the board count must include inventory boards and unmatched configured targets');
check(bundleSource.includes('"aria-label": "搜索开发板"'), 'the board search input must have an accessible name');
check(bundleSource.includes('typeof ResizeObserver === "function"'), 'panel height must also react to host layout changes through ResizeObserver');
check(bundleSource.includes('staleError: null') && bundleSource.includes('target: payload.target ?? previous.payload.target'), 'system refresh must clear stale errors and commit refreshed target identity');
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
check(bundleSource.includes('props.onField("device", selectedValue)'), 'selected camera node must be synchronized into the capture form');
check(
	bundleSource.includes('preferredFormat: capture ? (usb ? "MJPG" : "YUYV")'),
	'USB capture nodes must prefer MJPG while non-USB nodes prefer YUYV'
);
check(bundleSource.includes('AUDIO_REFRESH_URL') && bundleSource.includes('SYSTEM_REFRESH_URL'), 'system, camera and audio refresh endpoints must stay separate');
check(!bundleSource.includes('onClick: () => runProbe()'), 'the UI must not expose a global all-in-one device refresh');
check(!bundleSource.includes('const runProbe'), 'the frontend must not retain a dead all-in-one probe path');
check(bundleSource.includes('更新中 ${refreshElapsedSec}s…'), 'camera refresh must show elapsed progress instead of a silent wait');
check(bundleSource.includes('ad-global-status') && !bundleSource.includes('ad-global-detail'), 'async status messages must use a reserved one-line slot that does not expand');
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

// --- stateful camera interaction tests -----------------------------------
// The checks above render a snapshot. These tests run the panel with a small
// hook runtime so state updates, deferred requests, and event handlers execute
// as they do in the browser rather than being inferred from source strings.
function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

function jsonResponse(body, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		async json() {
			return body;
		}
	};
}

function abortable(promise, signal) {
	if (!signal) return promise;
	return new Promise((resolve, reject) => {
		const abort = () => {
			const error = new Error('aborted');
			error.name = 'AbortError';
			reject(error);
		};
		if (signal.aborted) abort();
		else signal.addEventListener('abort', abort, { once: true });
		promise.then(resolve, reject);
	});
}

function createHookRuntime(initialStates) {
	let component = null;
	const stateSlots = new Map();
	const refSlots = new Map();
	const effectSlots = new Map();
	const callbackSlots = new Map();
	let cursor = 0;
	let dirty = false;
	let currentUpdates = [];
	let queuedEffects = [];
	let tree = null;
	let unmounted = false;

	const sameDependencies = (left, right) => left !== undefined
		&& right !== undefined
		&& left.length === right.length
		&& left.every((value, index) => Object.is(value, right[index]));

	const react = {
		createElement(type, props, ...children) {
			if (typeof type === 'function') return type({ ...(props ?? {}), children });
			return { type, props: props ?? {}, children };
		},
		useState(initial) {
			const id = cursor++;
			if (!stateSlots.has(id)) {
				stateSlots.set(id, {
					value: Object.prototype.hasOwnProperty.call(initialStates, id) ? initialStates[id] : initial
				});
			}
			const slot = stateSlots.get(id);
			return [slot.value, (next) => {
				const value = typeof next === 'function' ? next(slot.value) : next;
				if (!Object.is(value, slot.value)) {
					slot.value = value;
					currentUpdates.push(id);
					dirty = true;
				}
			}];
		},
		useEffect(effect, dependencies) {
			const id = cursor++;
			let slot = effectSlots.get(id);
			if (slot === undefined) {
				slot = { dependencies: undefined, cleanup: null };
				effectSlots.set(id, slot);
			}
			if (dependencies === undefined || !sameDependencies(slot.dependencies, dependencies)) {
				const previousCleanup = slot.cleanup;
				slot.dependencies = dependencies === undefined ? undefined : [...dependencies];
				queuedEffects.push(() => {
					if (typeof previousCleanup === 'function') previousCleanup();
					slot.cleanup = effect();
				});
			}
		},
		useCallback(callback, dependencies) {
			const id = cursor++;
			const slot = callbackSlots.get(id);
			if (slot === undefined || dependencies === undefined || !sameDependencies(slot.dependencies, dependencies)) {
				callbackSlots.set(id, {
					callback,
					dependencies: dependencies === undefined ? undefined : [...dependencies]
				});
				return callback;
			}
			return slot.callback;
		},
		useRef(initial) {
			const id = cursor++;
			if (!refSlots.has(id)) refSlots.set(id, { current: initial });
			return refSlots.get(id);
		}
	};

	const flush = () => {
		for (let pass = 0; pass < 40; pass += 1) {
			dirty = false;
			currentUpdates = [];
			cursor = 0;
			queuedEffects = [];
			tree = component({});
			for (const effect of queuedEffects) effect();
			if (!dirty) return tree;
		}
		throw new Error(`stateful render did not settle after 40 passes; last updates: ${currentUpdates.join(',')}`);
	};

	return {
		react,
		setComponent(next) {
			component = next;
		},
		flush,
		tree: () => tree,
		state: (index) => stateSlots.get(index)?.value,
		setState(index, value) {
			const slot = stateSlots.get(index);
			if (slot !== undefined && !Object.is(slot.value, value)) {
				slot.value = value;
				dirty = true;
			}
		},
		unmount() {
			for (const slot of effectSlots.values()) {
				if (typeof slot.cleanup === 'function') slot.cleanup();
				slot.cleanup = null;
			}
			unmounted = true;
		}
	};
}

async function runStatefulCameraTests() {
	const payload8 = JSON.parse(JSON.stringify(cameraPayload));
	payload8.ok = true;
	payload8.target = { ...(payload8.target ?? {}), id: 'board-8t' };
	payload8.targets = [
		{ id: 'board-8t', label: '8T 板', host: '198.51.100.10', user: 'HwHiAiUser' },
		{ id: 'board-20t', label: '20T 板', host: '198.51.100.11', user: 'HwHiAiUser' }
	];
	const videoLines = String(payload8.facts.videoDevices.value).split('\n');
	videoLines.push('/dev/video4');
	payload8.facts.videoDevices = { ...payload8.facts.videoDevices, value: videoLines.join('\n') };
	const formatLines = String(payload8.facts.videoFormats.value).split('\n');
	const mipiFormatIndex = formatLines.indexOf('DEVICE=/dev/video2');
	if (mipiFormatIndex >= 0) {
		formatLines.splice(
			mipiFormatIndex + 1,
			0,
			"\t[1]: 'MJPG' (Motion-JPEG, compressed)",
			'\t\tSize: Discrete 640x480',
			'\t\t\tInterval: Discrete 0.033s (30.000 fps)'
		);
	}
	payload8.facts.videoFormats = { ...payload8.facts.videoFormats, value: formatLines.join('\n') };

	const pendingReports = [];
	const pendingCameraRefreshes = [];
	const fakeFetch = (input, options = {}) => {
		const url = String(input);
		if (url.startsWith('/ascend310b-debug/report.json')) {
			const parsed = new URL(url, 'http://local');
			if (parsed.searchParams.get('target') === 'board-20t') {
				const request = deferred();
				pendingReports.push(request);
				return abortable(request.promise, options.signal);
			}
			return Promise.resolve(jsonResponse(payload8));
		}
		if (url === '/ascend310b-debug/liveness') {
			return Promise.resolve(jsonResponse({
				ok: true,
				targets: [{ id: 'board-8t', online: true }],
				checkedAt: '2026-11-16T00:00:00.000Z',
				conflicts: []
			}));
		}
		if (url === '/ascend310b-debug/camera-refresh') {
			const request = deferred();
			pendingCameraRefreshes.push(request);
			return abortable(request.promise, options.signal);
		}
		return Promise.resolve(jsonResponse({ ok: false, error: `unexpected test route: ${url}` }, 404));
	};

	const originalFetch = globalThis.fetch;
	const originalAddEventListener = globalThis.window.addEventListener;
	const originalRemoveEventListener = globalThis.window.removeEventListener;
	globalThis.fetch = fakeFetch;
	globalThis.window.addEventListener = () => {};
	globalThis.window.removeEventListener = () => {};
	globalThis.window.innerHeight = 900;
	loaded.length = 0;
	new Function(bundleSource)();
	const initialState = {
		0: { kind: 'ready', payload: payload8 },
		1: 'board-8t',
		4: 'camera',
		7: { kind: 'done', byTarget: { 'board-8t': true }, checkedAt: '2026-11-16T00:00:00.000Z', message: '' },
		8: 'camera',
		10: { phase: 'idle', action: null, inventory: null, result: null, message: '' }
	};
	let runtime = createHookRuntime(initialState);
	try {
		const hookFace = loaded[0].factory((name) => {
			if (name === 'react') return runtime.react;
			throw new Error(`unexpected require("${name}")`);
		});
		let hookPanel = null;
		hookFace.apply({
			effect: (factory) => factory(),
			slots: {
				inject: (_name, callback) => {
					const produced = callback();
					if (produced !== undefined && typeof produced.next === 'function') for (const _ of produced) void _;
				},
				register: (options, component) => {
					if (options.name === 'main') hookPanel = component;
					return () => {};
				}
			}
		});
		runtime.setComponent(hookPanel);
		const settle = async () => {
			for (let index = 0; index < 10; index += 1) {
				runtime.flush();
				await Promise.resolve();
			}
			runtime.flush();
		};
		const cameraSelect = () => findNode(runtime.tree(), (node) => node.type === 'select' && node.props?.id === 'ad-peripheral-camera-device')[0];
		const formatSelect = () => findNode(runtime.tree(), (node) => node.type === 'select' && node.props?.id === 'ad-peripheral-camera-pixel')[0];
		const captureButton = () => findNode(runtime.tree(), (node) => node.type === 'button' && draw(node).join(' ').includes('采集一帧'))[0];
		const refreshButton = () => findNode(runtime.tree(), (node) =>
			node.type === 'button'
			&& node.props?.className?.includes('ad-refresh-button')
			&& draw(node).join(' ').includes('更新摄像头信息')
		)[0];

		runtime.flush();
		await settle();
		check(runtime.state(9)?.camera?.device === '/dev/video0', 'camera defaults must choose the USB capture node');
		check(runtime.state(9)?.camera?.pixelFormat === 'MJPG', 'USB capture must default to MJPG');
		const initialCameraSelect = cameraSelect();
		check(initialCameraSelect !== undefined, 'the stateful camera page must render the camera selector');
		if (initialCameraSelect === undefined) {
			const ids = findNode(runtime.tree(), (node) => node.type === 'select').map((node) => node.props?.id);
			throw new Error(`camera selector missing; rendered select ids: ${ids.join(', ')}`);
		}

		initialCameraSelect.props.onChange({ target: { value: '/dev/video2' } });
		runtime.flush();
		check(runtime.state(9)?.camera?.device === '/dev/video2', 'selecting a MIPI node must update the capture device');
		formatSelect().props.onChange({ target: { value: 'MJPG' } });
		runtime.flush();
		check(runtime.state(9)?.camera?.pixelFormat === 'MJPG', 'the test must start from a valid non-preferred MIPI format');

		cameraSelect().props.onChange({ target: { value: '/dev/video0' } });
		runtime.flush();
		check(runtime.state(9)?.camera?.pixelFormat === 'MJPG', 'switching to the USB node must follow its MJPG preference');
		cameraSelect().props.onChange({ target: { value: '/dev/video2' } });
		runtime.flush();
		check(
			runtime.state(9)?.camera?.pixelFormat === 'YUYV',
			'switching devices must apply the new MIPI preference even when the previous MJPG format is valid there'
		);

		formatSelect().props.onChange({ target: { value: 'MJPG' } });
		runtime.flush();
		refreshButton().props.onClick();
		runtime.flush();
		check(runtime.state(15)?.phase === 'running', 'camera refresh must enter a running state');
		check(captureButton().props.disabled === true, 'capture must be disabled while camera inventory is refreshing');
		pendingCameraRefreshes[0].resolve(jsonResponse({
			ok: true,
			target: payload8.target,
			facts: payload8.facts,
			durationMs: 800,
			updatedAt: '2026-11-16T00:00:01.000Z'
		}));
		await settle();
		check(
			runtime.state(9)?.camera?.pixelFormat === 'MJPG',
			`refreshing unchanged camera capabilities must preserve a manual format (got ${runtime.state(9)?.camera?.pixelFormat})`
		);

		const reducedFacts = JSON.parse(JSON.stringify(payload8.facts));
		reducedFacts.videoFormats = JSON.parse(JSON.stringify(cameraPayload.facts.videoFormats));
		refreshButton().props.onClick();
		runtime.flush();
		pendingCameraRefreshes[1].resolve(jsonResponse({
			ok: true,
			target: payload8.target,
			facts: reducedFacts,
			durationMs: 600,
			updatedAt: '2026-11-16T00:00:02.000Z'
		}));
		await settle();
		check(
			runtime.state(9)?.camera?.pixelFormat === 'YUYV',
			'a changed camera capability set must reset the format to the node preference'
		);

		refreshButton().props.onClick();
		runtime.flush();
		const staleRefresh = pendingCameraRefreshes[2];
		const historyButton = findNode(runtime.tree(), (node) =>
			node.type === 'button'
			&& node.props?.className?.includes('ad-history-toggle')
			&& draw(node).join(' ').includes('显示历史记录')
		)[0];
		historyButton?.props.onClick();
		runtime.flush();
		const board20 = findNode(runtime.tree(), (node) =>
			node.type === 'button'
			&& node.props?.className?.includes('ad-board-item')
			&& draw(node).join(' ').includes('20T')
		)[0];
		if (board20 === undefined) {
			const rows = findNode(runtime.tree(), (node) => node.props?.className?.includes('ad-board-item')).map((node) => draw(node).join(' '));
			throw new Error(`20T board row missing; rendered rows: ${rows.join(' | ')}`);
		}
		board20.props.onClick();
		runtime.flush();
		check(pendingReports.length === 1, 'switching boards must issue a deferred report request');
		const report20 = JSON.parse(JSON.stringify(payload8));
		report20.target = { ...(report20.target ?? {}), id: 'board-20t' };
		pendingReports[0].resolve(jsonResponse(report20));
		await settle();
		check(runtime.state(1) === 'board-20t', 'the deferred board switch must commit the requested board');
		check(runtime.state(15)?.phase === 'idle', 'switching boards must reset the previous camera refresh state');
		staleRefresh.resolve(jsonResponse({
			ok: true,
			target: payload8.target,
			facts: payload8.facts,
			durationMs: 200,
			updatedAt: '2026-11-16T00:00:03.000Z'
		}));
		await settle();
		check(runtime.state(1) === 'board-20t', 'a late camera refresh must not switch the selected board back');
		check(runtime.state(15)?.phase === 'idle', 'a late camera refresh must not commit stale completion state');
		check(captureButton().props.disabled === true, 'capture must stay disabled while board liveness is unknown');

		runtime.setState(7, { kind: 'done', byTarget: { 'board-20t': true }, checkedAt: '2026-11-16T00:00:04.000Z', message: '' });
		runtime.flush();
		cameraSelect().props.onChange({ target: { value: '/dev/video4' } });
		runtime.flush();
		check(captureButton().props.disabled === true, 'capture must be disabled when camera capability is unconfirmed');
		check(
			String(captureButton().props.title).includes('采集能力尚未确认'),
			'disabled capture must explain that the camera capability is unconfirmed'
		);

		runtime.setState(10, {
			phase: 'done',
			action: 'camera',
			inventory: null,
			result: {
				ok: true,
				executed: true,
				evidence: 'observed-pass',
				exitCode: 0,
				output: '/tmp/ascend310b-debug/peripherals/camera-state-test.jpg',
				data: { sha256: 'state-test', actualWidth: 640, actualHeight: 480, fourcc: 'YUYV', frameCount: 1, bytes: 42 },
				text: '摄像头采集完成'
			},
			message: ''
		});
		runtime.flush();
		let preview = findNode(runtime.tree(), (node) => node.type === 'img' && node.props?.alt === '摄像头本次采集画面')[0];
		check(preview !== undefined, 'a successful stateful capture must render a preview image');
		preview?.props.onError();
		runtime.flush();
		check(
			findNode(runtime.tree(), (node) => node.props?.role === 'alert' && draw(node).join(' ').includes('画面预览加载失败')).length === 1,
			'a preview load error must surface an accessible retry message'
		);
		const retry = findNode(runtime.tree(), (node) => node.type === 'button' && draw(node).join(' ').includes('重试预览'))[0];
		check(retry !== undefined, 'a failed preview must expose a retry button');
		retry?.props.onClick();
		runtime.flush();
		preview = findNode(runtime.tree(), (node) => node.type === 'img' && node.props?.alt === '摄像头本次采集画面')[0];
		check(preview?.props?.src?.includes('retry=1'), 'retrying a preview must request the image again');
		check(
			findNode(runtime.tree(), (node) => node.props?.role === 'alert' && draw(node).join(' ').includes('画面预览加载失败')).length === 0,
			'retrying a preview must clear the previous error'
		);
	} finally {
		runtime?.unmount();
		globalThis.fetch = originalFetch;
		globalThis.window.addEventListener = originalAddEventListener;
		globalThis.window.removeEventListener = originalRemoveEventListener;
	}
}

try {
	await runStatefulCameraTests();
} catch (error) {
	failures.push(`stateful camera interaction test threw: ${error?.stack ?? error}`);
}

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
