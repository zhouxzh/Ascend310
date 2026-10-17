/**
 * Windows environment adapter.
 *
 * Windows has no native POSIX shell, so running the repository's
 * `convert-vuepress.sh` means handing it to Git Bash or to WSL. Which one is
 * present decides two things: how to spell an argument, and how to spell a
 * Windows path so the shell can `cd` into it. Both are answered here, so no
 * other module has to know.
 *
 * @module dsh-plugin-ascend310b-writing/drivers/env-windows
 */

const DRIVE_RE = /^([A-Za-z]):[\\/](.*)$/u;

/**
 * `D:\a\b` → `/d/a/b`, the spelling MSYS/Git Bash understands.
 *
 * @param {string} path - a native Windows path.
 * @returns {string} The MSYS-style path.
 */
export function toMsysPath(path) {
	const match = DRIVE_RE.exec(path);
	if (match === null) return path.replace(/\\/gu, '/');
	return `/${match[1].toLowerCase()}/${match[2].replace(/\\/gu, '/')}`;
}

/**
 * `D:\a\b` → `/mnt/d/a/b`, the spelling WSL understands.
 *
 * @param {string} path - a native Windows path.
 * @returns {string} The WSL-style path.
 */
export function toWslPath(path) {
	const match = DRIVE_RE.exec(path);
	if (match === null) return path.replace(/\\/gu, '/');
	return `/mnt/${match[1].toLowerCase()}/${match[2].replace(/\\/gu, '/')}`;
}

/**
 * Build the adapter for this host.
 *
 * @param {object} options - detection results.
 * @param {object | undefined} options.bash - the detected bash record.
 * @param {object | undefined} options.python3 - the detected `python3`.
 * @param {object | undefined} options.pythonAlias - a detected `python`/`py`.
 * @returns {object} The Windows adapter.
 */
export function describe({ bash, python3, pythonAlias } = {}) {
	const wsl = bash?.flavor === 'wsl';
	const toShellPath = wsl ? toWslPath : toMsysPath;
	const notes = [];

	if (bash === undefined || !bash.ok) {
		notes.push('未找到 Bash：Windows 上没有 POSIX shell，convert-vuepress.sh 无法运行。装 Git for Windows 或启用 WSL。');
	} else if (wsl) {
		notes.push('通过 WSL 运行构建：路径会翻译成 /mnt/<盘符>/…，构建在 WSL 的文件系统视图里执行。');
		notes.push(
			'警告：WSL 看不到装在 Windows 侧的 pandoc/TeXLive。若工具是装在 Windows 上，请装 Git for Windows ' +
				'并把它放在 PATH 前面，或在本机 profile 里把 config.toolchain.bash 指向 Git 的 bash.exe。'
		);
	} else {
		notes.push(`通过 MSYS/Git Bash 运行构建：${bash.name ?? bash}。路径会翻译成 /<盘符>/…。`);
	}
	notes.push('注意：Windows 磁盘上的文件换行与权限位与 Ubuntu 不同；两个系统各自构建的产物不要混用。');

	return {
		platform: 'win32',
		label: wsl ? 'Windows / WSL' : 'Windows / Git Bash',
		syntax: shellSyntax,
		toShellPath,
		notes,
		/**
		 * How to run a shell command string.
		 *
		 * @param {string} script - the command to run.
		 * @returns {{ command: string, args: string[] } | null} argv, or null without bash.
		 */
		shellCommand(script) {
			if (bash === undefined || !bash.ok) return null;
			if (wsl) return { command: bash.name ?? 'wsl.exe', args: ['bash', '-lc', script] };
			return { command: bash.name ?? 'bash', args: ['-lc', script] };
		},
		/**
		 * How this host will satisfy the converter's `python3` call.
		 *
		 * @returns {{ kind: 'native' | 'shim' | 'missing', interpreter?: string, note: string }} The plan.
		 */
		pythonPlan() {
			return buildPythonPlan({ python3, pythonAlias });
		}
	};
}

/**
 * Quote a path for the detected shell.
 *
 * @param {string} value - the raw value.
 * @returns {string} A single-quoted shell word.
 */
export function shellSyntax(value) {
	return `'${String(value).replace(/'/gu, `'\\''`)}'`;
}

/**
 * Decide how the build gets a `python3`.
 *
 * The repository's converter calls `python3` by name. Windows often has
 * `python` or the `py` launcher instead, so the driver can point a temporary
 * `python3` at whichever interpreter exists — derived from the probe, not from
 * a path baked into this plugin.
 *
 * @param {object} options - detection results.
 * @param {object | undefined} options.python3 - the detected `python3`.
 * @param {object | undefined} options.pythonAlias - a detected `python`/`py`.
 * @returns {{ kind: 'native' | 'shim' | 'missing', interpreter?: string, note: string }} The plan.
 */
export function buildPythonPlan({ python3, pythonAlias } = {}) {
	if (python3 !== undefined && python3.ok) {
		return { kind: 'native', interpreter: python3.name, note: `python3 可用：${python3.version ?? python3.name}` };
	}
	if (pythonAlias !== undefined && pythonAlias.ok) {
		return {
			kind: 'shim',
			interpreter: pythonAlias.name,
			note: `没有 python3，但找到 ${pythonAlias.name}；构建时会临时提供一个 python3 指向它。`
		};
	}
	return { kind: 'missing', note: '没有可用的 Python 3；replace_block.py 会失败，请先安装。' };
}
