/**
 * Process and file helpers for the Ascend 310B debug plugin.
 *
 * Kept in its own module so the runnable parts (SSH command construction, the
 * read-only allowlist) can be unit-tested with a stub spawner instead of a
 * board.
 *
 * @module dsh-plugin-ascend310b-debug/shell
 */

import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

/** Host-name characters that may appear in an SSH target. */
const SAFE_HOST = /^[A-Za-z0-9._-]+$/;

/** User-name characters that may appear in an SSH destination. */
const SAFE_USER = /^[A-Za-z0-9._-]+$/;

/**
 * Whether the given text is a valid host, IP, or SSH alias.
 *
 * The check is deliberately narrow: the host is interpolated into an SSH argv
 * (never a shell string), but a value carrying whitespace or shell punctuation
 * is a configuration mistake worth rejecting rather than escaping.
 *
 * @param {string} host - candidate host.
 * @returns {boolean} true when safe to use as an SSH destination.
 */
export function isSafeHost(host) {
	return typeof host === 'string' && host.length > 0 && host.length <= 253 && SAFE_HOST.test(host);
}

/**
 * Check an optional SSH user name.
 *
 * @param {string|null|undefined} user - candidate user.
 * @returns {boolean} true when the user is absent or safe.
 */
export function isSafeUser(user) {
	return user === null || user === undefined || (typeof user === 'string' && user.length > 0 && user.length <= 64 && SAFE_USER.test(user));
}

/**
 * The read-only command allowlist used by `ascend_board_exec`.
 *
 * A leading token alone is not enough: several diagnostic programs have
 * subcommands or flags that write (`npu-smi set`, `sed -i`, `dmesg -C`,
 * `v4l2-ctl --set-*`). The list below contains only commands whose safe
 * invocation can be checked per argument, and each exception is validated by
 * {@link validateArguments}. Programs that can execute arbitrary code or write
 * artifacts (`bash`, `python -c`, `atc`, `msame`, `curl`, `pip`, `conda`) are
 * deliberately absent.
 *
 * Reading version files and running diagnostics is what the repository already
 * does in `collect_system_status.sh` and appendix 7; allowing an interactive
 * version of the same queries is a convenience, not a new capability.
 */
export const READ_ONLY_PREFIXES = Object.freeze([
	'npu-smi',
	'uname',
	'hostname',
	'whoami',
	'id',
	'date',
	'uptime',
	'free',
	'df',
	'ls',
	'lsblk',
	'readlink',
	'stat',
	'file',
	'sha256sum',
	'md5sum',
	'cat',
	'head',
	'tail',
	'grep',
	'wc',
	'printenv',
	'lsmod',
	'dmesg',
	'v4l2-ctl',
	'which',
	'type',
	'ps',
	'pgrep',
	'ss',
	'netstat',
]);

/** Substrings that indicate a command intends to change state. */
const MUTATING_TOKENS = Object.freeze([
	'rm ',
	'rm -',
	'mv ',
	'cp ',
	'mkdir ',
	'rmdir ',
	'touch ',
	'truncate ',
	'dd ',
	'mkfs',
	'mount ',
	'umount',
	'shutdown',
	'reboot',
	'poweroff',
	'kill',
	'pkill',
	'killall',
	'chmod ',
	'chown ',
	'sudo ',
	'apt ',
	'apt-get',
	'dnf ',
	'yum ',
	'pip install',
	'pip3 install',
	'conda install',
	'conda create',
	'conda remove',
	'pip uninstall',
	'systemctl start',
	'systemctl stop',
	'systemctl restart',
	'systemctl enable',
	'systemctl disable',
	'apt-get install',
	'insmod',
	'rmmod',
	'modprobe',
	'echo >',
	'tee ',
	'wget ',
	'upgrade-tool',
	'git checkout',
	'git reset',
	'git clean',
	'git pull',
	'git push',
]);

/** `hostname` options that only print a name or address. */
const HOSTNAME_READ_ONLY = new Set([
	'-A',
	'--all-fqdns',
	'-d',
	'--domain',
	'-f',
	'--fqdn',
	'-i',
	'--ip-address',
	'-I',
	'--all-ip-addresses',
	'-s',
	'--short',
]);

/** `dmesg` flags that clear or reconfigure the kernel ring buffer. */
const DMESG_MUTATING = new Set([
	'-C',
	'--clear',
	'-c',
	'--read-clear',
	'-n',
	'--console-level',
	'-D',
	'--console-off',
	'-E',
	'--console-on',
]);

/** `v4l2-ctl` options that only enumerate or query device state. */
const V4L2_READ_ONLY = new Set([
	'-D',
	'--info',
	'--list-devices',
	'--all',
	'--list-formats',
	'--list-formats-ext',
	'--list-formats-out',
	'--list-formats-out-ext',
	'--list-formats-overlay',
	'--get-fmt-video',
	'--get-fmt-video-out',
	'--get-fmt-overlay',
	'--get-selection',
	'--get-ctrl',
	'--get-parm',
	'--get-dv-timings',
	'--get-edid',
	'--get-audio-input',
	'--get-audio-output',
	'--get-modulator',
	'--get-standard',
	'--get-tuner',
	'--get-frequency',
	'--get-input',
	'--get-output',
	'--help',
	'--version',
]);

/** `v4l2-ctl` options that take one device path. */
const V4L2_DEVICE_OPTIONS = new Set(['-d', '--device']);

/** `v4l2-ctl` query options that take one non-path value. */
const V4L2_VALUE_OPTIONS = new Set(['--get-ctrl', '--get-selection']);

/**
 * Check a command whose program is on the allowlist.
 *
 * @param {string} base - program name.
 * @param {string[]} tokens - whitespace-split command.
 * @returns {{ok: boolean, reason: string}} verdict.
 */
function validateArguments(base, tokens) {
	const args = tokens.slice(1);
	if (base === 'npu-smi') {
		if (args.length > 0 && args[0] !== 'info') {
			return { ok: false, reason: `npu-smi 只允许 info 查询，收到子命令 ${args[0]}。` };
		}
		return { ok: true, reason: '允许：npu-smi info 查询。' };
	}
	if (base === 'hostname') {
		const unsafe = args.find((arg) => !HOSTNAME_READ_ONLY.has(arg));
		if (unsafe !== undefined) {
			return { ok: false, reason: `hostname 只允许查询选项，收到 ${unsafe}。` };
		}
		return { ok: true, reason: '允许：hostname 查询。' };
	}
	if (base === 'date') {
		for (const arg of args) {
			if (arg === '-s' || arg.startsWith('-s') || arg === '--set' || /^--set=/.test(arg)) {
				return { ok: false, reason: `date 不允许设置时间：${arg}。` };
			}
			// A bare operand is how GNU date sets the clock without -s.
			if (!arg.startsWith('-') && !arg.startsWith('+')) {
				return { ok: false, reason: `date 只允许查询选项或 +format，收到 ${arg}。` };
			}
		}
		return { ok: true, reason: '允许：date 查询。' };
	}
	if (base === 'dmesg') {
		const unsafe = args.find((arg) => {
			if (DMESG_MUTATING.has(arg)) return true;
			if (/^--(?:console-level|console-off|console-on|clear|read-clear)(?:=|$)/.test(arg)) return true;
			// Short options can be combined (`dmesg -cT`); any mutating letter
			// anywhere in the cluster is enough to refuse it.
			return /^-[^-]/.test(arg) && /[CcDEn]/.test(arg.slice(1));
		});
		if (unsafe !== undefined) {
			return { ok: false, reason: `dmesg 不允许清空或改写控制台级别：${unsafe}。` };
		}
		return { ok: true, reason: '允许：dmesg 读取。' };
	}
	if (base === 'ss') {
		const unsafe = args.find((arg) => arg.startsWith('-K') || arg === '--kill');
		if (unsafe !== undefined) {
			return { ok: false, reason: `ss 不允许强制关闭套接字：${unsafe}。` };
		}
		return { ok: true, reason: '允许：ss 套接字查询。' };
	}
	if (base === 'v4l2-ctl') {
		for (let index = 0; index < args.length; index += 1) {
			const arg = args[index];
			if (V4L2_VALUE_OPTIONS.has(arg)) {
				const value = args[index + 1];
				if (value === undefined || value.startsWith('-')) {
					return { ok: false, reason: `${arg} 需要一个查询参数。` };
				}
				index += 1;
				continue;
			}
			if (V4L2_READ_ONLY.has(arg)) continue;
			if (V4L2_DEVICE_OPTIONS.has(arg)) {
				const device = args[index + 1];
				if (device === undefined || !/^\/dev\/(?:video|media)\d+$/.test(device)) {
					return { ok: false, reason: `v4l2-ctl 的设备参数必须是 /dev/videoN 或 /dev/mediaN。` };
				}
				index += 1;
				continue;
			}
			return { ok: false, reason: `v4l2-ctl 只允许枚举与查询选项，收到 ${arg}。` };
		}
		return { ok: true, reason: '允许：v4l2-ctl 查询。' };
	}
	return { ok: true, reason: `允许：${base} 属于只读白名单。` };
}

/**
 * Validate a command against the allowlist.
 *
 * @param {string} command - the command text.
 * @returns {{ok: boolean, reason: string}} verdict with a reason.
 */
export function validateReadOnlyCommand(command) {
	if (typeof command !== 'string' || command.trim() === '') {
		return { ok: false, reason: '命令为空。' };
	}
	const text = command.trim();
	if (text.length > 4000) return { ok: false, reason: '命令过长（>4000 字符）。' };
	if (/[\n\r;|&<>`]|\$\(/.test(text)) {
		return {
			ok: false,
			reason: '命令包含链接、管道、重定向或命令替换；调试执行只接受一条只读命令。'
		};
	}
	const lower = ` ${text.toLowerCase()} `;
	for (const token of MUTATING_TOKENS) {
		if (lower.includes(token)) return { ok: false, reason: `命令包含可能改变系统状态的操作：${token.trim()}` };
	}
	const tokens = text.split(/\s+/);
	const base = tokens[0];
	// Only a bare program name from PATH is accepted. Allowing ./npu-smi would
	// let a repository-local executable borrow an allowlisted name.
	if (base.includes('/') || base.includes('\\')) {
		return { ok: false, reason: `命令必须使用 PATH 中的程序名，不能指定路径：${base}。` };
	}
	if (!READ_ONLY_PREFIXES.includes(base)) {
		return {
			ok: false,
			reason: `命令 ${base} 不在只读白名单内。允许的前缀：${READ_ONLY_PREFIXES.join('、')}。`
		};
	}
	return validateArguments(base, tokens);
}

/** Escape one argv element for POSIX `sh`. */
function quote(value) {
	return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

/**
 * Build the SSH argv for one remote command.
 *
 * @param {object} target - resolved target with host/user/port/identity fields.
 * @param {string} command - remote command text.
 * @returns {string[]} argv for the `ssh` executable.
 */
export function buildSshArgs(target, command) {
	const args = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new'];
	if (Number.isInteger(target.port) && target.port > 0) args.push('-p', String(target.port));
	if (typeof target.identityFile === 'string' && target.identityFile.trim() !== '') {
		args.push('-i', target.identityFile.trim());
		args.push('-o', 'IdentitiesOnly=yes');
	}
	const destination = target.user ? `${target.user}@${target.host}` : target.host;
	args.push(destination, command);
	return args;
}

/**
 * Run one command and capture its output.
 *
 * `execFile` is used instead of a shell so the host string and command text can
 * never be re-interpreted by the local shell.
 *
 * @param {object} options - execution options.
 * @param {string} options.file - executable.
 * @param {string[]} options.args - argv.
 * @param {string} [options.cwd] - working directory.
 * @param {number} [options.timeoutMs] - timeout.
 * @returns {Promise<object>} `{stdout, stderr, code, timedOut}`.
 */
export function execFileCapture({ file, args, cwd, timeoutMs = 30000 }) {
	const { execFile } = require('node:child_process');
	return new Promise((resolve) => {
		execFile(
			file,
			args,
			{ cwd, timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024, windowsHide: true },
			(error, stdout, stderr) => {
				resolve({
					stdout: String(stdout ?? ''),
					stderr: String(stderr ?? ''),
					code: error?.code === undefined ? 0 : typeof error.code === 'number' ? error.code : 1,
					timedOut: error?.killed === true || error?.signal === 'SIGTERM',
					error: error === null || error === undefined ? null : String(error.message ?? error)
				});
			}
		);
	});
}

/**
 * A runner that talks to a board over SSH.
 *
 * @param {object} options - runner options.
 * @param {string} options.script - remote command text.
 * @param {object} options.target - resolved target.
 * @param {number} [options.timeoutMs] - timeout in ms.
 * @param {function} [options.exec] - injectable executor for tests.
 * @returns {Promise<object>} execution result plus the exact command shown.
 */
export async function runRemoteScript({ script, target, timeoutMs = 60000, exec = execFileCapture }) {
	if (!isSafeHost(target.host)) {
		return { ok: false, stdout: '', stderr: `非法主机名：${target.host}`, display: '', reason: '主机名不安全。' };
	}
	if (!isSafeUser(target.user)) {
		return { ok: false, stdout: '', stderr: `非法 SSH 用户：${target.user}`, display: '', reason: 'SSH 用户不安全。' };
	}
	const args = buildSshArgs(target, script);
	const result = await exec({ file: 'ssh', args, timeoutMs });
	const display = target.user ? `ssh ${target.user}@${target.host}` : `ssh ${target.host}`;
	return {
		...result,
		ok: result.code === 0 && result.timedOut !== true,
		display,
		reason:
			result.code === 0
				? 'SSH 探测完成。'
				: `SSH 退出码 ${result.code}${result.timedOut ? '（超时）' : ''}：${result.stderr.trim().split('\n')[0] ?? ''}`
	};
}

/**
 * A runner that executes on the machine hosting DSH.
 *
 * @param {object} options - runner options.
 * @param {string} options.script - shell script text.
 * @param {string} [options.cwd] - working directory.
 * @param {number} [options.timeoutMs] - timeout in ms.
 * @param {function} [options.exec] - injectable executor for tests.
 * @returns {Promise<object>} execution result.
 */
export async function runLocalScript({ script, cwd, timeoutMs = 60000, exec = execFileCapture }) {
	const isWindows = process.platform === 'win32';
	const file = isWindows ? 'bash' : '/bin/sh';
	const args = ['-lc', script];
	const result = await exec({ file, args, cwd, timeoutMs });
	return {
		...result,
		ok: result.code === 0 && result.timedOut !== true,
		display: `${file} -lc <probe script>`,
		reason:
			result.code === 0
				? '本机探测完成。注意：开发机没有 NPU 运行时，这只能证明本机环境，不能证明板端。'
				: `本机探测退出码 ${result.code}：${result.stderr.trim().split('\n')[0] ?? ''}`
	};
}
