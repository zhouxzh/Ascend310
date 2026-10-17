/**
 * Offline mount test for the browser half.
 *
 * The bundle is a plain script that calls `window.__ModuleLoader__.load`, so
 * this test can execute it with a stub loader and a stub `require`, then drive
 * `apply()` against a fake slot registry. It proves the registration contract
 * (slot names, keys, ids, labels and exported faces) without a browser or a
 * running Harness.
 *
 * Usage: node test/client-mount.mjs
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const bundlePath = join(here, '..', 'lib', 'client.js');

/** Registrations the fake loader captured. */
const loaded = [];

globalThis.window = {
	__ModuleLoader__: {
		load(entry) {
			loaded.push(entry);
		}
	}
};

/** Minimal style-tag stand-in used by the bundle's CSS installer. */
globalThis.document = {
	querySelector: () => null,
	createElement: () => ({ dataset: {}, textContent: '' }),
	head: { appendChild: () => {} }
};

/** React stand-in covering exactly the hooks and helpers the panel uses. */
const reactStub = {
	createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
	useState: (initial) => [initial, () => {}],
	useEffect: () => {},
	useCallback: (fn) => fn
};

/** Module table the factory's `require` resolves against. */
const modules = { react: reactStub };

// Execute the bundle exactly as a browser would: it registers one factory.
// eslint-disable-next-line no-new-func
new Function(readFileSync(bundlePath, 'utf8'))();

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

check(loaded.length === 1, `expected exactly one loader registration, got ${loaded.length}`);
const entry = loaded[0];
check(entry?.id === 'dsh-plugin-ascend310b-writing', `unexpected bundle id: ${entry?.id}`);

const face = entry.factory((name) => {
	if (!(name in modules)) throw new Error(`unexpected require("${name}")`);
	return modules[name];
});

check(typeof face.apply === 'function', 'exports.apply must be a function');
check(Array.isArray(face.inject), 'exports.inject must be an array');
check(face.inject?.includes('slots'), 'exports.inject must declare the slots service');
check(face.PANEL_ID === 'ascend310b-writing', `unexpected PANEL_ID: ${face.PANEL_ID}`);

/** Registrations the fake slot service captured, keyed by slot name. */
const registrations = [];
const fakeCtx = {
	slots: {
		inject(name, callback) {
			const produced = callback();
			// A generator callback yields its disposers, exactly like the real
			// service consuming the registration.
			if (produced !== undefined && typeof produced.next === 'function') {
				for (const _ of produced) void _;
			}
		},
		register(options, component) {
			registrations.push({ options, component });
			return () => {};
		}
	}
};

face.apply(fakeCtx);

const main = registrations.find((item) => item.options.name === 'main');
const sidebar = registrations.find((item) => item.options.name === 'sidebar.panellist');
check(main !== undefined, 'the panel must register into the "main" slot');
check(main?.options.key === 'ascend310b-writing', `the main panel key must be "ascend310b-writing", got ${main?.options.key}`);
check(typeof main?.component === 'function', 'the main panel entry needs a component');
check(sidebar !== undefined, 'the panel must register a "sidebar.panellist" entry');
check(sidebar?.options.id === 'ascend310b-writing', `the sidebar entry id must be "ascend310b-writing", got ${sidebar?.options.id}`);
check(typeof sidebar?.options.label === 'string' || typeof sidebar?.options.label === 'function', 'the sidebar entry needs a label');
check(typeof sidebar?.options.order === 'number', 'the sidebar entry needs a numeric order');
check(typeof sidebar?.component === 'function', 'the sidebar entry needs an icon component');

if (failures.length > 0) {
	console.error('client mount test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}

console.log('client mount test passed');
console.log(`  bundle id      : ${entry.id}`);
console.log(`  inject         : ${face.inject.join(', ')}`);
console.log(`  main slot key  : ${main.options.key}`);
console.log(`  sidebar entry  : id=${sidebar.options.id} order=${sidebar.options.order} label=${JSON.stringify(sidebar.options.label)}`);
console.log(`  registrations  : ${registrations.length}`);
