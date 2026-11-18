/**
 * One-time SSH key provisioning for a configured 310B board.
 *
 * The function uses password authentication only to append one validated
 * public-key line to the fixed HwHiAiUser account. The password is supplied to
 * OpenSSH through a temporary askpass program and is never placed in argv.
 * Every elevation/account change remains outside this module.
 *
 * @module dsh-plugin-ascend310b-debug/ssh-key
 */

import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';

import { execFileCapture, shellQuote } from './shell.js';

const ASKPASS_ENV = 'DSH_ASCEND310B_SSH_PASSWORD';
const KEY_COMMENT = 'dsh-ascend310b-debug';

/** Resolve the local directory that owns generated SSH material. */
export function sshKeyDirectory(config = {}, env = process.env) {
	if (typeof config?.sshKeyDir === 'string' && config.sshKeyDir.trim() !== '') return resolve(config.sshKeyDir.trim());
	const home =
		(typeof env?.DSH_HOME === 'string' && env.DSH_HOME.trim() !== '' && env.DSH_HOME) ||
		(typeof env?.USERPROFILE === 'string' && env.USERPROFILE.trim() !== '' && env.USERPROFILE) ||
		(typeof env?.HOME === 'string' && env.HOME.trim() !== '' && env.HOME) ||
		homedir();
	return join(home, 'ascend310b-debug', 'ssh');
}

/** Default private-key path used when the target does not override it. */
export function defaultIdentityFile(config = {}, env = process.env) {
	return join(sshKeyDirectory(config, env), 'ascend310b_ed25519');
}

/** Normalize a configured private-key path and keep it local and absolute. */
export function normalizeIdentityFile(value, config = {}, env = process.env) {
	if (value === undefined || value === null || String(value).trim() === '') return defaultIdentityFile(config, env);
	const raw = String(value).trim();
	if (!isAbsolute(raw)) throw new Error('identityFile 必须是绝对路径。');
	return resolve(raw);
}

/** Resolve the one-time password without exposing it in tool arguments. */
export function resolveSshPassword(config = {}, target = {}) {
	for (const candidate of [config?.sshPassword, target?.sshPassword, config?.discovery?.password, config?.serial?.password, config?.sudoPassword]) {
		if (typeof candidate === 'string' && candidate !== '') return candidate;
	}
	return null;
}

/** Build the remote command that appends exactly one authorized key. */
export function buildAuthorizeCommand(publicKey) {
	const key = String(publicKey ?? '').trim();
	if (!/^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+)\s+[A-Za-z0-9+/=]+(?:\s+[^\r\n]*)?$/u.test(key)) {
		throw new Error('公钥格式无效。');
	}
	const quoted = shellQuote(key);
	return [
		'umask 077',
		'[ "$(id -un)" = "HwHiAiUser" ] || { echo "SSH_KEY_ACCOUNT_MISMATCH" >&2; exit 42; }',
		'test ! -L "$HOME/.ssh" || { echo "SSH_KEY_SSH_DIR_SYMLINK" >&2; exit 43; }',
		'test ! -L "$HOME/.ssh/authorized_keys" || { echo "SSH_KEY_AUTHORIZED_KEYS_SYMLINK" >&2; exit 44; }',
		'mkdir -p "$HOME/.ssh"',
		'touch "$HOME/.ssh/authorized_keys"',
		'chmod 700 "$HOME/.ssh"',
		'chmod 600 "$HOME/.ssh/authorized_keys"',
		`grep -qxF ${quoted} "$HOME/.ssh/authorized_keys" || printf '%s\\n' ${quoted} >> "$HOME/.ssh/authorized_keys"`,
		'printf "SSH_KEY_INSTALLED\\n"'
	].join('; ');
}

/** Args for the password-authenticated bootstrap session. */
export function buildPasswordSshArgs(target, command) {
	const args = [
		'-o', 'BatchMode=no',
		'-o', 'PreferredAuthentications=password',
		'-o', 'PubkeyAuthentication=no',
		'-o', 'KbdInteractiveAuthentication=no',
		'-o', 'NumberOfPasswordPrompts=1',
		'-o', 'ConnectTimeout=8',
		'-o', 'StrictHostKeyChecking=accept-new'
	];
	if (Number.isInteger(target.port) && target.port > 0) args.push('-p', String(target.port));
	args.push(target.user ? `${target.user}@${target.host}` : target.host, command);
	return args;
}

/** Args for the post-install BatchMode verification. */
export function buildKeySshArgs(target, identityFile, command) {
	const args = [
		'-o', 'BatchMode=yes',
		'-o', 'IdentitiesOnly=yes',
		'-o', 'ConnectTimeout=8',
		'-o', 'StrictHostKeyChecking=accept-new',
		'-i', identityFile
	];
	if (Number.isInteger(target.port) && target.port > 0) args.push('-p', String(target.port));
	args.push(target.user ? `${target.user}@${target.host}` : target.host, command);
	return args;
}

/** Create a temporary askpass helper that reads the password from the child env. */
export function createAskpassFile(directory, platform = process.platform) {
	mkdirSync(directory, { recursive: true, mode: 0o700 });
	const file = join(directory, platform === 'win32' ? `askpass-${randomUUID()}.cmd` : `askpass-${randomUUID()}.sh`);
	const body = platform === 'win32'
		? `@echo off\r\npowershell.exe -NoProfile -NonInteractive -Command "[Console]::Out.WriteLine($env:${ASKPASS_ENV})"\r\n`
		: `#!/bin/sh\nprintf '%s\\n' "$${ASKPASS_ENV}"\n`;
	writeFileSync(file, body, { encoding: 'utf8', mode: 0o700 });
	chmodSync(file, 0o700);
	return file;
}

/** Derive the public key if only the private half exists. */
async function readOrDerivePublicKey(identityFile, exec) {
	const publicPath = `${identityFile}.pub`;
	if (existsSync(publicPath)) return publicPath;
	const derived = await exec({
		file: 'ssh-keygen',
		args: ['-y', '-f', identityFile],
		timeoutMs: 15000
	});
	if (derived.code !== 0) throw new Error(`无法读取SSH公钥：${derived.stderr || derived.error || 'ssh-keygen失败'}`);
	writeFileSync(publicPath, `${String(derived.stdout).trim()}\n`, 'utf8');
	chmodSync(publicPath, 0o644);
	return publicPath;
}

/** Generate an Ed25519 key if the configured private key does not exist. */
export async function ensureIdentityFile(identityFile, { exec = execFileCapture, dryRun = false } = {}) {
	const privatePath = resolve(identityFile);
	const publicPath = `${privatePath}.pub`;
	if (existsSync(privatePath)) {
		return { identityFile: privatePath, publicKeyPath: dryRun ? publicPath : await readOrDerivePublicKey(privatePath, exec), generated: false };
	}
	if (dryRun) return { identityFile: privatePath, publicKeyPath: publicPath, generated: true };
	mkdirSync(dirname(privatePath), { recursive: true, mode: 0o700 });
	const result = await exec({
		file: 'ssh-keygen',
		args: ['-q', '-t', 'ed25519', '-N', '', '-C', KEY_COMMENT, '-f', privatePath],
		timeoutMs: 30000
	});
	if (result.code !== 0 || !existsSync(privatePath)) {
		throw new Error(`SSH密钥生成失败：${result.stderr || result.error || 'ssh-keygen失败'}`);
	}
	chmodSync(privatePath, 0o600);
	return { identityFile: privatePath, publicKeyPath: await readOrDerivePublicKey(privatePath, exec), generated: true };
}

/** Verify that the installed key works without password or agent fallback. */
export async function verifyIdentity(target, identityFile, { exec = execFileCapture, signal } = {}) {
	return exec({
		file: 'ssh',
		args: buildKeySshArgs(
			target,
			identityFile,
			'[ "$(id -un)" = "HwHiAiUser" ] || { echo "SSH_KEY_ACCOUNT_MISMATCH" >&2; exit 42; }; printf "SSH_KEY_VERIFIED\\n"'
		),
		timeoutMs: 30000,
		signal
	});
}

/** Run one command with one password attempt through a temporary ASKPASS file. */
export async function runPasswordSshCommand(target, command, password, { exec = execFileCapture, config = {}, timeoutMs = 45000, signal } = {}) {
	if (typeof password !== 'string' || password === '') throw new Error('profile未配置密码。');
	const askpass = createAskpassFile(sshKeyDirectory(config));
	try {
		const result = await exec({
			file: 'ssh',
			args: buildPasswordSshArgs(target, command),
			env: {
				SSH_ASKPASS: askpass,
				SSH_ASKPASS_REQUIRE: 'force',
				DISPLAY: ':0',
				[ASKPASS_ENV]: password
			},
			timeoutMs,
			signal
		});
		signal?.throwIfAborted?.();
		return { ...result, ok: result.code === 0 && result.timedOut !== true };
	} finally {
		rmSync(askpass, { force: true });
	}
}

/** Install one public key through a one-time password-authenticated session. */
export async function installPublicKey(target, identityFile, password, { exec = execFileCapture, config = {}, signal } = {}) {
	if (typeof password !== 'string' || password === '') throw new Error('profile未配置可用于首次密钥登记的公钥登录密码。');
	const publicKey = readFileSync(`${identityFile}.pub`, 'utf8').trim();
	const command = buildAuthorizeCommand(publicKey);
	return runPasswordSshCommand(target, command, password, { exec, config, timeoutMs: 45000, signal });
}

/** Redact a one-time password from process output. */
export function redactSshPassword(value, password) {
	if (typeof password !== 'string' || password === '') return String(value ?? '');
	return String(value ?? '').split(password).join('<redacted>');
}
