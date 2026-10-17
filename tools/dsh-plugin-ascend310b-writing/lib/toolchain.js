/**
 * External-tool discovery for the build path.
 *
 * The plugin must work on a machine it has never seen, so nothing here names a
 * person's checkout, home directory or DSH installation. Every candidate is
 * either a bare command resolved on `PATH`, a path derived from a standard
 * environment variable, or a documented install root of the tool itself.
 *
 * The rule that keeps this honest: a candidate may be a place a tool is
 * *installed by default*, never a place a *particular person* put something.
 * When nothing is found the report says what to install, per platform, instead
 * of guessing a path.
 *
 * @module dsh-plugin-ascend310b-writing/toolchain
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';

/** How long a single `--version` probe may take before the tool counts as unusable. */
const PROBE_TIMEOUT_MS = 8000;

/**
 * What the build path needs, and what each one is for.
 *
 * `required` marks the LaTeX/PDF path, which is the only reason any of this
 * exists. The site build is optional: the manuscript checks work without it.
 */
export const COMPONENTS = [
	{ id: 'bash', label: 'Bash', required: true, unlocks: '运行仓库的 convert-vuepress.sh（它是 bash 脚本）' },
	{ id: 'pandoc', label: 'Pandoc', required: true, unlocks: 'Markdown → LaTeX' },
	{ id: 'latexmk', label: 'latexmk', required: true, unlocks: '驱动多轮 XeLaTeX 直到交叉引用收敛' },
	{ id: 'xelatex', label: 'XeLaTeX', required: true, unlocks: '中文 PDF 排版' },
	{ id: 'python3', label: 'Python 3', required: true, unlocks: 'replace_block.py（表格符号修正）' },
	{ id: 'pnpm', label: 'pnpm', required: false, unlocks: 'VuePress 站点构建（latex 不需要）' },
	{ id: 'node', label: 'Node.js', required: false, unlocks: 'VuePress 站点构建（latex 不需要）' }
];

/** The command names to try on `PATH`, in order, for one component. */
const PATH_NAMES = {
	bash: ['bash'],
	pandoc: ['pandoc'],
	latexmk: ['latexmk'],
	xelatex: ['xelatex'],
	python3: ['python3', 'python'],
	pnpm: ['pnpm'],
	node: ['node']
};

/** The argument that makes a tool print its version. */
const VERSION_ARG = { python3: '--version' };

/**
 * Every directory a conda distribution installs into by default, derived from
 * the environment instead of being written out as a literal.
 *
 * Anaconda and Miniconda deliberately do not add their interpreter to `PATH`,
 * so a machine whose Python comes from conda looks like a machine with no
 * Python at all — which is exactly the trap this function exists to avoid.
 * Active-environment variables come first, then the per-user and per-machine
 * install roots, then the POSIX locations.
 *
 * @param {Record<string, string|undefined>} env - the environment to read.
 * @returns {Array<{ root: string, label: string, kind: 'active'|'base' }>} Existing roots.
 */
export function condaRoots(env) {
	const seen = new Set();
	const roots = [];
	const add = (root, label, kind) => {
		if (typeof root !== 'string' || root.trim() === '') return;
		const key = root.toLowerCase();
		if (seen.has(key) || !existsSync(root)) return;
		seen.add(key);
		roots.push({ root, label, kind });
	};

	if (env.CONDA_PREFIX !== undefined) add(env.CONDA_PREFIX, 'conda', 'active');
	if (env.CONDA_EXE !== undefined) add(dirname(dirname(env.CONDA_EXE)), 'conda', 'base');
	if (env.CONDA_ROOT !== undefined) add(env.CONDA_ROOT, 'conda', 'base');

	const home = env.USERPROFILE ?? env.HOME;
	const names = [
		['anaconda3', 'Anaconda'],
		['Anaconda3', 'Anaconda'],
		['anaconda', 'Anaconda'],
		['miniconda3', 'Miniconda'],
		['Miniconda3', 'Miniconda'],
		['miniconda', 'Miniconda']
	];
	if (home !== undefined) for (const [name, label] of names) add(join(home, name), label, 'base');
	if (env.LOCALAPPDATA !== undefined) add(join(env.LOCALAPPDATA, 'Continuum', 'anaconda3'), 'Anaconda', 'base');
	for (const key of ['ProgramData', 'ProgramFiles', 'ProgramFiles(x86)']) {
		const base = env[key];
		if (base === undefined) continue;
		for (const [name, label] of names) add(join(base, name), label, 'base');
	}
	for (const root of ['/opt/conda', '/opt/anaconda3', '/opt/miniconda3', '/usr/local/anaconda3']) add(root, 'Anaconda', 'base');
	return roots;
}

/** The interpreter's path inside a conda root, per platform. */
function condaInterpreterPath(root, platform) {
	return platform === 'win32' ? join(root, 'python.exe') : join(root, 'bin', 'python');
}

/**
 * Interpreters reachable through conda: the active environment, each root's
 * base environment, then every named environment under it.
 *
 * @param {Record<string, string|undefined>} env - the environment to read.
 * @param {string} platform - `process.platform`.
 * @returns {Array<{ name: string, provider: string }>} Candidates in preference order.
 */
export function condaInterpreters(env, platform) {
	const interpreters = [];
	const seen = new Set();
	const push = (name, provider) => {
		const key = name.toLowerCase();
		if (seen.has(key) || !existsSync(name)) return;
		seen.add(key);
		interpreters.push({ name, provider });
	};
	for (const { root, label, kind } of condaRoots(env)) {
		push(condaInterpreterPath(root, platform), kind === 'active' ? `${label} 活动环境` : `${label} base`);
		let environments;
		try {
			environments = readdirSync(join(root, 'envs'), { withFileTypes: true });
		} catch {
			continue;
		}
		for (const entry of environments) {
			if (!entry.isDirectory()) continue;
			push(condaInterpreterPath(join(root, 'envs', entry.name), platform), `${label} envs/${entry.name}`);
		}
	}
	return interpreters;
}

/**
 * Probe `PATH` plus the documented install roots for one component.
 *
 * @param {string} id - the component id.
 * @param {object} options - probe inputs.
 * @param {string} options.platform - `process.platform`.
 * @param {Record<string, string|undefined>} options.env - the environment to read.
 * @returns {Array<{ name: string, flavor?: string, provider?: string }>} Candidates in preference order.
 */
function candidatesFor(id, { platform, env }) {
	const list = (PATH_NAMES[id] ?? [id]).map((name) => ({ name }));
	// A conda interpreter answers the converter's `python3` call regardless of
	// whether conda put anything on PATH.
	if (id === 'python3') list.push(...condaInterpreters(env, platform));
	if (platform !== 'win32') return list;

	const programFiles = env.ProgramFiles;
	const programFilesX86 = env['ProgramFiles(x86)'];
	const localAppData = env.LOCALAPPDATA;
	if (id === 'bash') {
		// On Windows the shell must be able to see the Windows-side toolchain, so
		// a Git Bash install root is preferred over whatever bare `bash` happens
		// to be first on PATH — that is often the WSL shim, whose filesystem view
		// cannot see software installed on the Windows side.
		const preferred = [];
		for (const root of [programFiles, programFilesX86]) {
			if (root === undefined) continue;
			preferred.push({ name: join(root, 'Git', 'bin', 'bash.exe'), flavor: 'msys' });
			preferred.push({ name: join(root, 'Git', 'usr', 'bin', 'bash.exe'), flavor: 'msys' });
		}
		// Fall back to PATH; the banner then decides which shell it really is.
		preferred.push({ name: 'bash' });
		// WSL is a legitimate POSIX shell on Windows, but its filesystem view does
		// not include software installed on the Windows side.
		preferred.push({ name: 'wsl.exe', flavor: 'wsl' });
		return [...preferred, ...list.filter((candidate) => candidate.name !== 'bash')];
	}
	if (id === 'pandoc') {
		if (localAppData !== undefined) list.push({ name: join(localAppData, 'Pandoc', 'pandoc.exe') });
		if (programFiles !== undefined) list.push({ name: join(programFiles, 'Pandoc', 'pandoc.exe') });
	}
	if (id === 'xelatex' || id === 'latexmk') {
		// TeX Live keeps one directory per release under a documented root.
		for (const root of ['C:\\texlive', programFiles === undefined ? undefined : join(programFiles, 'texlive')]) {
			if (root === undefined || !existsSync(root)) continue;
			for (const release of readdirSync(root)) {
				for (const bin of ['bin\\windows', 'bin']) {
					list.push({ name: join(root, release, bin, `${id}.exe`) });
				}
			}
		}
	}
	return list;
}

/**
 * Pick the most useful line out of a `--version` banner.
 *
 * Some tools print a locale or banner line first (`latexmk` on Windows starts
 * with a Win CP notice), so a line carrying something that looks like a version
 * is preferred over simply the first non-empty one.
 *
 * @param {string} output - the captured stdout.
 * @returns {string} The chosen line, trimmed.
 */
function versionLine(output) {
	const lines = String(output).split(/\r?\n/u).filter((line) => line.trim() !== '');
	if (lines.length === 0) return '';
	const withNumber = lines.find((line) => /\d+\.\d+/u.test(line));
	return (withNumber ?? lines[0]).trim().slice(0, 120);
}

/**
 * Decide whether a `bash` on Windows is an MSYS/Cygwin shell or the WSL shim.
 *
 * The path cannot answer this — `bash` on `PATH` may be either — so the version
 * banner does: a Linux build (`x86_64-pc-linux-gnu`) is WSL, an MSYS or MinGW
 * build is Git Bash. The distinction decides both the path spelling (`/d/x`
 * versus `/mnt/d/x`) and whether the Windows-side toolchain is visible at all.
 *
 * @param {string} version - the detected version banner.
 * @param {string} platform - `process.platform`.
 * @returns {string | undefined} `'msys'`, `'wsl'`, or undefined off Windows.
 */
export function inferBashFlavor(version, platform) {
	if (platform !== 'win32') return undefined;
	const text = String(version).toLowerCase();
	if (text.includes('msys') || text.includes('mingw') || text.includes('cygwin')) return 'msys';
	if (text.includes('linux')) return 'wsl';
	return 'msys';
}

/**
 * Run one `--version` probe.
 *
 * A probe only counts when it both succeeds and prints something: the
 * Microsoft Store's `python.exe` alias is an executable that exits nonzero with
 * no output, so "the file exists and ran" is not evidence of a working tool.
 *
 * On Windows a bare name may resolve to a `.cmd`/`.bat` shim (pnpm is one),
 * which `execFileSync` cannot execute directly, so that case is retried through
 * `cmd.exe /c` — the same resolution the user's shell performs.
 *
 * @param {string} name - the executable or subcommand to run.
 * @param {object} options - probe inputs.
 * @param {string} [options.id] - the component id, choosing the version flag.
 * @param {string} [options.platform] - `process.platform`.
 * @returns {{ ok: true, version: string } | { ok: false, reason: string }} The probe result.
 */
function probe(name, options) {
	const args = [VERSION_ARG[options.id] ?? '--version'];
	const attempts = [{ command: name, args }];
	if (options.platform === 'win32' && !/[\\/]/u.test(name)) {
		attempts.push({ command: 'cmd.exe', args: ['/c', name, ...args] });
	}
	let last = { ok: false, reason: 'not-found' };
	// A bare name that cannot be executed at all and cannot be resolved by the
	// shell either is "missing", not "broken": report that, because the remedy
	// is the same install hint and the shell's own message is locale-dependent.
	let directMissing = false;
	for (const attempt of attempts) {
		try {
			const output = execFileSync(attempt.command, attempt.args, {
				encoding: 'utf8',
				timeout: PROBE_TIMEOUT_MS,
				stdio: ['ignore', 'pipe', 'pipe'],
				windowsHide: true
			});
			const version = versionLine(output);
			if (version === '') {
				last = { ok: false, reason: 'ran but printed no version' };
				continue;
			}
			return { ok: true, version };
		} catch (error) {
			const stderr = String(error?.stderr ?? '').trim().split(/\r?\n/u)[0] ?? '';
			if (error?.code === 'ENOENT') {
				directMissing = true;
				last = { ok: false, reason: 'not-found' };
			} else if (error?.code === 'ETIMEDOUT') {
				last = { ok: false, reason: `no answer within ${PROBE_TIMEOUT_MS}ms` };
			} else {
				last = { ok: false, reason: stderr || `exit ${error?.status ?? '?'}` };
			}
		}
	}
	if (directMissing) return { ok: false, reason: 'not-found' };
	return last;
}

/**
 * Detect every component.
 *
 * @param {object} [options] - detection inputs.
 * @param {string} [options.platform] - defaults to `process.platform`.
 * @param {Record<string, string|undefined>} [options.env] - defaults to `process.env`.
 * @param {Record<string, string>} [options.overrides] - per-component executable
 *   supplied by configuration, for a machine whose tool is not on `PATH`.
 * @returns {Array<object>} One record per component, in {@link COMPONENTS} order.
 */
export function detectToolchain({ platform = process.platform, env = process.env, overrides = {} } = {}) {
	const results = [];
	for (const component of COMPONENTS) {
		const configured = overrides[component.id];
		const list = configured === undefined
			? candidatesFor(component.id, { platform, env })
			: [{ name: configured, configured: true }];
		let found;
		for (const candidate of list) {
			const attempt = probe(candidate.name, { id: component.id, platform });
			if (!attempt.ok) {
				found = { ...candidate, ok: false, reason: attempt.reason };
				continue;
			}
			// A bare `bash` on Windows could be Git Bash or the WSL shim; the
			// banner decides, and the adapter needs the answer.
			const flavor = candidate.flavor ?? (component.id === 'bash' ? inferBashFlavor(attempt.version, platform) : undefined);
			found = { ...candidate, ok: true, version: attempt.version, ...(flavor === undefined ? {} : { flavor }) };
			break;
		}
		results.push({
			id: component.id,
			label: component.label,
			required: component.required,
			unlocks: component.unlocks,
			...(found ?? { ok: false, reason: 'not-found' })
		});
	}
	return results;
}

/**
 * Per-platform install instructions for one component.
 *
 * These are the commands a person can paste; they are not run by the plugin.
 */
export const INSTALL_HINTS = {
	win32: {
		bash: ['winget install --id Git.Git -e', '备选：choco install git'],
		pandoc: ['winget install --id JohnMacFarlane.Pandoc -e', '备选：choco install pandoc'],
		latexmk: ['winget install --id MiKTeX.MiKTeX -e', '备选：choco install miktex / choco install texlive'],
		xelatex: ['winget install --id MiKTeX.MiKTeX -e', '备选：choco install miktex / choco install texlive'],
		python3: [
			'winget install --id Python.Python.3.12 -e',
			'备选：winget install --id Anaconda.Anaconda3 -e（Anaconda 用户可直接用已有的 conda python，本插件会自动探测 %USERPROFILE%\\anaconda3）'
		],
		pnpm: ['corepack enable', '备选：npm install -g pnpm'],
		node: ['winget install --id OpenJS.NodeJS.LTS -e']
	},
	linux: {
		bash: ['通常系统自带；缺失时：sudo apt install -y bash'],
		pandoc: ['sudo apt install -y pandoc'],
		latexmk: ['sudo apt install -y latexmk texlive-xetex texlive-latex-extra texlive-fonts-recommended'],
		xelatex: ['sudo apt install -y texlive-xetex texlive-latex-extra'],
		python3: ['sudo apt install -y python3', 'conda 用户亦可直接用 $CONDA_PREFIX/bin/python（本插件会自动探测 ~/anaconda3、~/.conda 与 /opt/conda）'],
		pnpm: ['corepack enable', '备选：npm install -g pnpm'],
		node: ['sudo apt install -y nodejs npm']
	},
	darwin: {
		bash: ['系统自带'],
		pandoc: ['brew install pandoc'],
		latexmk: ['brew install --cask mactex', '（或 brew install texlive）'],
		xelatex: ['brew install --cask mactex'],
		python3: ['brew install python'],
		pnpm: ['corepack enable'],
		node: ['brew install node']
	}
};

/**
 * Package-manager specs per platform, so one pasteable command can cover every
 * missing component. `null` means "this platform needs no package for it".
 *
 * On Windows the LyX-style TeX distributions ship `latexmk` and `xelatex`
 * together, and `pnpm` comes from Corepack rather than a package, which is why
 * the map is not a plain id-to-package rename.
 */
const PACKAGE_SPECS = {
	win32: {
		manager: 'winget',
		packages: {
			bash: ['--id', 'Git.Git'],
			pandoc: ['--id', 'JohnMacFarlane.Pandoc'],
			latexmk: ['--id', 'MiKTeX.MiKTeX'],
			xelatex: ['--id', 'MiKTeX.MiKTeX'],
			python3: ['--id', 'Python.Python.3.12'],
			node: ['--id', 'OpenJS.NodeJS.LTS'],
			pnpm: null
		},
		forms: (wanted) => {
			const commands = [];
			const seen = new Set();
			for (const id of wanted) {
				const spec = PACKAGE_SPECS.win32.packages[id];
				if (spec === undefined) return null;
				if (spec === null) {
					if (!seen.has('corepack')) {
						seen.add('corepack');
						commands.push('corepack enable');
					}
					continue;
				}
				const shell = `winget install ${spec.join(' ')} -e`;
				if (!seen.has(shell)) {
					seen.add(shell);
					commands.push(shell);
				}
			}
			return commands.length === 0 ? null : commands.join(' ; ');
		}
	},
	linux: {
		manager: 'apt',
		packages: {
			pandoc: ['pandoc'],
			latexmk: ['latexmk', 'texlive-xetex', 'texlive-latex-extra', 'texlive-fonts-recommended'],
			xelatex: ['texlive-xetex', 'texlive-latex-extra'],
			python3: ['python3'],
			node: ['nodejs', 'npm'],
			pnpm: null
		},
		forms: (wanted) => {
			const set = new Set();
			for (const id of wanted) for (const name of PACKAGE_SPECS.linux.packages[id] ?? []) set.add(name);
			// CJK typesetting needs the fonts too; a missing glyph shows up later as
			// `render-missing-character`, so install them together.
			if (set.size > 0) set.add('fonts-noto-cjk');
			return set.size === 0 ? 'corepack enable' : `sudo apt update && sudo apt install -y ${[...set].sort().join(' ')}`;
		}
	},
	darwin: {
		manager: 'brew',
		packages: {
			pandoc: ['pandoc'],
			latexmk: ['--cask', 'mactex'],
			xelatex: ['--cask', 'mactex'],
			python3: ['python'],
			node: ['node'],
			pnpm: ['pnpm']
		},
		forms: (wanted) => {
			const commands = [];
			const seen = new Set();
			for (const id of wanted) {
				const spec = PACKAGE_SPECS.darwin.packages[id];
				if (spec === undefined) return null;
				const shell = `brew install ${spec.join(' ')}`;
				if (!seen.has(shell)) {
					seen.add(shell);
					commands.push(shell);
				}
			}
			return commands.length === 0 ? null : commands.join(' && ');
		}
	}
};

/**
 * One copy-pasteable command that installs every missing component.
 *
 * Bash is only listed where having it is itself an install (Windows), not on
 * platforms that ship one.
 *
 * @param {string} platform - `process.platform`.
 * @param {string[]} missingIds - ids that are missing.
 * @returns {string | null} A command line, or null when there is no single one.
 */
export function installCommand(platform, missingIds) {
	const table = PACKAGE_SPECS[platform];
	if (table === undefined) return null;
	const wanted = missingIds.filter((id) => id !== 'bash' || platform === 'win32');
	if (wanted.length === 0) return null;
	return table.forms(wanted);
}

/**
 * Turn a detection result into the report the model and a person both read.
 *
 * @param {object} options - report inputs.
 * @param {Array<object>} options.components - the detection result.
 * @param {string} options.platform - `process.platform`.
 * @param {string[]} [options.missingRequired] - required ids that were missing.
 * @returns {string} The rendered report.
 */
export function formatToolchain({ components, platform, missingRequired = [] }) {
	const lines = [`工具链体检 · ${platform}`, ''];
	for (const component of components) {
		const mark = component.ok ? 'OK  ' : component.required ? '缺失' : '可选';
		const origin = component.name === component.id
			? ''
			: `  ← ${component.name}${component.provider === undefined ? '' : `（${component.provider}）`}`;
		const where = component.ok ? `${component.version ?? ''}${origin}` : component.reason ?? '';
		lines.push(`  [${mark}] ${component.id.padEnd(8)} ${component.label}${where === '' ? '' : `  ${where}`}`);
		lines.push(`           ${component.unlocks}`);
	}
	const optional = components.filter((item) => !item.required && !item.ok).map((item) => item.id);
	if (missingRequired.length === 0) {
		lines.push('', 'LaTeX/PDF 构建所需的工具都在。');
	} else {
		lines.push('', `LaTeX/PDF 构建缺少：${missingRequired.join('、')}。`);
		const command = installCommand(platform, missingRequired);
		if (command !== null) lines.push(`一条命令装齐（${platform}）：`, `  ${command}`);
		const hints = INSTALL_HINTS[platform] ?? {};
		for (const id of missingRequired) {
			const hint = hints[id];
			if (hint === undefined) continue;
			lines.push(`  ${id}：${hint.join('  |  ')}`);
		}
		lines.push(
			'',
			'装完后重跑本检查确认。若工具装在非 PATH 位置，不必改代码：',
			'在 profile 的 cordis.patch.yml 里给 dsh-plugin-ascend310b-writing 设置 config.toolchain.',
			'{ bash: "...", pandoc: "...", latexmk: "...", xelatex: "...", python3: "..." }。'
		);
	}
	if (optional.length > 0) {
		lines.push('', `站点构建可选组件缺失：${optional.join('、')}（不影响 LaTeX/PDF 与全部检查）。`);
	}
	lines.push(
		'',
		'字体：中文字形缺失不会让构建失败，但会在 PDF 里变成缺字（由 ascend310b_writing_render 报出）。',
		'Ubuntu/WSL 上若出现缺字，装 fonts-noto-cjk 后重建。'
	);
	return lines.join('\n');
}
