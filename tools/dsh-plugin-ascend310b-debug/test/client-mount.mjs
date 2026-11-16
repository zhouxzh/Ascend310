/**
 * Offline mount test for the browser half.
 *
 * The bundle is a plain script that calls `window.__ModuleLoader__.load`, so it
 * can be executed with a stub loader and a stub `require`, then driven through
 * `apply()` against a fake slot registry. This proves the registration contract
 * (bundle id, slot names, panel key, sidebar id/order/label) without a browser.
 *
 * Usage: node test/client-mount.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

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

/** Style-tag stand-ins used to verify in-place updates and lifecycle cleanup. */
const styleTags = [];
globalThis.document = {
	querySelector: () => styleTags[0] ?? null,
	createElement: () => {
		const tag = {
			dataset: {},
			textContent: '',
			parentNode: null,
			remove() {
				const index = styleTags.indexOf(tag);
				if (index >= 0) styleTags.splice(index, 1);
				tag.parentNode = null;
			}
		};
		return tag;
	},
	head: {
		appendChild(tag) {
			tag.parentNode = globalThis.document.head;
			styleTags.push(tag);
		}
	}
};

/** React stand-in covering exactly the hooks and helpers the panel uses. */
const reactStub = {
	createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
	useState: (initial) => [initial, () => {}],
	useEffect: () => {},
	useCallback: (fn) => fn,
	useRef: (initial) => ({ current: initial })
};

const modules = { react: reactStub };

// Execute the bundle exactly as a browser would: it registers one factory.
new Function(readFileSync(bundlePath, 'utf8'))();

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

check(loaded.length === 1, `expected exactly one loader registration, got ${loaded.length}`);
const entry = loaded[0];
check(entry?.id === 'dsh-plugin-ascend310b-debug', `unexpected bundle id: ${entry?.id}`);

const face = entry.factory((name) => {
	if (!(name in modules)) throw new Error(`unexpected require("${name}")`);
	return modules[name];
});

check(typeof face.apply === 'function', 'exports.apply must be a function');
check(Array.isArray(face.inject), 'exports.inject must be an array');
check(face.inject?.includes('slots'), 'exports.inject must declare the slots service');
check(face.PANEL_ID === 'ascend310b-debug', `unexpected PANEL_ID: ${face.PANEL_ID}`);

/** Registrations and effects the fake context captured. */
const registrations = [];
const effectDisposers = [];
const fakeCtx = {
	effect(factory) {
		const dispose = factory();
		if (typeof dispose === 'function') effectDisposers.push(dispose);
		return dispose;
	},
	slots: {
		inject(name, callback) {
			const produced = callback();
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
check(main?.options.key === 'ascend310b-debug', `the main panel key must be "ascend310b-debug", got ${main?.options.key}`);
check(typeof main?.component === 'function', 'the main panel entry needs a component');
check(sidebar !== undefined, 'the panel must register a "sidebar.panellist" entry');
check(sidebar?.options.id === main?.options.key, 'the sidebar id must equal the main panel key');
check(typeof sidebar?.options.label === 'string', 'the sidebar entry needs a label');
check(
	sidebar?.options.label === '昇腾310B调试',
	`the sidebar label must name the chip family (昇腾310B调试), got ${sidebar?.options.label}`
);
check(typeof sidebar?.options.order === 'number', 'the sidebar entry needs a numeric order');
check(typeof sidebar?.component === 'function', 'the sidebar entry needs an icon component');

// A panel that renders before any probe must not invent a board version.
const panel = main?.component;
if (typeof panel === 'function') {
	let tree;
	try {
		tree = panel({});
	} catch (error) {
		failures.push(`the panel must render before data arrives: ${error?.message ?? error}`);
	}
	check(tree !== undefined, 'the panel should return a render tree for its loading state');
}

// HMR can apply a new browser module while the old style tag is still present.
// The new apply must reuse that tag, and only the newest owner may remove it.
check(styleTags.length === 1, `expected one installed style tag, got ${styleTags.length}`);
check(styleTags[0]?.textContent?.includes('.ad-root'), 'the installed style tag must contain the panel CSS');
check(typeof effectDisposers[0] === 'function', 'style installation must register a context-owned disposer');
const firstStyleOwner = styleTags[0]?.dataset?.pluginOwner;
styleTags[0].textContent = 'stale plugin CSS';
face.apply(fakeCtx);
check(styleTags.length === 1, 're-applying the module must update CSS in place instead of adding a tag');
check(styleTags[0]?.textContent?.includes('.ad-root'), 're-applying the module must replace stale CSS content');
check(styleTags[0]?.dataset?.pluginOwner !== firstStyleOwner, 'the newest module apply must own the shared style tag');
effectDisposers[0]?.();
check(styleTags.length === 1, 'an old HMR owner must not remove the replacement style tag');
effectDisposers[1]?.();
check(styleTags.length === 0, 'disposing the current module must remove its style tag');

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
