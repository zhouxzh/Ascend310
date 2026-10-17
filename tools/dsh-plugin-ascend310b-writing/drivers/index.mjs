/**
 * Pick the environment adapter for this host.
 *
 * The rest of the plugin asks this module for the adapter and then only uses
 * the adapter's vocabulary — `shellCommand`, `toShellPath`, `pythonPlan` — so
 * no other file branches on the platform, and adding a third platform means
 * adding one adapter rather than editing the driver.
 *
 * @module dsh-plugin-ascend310b-writing/drivers
 */

import { describe as describePosix } from './env-linux.mjs';
import { describe as describeWindows } from './env-windows.mjs';

/**
 * Does a probe result name the conventional `python3`, or an alias for it?
 *
 * @param {object | undefined} record - the `python3` detection record.
 * @returns {boolean} True when the name really is `python3`.
 */
function isRealPython3(record) {
	return record !== undefined && record.ok === true && /(^|[\\/])python3(\.exe)?$/iu.test(record.name ?? '');
}

/**
 * Build the adapter for this host from a detection result.
 *
 * @param {object} [options] - inputs.
 * @param {string} [options.platform] - defaults to `process.platform`.
 * @param {Array<object>} [options.components] - {@link detectToolchain} output.
 * @returns {object} The adapter, with detection already bound.
 */
export function describeEnvironment({ platform = process.platform, components = [] } = {}) {
	const byId = new Map(components.map((component) => [component.id, component]));
	const python = byId.get('python3');
	const python3 = isRealPython3(python) ? python : undefined;
	const pythonAlias = python !== undefined && python.ok === true && python3 === undefined ? python : undefined;
	const detected = { bash: byId.get('bash'), python3, pythonAlias };

	if (platform === 'win32') return describeWindows(detected);
	return describePosix(detected);
}

export { describeWindows, describePosix };
