/**
 * Apt-install policy tests. These never install packages on a board.
 */

import { buildAptInstallCommand, installAptPackages, validateAptPackages } from '../lib/install.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => {
	if (actual !== expected) failures.push(`${message}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
};

const good = validateAptPackages(['v4l-utils', 'alsa-utils', 'v4l-utils']);
check(good.ok, 'normal apt package names must be accepted');
equal(good.packages.join(','), 'v4l-utils,alsa-utils', 'duplicate package names must be removed');
check(!validateAptPackages([]).ok, 'empty package list must be rejected');
check(!validateAptPackages(['v4l-utils;reboot']).ok, 'shell metacharacters must be rejected');
check(!validateAptPackages(['../etc/passwd']).ok, 'path-like package names must be rejected');

const command = buildAptInstallCommand(['v4l-utils'], { update: true });
check(command.startsWith('sudo -S '), 'command must use sudo -S');
check(command.includes('apt-get update'), 'command must include apt-get update');
check(command.includes('apt-get install -y --no-install-recommends v4l-utils'), 'command must install normalized packages');
check(!command.includes('Mind@123'), 'command must never contain the password');

let captured = null;
const result = await installAptPackages({
	target: { transport: 'ssh', host: 'example.invalid', user: 'HwHiAiUser' },
	password: 'test-only',
	packages: ['v4l-utils'],
	dryRun: false,
	exec: async (options) => {
		captured = options;
		return { code: 0, stdout: 'installed', stderr: '', timedOut: false };
	}
});
check(result.ok, 'mock installation should succeed');
check(captured !== null, 'installer must invoke the provided executor');
equal(captured.input, 'test-only\n', 'password must be sent through stdin');
check(!JSON.stringify(result).includes('test-only'), 'password must not be returned in the result');
equal(result.command, command, 'returned command must be reproducible');

const dry = await installAptPackages({
	target: { transport: 'ssh', host: 'example.invalid', user: 'HwHiAiUser' },
	password: 'test-only',
	packages: ['v4l-utils'],
	dryRun: true,
	exec: async () => {
		throw new Error('dry-run executed');
	}
});
check(dry.ok && dry.executed === false, 'dry-run must not execute');

const dryWithoutPassword = await installAptPackages({
	target: { transport: 'ssh', host: 'example.invalid', user: 'HwHiAiUser' },
	packages: ['v4l-utils'],
	dryRun: true
});
check(dryWithoutPassword.ok, 'dry-run preview must not require a configured password');

const wrongUser = await installAptPackages({
	target: { transport: 'ssh', host: 'example.invalid', user: 'root' },
	password: 'test-only',
	packages: ['v4l-utils'],
	exec: async () => ({ code: 0, stdout: '', stderr: '', timedOut: false })
});
check(!wrongUser.ok, 'apt execution must require the HwHiAiUser SSH account');

if (failures.length > 0) {
	console.error(`install test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('install test passed');
console.log('  package validation, sudo -S stdin and dry-run are covered');
