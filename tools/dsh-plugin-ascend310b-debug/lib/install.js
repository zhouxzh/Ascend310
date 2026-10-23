/**
 * Explicit, narrowly scoped apt installation for Ascend 310B boards.
 *
 * The board login remains HwHiAiUser. A configured default sudo password is
 * written only to the SSH process stdin, never to argv, logs or tool output.
 */

import { runRemoteShell, shellQuote } from './shell.js';

const PACKAGE_NAME = /^[a-z0-9][a-z0-9+.-]{0,127}$/iu;
const MAX_PACKAGES = 24;

/** Validate and normalize package names before they become one apt argument. */
export function validateAptPackages(packages) {
	if (!Array.isArray(packages) || packages.length === 0) {
		return { ok: false, reason: 'packages 必须是非空数组。', packages: [] };
	}
	if (packages.length > MAX_PACKAGES) {
		return { ok: false, reason: `一次最多安装 ${MAX_PACKAGES} 个包。`, packages: [] };
	}
	const normalized = [];
	for (const value of packages) {
		const name = typeof value === 'string' ? value.trim().toLowerCase() : '';
		if (!PACKAGE_NAME.test(name)) {
			return { ok: false, reason: `非法 apt 包名：${String(value)}`, packages: [] };
		}
		if (!normalized.includes(name)) normalized.push(name);
	}
	return { ok: true, reason: 'apt 包名通过检查。', packages: normalized };
}

/** Build one shell-safe privileged command; package names are validated first. */
export function buildAptInstallCommand(packages, { update = true } = {}) {
	const validation = validateAptPackages(packages);
	if (!validation.ok) throw new Error(validation.reason);
	const joined = validation.packages.join(' ');
	const install = `DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ${joined}`;
	const inner = update ? `apt-get update && ${install}` : install;
	return `sudo -S env DEBIAN_FRONTEND=noninteractive sh -c ${shellQuote(inner)}`;
}

/**
 * Install packages over SSH. `password` must come from profile configuration.
 * It is passed as stdin and is intentionally absent from the returned object.
 */
export async function installAptPackages({
	target,
	password,
	packages,
	update = true,
	timeoutMs = 300000,
	dryRun = false,
	exec
} = {}) {
	const validation = validateAptPackages(packages);
	if (!validation.ok) return { ok: false, allowed: false, executed: false, reason: validation.reason, packages: [] };
	const command = buildAptInstallCommand(validation.packages, { update });
	if (dryRun) {
		return {
			ok: true,
			allowed: true,
			executed: false,
			command,
			packages: validation.packages,
			update,
			stdout: '',
			stderr: '',
			code: 0,
			reason: 'dry-run：未执行 apt。'
		};
	}
	if (typeof password !== 'string' || password === '') {
		return { ok: false, allowed: false, executed: false, reason: 'profile 未配置 sudoPassword。', packages: validation.packages };
	}
	if (target?.transport !== 'ssh' || typeof target?.host !== 'string') {
		return {
			ok: false,
			allowed: false,
			executed: false,
			command,
			packages: validation.packages,
			update,
			stdout: '',
			stderr: '',
			code: -1,
			reason: 'apt 安装只允许通过 SSH 目标执行。'
		};
	}
	if (target.user !== 'HwHiAiUser') {
		return {
			ok: false,
			allowed: false,
			executed: false,
			command,
			packages: validation.packages,
			update,
			stdout: '',
			stderr: '',
			code: -1,
			reason: 'apt 安装目标必须是 HwHiAiUser，root 只能通过 sudo 执行单条安装命令。'
		};
	}
	const result = await runRemoteShell({
		script: command,
		target,
		timeoutMs,
		exec,
		input: `${password}\n`
	});
	return {
		ok: result.ok === true,
		allowed: true,
		executed: true,
		command,
		packages: validation.packages,
		update,
		stdout: String(result.stdout ?? ''),
		stderr: String(result.stderr ?? ''),
		code: Number.isInteger(result.code) ? result.code : 1,
		reason: result.reason ?? 'apt 安装完成。'
	};
}
