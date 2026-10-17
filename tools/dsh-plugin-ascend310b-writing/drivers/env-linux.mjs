/**
 * POSIX environment adapter (Ubuntu, WSL distributions, macOS).
 *
 * This is the simple case: the shell is native, paths need no translation, and
 * `python3` is the conventional name. The adapter still exists so the build
 * driver never branches on the platform itself — it asks the adapter.
 *
 * @module dsh-plugin-ascend310b-writing/drivers/env-linux
 */

/**
 * Build the adapter for this host.
 *
 * @param {object} options - detection results.
 * @param {object | undefined} options.bash - the detected bash record.
 * @param {object | undefined} options.python3 - the detected `python3`.
 * @param {object | undefined} options.pythonAlias - a detected `python`.
 * @returns {object} The POSIX adapter.
 */
export function describe({ bash, python3, pythonAlias } = {}) {
	const command = bash?.ok ? bash.name ?? 'bash' : null;
	const notes = [];

	if (command === null) {
		notes.push('未找到 Bash：请安装（Debian/Ubuntu 上 sudo apt install -y bash）。');
	} else {
		notes.push(`原生 POSIX shell：${command}。路径无需翻译。`);
	}
	notes.push('中文 PDF 需要 CJK 字体；缺字形不会让构建失败，但会在 PDF 里变成缺字。');

	return {
		platform: 'linux',
		label: 'POSIX / Ubuntu',
		syntax: shellSyntax,
		toShellPath: (path) => path,
		notes,
		/**
		 * How to run a shell command string.
		 *
		 * @param {string} script - the command to run.
		 * @returns {{ command: string, args: string[] } | null} argv, or null without bash.
		 */
		shellCommand(script) {
			if (command === null) return null;
			return { command, args: ['-lc', script] };
		},
		/**
		 * How this host will satisfy the converter's `python3` call.
		 *
		 * A distribution that only ships `python` still needs the name
		 * `python3`, so that case gets the same temporary shim Windows uses.
		 *
		 * @returns {{ kind: 'native' | 'shim' | 'missing', interpreter?: string, note: string }} The plan.
		 */
		pythonPlan() {
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
			return { kind: 'missing', note: '没有 python3；replace_block.py 会失败，请先安装（sudo apt install -y python3）。' };
		}
	};
}

/**
 * Quote a path for the POSIX shell.
 *
 * @param {string} value - the raw value.
 * @returns {string} A single-quoted shell word.
 */
export function shellSyntax(value) {
	return `'${String(value).replace(/'/gu, `'\\''`)}'`;
}
