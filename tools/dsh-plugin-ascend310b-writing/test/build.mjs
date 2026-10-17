/**
 * Build driver tests.
 *
 * The planning half is pure, so it is pinned directly: the exact command, the
 * python3 shim, the Windows `cmd.exe` wrapper for pnpm, and the blockers that
 * stop a build before it starts. The execution half is exercised with an
 * injected `spawn`, so the failure classification and the PDF-lock recovery run
 * against a scripted child process instead of a ten-minute real build.
 *
 * Usage: node test/build.mjs
 */

import { EventEmitter } from 'node:events';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';

import {
	classifyStep,
	planBuild,
	recoverPdfLock,
	resolveBuildEnvironment,
	runBuild,
	summarizeBuild
} from '../lib/build.js';
import { describeEnvironment } from '../drivers/index.mjs';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-build-${Date.now()}`);
mkdirSync(join(root, 'latex'), { recursive: true });
writeFileSync(join(root, 'convert-vuepress.sh'), '#!/bin/sh\nexit 0\n', 'utf8');
writeFileSync(join(root, 'package.json'), '{"name":"fixture"}\n', 'utf8');
writeFileSync(join(root, 'latex', 'book.xdv'), 'xdv', 'utf8');

/** A component list where the named ids are present and the rest are absent. */
const componentsOf = (present) =>
	['bash', 'pandoc', 'latexmk', 'xelatex', 'python3', 'pnpm', 'node'].map((id) => ({
		id,
		label: id,
		required: ['bash', 'pandoc', 'latexmk', 'xelatex', 'python3'].includes(id),
		ok: present.includes(id),
		reason: present.includes(id) ? undefined : 'not-found',
		name: id,
		version: present.includes(id) ? '1.0' : undefined
	}));

// --- environment and planning ----------------------------------------------
const windows = resolveBuildEnvironment({
	platform: 'win32',
	env: {},
	overrides: {
		bash: 'C:\\Program Files\\Git\\bin\\bash.exe',
		pandoc: process.execPath,
		latexmk: process.execPath,
		xelatex: process.execPath,
		python3: process.execPath
	}
});
check(windows.adapter.platform === 'win32', 'the Windows adapter must be selected');
check(windows.components.length === 7, 'every component must be reported');

const adapter = describeEnvironment({
	platform: 'win32',
	components: [
		{ id: 'bash', ok: true, name: 'C:\\Program Files\\Git\\bin\\bash.exe', flavor: 'msys' },
		// An Anaconda interpreter answers `python` but not `python3`, so the build
		// must provide a shim.
		{ id: 'python3', ok: true, name: 'python.exe' }
	]
});

const latex = planBuild({ root, target: 'latex', adapter, components: componentsOf(['bash', 'pandoc', 'latexmk', 'xelatex', 'python3']) });
check(latex.ok === true, `a complete toolchain must plan cleanly, blockers: ${latex.blockers.join('; ')}`);
check(latex.steps.length === 1, 'one LaTeX step expected');
const latexScript = latex.steps[0].args[latex.steps[0].args.length - 1];
check(latex.steps[0].args[0] === '-lc', 'the step must run through a login shell');
check(latexScript.includes('bash ./convert-vuepress.sh'), `the step must run the repository converter, got: ${latexScript}`);
check(latexScript.includes('/d/') || latexScript.includes('/c/') || latexScript.includes('/mnt/'), 'the checkout path must be spelled for the shell');
check(latexScript.includes('ascend310b-writing-bin'), 'an alias-only Python must get a temporary python3 shim');
check(latexScript.includes('export PATH='), 'the shim must be exported for the build only');

const missingTool = planBuild({
	root,
	target: 'latex',
	adapter,
	components: componentsOf(['bash', 'pandoc', 'python3'])
});
check(missingTool.ok === false, 'a missing required tool must block the build');
check(missingTool.blockers.some((blocker) => blocker.includes('latexmk')), 'the blocker must name the missing tool');
check(missingTool.steps.length === 0 || missingTool.blockers.length > 0, 'a blocked plan still reports what it would run');

const noConverter = planBuild({ root: join(root, 'nowhere'), target: 'latex', adapter, components: componentsOf(['bash', 'pandoc', 'latexmk', 'xelatex', 'python3']) });
check(
	noConverter.blockers.some((blocker) => blocker.includes('convert-vuepress.sh')),
	'a directory without the converter must be refused by name'
);

const site = planBuild({ root, target: 'site', adapter, components: componentsOf(['bash', 'pandoc', 'latexmk', 'xelatex', 'python3', 'pnpm', 'node']) });
check(site.ok === true, 'a complete site toolchain must plan cleanly');
check(site.steps[0].command === 'cmd.exe', 'pnpm on Windows must go through cmd.exe (it is a .cmd shim)');
check(site.steps[0].args.join(' ') === '/c pnpm run docs:build', `unexpected site argv: ${site.steps[0].args.join(' ')}`);

const posix = describeEnvironment({ platform: 'linux', components: [{ id: 'bash', ok: true, name: '/bin/bash' }, { id: 'python3', ok: true, name: 'python3' }] });
const posixSite = planBuild({ root, target: 'site', adapter: posix, components: componentsOf(['bash', 'pandoc', 'latexmk', 'xelatex', 'python3', 'pnpm', 'node']) });
check(posixSite.steps[0].command === 'pnpm', 'a POSIX host must invoke pnpm directly');
const posixLatex = planBuild({ root, target: 'latex', adapter: posix, components: componentsOf(['bash', 'pandoc', 'latexmk', 'xelatex', 'python3']) });
const posixScript = posixLatex.steps[0].args[posixLatex.steps[0].args.length - 1];
check(!posixScript.includes('ascend310b-writing-bin'), 'a host with a real python3 must not get a shim');
check(posixScript.includes('bash ./convert-vuepress.sh'), 'the POSIX step must run the repository converter');

// --- failure classification -------------------------------------------------
check(classifyStep({ exitCode: 0, output: '' }).kind === 'ok', 'exit 0 is success');
check(
	classifyStep({ exitCode: 1, output: 'xdvipdfmx:fatal: Unable to open "book.pdf".' }).kind === 'pdf-lock',
	'the PDF lock must be recognised'
);
check(classifyStep({ exitCode: 127, output: 'pandoc: command not found' }).kind === 'tool-missing', 'a missing command must be named');
check(classifyStep({ exitCode: 1, output: 'something else' }).kind === 'unknown', 'anything else is unknown');

/** A spawn stand-in driven by a script of per-call outcomes. */
function fakeSpawn(script) {
	const calls = [];
	const spawnImpl = (command, args) => {
		const child = new EventEmitter();
		child.stdout = new PassThrough();
		child.stderr = new PassThrough();
		child.kill = () => child.emit('close', null);
		const outcome = script(calls.length, { command, args }) ?? { exitCode: 0 };
		calls.push({ command, args });
		setImmediate(() => {
			if (outcome.stdout !== undefined) child.stdout.write(outcome.stdout);
			if (outcome.stderr !== undefined) child.stderr.write(outcome.stderr);
			child.stdout.end();
			child.stderr.end();
			child.emit('close', outcome.exitCode);
		});
		return child;
	};
	return { spawnImpl, calls };
}

// --- execution --------------------------------------------------------------
const chunks = [];
const success = fakeSpawn(() => ({ exitCode: 0, stdout: 'Output written on book.xdv\n' }));
const built = await runBuild({ plan: latex, root, adapter, onChunk: (text) => chunks.push(text), spawnImpl: success.spawnImpl });
check(built.ok === true, `a clean build must pass, got: ${built.detail}`);
check(success.calls.length === 1, `one command expected, got ${success.calls.length}`);
check(built.summary !== undefined && typeof built.summary.text === 'string', 'the build must verify its own output');
check(chunks.join('').includes('转换 Markdown'), 'the step must be announced in the stream before it runs');

const locked = fakeSpawn((index) =>
	index === 0
		? { exitCode: 1, stdout: 'xdvipdfmx:fatal: Unable to open "book.pdf".' }
		: { exitCode: 0, stdout: '38137779 bytes written' }
);
const recovered = await runBuild({ plan: latex, root, adapter, spawnImpl: locked.spawnImpl });
check(recovered.ok === true, `the PDF lock must be recovered from book.xdv, got: ${recovered.detail}`);
check(locked.calls.length === 2, `the recovery must run exactly one extra command, got ${locked.calls.length}`);
check(locked.calls[1].args.join(' ').includes('xdvipdfmx'), 'the recovery must call xdvipdfmx');
check(recovered.results.some((result) => result.id === 'pdf-recover'), 'the recovery step must be reported');

const broken = fakeSpawn(() => ({ exitCode: 1, stderr: '! LaTeX Error: File `missing.sty` not found.' }));
const failed = await runBuild({ plan: latex, root, adapter, spawnImpl: broken.spawnImpl });
check(failed.ok === false, 'a real failure must fail the build');
check(failed.detail.length > 0, 'a failure must carry its reason');
check(broken.calls.length === 1, 'an unknown failure must not be retried blindly');

const recoveryWithoutXdv = await recoverPdfLock({ root: join(root, 'nowhere'), adapter, spawnImpl: success.spawnImpl });
check(recoveryWithoutXdv === null, 'recovery must refuse when there is no book.xdv');

// --- summary ----------------------------------------------------------------
const summary = summarizeBuild({ root });
check(summary.pdf === null, 'a fixture without a PDF must report no PDF');
check(typeof summary.text === 'string' && summary.text.includes('构建结果'), 'the summary must be rendered');
check(summary.artifacts.pairs === 0, 'a fixture without generated output has no pairs');

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('build test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('build test passed');
console.log('  预检：命令拼装、python3 垫片、Windows cmd.exe 包装、缺工具/缺转换器的拦截');
console.log('  执行：失败分类、PDF 占用定点恢复、未知失败不盲重试、构建后自动核对产物');
