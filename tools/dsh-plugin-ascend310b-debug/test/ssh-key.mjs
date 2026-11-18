import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
	buildAuthorizeCommand,
	buildKeySshArgs,
	buildPasswordSshArgs,
	createAskpassFile,
	normalizeIdentityFile,
	redactSshPassword,
	resolveSshPassword
} from '../lib/ssh-key.js';

const directory = mkdtempSync(join(tmpdir(), 'dsh-ascend-ssh-key-test-'));
const failures = [];
const check = (fn, message) => {
	try {
		fn();
	} catch (error) {
		failures.push(`${message}: ${error?.message ?? error}`);
	}
};

try {
	check(
		() => assert.throws(() => normalizeIdentityFile('relative-key', {}, { DSH_HOME: directory })),
		'relative private-key paths must be rejected'
	);
	const identity = normalizeIdentityFile(join(directory, 'id_ed25519'), {}, { DSH_HOME: directory });
	assert.equal(identity, join(directory, 'id_ed25519'));

	const publicKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyKeyMaterial dsh-test';
	const command = buildAuthorizeCommand(publicKey);
	assert.match(command, /id -un/u);
	assert.match(command, /HwHiAiUser/u);
	assert.match(command, /authorized_keys/u);
	assert.match(command, /grep -qxF/u);
	assert.match(command, /chmod 700/u);
	assert.match(command, /chmod 600/u);
	assert.match(command, /SSH_KEY_INSTALLED/u);
	assert.ok(!command.includes('sudo'));
	check(() => assert.throws(() => buildAuthorizeCommand('not-a-key')), 'invalid public keys must be refused');
	check(
		() => assert.throws(() => buildAuthorizeCommand(`${publicKey}\nmalicious`)),
		'multi-line public keys must be refused'
	);

	const target = { host: '192.0.2.10', user: 'HwHiAiUser', port: 2222 };
	const passwordArgs = buildPasswordSshArgs(target, 'printf ok');
	assert.ok(passwordArgs.includes('PreferredAuthentications=password'));
	assert.ok(!passwordArgs.some((value) => value.includes('secret-password')), 'password must never enter SSH argv');
	const keyArgs = buildKeySshArgs(target, identity, 'printf ok');
	assert.ok(keyArgs.includes('BatchMode=yes'));
	assert.ok(keyArgs.includes('IdentitiesOnly=yes'));
	assert.ok(keyArgs.includes(identity));
	assert.ok(keyArgs.includes('2222'));

	const windowsAskpass = createAskpassFile(directory, 'win32');
	assert.match(readFileSync(windowsAskpass, 'utf8'), /DSH_ASCEND310B_SSH_PASSWORD/u);
	assert.ok(!readFileSync(windowsAskpass, 'utf8').includes('secret-password'));
	const posixAskpass = createAskpassFile(directory, 'linux');
	assert.match(readFileSync(posixAskpass, 'utf8'), /printf '%s\\n'/u);
	assert.ok(!readFileSync(posixAskpass, 'utf8').includes('secret-password'));

	assert.equal(resolveSshPassword({ discovery: { password: 'discovery-only' } }, {}), 'discovery-only');
	assert.equal(resolveSshPassword({ serial: { password: 'serial-only' } }, {}), 'serial-only');
	assert.equal(resolveSshPassword({ sudoPassword: 'sudo-only' }, {}), 'sudo-only');
	assert.equal(resolveSshPassword({ sshPassword: 'ssh-only' }, { sshPassword: 'target-only' }), 'ssh-only');
	assert.equal(redactSshPassword('failed secret-password here', 'secret-password'), 'failed <redacted> here');
} finally {
	rmSync(directory, { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error('ssh-key tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('ssh-key tests passed');
