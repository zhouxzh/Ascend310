/** Real-browser layout checks for a local VuePress page. */

import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

/**
 * Run the repository's Puppeteer page-layout checker.
 *
 * @param {object} options - checker options.
 * @param {string} options.root - repository root.
 * @param {string} options.url - local page URL.
 * @param {string} [options.viewports] - comma-separated viewport list.
 * @returns {{available:boolean, ok:boolean, text:string, issues:string[], results:object[]}}
 */
export function collectPageLayoutFindings({ root, url, viewports = '1440x1000,390x844' }) {
	const script = join(root, 'scripts', 'check_page_layout.mjs');
	if (!existsSync(script)) {
		return {
			available: false,
			ok: false,
			text: '页面排版检查不可用：缺少 scripts/check_page_layout.mjs。',
			issues: ['page-layout-script-missing'],
			results: []
		};
	}
	const result = spawnSync(
		process.env.DSH_NODE_BIN ?? 'node',
		[script, '--url', url, '--viewports', viewports, '--json'],
		{ cwd: root, encoding: 'utf8', timeout: 180_000, maxBuffer: 16 * 1024 * 1024 }
	);
	if (result.error) {
		return {
			available: false,
			ok: false,
			text: `页面排版检查不可用：${result.error.message}`,
			issues: ['page-layout-unavailable'],
			results: []
		};
	}
	let payload;
	try {
		payload = JSON.parse(result.stdout);
	} catch {
		const detail = `${result.stderr ?? ''}\n${result.stdout ?? ''}`.trim().slice(0, 500);
		return {
			available: false,
			ok: false,
			text: `页面排版检查没有返回 JSON：${detail || '无输出'}`,
			issues: ['page-layout-invalid-output'],
			results: []
		};
	}
	const issues = (payload.hardFailures ?? []).map((issue) => {
		if (issue.type === 'page-horizontal-overflow') {
			return `页面横向溢出：${issue.viewport.width}x${issue.viewport.height}`;
		}
		return `元素越界：${issue.element ?? issue.type} ${issue.left ?? ''}..${issue.right ?? ''}`;
	});
	const resultCount = Array.isArray(payload.results) ? payload.results.length : 0;
	return {
		available: true,
		ok: payload.ok === true && issues.length === 0,
		text: payload.ok === true
			? `页面排版检查通过：${resultCount} 个视口，0 个硬问题。人工视觉验收仍需完成。`
			: `页面排版检查失败：${issues.length} 个硬问题。`,
		issues,
		results: payload.results ?? []
	};
}
