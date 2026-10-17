/**
 * Doctor: what this machine can do with this repository right now.
 *
 * This is the answer to "the plugin installed — now what?". It joins the
 * toolchain probe, the platform adapter and the repository's own build entry
 * points into one report that says which capabilities are available, which are
 * missing, and the exact commands that install what is missing.
 *
 * Nothing here runs a build or writes anything.
 *
 * @module dsh-plugin-ascend310b-writing/doctor
 */

import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { describeEnvironment } from '../drivers/index.mjs';
import { condaRoots, detectToolchain, formatToolchain, installCommand } from './toolchain.js';

/** The repository's own entry points, relative to its root. */
const ENTRY_POINTS = {
	converter: 'convert-vuepress.sh',
	buildLog: join('latex', 'book.log'),
	buildPdf: join('latex', 'book.pdf')
};

/**
 * Run the full diagnosis.
 *
 * @param {object} [options] - inputs.
 * @param {string | null} [options.root] - the repository root, or null when unresolved.
 * @param {string} [options.platform] - defaults to `process.platform`.
 * @param {Record<string, string|undefined>} [options.env] - defaults to `process.env`.
 * @param {Record<string, string>} [options.overrides] - per-component executables from config.
 * @returns {object} The structured diagnosis plus its rendered text.
 */
export function diagnose({ root = null, platform = process.platform, env = process.env, overrides = {} } = {}) {
	const components = detectToolchain({ platform, env, overrides });
	const adapter = describeEnvironment({ platform, components });
	const missingRequired = components.filter((component) => component.required && !component.ok).map((component) => component.id);
	const missingOptional = components.filter((component) => !component.required && !component.ok).map((component) => component.id);
	const python = adapter.pythonPlan();
	// Reported whether or not an interpreter was found through it: "conda is
	// installed but nothing answered" is a different problem from "no Python".
	const conda = condaRoots(env);

	const files = root === null
		? null
		: {
				root,
				converter: existsSync(join(root, ENTRY_POINTS.converter)),
				buildLog: existsSync(join(root, ENTRY_POINTS.buildLog)),
				buildPdf: existsSync(join(root, ENTRY_POINTS.buildPdf))
			};

	// A capability is available only when everything it needs is present. The
	// manuscript checks are pure Node, so they never depend on the toolchain.
	const capabilities = [
		{
			id: 'checks',
			label: '正文审计 / 命名 / 交叉产物检查',
			available: true,
			detail: '只需 Node，无需任何外部工具'
		},
		{
			id: 'latex',
			label: 'LaTeX/PDF 构建',
			available: missingRequired.length === 0 && python.kind !== 'missing' && (files === null || files.converter),
			detail: (() => {
				if (missingRequired.length > 0) return `缺少 ${missingRequired.join('、')}`;
				if (python.kind === 'missing') return '缺少 Python 3';
				if (files !== null && !files.converter) return `仓库里没有 ${ENTRY_POINTS.converter}`;
				return '工具链齐备';
			})()
		},
		{
			id: 'site',
			label: 'VuePress 站点构建',
			available: missingOptional.length === 0,
			detail: missingOptional.length === 0 ? 'pnpm 与 node 齐备' : `缺少 ${missingOptional.join('、')}`
		},
		{
			id: 'render-audit',
			label: '渲染体检（读 latex/book.log）',
			available: files === null || files.buildLog,
			detail: files === null ? '未确定仓库根目录' : files.buildLog ? '已有构建日志' : '还没有构建日志，先构建一次'
		}
	];

	const text = formatDiagnosis({ components, adapter, python, conda, missingRequired, missingOptional, capabilities, files, platform });
	return {
		components,
		adapter,
		python,
		conda,
		missingRequired,
		missingOptional,
		capabilities,
		files,
		installCommand: installCommand(platform, missingRequired),
		text
	};
}

/**
 * Render the diagnosis for a person and for the model.
 *
 * @param {object} input - the pieces of {@link diagnose}.
 * @returns {string} The report text.
 */
function formatDiagnosis({ components, adapter, python, conda, missingRequired, missingOptional, capabilities, files, platform }) {
	const lines = [
		formatToolchain({ components, platform, missingRequired }),
		'',
		`环境适配器：${adapter.label}`,
		...adapter.notes.map((note) => `  · ${note}`),
		`  · ${python.note}`
	];
	if (conda.length > 0) {
		lines.push(`  · 检测到 conda/Anaconda：${conda.map((entry) => entry.root).join('、')}`);
		if (python.kind === 'missing') {
			lines.push('    conda 已在，但没有解释器应答：确认它装齐了，或把 config.toolchain.python3 指向某个环境里的 python。');
		}
	}
	lines.push('', '能力');
	for (const capability of capabilities) {
		lines.push(`  [${capability.available ? '可用' : '缺失'}] ${capability.id.padEnd(12)} ${capability.label} — ${capability.detail}`);
	}

	if (files === null) {
		lines.push('', '仓库：未解析到根目录（配置 config.repoRoot 或传 root 参数后再看这一节）。');
	} else {
		lines.push('', `仓库：${files.root}`);
		lines.push(`  ${ENTRY_POINTS.converter}：${files.converter ? '存在' : '不存在'}`);
		lines.push(`  ${ENTRY_POINTS.buildPdf}：${files.buildPdf ? '存在' : '尚未构建'}（${ENTRY_POINTS.buildLog}：${files.buildLog ? '存在' : '不存在'}）`);
	}

	const shell = adapter.shellCommand('echo ok');
	lines.push('', '构建将这样调用 shell：');
	lines.push(shell === null ? '  （没有可用的 shell）' : `  ${shell.command} ${shell.args.map((arg) => (arg.includes(' ') ? JSON.stringify(arg) : arg)).join(' ')}`);

	if (missingRequired.length > 0) {
		const command = installCommand(platform, missingRequired);
		if (command !== null) lines.push('', `装齐缺失的必需组件：${command}`);
	}
	if (missingOptional.length > 0) {
		lines.push('', `可选组件缺失：${missingOptional.join('、')}（只影响站点构建）。`);
	}
	return lines.join('\n');
}
