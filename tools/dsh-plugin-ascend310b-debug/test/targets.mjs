/**
 * Target-merge regression for two physical boards that share a hostname.
 *
 * A configured target may match one MAC, but a second board with the same
 * hostname must remain addressable by its own MAC-derived target id.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, '..');
const stubRoot = join(pluginRoot, 'node_modules', '@deepseek-ai', 'dsh-tools');
if (existsSync(stubRoot)) {
	console.error(`target test refuses to overwrite existing ${stubRoot}`);
	process.exit(1);
}
mkdirSync(stubRoot, { recursive: true });
writeFileSync(
	join(stubRoot, 'package.json'),
	JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-stub', type: 'module', main: './index.js' }),
	'utf8'
);
writeFileSync(join(stubRoot, 'index.js'), 'export const defineTool = (tool) => tool;\n', 'utf8');

const directory = mkdtempSync(join(tmpdir(), 'ascend-targets-'));
const inventoryPath = join(directory, 'inventory.json');
writeFileSync(
	inventoryPath,
	JSON.stringify({
		version: 1,
		boards: {
			'c0:74:2b:fd:f6:96': {
				id: 'ascend-c0742bfdf696',
				mac: 'c0:74:2b:fd:f6:96',
				macs: ['c0:74:2b:fd:f6:96', 'aa:bb:cc:dd:ee:ff'],
				hostname: 'orangepiaipro',
				lastIp: '10.42.8.212',
				lastSeen: '2026-10-23T03:40:24.134Z',
				system: { soc: 'Ascend310B4', tier: '8T' },
				configuredId: 'board-8t'
			},
			'c0:74:2b:fd:df:83': {
				id: 'ascend-c0742bfddf83',
				mac: 'c0:74:2b:fd:df:83',
				hostname: 'orangepiaipro',
				lastIp: '10.42.8.249',
				lastSeen: '2026-10-23T03:40:24.134Z',
				system: { soc: 'Ascend310B4', tier: '8T' },
				configuredId: null
			}
		}
	}),
	'utf8'
);

let failures = [];
try {
	const { internals } = await import('../lib/index.js');
	const config = {
		inventoryPath,
		targets: [{ id: 'board-8t', label: 'configured 8T', transport: 'ssh', host: 'orangepiaipro.local', user: 'HwHiAiUser' }]
	};
	const table = internals.targetTable(config);
	const board8t = table.get('board-8t');
	const second = table.get('ascend-c0742bfddf83');
	if (board8t?.mac !== 'c0:74:2b:fd:f6:96') failures.push('configured target must bind the newest matching MAC');
	if (board8t?.host !== '10.42.8.212') failures.push('configured target must use the observed IPv4 for execution');
	if (second?.mac !== 'c0:74:2b:fd:df:83') failures.push('same-hostname second board must remain a separate target');
	const macBound = internals.targetTable({
		inventoryPath,
		targets: [{ id: 'board-8t', transport: 'ssh', host: 'orangepiaipro.local', mac: 'c0:74:2b:fd:df:83' }]
	});
	if (macBound.get('board-8t')?.mac !== 'c0:74:2b:fd:df:83') {
		failures.push('configured MAC must not bind to a different discovered MAC');
	}
	const aliasBound = internals.targetTable({
		inventoryPath,
		targets: [{ id: 'board-8t', transport: 'ssh', host: 'orangepiaipro.local', mac: 'aa:bb:cc:dd:ee:ff' }]
	});
	if (aliasBound.get('board-8t')?.host !== '10.42.8.212') {
		failures.push('configured MAC must match any retained interface alias');
	}
	const boundPath = join(directory, 'bound-inventory.json');
	const boundInventory = JSON.parse(readFileSync(inventoryPath, 'utf8'));
	boundInventory.preferences = { bindings: { 'board-8t': 'ascend-c0742bfddf83' } };
	writeFileSync(boundPath, JSON.stringify(boundInventory), 'utf8');
	const explicitlyBound = internals.targetTable({
		inventoryPath: boundPath,
		targets: [{ id: 'board-8t', transport: 'ssh', host: 'orangepiaipro.local', mac: 'c0:74:2b:fd:f6:96' }]
	});
	if (explicitlyBound.get('board-8t')?.mac !== 'c0:74:2b:fd:df:83' || explicitlyBound.get('board-8t')?.host !== '10.42.8.249') {
		failures.push('explicit binding must override a stale configured MAC only when the user asked for it');
	}
	const ambiguous = internals.targetTable({
		inventoryPath,
		targets: [{ id: 'board-x', transport: 'ssh', host: 'orangepiaipro.local' }]
	});
	if (ambiguous.get('board-x')?.mac !== null || ambiguous.get('board-x')?.host !== 'orangepiaipro.local') {
		failures.push('ambiguous hostname must not redirect a configured target');
	}
	const localLiveness = await internals.checkTargetLiveness({
		 targets: [{ id: 'local', transport: 'local', host: 'localhost' }]
	}, { timeoutMs: 250 });
	if (localLiveness.targets?.[0]?.online !== true || localLiveness.targets?.[0]?.state !== 'local') {
		failures.push('local liveness target must be reported online without opening a network socket');
	}
	const invalidLiveness = await internals.checkTargetLiveness({
		targets: [{ id: 'invalid', transport: 'ssh', host: 'bad host', port: 22 }]
	}, { timeoutMs: 250 });
	if (invalidLiveness.targets?.[0]?.online !== false || invalidLiveness.targets?.[0]?.state !== 'invalid') {
		failures.push('invalid liveness targets must be reported offline, not attempted');
	}
	const reportStorePath = join(directory, 'reports.json');
	const reportConfig = { ...config, reportStorePath };
	const savedValue = {
		ok: true,
		probedAt: '2026-10-26T12:00:00.000Z',
		source: 'ssh',
		target: { id: 'board-8t', label: 'configured 8T', transport: 'ssh', host: '10.42.8.212', user: 'HwHiAiUser', identityFile: '/secret/key' },
		board: { soc: 'Ascend310B4', tier: '8T' },
		facts: { cannVersion: { value: '8.0.0' } },
		scope: { applicable: true },
		pairing: { verdict: 'observed-match' },
		missingCore: [],
		transport: { ok: true, reason: 'ok' },
		rawExcerpt: 'saved output'
	};
	internals.rememberSavedReport(reportConfig, savedValue);
	const saved = internals.readSavedReport(reportConfig, 'board-8t');
	if (saved?.facts?.cannVersion?.value !== '8.0.0' || saved?.rawExcerpt !== 'saved output') {
		failures.push('successful device information must persist across plugin restarts');
	}
	if (Object.prototype.hasOwnProperty.call(saved?.target ?? {}, 'identityFile')) {
		failures.push('persisted reports must not store identityFile or other private target fields');
	}
	internals.mergeSavedCameraFacts(
		reportConfig,
		{ id: 'board-8t', label: 'configured 8T', transport: 'ssh', host: '10.42.8.212', user: 'HwHiAiUser' },
		{ videoFormats: { value: 'DEVICE=/dev/video0', source: 'video_formats' } },
		'2026-10-29T12:00:00.000Z'
	);
	const cameraMerged = internals.readSavedReport(reportConfig, 'board-8t');
	if (cameraMerged?.facts?.cannVersion?.value !== '8.0.0' || cameraMerged?.facts?.videoFormats?.value !== 'DEVICE=/dev/video0') {
		failures.push('camera-only refresh must merge V4L2 facts without discarding saved environment facts');
	}
	internals.mergeSavedFacts(
		reportConfig,
		{ id: 'board-8t', label: 'configured 8T', transport: 'ssh', host: '10.42.8.212', user: 'HwHiAiUser' },
		{ driverVersion: { value: '25.2.0', source: 'system-refresh' } },
		'system',
		'2026-10-29T12:01:00.000Z',
		{ probedAt: '2026-10-29T12:01:00.000Z', board: { soc: 'Ascend310B4' }, scope: { applicable: true }, missingCore: [], pairing: { verdict: 'observed-match' }, transport: { ok: true } }
	);
	const systemMerged = internals.readSavedReport(reportConfig, 'board-8t');
	if (systemMerged?.board?.soc !== 'Ascend310B4' || systemMerged?.facts?.videoFormats?.value !== 'DEVICE=/dev/video0') {
		failures.push('system-only refresh must update system metadata while retaining camera facts');
	}
	if (!systemMerged?.categoryUpdatedAt?.camera || !systemMerged?.categoryUpdatedAt?.system) {
		failures.push('category refresh timestamps must be retained independently');
	}
} finally {
	rmSync(directory, { recursive: true, force: true });
	rmSync(stubRoot, { recursive: true, force: true });
	try {
		rmdirSync(join(pluginRoot, 'node_modules', '@deepseek-ai'));
	} catch {
		// Keep a real dependency tree if one existed.
	}
	try {
		rmdirSync(join(pluginRoot, 'node_modules'));
	} catch {
		// Keep a real dependency tree if one existed.
	}
}

if (failures.length > 0) {
	console.error('target test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('target test passed');
console.log('  configured 8T and same-hostname second 8T remain distinct');
