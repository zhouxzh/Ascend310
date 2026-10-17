/**
 * Scope tests.
 *
 * The plugin's procedures, sample index and version tables describe Ascend
 * 310B1/310B4 only. These tests pin that boundary: a 310B board is in scope, a
 * different accelerator is explicitly out of scope, and an unread SoC stays
 * `unknown` rather than being guessed either way.
 *
 * Usage: node test/scope.mjs
 */

import assert from 'node:assert/strict';

import { assessBoardScope, PLUGIN_DISPLAY_NAME, SUPPORTED_SOCS } from '../lib/compat.js';
import { parseProbeOutput } from '../lib/probe.js';
import { NPU_SMI_310B4, NPU_SMI_310B1 } from './fixtures.mjs';

const failures = [];
const check = (name, run) => {
	try {
		run();
	} catch (error) {
		failures.push(`${name}: ${error?.message ?? error}`);
	}
};

check('display name names the chip family', () => {
	assert.equal(PLUGIN_DISPLAY_NAME, '昇腾310B调试');
	assert.match(PLUGIN_DISPLAY_NAME, /昇腾/);
});

check('supported SoCs are the two 310B variants', () => {
	assert.deepEqual([...SUPPORTED_SOCS], ['Ascend310B1', 'Ascend310B4']);
});

check('310B4 is in scope', () => {
	const report = parseProbeOutput(NPU_SMI_310B4);
	assert.equal(report.scope.applicable, true);
	assert.equal(report.scope.soc, 'Ascend310B4');
	assert.equal(report.scope.tier, '8T');
	assert.match(report.scope.message, /昇腾 310B/);
});

check('310B1 is in scope', () => {
	const report = parseProbeOutput(NPU_SMI_310B1);
	assert.equal(report.scope.applicable, true);
	assert.equal(report.scope.soc, 'Ascend310B1');
	assert.equal(report.scope.tier, '20T');
});

check('another Ascend chip is out of scope', () => {
	const report = parseProbeOutput('| 0       310P3             | OK            | 12.8         45                0    / 0              |\n| 0       0                 | 0000:00:00.0  | 0            2433 / 7564                            |');
	assert.equal(report.scope.applicable, false);
	assert.equal(report.scope.soc, 'Ascend310P3');
	assert.match(report.scope.message, /不是昇腾 310B1\/310B4/);
	assert.match(report.scope.message, /不适用/);
});

check('a non-Ascend accelerator is out of scope', () => {
	const report = parseProbeOutput('| 0       NVIDIA-A100        | OK            | 12.8         45                0    / 0              |\n| 0       0                 | 0000:00:00.0  | 0            2433 / 7564                            |');
	assert.equal(report.scope.applicable, false);
	assert.match(report.scope.message, /不是昇腾设备/);
});

check('an unread SoC stays unknown', () => {
	const report = parseProbeOutput('===ABS_BEGIN:kernel===\n5.10.0\n===ABS_END:kernel===');
	assert.equal(report.scope.applicable, 'unknown');
	assert.equal(report.scope.soc, null);
	assert.match(report.scope.message, /无法确认/);
});

check('assessBoardScope tolerates an empty environment', () => {
	const scope = assessBoardScope(null);
	assert.equal(scope.applicable, 'unknown');
	assert.deepEqual(scope.supportedSocs, ['Ascend310B1', 'Ascend310B4']);
});

if (failures.length > 0) {
	console.error(`scope tests FAILED (${failures.length}):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('scope tests passed');
console.log('  display name  : 昇腾310B调试');
console.log('  supported SoCs: Ascend310B1, Ascend310B4');
console.log('  out-of-scope  : reported as applicable=false, never as a 310B procedure');
