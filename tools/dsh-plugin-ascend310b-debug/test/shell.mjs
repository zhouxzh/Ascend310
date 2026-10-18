/**
 * Read-only command gate tests.
 *
 * `ascend_board_exec` is model-callable, so its allowlist is a security
 * boundary, not a documentation promise. These cases pin the commands that must
 * stay available for diagnosis and the writes, pipelines and program loaders
 * that must stay rejected.
 *
 * Usage: node test/shell.mjs
 */

import assert from 'node:assert/strict';

import { buildSshArgs, isSafeHost, isSafeUser, runRemoteShell, validateReadOnlyCommand, validateTaskCommand } from '../lib/shell.js';

const failures = [];
const check = (name, run) => {
	try {
		run();
	} catch (error) {
		failures.push(`${name}: ${error?.message ?? error}`);
	}
};

/** Commands the debug workflow genuinely needs. */
const ALLOWED = [
	'npu-smi info',
	'npu-smi info -t proc-mem -i 0 -c 0',
	'uname -a',
	'hostname -f',
	'date -u +%s',
	'free -m',
	'df -h',
	'ls -l /dev/davinci0',
	'readlink -f /usr/local/Ascend/ascend-toolkit/latest',
	'cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg',
	'grep -n runtime_running_version /usr/local/Ascend/ascend-toolkit/latest/version.cfg',
	'head -n 40 /var/log/syslog',
	'tail -n 40 /var/log/syslog',
	'lsmod',
	'dmesg -T',
	'dmesg -T -H',
	'v4l2-ctl --list-devices',
	'v4l2-ctl -d /dev/video0 --list-formats-ext',
	'v4l2-ctl --device /dev/video0 --get-ctrl brightness',
	'ps -ef',
	'ss -lntp'
];

/** Commands that would change board state or execute arbitrary code. */
const BLOCKED = [
	'npu-smi set -t health -i 0 -c 0',
	'npu-smi reset -i 0',
	'npu-smi upgrade -t mcu -f file.bin',
	'hostname evil-board',
	'date 102710002026',
	'date -s 2026-10-17',
	'date -s20261017',
	'sed -i s/1/2/ /etc/hosts',
	`python3 -c "open('/tmp/x','w').write('x')"`,
	'python -c "import acl; print(acl.__file__)"',
	'bash -lc "reboot"',
	'source /tmp/setup.sh',
	'env python3 -c "print(1)"',
	'command touch /tmp/x',
	'pip install requests',
	'conda create -n x python=3.11',
	'atc --model=a.onnx --output=b',
	'msame --model a.om --output out',
	'curl -X POST http://127.0.0.1:7860/api/restart',
	'dmesg -C',
	'dmesg --clear',
	'dmesg -cT',
	'dmesg -n 1',
	'dmesg --console-level=1',
	'ss -K',
	'ss -Ktn',
	'ss --kill',
	'v4l2-ctl --set-ctrl brightness=1',
	'v4l2-ctl --stream-to /tmp/frame.raw',
	'cat /etc/hostname | bash',
	'cat /etc/hostname && reboot',
	'cat /etc/hostname > /tmp/out',
	'cat /tmp/$(whoami)',
	'cat /tmp/`whoami`',
	'./npu-smi info',
	'/usr/local/bin/npu-smi info',
	'rm -rf /tmp/x',
	'echo x > /tmp/y',
	''
];

for (const command of ALLOWED) {
	check(`must allow: ${command}`, () => {
		const verdict = validateReadOnlyCommand(command);
		assert.equal(verdict.ok, true, verdict.reason);
	});
}

for (const command of BLOCKED) {
	check(`must block: ${command || '(empty)'}`, () => {
		const verdict = validateReadOnlyCommand(command);
		assert.equal(verdict.ok, false, `unexpectedly allowed: ${command}`);
		assert.ok(verdict.reason.length > 0, 'a refusal must explain itself');
	});
}

check('non-string command is refused', () => {
	assert.equal(validateReadOnlyCommand(null).ok, false);
});

check('overlong command is refused', () => {
	assert.equal(validateReadOnlyCommand(`cat ${'x'.repeat(4000)}`).ok, false);
});

check('safe SSH host accepts host, IP and alias forms', () => {
	for (const host of ['192.0.2.10', 'board-8t.local', 'ascend_alias']) {
		assert.equal(isSafeHost(host), true, host);
	}
});

check('unsafe SSH host is refused', () => {
	for (const host of ['', 'host name', 'host;reboot', '-oProxyCommand=x', 'a'.repeat(254)]) {
		assert.equal(isSafeHost(host), false, host);
	}
});

check('safe SSH user accepts account names', () => {
	for (const user of [null, 'HwHiAiUser', 'root', 'user.name', 'user-name']) {
		assert.equal(isSafeUser(user), true, String(user));
	}
});

check('unsafe SSH user is refused', () => {
	for (const user of ['', 'user name', '-oProxyCommand=x', 'user@host', 'a'.repeat(65)]) {
		assert.equal(isSafeUser(user), false, user);
	}
});

check('SSH argv never goes through a local shell', () => {
	const args = buildSshArgs(
		{ host: '192.0.2.10', user: 'HwHiAiUser', port: 2222, identityFile: 'keys/board' },
		'npu-smi info'
	);
	assert.deepEqual(args.slice(-2), ['HwHiAiUser@192.0.2.10', 'npu-smi info']);
	assert.ok(args.includes('-p'));
	assert.ok(args.includes('2222'));
	assert.ok(args.includes('BatchMode=yes'));
});

// Explicit task commands may download/build/run, but hardware and destructive
// operations remain blocked unless the caller opts in separately.
for (const command of [
	'wget -O model.onnx https://example.com/model.onnx',
	'atc --model=model.onnx --output=model --soc_version=Ascend310B4',
	'./msame --model model.om --input input.bin --output out',
	'python sample.py',
	'bash run.sh',
	'source /usr/local/Ascend/ascend-toolkit/set_env.sh && python sample.py'
]) {
	check(`task allows: ${command}`, () => assert.equal(validateTaskCommand(command).ok, true));
}

for (const command of [
	'npu-smi set -t health -i 0 -c 0',
	'reboot',
	'mkfs.ext4 /dev/mmcblk0',
	'sudo apt-get install opencv'
]) {
	check(`task blocks: ${command}`, () => assert.equal(validateTaskCommand(command).ok, false));
}
check('task blocks destructive rm by default', () => assert.equal(validateTaskCommand('rm -rf /tmp/x').ok, false));
check('task permits rm only with explicit opt-in', () => assert.equal(validateTaskCommand('rm -rf /tmp/x', { allowDestructive: true }).ok, true));
check('task permits sudo only with explicit opt-in', () => assert.equal(validateTaskCommand('sudo true', { allowPrivileged: true }).ok, true));

try {
	let captured = null;
	await runRemoteShell({
		script: 'python sample.py',
		cwd: '/home/HwHiAiUser/Ascend310/samples',
		target: { host: '192.0.2.10', user: 'HwHiAiUser' },
		exec: async ({ args }) => {
			captured = args;
			return { stdout: 'ok', stderr: '', code: 0, timedOut: false };
		}
	});
	assert.ok(captured?.at(-1).includes("cd '/home/HwHiAiUser/Ascend310/samples'"));
	assert.ok(captured?.at(-1).includes('python sample.py'));
} catch (error) {
	failures.push(`task cwd construction: ${error?.message ?? error}`);
}

if (failures.length > 0) {
	console.error(`shell tests FAILED (${failures.length}):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('shell tests passed');
console.log(`  allowed commands : ${ALLOWED.length}`);
console.log(`  blocked commands : ${BLOCKED.length}`);
