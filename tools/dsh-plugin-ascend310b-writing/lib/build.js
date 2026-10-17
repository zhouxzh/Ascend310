/**
 * Build driver: run the repository's own converter through the platform
 * adapter, then verify what it produced.
 *
 * Three things make this worth being a tool rather than a documented command:
 *
 * 1. The environment is *discovered*, not assumed. The adapter decides whether
 *    the shell is Git Bash or WSL, how to spell the checkout path for it, and
 *    whether the converter's `python3` call needs a temporary shim — the three
 *    things that actually went wrong on real machines.
 * 2. A build is not finished when the converter exits 0. The step that is
 *    easiest to forget — reading the fresh log for missing glyphs and checking
 *    the generated anchors against the source — runs in the same job, so its
 *    verdict arrives with the build instead of being remembered later.
 * 3. It is a background job. A full book build takes minutes; the caller gets a
 *    job id and reads output through the platform's own job tools.
 *
 * The repository's `convert-vuepress.sh` stays the source of truth for *how* to
 * convert. This module only decides how to invoke it here, and what to check
 * afterwards.
 *
 * @module dsh-plugin-ascend310b-writing/build
 */

import { spawn } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describeEnvironment } from '../drivers/index.mjs';
import { collectArtifactFindings, collectRenderFindings } from './checks.js';
import { detectToolchain } from './toolchain.js';

/** The tools the LaTeX path needs before anything is attempted. */
const LATEX_REQUIRED = ['bash', 'pandoc', 'latexmk', 'xelatex'];

/** The tools the site build needs. */
const SITE_REQUIRED = ['pnpm', 'node'];

/** Where the temporary `python3` shim is written, inside the shell's view. */
const SHIM_DIR = '/tmp/ascend310b-writing-bin';

/**
 * A failure signature this project has hit: `xdvipdfmx` cannot replace
 * `book.pdf` while another process holds it, which happens after a viewer or a
 * previous build left a handle open. The LaTeX work is already done at that
 * point, so the PDF is rebuilt from the `.xdv` rather than from scratch.
 */
const PDF_LOCK = /Unable to open "?book\.pdf"?|xdvipdfmx:fatal/i;

/**
 * Discover what this host can build with.
 *
 * @param {object} [options] - detection inputs.
 * @param {string} [options.platform] - defaults to `process.platform`.
 * @param {Record<string, string|undefined>} [options.env] - defaults to `process.env`.
 * @param {Record<string, string>} [options.overrides] - per-component executables from config.
 * @returns {{ components: Array<object>, adapter: object, platform: string }} The environment.
 */
export function resolveBuildEnvironment({ platform = process.platform, env = process.env, overrides = {} } = {}) {
	const components = detectToolchain({ platform, env, overrides });
	return { components, adapter: describeEnvironment({ platform, components }), platform };
}

/**
 * Quote a value as a single shell word using the adapter's own quoting.
 *
 * @param {object} adapter - the platform adapter.
 * @param {string} value - the value to quote.
 * @returns {string} A quoted shell word.
 */
function quote(adapter, value) {
	return adapter.syntax(value);
}

/**
 * Build the one shell command that sets up `python3` when the host lacks it.
 *
 * The converter calls `python3 replace_block.py`. A machine whose Python comes
 * from Anaconda has no `python3` on `PATH`, so a shim is written into a temp
 * directory and exported for the duration of this command only.
 *
 * @param {object} adapter - the platform adapter.
 * @param {object} pythonPlan - the adapter's python plan.
 * @returns {string} A shell fragment ending with `export PATH=…`.
 */
function pythonShimCommand(adapter, pythonPlan) {
	const interpreter = quote(adapter, adapter.toShellPath(pythonPlan.interpreter));
	return [
		`mkdir -p ${SHIM_DIR}`,
		`printf '#!/bin/sh\\nexec "%s" "$@"\\n' ${interpreter} > ${SHIM_DIR}/python3`,
		`chmod +x ${SHIM_DIR}/python3`,
		`export PATH="${SHIM_DIR}:$PATH"`
	].join(' && ');
}

/**
 * Decide what to run, and refuse honestly when something is missing.
 *
 * Pure: it reads the filesystem and the detection result, and returns a plan.
 * Nothing is executed here.
 *
 * @param {object} options - planning inputs.
 * @param {string} options.root - the repository root.
 * @param {string} options.target - `latex`, `site`, or `all`.
 * @param {object} options.adapter - the platform adapter.
 * @param {Array<object>} options.components - the detection result.
 * @param {object} [options.settings] - the resolved layout, for the artifact paths.
 * @returns {{ target: string, ok: boolean, blockers: string[], steps: Array<object>, notes: string[] }} The plan.
 */
export function planBuild({ root, target, adapter, components, settings }) {
	const byId = new Map(components.map((component) => [component.id, component]));
	const missing = (ids) => ids.filter((id) => byId.get(id)?.ok !== true);
	const python = adapter.pythonPlan();
	const steps = [];
	const blockers = [];
	const notes = [];

	const wantsLatex = target === 'latex' || target === 'all';
	const wantsSite = target === 'site' || target === 'all';

	if (wantsLatex) {
		const absent = missing(LATEX_REQUIRED);
		if (absent.length > 0) blockers.push(`LaTeX 构建缺少：${absent.join('、')}`);
		if (python.kind === 'missing') blockers.push('LaTeX 构建缺少 Python 3（convert-vuepress.sh 要调用 replace_block.py）');
		if (!existsSync(join(root, 'convert-vuepress.sh'))) blockers.push(`仓库里没有 convert-vuepress.sh（${root}）`);
		const shell = adapter.shellCommand('true');
		if (shell === null) blockers.push('没有可用的 shell，无法运行 convert-vuepress.sh');

		const fragments = [];
		if (python.kind === 'shim') fragments.push(pythonShimCommand(adapter, python));
		fragments.push(`cd ${quote(adapter, adapter.toShellPath(root))}`);
		fragments.push('bash ./convert-vuepress.sh');
		if (shell !== null) {
			steps.push({
				id: 'latex',
				label: '转换 Markdown → LaTeX → PDF',
				command: shell.command,
				args: [...shell.args.slice(0, -1), fragments.join(' && ')],
				note: python.kind === 'shim' ? `构建期间临时提供 python3 → ${python.interpreter}` : undefined
			});
		}
		notes.push('convert-vuepress.sh 是仓库自己的脚本；本工具不改变它的转换方式。');
	}

	if (wantsSite) {
		const absent = missing(SITE_REQUIRED);
		if (absent.length > 0) blockers.push(`站点构建缺少：${absent.join('、')}`);
		// pnpm on Windows is a `.cmd` shim, which cannot be spawned directly.
		const command = adapter.platform === 'win32' ? 'cmd.exe' : 'pnpm';
		steps.push({
			id: 'site',
			label: '构建 VuePress 站点',
			command,
			args: adapter.platform === 'win32' ? ['/c', 'pnpm', 'run', 'docs:build'] : ['run', 'docs:build'],
			cwd: root
		});
		notes.push('站点构建输出到 src/.vuepress/dist，不进 Git。');
	}

	if (settings === undefined) notes.push('（未提供布局设置：产物检查将按内置布局读取 latex/）');

	return { target, ok: blockers.length === 0, blockers, steps, notes };
}

/**
 * Classify what a finished step means.
 *
 * A PDF lock is recoverable without redoing the conversion, so it is told apart
 * from a real tool failure instead of being retried blindly.
 *
 * @param {object} options - the step outcome.
 * @param {number | null} options.exitCode - the exit status.
 * @param {string} options.output - everything the step printed.
 * @returns {{ kind: 'ok'|'pdf-lock'|'tool-missing'|'unknown', advice: string }} The classification.
 */
export function classifyStep({ exitCode, output }) {
	if (exitCode === 0) return { kind: 'ok', advice: '' };
	if (PDF_LOCK.test(output)) {
		return {
			kind: 'pdf-lock',
			advice: 'book.pdf 被占用：LaTeX 产物已生成，只需用 xdvipdfmx 重新写出 PDF（下面会尝试一次）。'
		};
	}
	if (/command not found|not recognized as an internal|无法将.*项识别为/i.test(output)) {
		return { kind: 'tool-missing', advice: '有命令在构建过程中找不到：先跑工具链体检确认 PATH。' };
	}
	return { kind: 'unknown', advice: '构建失败，详见上面的输出。' };
}

/**
 * Spawn one step and stream its output.
 *
 * @param {object} options - execution inputs.
 * @param {object} options.step - the planned step.
 * @param {Record<string, string|undefined>} options.env - the environment.
 * @param {Function} options.onChunk - receives `(text, channel)`.
 * @param {Function} options.onSpawn - receives the child, for cancellation.
 * @param {Function} options.spawnImpl - the spawn implementation (injectable for tests).
 * @returns {Promise<{ exitCode: number | null, output: string }>} The step outcome.
 */
function runStep({ step, env, onChunk, onSpawn, spawnImpl }) {
	return new Promise((resolve, reject) => {
		let child;
		try {
			child = spawnImpl(step.command, step.args, {
				cwd: step.cwd,
				env,
				stdio: ['ignore', 'pipe', 'pipe'],
				windowsHide: true
			});
		} catch (error) {
			reject(error);
			return;
		}
		if (onSpawn !== undefined) onSpawn(child);
		let output = '';
		const pump = (stream, channel) => {
			stream.setEncoding('utf8');
			stream.on('data', (text) => {
				output += text;
				if (onChunk !== undefined) onChunk(text, channel);
			});
		};
		pump(child.stdout, 'stdout');
		pump(child.stderr, 'stderr');
		child.on('error', reject);
		child.on('close', (exitCode) => resolve({ exitCode, output }));
	});
}

/**
 * Rebuild the PDF from the already-generated `.xdv` after a lock failure.
 *
 * @param {object} options - recovery inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.adapter - the platform adapter.
 * @param {Function} options.onChunk - receives `(text, channel)`.
 * @param {Function} options.onSpawn - receives the child, for cancellation.
 * @param {Function} options.spawnImpl - the spawn implementation.
 * @returns {Promise<{ exitCode: number | null, output: string } | null>} The outcome, or null when the `.xdv` is gone.
 */
export async function recoverPdfLock({ root, adapter, onChunk, onSpawn, spawnImpl }) {
	const latexDir = join(root, 'latex');
	if (!existsSync(join(latexDir, 'book.xdv'))) return null;
	const shell = adapter.shellCommand('true');
	if (shell === null) return null;
	const script = `cd ${quote(adapter, adapter.toShellPath(latexDir))} && xdvipdfmx -E -o book.pdf book.xdv`;
	return runStep({
		step: { command: shell.command, args: [...shell.args.slice(0, -1), script] },
		env: process.env,
		onChunk,
		onSpawn,
		spawnImpl
	});
}

/**
 * Read what the build produced: the log's verdict and the artifact anchors.
 *
 * @param {object} options - summarise inputs.
 * @param {string} options.root - the repository root.
 * @param {object} [options.settings] - the resolved layout.
 * @returns {{ render: object, artifacts: object, pdf: string | null, text: string }} The summary.
 */
export function summarizeBuild({ root, settings }) {
	const render = collectRenderFindings({ root, settings });
	const artifacts = collectArtifactFindings({ root, settings });
	const pdf = settings === undefined ? join(root, 'latex', 'book.pdf') : join(root, settings.latex.pdf);
	const pdfSize = existsSync(pdf) ? statSync(pdf).size : null;
	const renderErrors = render.findings.filter((finding) => finding.severity === 'error').length;
	const text = [
		'构建结果',
		`  渲染体检：硬错误 ${render.stats?.hardErrors ?? '?'} · 缺字形 ${render.stats?.missingCharacters ?? '?'} · ` +
			`未定义引用 ${render.stats?.undefinedReferences ?? '?'}`,
		`  产物锚点：${artifacts.pairs} 对源/产物、${artifacts.anchors} 个源锚点、${artifacts.findings.length} 条问题`,
		pdfSize === null ? '  book.pdf：不存在' : `  book.pdf：${(pdfSize / (1024 * 1024)).toFixed(1)} MB`,
		renderErrors === 0 && artifacts.findings.length === 0
			? '  结论：构建产物与源一致，渲染无错误。'
			: '  结论：产物有问题，见上；缺字形要改源 Markdown，锚点缺失要区分"改源"与"重建"。'
	].join('\n');
	return { render, artifacts, pdf: pdfSize === null ? null : pdf, text };
}

/**
 * Run a plan to completion.
 *
 * @param {object} options - run inputs.
 * @param {object} options.plan - the plan from {@link planBuild}.
 * @param {string} options.root - the repository root.
 * @param {object} options.adapter - the platform adapter.
 * @param {object} [options.settings] - the resolved layout.
 * @param {Function} [options.onChunk] - receives `(text, channel)`.
 * @param {Function} [options.onSpawn] - receives the child, for cancellation.
 * @param {Function} [options.spawnImpl] - the spawn implementation.
 * @returns {Promise<object>} The outcome, including the post-build verification.
 */
export async function runBuild({ plan, root, adapter, settings, onChunk, onSpawn, spawnImpl = spawn }) {
	const results = [];
	for (const step of plan.steps) {
		if (onChunk !== undefined) onChunk(`\n=== ${step.label} ===\n`, 'log');
		const outcome = await runStep({ step, env: process.env, onChunk, onSpawn, spawnImpl });
		const verdict = classifyStep(outcome);
		results.push({ id: step.id, exitCode: outcome.exitCode, kind: verdict.kind, advice: verdict.advice });
		if (verdict.kind === 'ok') continue;

		if (verdict.kind === 'pdf-lock') {
			if (onChunk !== undefined) onChunk('\n=== PDF 被占用，尝试从 book.xdv 重新写出 ===\n', 'log');
			const recovered = await recoverPdfLock({ root, adapter, onChunk, onSpawn, spawnImpl });
			const recoveredVerdict = recovered === null ? { kind: 'unknown', advice: 'book.xdv 不存在，无法定点恢复。' } : classifyStep(recovered);
			results.push({ id: 'pdf-recover', exitCode: recovered?.exitCode ?? null, kind: recoveredVerdict.kind, advice: recoveredVerdict.advice });
			if (recoveredVerdict.kind === 'ok') continue;
		}
		return { ok: false, results, detail: verdict.advice, summary: summarizeBuild({ root, settings }) };
	}

	const summary = summarizeBuild({ root, settings });
	return { ok: true, results, detail: '构建完成，产物已核对。', summary };
}
