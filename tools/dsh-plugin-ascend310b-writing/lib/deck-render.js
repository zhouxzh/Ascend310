/**
 * Real browser layout check for the built Marp decks.
 *
 * `deck.js` estimates height from Markdown and `deck-web.js` measures text
 * volume in the HTML, but neither can prove that a slide does not clip. This
 * module runs the repository's Puppeteer checker in JSON mode (optionally for a
 * single deck) and turns its issues into plugin findings:
 *
 *   deck-render-overflow
 *   deck-render-image-missing
 *   deck-render-no-decks
 *   deck-render-unavailable
 *
 * @module dsh-plugin-ascend310b-writing/deck-render
 */

import { spawnSync } from 'node:child_process';

/**
 * Run the real render check.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {string} [options.deck] - optional deck basename, e.g. `10-chapter1`.
 * @returns {{ available: boolean, decks: number, pages: number, findings: Array<object>, text: string }} Result.
 */
export function collectDeckRenderFindings({ root, deck }) {
	const args = ['scripts/check_presentation_render.mjs', '--json'];
	if (typeof deck === 'string' && deck.trim() !== '') {
		args.push('--deck', deck.trim());
	}
	const result = spawnSync(process.env.DSH_NODE_BIN ?? 'node', args, {
		cwd: root,
		encoding: 'utf8',
		timeout: 240_000,
		maxBuffer: 16 * 1024 * 1024
	});
	if (result.error) {
		return {
			available: false,
			decks: 0,
			pages: 0,
			findings: [
				{
					rule: 'deck-render-unavailable',
					severity: 'warning',
					file: 'scripts/check_presentation_render.mjs',
					line: 1,
					message: `无法运行真实渲染检查：${result.error.message}`
				}
			],
			text: '课件真实渲染：无法运行（缺少 node 或 Puppeteer/Chrome）。'
		};
	}
	let payload;
	try {
		payload = JSON.parse((result.stdout ?? '').trim());
	} catch {
		const detail = ((result.stderr || '') + (result.stdout || '')).trim().slice(0, 400);
		return {
			available: false,
			decks: 0,
			pages: 0,
			findings: [
				{
					rule: 'deck-render-unavailable',
					severity: 'warning',
					file: 'scripts/check_presentation_render.mjs',
					line: 1,
					message: `真实渲染检查没有返回 JSON：${detail || '无输出'}`
				}
			],
			text: '课件真实渲染：检查脚本没有返回可解析结果。'
		};
	}

	const findings = [];
	for (const issue of payload.issues ?? []) {
		const rule =
			issue.type === 'image-missing'
				? 'deck-render-image-missing'
				: issue.type === 'no-decks'
					? 'deck-render-no-decks'
					: 'deck-render-overflow';
		findings.push({
			rule,
			severity: issue.type === 'no-decks' ? 'warning' : 'error',
			file: `src/.vuepress/public/presentation/${issue.file}`,
			line: issue.page,
			message: `第 ${issue.page} 页${issue.detail}`
		});
	}
	const decks = Number(payload.files ?? 0);
	const pages = Number(payload.slides ?? 0);
	return {
		available: true,
		decks,
		pages,
		findings,
		text:
			`课件真实渲染：${decks} 份、${pages} 页，问题 ${findings.length} 条` +
			(deck ? `（仅 ${deck}）` : '') +
			'。'
	};
}
