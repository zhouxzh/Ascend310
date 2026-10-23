/**
 * Target-merge regression for two physical boards that share a hostname.
 *
 * A configured target may match one MAC, but a second board with the same
 * hostname must remain addressable by its own MAC-derived target id.
 */

import { existsSync, mkdirSync, mkdtempSync, rmdirSync, rmSync, writeFileSync } from 'node:fs';
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
	const ambiguous = internals.targetTable({
		inventoryPath,
		targets: [{ id: 'board-x', transport: 'ssh', host: 'orangepiaipro.local' }]
	});
	if (ambiguous.get('board-x')?.mac !== null || ambiguous.get('board-x')?.host !== 'orangepiaipro.local') {
		failures.push('ambiguous hostname must not redirect a configured target');
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
