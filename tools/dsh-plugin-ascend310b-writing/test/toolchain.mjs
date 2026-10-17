/**
 * Toolchain detection, platform adapters and the doctor report.
 *
 * Detection is exercised through explicit overrides rather than by trusting
 * whatever this machine happens to have installed: `override` points a
 * component at a real executable (`process.execPath` prints a version), so the
 * probe plumbing is tested the same way on every host. Anything that depends on
 * the real `PATH` is asserted structurally, never by value — otherwise this
 * test would fail on a machine that simply has different software.
 *
 * Usage: node test/toolchain.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describeEnvironment, describeWindows, describePosix } from '../drivers/index.mjs';
import { buildPythonPlan, toMsysPath, toWslPath } from '../drivers/env-windows.mjs';
import {
	COMPONENTS,
	INSTALL_HINTS,
	condaInterpreters,
	condaRoots,
	detectToolchain,
	formatToolchain,
	inferBashFlavor,
	installCommand
} from '../lib/toolchain.js';
import { diagnose } from '../lib/doctor.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

// --- detection --------------------------------------------------------------
const found = detectToolchain({ overrides: { pandoc: process.execPath } });
const pandoc = found.find((component) => component.id === 'pandoc');
check(pandoc?.ok === true, 'an override pointing at a real executable must be detected');
check(typeof pandoc?.version === 'string' && pandoc.version.length > 0, 'a detected tool must report a version');
check(pandoc?.name === process.execPath, 'the record must name the executable that answered');

const missing = detectToolchain({ overrides: { bash: 'definitely-not-a-real-binary-xyz' } });
const bash = missing.find((component) => component.id === 'bash');
check(bash?.ok === false, 'a nonexistent override must be reported missing, not throw');
check(bash?.reason === 'not-found', `a nonexistent override must say why, got ${bash?.reason}`);

check(found.length === COMPONENTS.length, `one record per component expected, got ${found.length}`);
check(
	found.every((component) => typeof component.id === 'string' && typeof component.label === 'string' && typeof component.required === 'boolean' && typeof component.ok === 'boolean'),
	'every record must carry id, label, required and ok'
);

// The real detection must never throw, whatever this host has installed.
const real = detectToolchain({});
check(real.length === COMPONENTS.length, 'detection against the real PATH must return one record per component');
const realRequired = real.filter((component) => component.required && !component.ok).map((component) => component.id);
const realReport = formatToolchain({ components: real, platform: process.platform, missingRequired: realRequired });
check(realReport.includes('工具链体检'), 'the report must be rendered');
if (realRequired.length > 0) {
	check(realReport.includes('config.toolchain'), 'a report naming missing tools must say how to point at one outside PATH');
} else {
	check(realReport.includes('都在'), 'a complete toolchain must be stated plainly');
}

// --- install hints ----------------------------------------------------------
check(installCommand('win32', []) === null, 'nothing missing means no install command');
check(installCommand('linux', ['bash']) === null, 'bash alone needs no package list');
const apt = installCommand('linux', ['pandoc', 'latexmk', 'xelatex', 'python3']);
check(typeof apt === 'string' && apt.startsWith('sudo apt update'), `a Linux install command must be an apt line, got ${apt}`);
for (const pkg of ['pandoc', 'latexmk', 'texlive-xetex', 'python3', 'fonts-noto-cjk']) {
	check(apt.includes(pkg), `the Linux install command must include ${pkg}`);
}
check(apt !== null && !apt.includes('fonts-noto-cjk fonts-noto-cjk'), 'packages must not be duplicated');
check(typeof installCommand('darwin', ['pandoc']) === 'string', 'macOS must get a brew command');
check(installCommand('win32', ['pandoc']).length > 0, 'Windows must get a winget/choco hint');
for (const platform of ['win32', 'linux', 'darwin']) {
	for (const component of COMPONENTS) {
		check(
			Array.isArray(INSTALL_HINTS[platform]?.[component.id]),
			`${platform} must document how to install ${component.id}`
		);
	}
}

// --- platform adapters ------------------------------------------------------
check(toMsysPath('D:\\a\\b') === '/d/a/b', `MSYS path, got ${toMsysPath('D:\\a\\b')}`);
check(toWslPath('D:\\a\\b') === '/mnt/d/a/b', `WSL path, got ${toWslPath('D:\\a\\b')}`);
check(toMsysPath('/already/posix') === '/already/posix', 'an already-POSIX path must pass through');

const msysBash = { ok: true, name: 'C:\\Program Files\\Git\\bin\\bash.exe' };
const wsl = describeWindows({ bash: { ok: true, name: 'wsl.exe', flavor: 'wsl' } });
const msys = describeWindows({ bash: msysBash });
check(msys.toShellPath('D:\\x') === '/d/x', 'the Git Bash adapter must translate to /d/x');
check(wsl.toShellPath('D:\\x') === '/mnt/d/x', 'the WSL adapter must translate to /mnt/d/x');
check(msys.shellCommand('echo hi').args[0] === '-lc', 'Git Bash runs the script with -lc');
check(wsl.shellCommand('echo hi').command === 'wsl.exe', 'WSL is invoked through wsl.exe');
check(wsl.shellCommand('echo hi').args.join(' ') === 'bash -lc echo hi', 'WSL needs an explicit bash');
check(describeWindows({}).shellCommand('x') === null, 'no bash means no way to run the build, not a crash');
check(msys.syntax("it's") === `'it'\\''s'`, `shell quoting must escape a quote, got ${msys.syntax("it's")}`);

const posix = describePosix({ bash: { ok: true, name: '/bin/bash' } });
check(posix.toShellPath('D:\\x') === 'D:\\x', 'a POSIX host must not rewrite paths');
check(posix.shellCommand('x').args[0] === '-lc', 'POSIX runs the script with -lc');

// --- python plans -----------------------------------------------------------
check(buildPythonPlan({ python3: { ok: true, name: 'python3' } }).kind === 'native', 'a real python3 is native');
const shim = buildPythonPlan({ pythonAlias: { ok: true, name: 'python.exe' } });
check(shim.kind === 'shim' && shim.interpreter === 'python.exe', 'an alias-only host must fall back to a shim');
check(buildPythonPlan({}).kind === 'missing', 'no interpreter must be reported as missing');

// --- bash flavor and conda --------------------------------------------------
// The path cannot decide which shell a `bash` is; the banner can. A cheap
// wrong answer here means every build path is spelled for the wrong shell.
check(inferBashFlavor('GNU bash, version 5.2.37(1)-release (x86_64-pc-msys)', 'win32') === 'msys', 'an MSYS banner is Git Bash');
check(inferBashFlavor('GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)', 'win32') === 'wsl', 'a Linux banner on Windows is WSL');
check(inferBashFlavor('GNU bash (x86_64-w64-mingw32)', 'win32') === 'msys', 'a MinGW banner is MSYS');
check(inferBashFlavor('GNU bash (x86_64-unknown-cygwin)', 'win32') === 'msys', 'a Cygwin banner is MSYS');
check(inferBashFlavor('GNU bash', 'win32') === 'msys', 'an unrecognized banner falls back to MSYS, the safer spelling');
check(inferBashFlavor('GNU bash (x86_64-pc-linux-gnu)', 'linux') === undefined, 'flavor is a Windows-only question');

// A conda install is discovered from the environment, never from a literal, and
// its interpreter is what satisfies the converter's `python3`.
const condaRoot = join(tmpdir(), `tw-conda-${Date.now()}`);
mkdirSync(join(condaRoot, 'anaconda3', 'envs', 'pdf'), { recursive: true });
writeFileSync(join(condaRoot, 'anaconda3', 'python.exe'), '');
writeFileSync(join(condaRoot, 'anaconda3', 'envs', 'pdf', 'python.exe'), '');
const fakeEnv = { USERPROFILE: condaRoot, HOME: condaRoot };
const roots = condaRoots(fakeEnv);
check(roots.length >= 1, `a conda root under the user profile must be found, got ${roots.length}`);
check(roots.some((entry) => entry.root.endsWith('anaconda3') && entry.label === 'Anaconda'), 'the root must be labelled Anaconda');
check(condaRoots({ USERPROFILE: join(condaRoot, 'nowhere') }).length === 0, 'a nonexistent root must not be reported');
const interpreters = condaInterpreters(fakeEnv, 'win32');
check(interpreters.length === 2, `base plus one named environment expected, got ${interpreters.length}`);
check(
	interpreters.some((entry) => entry.provider === 'Anaconda base'),
	'a base interpreter must be labelled'
);
check(
	interpreters.some((entry) => entry.provider === 'Anaconda envs/pdf'),
	'a named environment must be labelled with its name'
);
const activeEnv = condaInterpreters({ CONDA_PREFIX: join(condaRoot, 'anaconda3') }, 'win32');
check(activeEnv.some((entry) => entry.provider === 'conda 活动环境'), 'an activated environment must be recognized');
rmSync(condaRoot, { recursive: true, force: true });

// --- adapter selection ------------------------------------------------------
const selected = describeEnvironment({
	platform: 'win32',
	components: [
		{ id: 'bash', ok: true, name: 'bash.exe' },
		{ id: 'python3', ok: true, name: 'python.exe' }
	]
});
check(selected.platform === 'win32', 'the Windows adapter must be selected on win32');
check(selected.pythonPlan().kind === 'shim', 'a `python.exe` answer is an alias for the converter\'s `python3`');
const selectedPosix = describeEnvironment({ platform: 'linux', components: [{ id: 'python3', ok: true, name: 'python3' }] });
check(selectedPosix.pythonPlan().kind === 'native', 'the POSIX adapter must accept a real python3');

// --- doctor -----------------------------------------------------------------
const bare = join(tmpdir(), `tw-doctor-${Date.now()}`);
mkdirSync(bare, { recursive: true });
writeFileSync(join(bare, 'README.md'), '# not the repository\n', 'utf8');

// Force every required component missing so the capability answer is
// deterministic on any host.
const allMissing = Object.fromEntries(COMPONENTS.map((component) => [component.id, 'definitely-not-a-real-binary-xyz']));
const empty = diagnose({ root: bare, env: {}, overrides: allMissing });
check(empty.missingRequired.includes('pandoc'), 'the doctor must list missing required components');
check(empty.capabilities.find((c) => c.id === 'checks').available === true, 'manuscript checks need no external tool');
check(empty.capabilities.find((c) => c.id === 'latex').available === false, 'the LaTeX capability must be unavailable without the toolchain');
check(empty.files.converter === false, 'the doctor must report the missing repository entry point');
check(empty.text.includes('工具链体检'), 'the doctor must render its report');
check(empty.installCommand !== null, 'the doctor must offer an install command when something required is missing');

const realDoctor = diagnose({ root: bare });
check(realDoctor.components.length === COMPONENTS.length, 'the doctor must report every component');
check(realDoctor.capabilities.length === 4, `four capabilities expected, got ${realDoctor.capabilities.length}`);

rmSync(bare, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('toolchain test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('toolchain test passed');
console.log(`  ${COMPONENTS.length} 个组件、3 个平台适配器、3 种 python 方案、install 提示全覆盖`);
console.log(`  真实环境探测：必需缺失 ${real.filter((c) => c.required && !c.ok).map((c) => c.id).join('、') || '（无）'}`);
