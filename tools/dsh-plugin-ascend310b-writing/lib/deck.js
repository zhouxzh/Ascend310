/**
 * Presentation-deck layout and consistency checks.
 *
 * The Marp theme pins every slide to 1280x720 with `overflow: hidden`, so a
 * slide that carries more than fits does not scroll and does not wrap onto the
 * next slide — the bottom of it simply disappears. Nothing in the Markdown looks
 * wrong when that happens, which is why it needs a measurement rather than a
 * reviewer's eye.
 *
 * The rules are deliberately structural. They cannot tell whether an argument
 * is coherent; they can tell that a slide is 35 lines of code in a 720px frame,
 * that a table is nine columns wide, or that the deck claims a compute tier the
 * repository does not use.
 *
 * @module dsh-plugin-ascend310b-writing/deck
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The theme's usable height is 720 - 62 (top) - 84 (bottom) = 574px.
 * The estimate below deliberately keeps 640px of headroom because it is a
 * coarse source-level precheck; real clipping is measured in a browser by
 * scripts/check_presentation_render.mjs and the deck-render tool.
 */
export const SLIDE_HEIGHT_BUDGET = 640;

/** Capacity limits for the current 26px body / 21px table theme. */
// These are editorial limits, not a clipping prediction. The browser render
// check is authoritative for clipping; these limits stop a page before it
// becomes unreadable on a projector.
export const LIMITS = {
	codeLines: 22,
	tableColumns: 7,
	tableRows: 10,
	bullets: 8,
	slideCjk: 320
};

/** Estimated heights of one element, in theme pixels. */
const HEIGHT = { heading: 60, bullet: 34, codeLine: 22, tableRow: 40, paragraph: 44, image: 300 };

/**
 * Split a deck into slides, tracking the source line each slide starts on.
 *
 * The first `---` pair is YAML frontmatter and is skipped. Separators inside a
 * fenced block are code, not pagination.
 *
 * @param {string} text - the deck source.
 * @returns {Array<{ startLine: number, lines: string[] }>} The slides.
 */
export function splitSlides(text) {
	const lines = text.split(/\r?\n/u);
	let bodyStart = 0;
	if (lines[0]?.trim() === '---') {
		for (let index = 1; index < lines.length; index += 1) {
			if (lines[index].trim() === '---') {
				bodyStart = index + 1;
				break;
			}
		}
	}
	const slides = [];
	let current = [];
	let startLine = bodyStart + 1;
	let inFence = false;
	for (let index = bodyStart; index < lines.length; index += 1) {
		const line = lines[index];
		if (/^\s*(?:```|~~~)/u.test(line)) inFence = !inFence;
		if (!inFence && line.trim() === '---') {
			slides.push({ startLine, lines: current });
			current = [];
			startLine = index + 2;
			continue;
		}
		current.push(line);
	}
	if (current.length > 0) slides.push({ startLine, lines: current });
	return slides;
}

/**
 * Measure one slide.
 *
 * @param {string[]} lines - the slide's source lines.
 * @returns {object} Element counts, an estimated height, and the CJK count.
 */
export function measureSlide(lines) {
	let inFence = false;
	let bullets = 0;
	let codeLines = 0;
	let tableRows = 0;
	let tableColumns = 0;
	let images = 0;
	let paragraphs = 0;
	let heading = null;
	for (const line of lines) {
		if (/^\s*(?:```|~~~)/u.test(line)) {
			inFence = !inFence;
			continue;
		}
		if (inFence) {
			codeLines += 1;
			continue;
		}
		const headingMatch = /^\s*#{1,3}\s+(.+?)\s*$/u.exec(line);
		if (headingMatch !== null) {
			heading ??= headingMatch[1];
			continue;
		}
		if (/^\s*(?:[-*+]|\d+[.)])\s+/u.test(line)) bullets += 1;
		if (/^\s*\|/u.test(line)) {
			tableRows += 1;
			tableColumns = Math.max(tableColumns, (line.match(/\|/gu) ?? []).length - 1);
			continue;
		}
		if (/!\[[^\]]*\]\(|<img\b/iu.test(line)) images += 1;
		const trimmed = line.trim();
		if (trimmed !== '' && !/^<!--/u.test(trimmed) && !/^<div/iu.test(trimmed) && !/^<a\b/iu.test(trimmed)) {
			paragraphs += 1;
		}
	}
	const cjk = (lines.join('\n').match(/[\u4e00-\u9fff]/gu) ?? []).length;
	const height =
		HEIGHT.heading +
		bullets * HEIGHT.bullet +
		codeLines * HEIGHT.codeLine +
		tableRows * HEIGHT.tableRow +
		paragraphs * HEIGHT.paragraph +
		Math.min(images, 1) * HEIGHT.image;
	return { heading, bullets, codeLines, tableRows, tableColumns, images, paragraphs, cjk, height };
}

/**
 * Audit one deck's slides and return its problems.
 *
 * @param {object} options - inputs.
 * @param {string} options.file - the repository-relative path.
 * @param {string} options.text - the deck source.
 * @returns {Array<object>} Findings.
 */
export function auditDeckText({ file, text }) {
	const findings = [];
	for (const [index, slide] of splitSlides(text).entries()) {
		const measured = measureSlide(slide.lines);
		const where = measured.heading === null ? `第 ${index + 1} 页` : `第 ${index + 1} 页「${measured.heading}」`;
		if (measured.height > SLIDE_HEIGHT_BUDGET) {
			findings.push({
				rule: 'deck-slide-overflow',
				severity: 'error',
				file,
				line: slide.startLine,
				message:
					`${where}估算高度 ${measured.height}px 超过 ${SLIDE_HEIGHT_BUDGET}px 安全高度，` +
					`底部内容会被 overflow:hidden 裁掉（代码 ${measured.codeLines} 行、表 ${measured.tableRows} 行、要点 ${measured.bullets} 条）`
			});
		}
		if (measured.codeLines > LIMITS.codeLines) {
			findings.push({
				rule: 'deck-code-too-long',
				severity: 'warning',
				file,
				line: slide.startLine,
				message: `${where}代码 ${measured.codeLines} 行，超过 ${LIMITS.codeLines} 行；改成关键片段并链接到 samples/ 全文`
			});
		}
		if (measured.tableColumns > LIMITS.tableColumns || measured.tableRows > LIMITS.tableRows) {
			findings.push({
				rule: 'deck-table-too-wide',
				severity: 'warning',
				file,
				line: slide.startLine,
				message:
					`${where}表格 ${measured.tableColumns} 列 × ${measured.tableRows} 行，超过 ` +
					`${LIMITS.tableColumns} 列 / ${LIMITS.tableRows} 行；拆页或改成对照格式`
			});
		}
		if (measured.cjk > LIMITS.slideCjk) {
			findings.push({
				rule: 'deck-slide-overflow',
				severity: 'error',
				file,
				line: slide.startLine,
				message: `${where}正文 ${measured.cjk} 个汉字，超过单页 ${LIMITS.slideCjk} 字上限，需要拆页`
			});
		}
	}
	if (/20\s*T|310B1/u.test(text) && !/8\s*T|310B4/u.test(text)) {
		findings.push({
			rule: 'deck-tier-claim',
			severity: 'error',
			file,
			line: 1,
			message: '课件只写了 20T/310B1 口径，但本仓库的实测范围是 Ascend 310B4 / 8T；请标注算力档位适用范围'
		});
	}
	return findings;
}

/**
 * Audit every deck, plus the README ordering.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.settings - the resolved layout.
 * @returns {{ findings: Array<object>, decks: number, slides: number }} The result.
 */
export function collectDeckFindings({ root, settings }) {
	const part = settings.parts.find((entry) => entry.key === 'presentation');
	if (part === undefined) return { findings: [], decks: 0, slides: 0 };
	const directory = join(root, part.dir);
	if (!existsSync(directory)) return { findings: [], decks: 0, slides: 0 };

	const findings = [];
	let decks = 0;
	let slides = 0;
	const names = [];
	for (const name of readdirSafe(directory)) {
		if (!/^\d{2}-.*\.md$/iu.test(name)) continue;
		names.push(name);
		const file = `${part.dir}/${name}`;
		let text;
		try {
			text = readFileSync(join(root, file), 'utf8');
		} catch {
			continue;
		}
		decks += 1;
		slides += splitSlides(text).length;
		findings.push(...auditDeckText({ file, text }));
	}

	// Case decks carry the visual material; a deck about a camera pipeline with
	// one diagram is not teaching the pipeline.
	for (const name of names.filter((entry) => /^(?:2[0-8])-case/u.test(entry))) {
		const text = readFileSync(join(root, part.dir, name), 'utf8');
		const images = (text.match(/!\[[^\]]*\]\(|<img\b/giu) ?? []).length;
		if (images < 1) {
			findings.push({
				rule: 'deck-visual-missing',
				severity: 'info',
				file: `${part.dir}/${name}`,
				line: 1,
				message: '案例课件没有图；建议补流水线、源码地图或板端证据图'
			});
		}
	}

	// README order is the reader's order; the file numbers are the sort order.
	const readmeFindings = checkReadmeOrder({ root, part, names });
	findings.push(...readmeFindings);
	findings.sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line);
	return { findings, decks, slides };
}

/** Compare the README table order with the file-number order. */
function checkReadmeOrder({ root, part, names }) {
	const readmePath = join(root, part.dir, 'README.md');
	if (!existsSync(readmePath)) return [];
	let text;
	try {
		text = readFileSync(readmePath, 'utf8');
	} catch {
		return [];
	}
	// Only the catalogue table defines the reading order; the local-preview list
	// repeats a few links further down and must not be counted.
	const catalogue = text.split(/^## /mu).find((section) => section.startsWith('课件目录')) ?? text;
	const listed = [...catalogue.matchAll(/presentation\/(\d{2}-[a-z0-9-]+)\.html/giu)].map((match) => `${match[1]}.md`);
	if (listed.length === 0) return [];
	const sorted = [...names].sort();
	if (listed.join('|') === sorted.join('|')) return [];
	return [
		{
			rule: 'deck-readme-order',
			severity: 'warning',
			file: `${part.dir}/README.md`,
			line: 1,
			message: `README 目录顺序与文件编号不一致：README ${listed.join(' → ')}；编号 ${sorted.join(' → ')}`
		}
	];
}

/** Read a directory's entries, or an empty list. */
function readdirSafe(directory) {
	try {
		return readdirSync(directory);
	} catch {
		return [];
	}
}
