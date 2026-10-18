/**
 * Render the Writing panel once with a ready payload and assert that the
 * code-documentation coverage/sync area is visible. This is a structural UI
 * test; it does not replace a browser screenshot.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const bundlePath = join(here, '..', 'lib', 'client.js');
const loaded = [];
const ready = {
	root: '/repo',
	generatedAt: '2026-10-18T00:00:00.000Z',
	audit: { summary: { errors: 0, warnings: 1 }, findings: [{ severity: 'warning', file: 'src/case.md', line: 1, rule: 'sample-path-missing', message: 'missing sample' }] },
	progress: {
		totals: { files: 2, cjk: 100, figures: 1, codeBlocks: 2 },
		parts: [{ key: 'book', label: '理论教程', cjk: 100, files: [] }],
		missingFromSidebar: []
	},
	codeDocs: {
		summary: { samples: 2, readmes: 2, withDocs: 1, readmeOnly: 1, missingReadme: 0, codeFiles: 7, docsFiles: 3, findings: 1, indexedCases: 2 },
		entries: [
			{ sample: 'samples/case1', readme: true, docs: 3, codeFiles: 5, status: 'complete', issues: [] },
			{ sample: 'samples/case2', readme: true, docs: 0, codeFiles: 2, status: 'readme-only', issues: ['缺少详细 docs'] }
		],
		findings: [{ severity: 'warning', file: 'samples/case2/docs', line: 1, rule: 'code-docs-detailed-missing', message: '缺少代码说明' }]
	}
};

let stateIndex = 0;
const states = [[{ kind: 'ready', payload: ready }], [false]];
const react = {
	createElement(type, props, ...children) {
		if (typeof type === 'function') return type({ ...(props ?? {}), children });
		return { type, props: props ?? {}, children };
	},
	useState(initial) {
		const index = stateIndex++;
		if (states[index] === undefined) states[index] = initial;
		return [states[index][0], () => {}];
	},
	useEffect() {},
	useCallback(fn) {
		return fn;
	}
};

globalThis.window = {
	__ModuleLoader__: {
		load(entry) {
			loaded.push(entry);
		}
	}
};
globalThis.document = {
	querySelector: () => null,
	createElement: () => ({ dataset: {}, textContent: '' }),
	head: { appendChild: () => {} }
};

new Function(readFileSync(bundlePath, 'utf8'))();
const entry = loaded[0];
const face = entry.factory((name) => {
	if (name !== 'react') throw new Error(`unexpected module ${name}`);
	return react;
});
const registrations = [];
face.apply({
	slots: {
		inject(_name, callback) {
			const produced = callback();
			if (produced !== undefined && typeof produced.next === 'function') for (const _item of produced) void _item;
		},
		register(options, component) {
			registrations.push({ options, component });
		}
	}
});
const panel = registrations.find((item) => item.options.name === 'main')?.component;
if (typeof panel !== 'function') throw new Error('Writing panel did not register');
stateIndex = 0;
const tree = panel({});
const text = (node) => {
	if (node === null || node === undefined || typeof node === 'boolean') return '';
	if (typeof node === 'string' || typeof node === 'number') return String(node);
	if (Array.isArray(node)) return node.map(text).join(' ');
	return `${text(node.children ?? '')}`;
};
const rendered = text(tree);
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
check(rendered.includes('代码文档与样例同步'), 'panel must show code-documentation coverage');
check(rendered.includes('samples/case1'), 'panel must list sample documentation rows');
check(rendered.includes('代码 docs'), 'panel must show a code-docs card');
check(rendered.includes('跨对象同步问题'), 'panel must show cross-object sync issues');
if (failures.length > 0) {
	console.error('panel render test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('panel render test passed');
console.log('  code docs card + coverage table + sync issues rendered');
