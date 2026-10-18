/**
 * Read the GitHub Pages deployment result for this repository.
 *
 * A local `pnpm run docs:build` proves the site builds on this machine; it does
 * not prove GitHub Actions produced it. The Pages workflow can fail on a fresh
 * checkout for reasons that never appear locally — an ignored model file, a
 * case-sensitive path, a missing lockfile entry. This module asks `gh` for the
 * latest `Deploy GitHub Pages` run, compares its commit with `origin/main`, and
 * extracts the failing step and message when the run failed.
 *
 * @module dsh-plugin-ascend310b-writing/ci
 */

import { spawnSync } from 'node:child_process';

const WORKFLOW = 'deploy-pages.yml';
const PAGE_SIZE = 8;
const MAX_LOG_LINES = 4;

/** Run a command without throwing, returning status/stdout/stderr. */
function run(command, args, cwd) {
	const result = spawnSync(command, args, {
		cwd,
		encoding: 'utf8',
		timeout: 30_000,
		maxBuffer: 32 * 1024 * 1024
	});
	return {
		ok: result.status === 0,
		status: result.status ?? -1,
		stdout: result.stdout ?? '',
		stderr: result.stderr ?? ''
	};
}

/** Read a git revision. */
function git(root, args) {
	const result = run('git', args, root);
	return result.ok ? result.stdout.trim() : '';
}

/** Strip ANSI escapes and the Actions timestamp prefix. */
function cleanLogLine(line) {
	return line
		.replace(/\u001b\[[0-9;]*m/gu, '')
		// Actions log lines are `job<TAB>step<TAB>timestamp message`; strip every
		// leading tab-separated column before the ISO timestamp.
		.replace(/^(?:[^\t]*\t)+/u, '')
		.replace(/^\d{4}-\d{2}-\d{2}T\S+\s*/u, '')
		.replace(/^##\[(?:error|warning)\]\s*/u, '')
		.trim();
}

/** Keep the error lines that explain why the job stopped. */
function failureLines(log) {
	const lines = log.split(/\r?\n/u);
	const matches = lines
		.map(cleanLogLine)
		.filter((line) =>
			/##\[error\]|错误\s*\d|error:|Error:|failed|失败|不存在|ELIFECYCLE|Process completed with exit code/iu.test(line)
		)
		.filter((line) => line !== '');
	return [...new Set(matches)].slice(-MAX_LOG_LINES);
}

/**
 * Inspect the latest Pages run.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {string} [options.workflow] - workflow file name.
 * @param {number|string} [options.runId] - inspect one run instead of the latest.
 * @returns {{ available: boolean, status: string, conclusion: string, run: object | null, findings: Array<object>, text: string }} Result.
 */
export function collectCiStatus({ root, workflow = WORKFLOW, runId }) {
	const findings = [];
	const head = git(root, ['rev-parse', 'HEAD']);
	const originMain = git(root, ['rev-parse', 'origin/main']);
	const ghVersion = run('gh', ['--version'], root);
	if (!ghVersion.ok) {
		findings.push({
			rule: 'ci-gh-unavailable',
			severity: 'info',
			file: 'package.json',
			line: 1,
			message: '未安装或无法运行 gh；无法读取 GitHub Actions 状态'
		});
		return {
			available: false,
			status: 'unavailable',
			conclusion: '',
			run: null,
			findings,
			text: 'GitHub Pages：无法检查（未找到 gh CLI）。'
		};
	}

	let runs = [];
	if (runId !== undefined && runId !== '') {
		const view = run(
			'gh',
			['run', 'view', String(runId), '--json', 'databaseId,number,headSha,status,conclusion,url,workflowName,createdAt'],
			root
		);
		if (view.ok) {
			try {
				runs = [JSON.parse(view.stdout)];
			} catch {
				runs = [];
			}
		}
	} else {
		const listed = run(
			'gh',
			[
				'run',
				'list',
				'--workflow',
				workflow,
				'--limit',
				String(PAGE_SIZE),
				'--json',
				'databaseId,number,headSha,status,conclusion,url,workflowName,createdAt'
			],
			root
		);
		if (listed.ok) {
			try {
				runs = JSON.parse(listed.stdout);
			} catch {
				runs = [];
			}
		} else {
			findings.push({
				rule: 'ci-query-failed',
				severity: 'warning',
				file: 'package.json',
				line: 1,
				message: `gh 查询失败：${(listed.stderr || listed.stdout).trim().slice(0, 300)}`
			});
		}
	}

	const target = runs.find((entry) => entry.headSha === originMain) ?? runs[0] ?? null;
	if (target === null) {
		findings.push({
			rule: 'ci-run-missing',
			severity: 'warning',
			file: 'package.json',
			line: 1,
			message: '没有找到 GitHub Pages 工作流运行记录'
		});
		return {
			available: true,
			status: 'missing',
			conclusion: '',
			run: null,
			findings,
			text: 'GitHub Pages：没有找到工作流运行记录。'
		};
	}

	const isHead = target.headSha === head;
	const isRemote = target.headSha === originMain;
	if (target.status !== 'completed') {
		findings.push({
			rule: 'ci-pages-pending',
			severity: 'info',
			file: 'package.json',
			line: 1,
			message: `GitHub Pages 仍在运行：run #${target.number}（${target.status}）`
		});
	} else if (target.conclusion !== 'success') {
		const log = run('gh', ['run', 'view', String(target.databaseId), '--log-failed'], root);
		const lines = failureLines(`${log.stdout}\n${log.stderr}`);
		const detail = lines.length > 0 ? lines.join(' | ') : '未从日志中提取到错误行';
		findings.push({
			rule: 'ci-pages-failed',
			severity: 'error',
			file: 'package.json',
			line: 1,
			message: `GitHub Pages run #${target.number} ${target.conclusion}（${target.headSha.slice(0, 7)}）：${detail}`
		});
	}
	if (head !== '' && originMain !== '' && head !== originMain) {
		findings.push({
			rule: 'ci-remote-behind',
			severity: 'info',
			file: 'package.json',
			line: 1,
			message: `本地 HEAD ${head.slice(0, 7)} 尚未推送；最新远端构建对应 ${originMain.slice(0, 7)}`
		});
	}

	const status = target.status === 'completed' ? target.conclusion : target.status;
	const lines = [
		`GitHub Pages：run #${target.number} ${status}（${target.headSha.slice(0, 7)}）`,
		`  ${target.url}`,
		`  对应提交：${isHead ? '本地 HEAD' : isRemote ? 'origin/main' : '其他提交'}`
	];
	if (status !== 'success' && status !== 'in_progress' && status !== 'queued') {
		const failed = findings.find((finding) => finding.rule === 'ci-pages-failed');
		if (failed !== undefined) lines.push(`  失败原因：${failed.message.replace(/^.*：/u, '')}`);
	}
	if (findings.some((finding) => finding.rule === 'ci-remote-behind')) {
		lines.push('  注意：本地还有未推送提交，远端构建不代表这些提交。');
	}
	return {
		available: true,
		status,
		conclusion: target.conclusion ?? '',
		run: {
			number: target.number,
			databaseId: target.databaseId,
			headSha: target.headSha,
			url: target.url,
			workflowName: target.workflowName,
			createdAt: target.createdAt
		},
		findings,
		text: lines.join('\n')
	};
}
