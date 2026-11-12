/**
 * Remote task and download path boundary tests.
 *
 * Usage: node test/task-paths.mjs
 */

import assert from 'node:assert/strict';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, '..');
const stubRoot = join(pluginRoot, 'node_modules', '@deepseek-ai', 'dsh-tools');
if (existsSync(stubRoot)) {
	console.error(`task-path test refuses to overwrite existing ${stubRoot}`);
	process.exit(1);
}
mkdirSync(stubRoot, { recursive: true });
writeFileSync(
	join(stubRoot, 'package.json'),
	JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-stub', type: 'module', main: './index.js' }),
	'utf8'
);
writeFileSync(join(stubRoot, 'index.js'), 'export const defineTool = (tool) => tool;\n', 'utf8');

let failures = [];
try {
	const { internals } = await import('../lib/index.js');
	const target = {
		transport: 'ssh',
		user: 'HwHiAiUser',
		workdir: internals.REMOTE_WORKSPACE_ROOT
	};
	const check = (name, run) => {
		try {
			run();
		} catch (error) {
			failures.push(`${name}: ${error?.message ?? error}`);
		}
	};
	const mustReject = (name, run) => check(name, () => assert.throws(run));

	check('defaults task cwd to configured workspace', () => {
		assert.equal(internals.resolveTaskCwd(target), internals.REMOTE_WORKSPACE_ROOT);
	});
	for (const allowed of [
		'/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/debug-1',
		'/home/HwHiAiUser/Documents/Ascend310/deployments/case4/8t/release-a',
		'/tmp/ascend310b-debug/peripherals'
	]) {
		check(`allows task cwd ${allowed}`, () => assert.equal(internals.resolveTaskCwd(target, allowed), allowed));
	}
	mustReject('requires configured workdir', () => internals.resolveTaskCwd({ transport: 'ssh', user: 'HwHiAiUser' }));
	mustReject('rejects a noncanonical configured workdir', () => internals.resolveTaskCwd({ ...target, workdir: '/home/HwHiAiUser/Documents' }));
	for (const blocked of [
		'/home/HwHiAiUser',
		'/home/HwHiAiUser/Documents',
		'/home/HwHiAiUser/Documents/Ascend310/assets/case4',
		'/home/HwHiAiUser/Documents/Ascend310/archives/case4',
		'/tmp',
		'/tmp/other',
		'/home/HwHiAiUser/Documents/Ascend310/workspaces/../assets'
	]) {
		mustReject(`rejects task cwd ${blocked}`, () => internals.resolveTaskCwd(target, blocked));
	}

	for (const allowed of [
		'/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/run/model.onnx',
		'/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/model.onnx',
		'/tmp/ascend310b-debug/downloads/model.onnx'
	]) {
		check(`allows download destination ${allowed}`, () => {
			assert.equal(internals.resolveDownloadDestination(target, internals.REMOTE_WORKSPACE_ROOT, allowed), allowed);
		});
	}
	check('resolves a relative download beneath the configured workspace', () => {
		assert.equal(
			internals.resolveDownloadDestination(target, internals.REMOTE_WORKSPACE_ROOT, 'case4/run/model.onnx'),
			'/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/run/model.onnx'
		);
	});
	for (const blocked of [
		'/home/HwHiAiUser/.ssh/authorized_keys',
		'/home/HwHiAiUser/Documents/model.onnx',
		'/home/HwHiAiUser/Documents/Ascend310/deployments/case4/model.onnx',
		'/tmp/model.onnx',
		'../assets/model.onnx'
	]) {
		mustReject(`rejects download destination ${blocked}`, () => {
			internals.resolveDownloadDestination(target, internals.REMOTE_WORKSPACE_ROOT, blocked);
		});
	}
} finally {
	rmSync(join(pluginRoot, 'node_modules'), { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error(`task-path tests FAILED (${failures.length}):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('task-path tests passed');
console.log('  workdir : required /home/HwHiAiUser/Documents/Ascend310/workspaces');
console.log('  roots   : workspace, deployments for task cwd, assets for downloads, fixed debug tmp');
