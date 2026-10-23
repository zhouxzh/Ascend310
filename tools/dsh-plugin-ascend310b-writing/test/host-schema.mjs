/**
 * Host-side output-schema guard.
 *
 * DSH rejects a tool schema whose object node does not explicitly declare
 * additionalProperties. That failure prevents the whole writing Host plugin
 * from activating, so the report route also disappears. Load the Host with a
 * minimal defineTool stub and walk every registered output schema.
 */

import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const stubRoot = join(root, 'node_modules', '@deepseek-ai', 'dsh-tools');
if (existsSync(stubRoot)) {
	console.error(`host-schema test refuses to overwrite existing ${stubRoot}`);
	process.exit(1);
}
mkdirSync(stubRoot, { recursive: true });
writeFileSync(
	join(stubRoot, 'package.json'),
	JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-stub', type: 'module', main: './index.js' }),
	'utf8'
);
writeFileSync(join(stubRoot, 'index.js'), 'export const defineTool = (tool) => tool;\n', 'utf8');

const tools = [];
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
function walk(node, path) {
	if (node === null || typeof node !== 'object') return;
	if (node.type === 'object' && typeof node.additionalProperties !== 'boolean') {
		failures.push(`${path}: object schema must declare additionalProperties`);
	}
	if (node.type === 'array' && node.items !== undefined) walk(node.items, `${path}.items`);
	for (const [key, child] of Object.entries(node.properties ?? {})) walk(child, `${path}.properties.${key}`);
}

try {
	const host = await import('../lib/index.js?host-schema');
	const fakeCtx = {
		tools: {
			register(tool) {
				tools.push(tool);
				return () => {};
			},
			get() {
				return undefined;
			}
		},
		get() {
			return undefined;
		},
		inject() {},
		effect() {}
	};
	host.apply(fakeCtx, { repoRoot: root });
	check(tools.length > 0, 'host must register at least one tool');
	for (const tool of tools) {
		check(tool.output?.schema !== undefined, `${tool.name}: missing output schema`);
		if (tool.output?.schema) walk(tool.output.schema, tool.name);
	}
} finally {
	rmSync(stubRoot, { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error(`host-schema test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('host-schema test passed');
console.log(`  registered tools: ${tools.length}`);
