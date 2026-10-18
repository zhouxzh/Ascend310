/**
 * Audit the generated presentation HTML, one slide at a time.
 *
 * The Markdown audit knows what the author wrote; this module knows what Marp
 * actually emitted. It reads every `<section>` in the built deck and measures
 * the content a viewer will see: text volume, code lines, table shape, images.
 * Academic decks need a stable rhythm — a page with three bullets next to a
 * page with a 200-character wall reads as an accident, even when both fit.
 *
 * Density is expressed in CJK-equivalent units: one Chinese character = 1,
 * one Latin word = 2. Ranges are calibrated against the current 26 decks:
 *
 *   prose   61–155 units  -> target 70–280
 *   table   50–244 units  -> target 90–340, 2–10 rows, 2–7 columns
 *   code    80–182 units  -> target 70–320, 4–30 lines
 *   visual  18–125 units  -> target at least 15 units of caption/context
 *
 * @module dsh-plugin-ascend310b-writing/deck-web
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';

const PROSE_MIN = 70;
const PROSE_MAX = 280;
const TABLE_MIN = 90;
const TABLE_MAX = 340;
const TABLE_ROWS_MAX = 10;
const TABLE_COLS_MAX = 7;
const CODE_MIN_UNITS = 70;
const CODE_MAX_UNITS = 320;
const CODE_MIN_LINES = 4;
const CODE_MAX_LINES = 30;
const VISUAL_MIN = 15;
const UNEVEN_RATIO = 2.8;
const UNEVEN_GAP = 90;

const strip = (html) =>
	html
		.replace(/<header\b[\s\S]*?<\/header>/gu, ' ')
		.replace(/<footer\b[\s\S]*?<\/footer>/gu, ' ')
		.replace(/<script\b[\s\S]*?<\/script>/gu, ' ')
		.replace(/<style\b[\s\S]*?<\/style>/gu, ' ')
		.replace(/<[^>]+>/gu, ' ')
		.replace(/&[a-z#0-9]+;/giu, ' ')
		.replace(/\s+/gu, ' ')
		.trim();

/** Resolve the directory that holds the built presentation HTML. */
function presentationDir(root, settings) {
	const distIndex = settings.site?.distIndex ?? 'src/.vuepress/dist/index.html';
	const dist = join(dirname(join(root, distIndex)), 'presentation');
	if (existsSync(dist)) return dist;
	return join(root, 'src', '.vuepress', 'public', 'presentation');
}

/** Count CJK-equivalent content units. */
function unitsOf(text) {
	const cjk = (text.match(/[\u4e00-\u9fff]/gu) ?? []).length;
	const latin = (text.match(/[A-Za-z][A-Za-z0-9_.+-]*/gu) ?? []).length;
	return cjk + latin * 2;
}

/**
 * Collect one record per slide in the generated decks.
 *
 * @param {string} directory - built presentation directory.
 * @returns {Array<object>} Slide records.
 */
function readSlides(directory) {
	const slides = [];
	const files = readdirSync(directory).filter((name) => /^\d{2}-.*\.html$/u.test(name));
	for (const file of files) {
		const html = readFileSync(join(directory, file), 'utf8');
		for (const match of html.matchAll(/<section\b([^>]*)>([\s\S]*?)<\/section>/gu)) {
			const attributes = match[1];
			const body = match[2];
			const className = /class="([^"]*)"/u.exec(attributes)?.[1] ?? '';
			const theme = /data-theme="([^"]*)"/u.exec(attributes)?.[1] ?? '';
			const page = Number(/data-marpit-pagination="(\d+)"/u.exec(attributes)?.[1] ?? 0);
			const text = strip(body);
			const units = unitsOf(text);
			const code = [...body.matchAll(/<pre\b[\s\S]*?<\/pre>/gu)]
				.map((entry) => strip(entry[0]))
				.join('\n');
			const codeLines = code === '' ? 0 : code.split('\n').length;
			const table = /<table\b/u.test(body);
			const rows = (body.match(/<tr\b/gu) ?? []).length;
			const columns = (body.match(/<th\b/gu) ?? []).length;
			const images = (body.match(/<img\b/gu) ?? []).length;
			const bullets = (body.match(/<li\b/gu) ?? []).length;
			const kind = /cover|title/u.test(className)
				? 'cover'
				: /section/u.test(className)
					? 'section'
					: /<pre\b/u.test(body)
						? 'code'
						: table
							? 'table'
							: images > 0
								? 'visual'
								: 'prose';
			slides.push({ file, page, className, theme, kind, units, codeLines, rows, columns, images, bullets });
		}
	}
	return slides;
}

/**
 * Audit generated slide density and theme identity.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {object} options.settings - resolved layout.
 * @returns {{ findings: Array<object>, available: boolean, decks: number, pages: number }} The result.
 */
export function collectDeckWebFindings({ root, settings }) {
	const directory = presentationDir(root, settings);
	if (!existsSync(directory)) {
		return { findings: [], available: false, decks: 0, pages: 0 };
	}
	let slides;
	try {
		slides = readSlides(directory);
	} catch {
		return { findings: [], available: false, decks: 0, pages: 0 };
	}
	const findings = [];
	const decks = new Set(slides.map((slide) => slide.file));
	for (const slide of slides) {
		const file = relative(root, join(directory, slide.file)).split(sep).join('/');
		if (slide.theme !== '' && slide.theme !== 'ascend310') {
			findings.push({
				rule: 'deck-web-theme-drift',
				severity: 'warning',
				file,
				line: slide.page,
				message: `第 ${slide.page} 页使用主题 ${slide.theme}，不是统一的 ascend310`
			});
		}
		if (['cover', 'section'].includes(slide.kind)) continue;
		if (slide.kind === 'prose') {
			if (slide.units < PROSE_MIN) {
				findings.push({
					rule: 'deck-web-density-low',
					severity: 'warning',
					file,
					line: slide.page,
					message: `第 ${slide.page} 页正文 ${slide.units} 单位，低于学术报告下限 ${PROSE_MIN}；补一个判断、证据或课堂问题`
				});
			}
			if (slide.units > PROSE_MAX) {
				findings.push({
					rule: 'deck-web-density-high',
					severity: 'warning',
					file,
					line: slide.page,
					message: `第 ${slide.page} 页正文 ${slide.units} 单位，超过上限 ${PROSE_MAX}；拆页或改成表格`
				});
			}
			continue;
		}
		if (slide.kind === 'table') {
			const reasons = [];
			if (slide.units < TABLE_MIN) reasons.push(`文字 ${slide.units} 单位 < ${TABLE_MIN}`);
			if (slide.rows > TABLE_ROWS_MAX) reasons.push(`${slide.rows} 行 > ${TABLE_ROWS_MAX}`);
			if (slide.columns > TABLE_COLS_MAX) reasons.push(`${slide.columns} 列 > ${TABLE_COLS_MAX}`);
			if (slide.units > TABLE_MAX) reasons.push(`文字 ${slide.units} 单位 > ${TABLE_MAX}`);
			if (reasons.length > 0) {
				findings.push({
					rule: slide.units < TABLE_MIN ? 'deck-web-density-low' : 'deck-web-density-high',
					severity: 'warning',
					file,
					line: slide.page,
					message: `第 ${slide.page} 页表格失衡：${reasons.join('，')}`
				});
			}
			continue;
		}
		if (slide.kind === 'code') {
			if (slide.codeLines > CODE_MAX_LINES || slide.units > CODE_MAX_UNITS) {
				findings.push({
					rule: 'deck-web-density-high',
					severity: 'warning',
					file,
					line: slide.page,
					message: `第 ${slide.page} 页代码 ${slide.codeLines} 行 / ${slide.units} 单位，超过 ${CODE_MAX_LINES} 行或 ${CODE_MAX_UNITS} 单位`
				});
			}
			if (slide.codeLines < CODE_MIN_LINES && slide.units < CODE_MIN_UNITS) {
				findings.push({
					rule: 'deck-web-density-low',
					severity: 'warning',
					file,
					line: slide.page,
					message: `第 ${slide.page} 页代码只有 ${slide.codeLines} 行且说明不足 ${CODE_MIN_UNITS} 单位`
				});
			}
			continue;
		}
		if (slide.kind === 'visual' && slide.units < VISUAL_MIN) {
			findings.push({
				rule: 'deck-web-density-low',
				severity: 'warning',
				file,
				line: slide.page,
				message: `第 ${slide.page} 页只有图片、说明不足 ${VISUAL_MIN} 单位；图片要配一句它能证明什么`
			});
		}
	}

	// A deck should not swing between a near-empty page and a dense one. Only
	// prose and table pages are compared; code and screenshots have their own
	// natural volumes.
	for (const deck of decks) {
		const list = slides
			.filter((slide) => slide.file === deck && ['prose', 'table'].includes(slide.kind))
			.map((slide) => slide.units);
		if (list.length < 4) continue;
		const min = Math.min(...list);
		const max = Math.max(...list);
		if (min > 0 && max / min >= UNEVEN_RATIO && max - min >= UNEVEN_GAP) {
			findings.push({
				rule: 'deck-web-density-uneven',
				severity: 'warning',
				file: relative(root, join(directory, deck)).split(sep).join('/'),
				line: 1,
				message: `正文/表格页密度跨度 ${min}–${max} 单位（${(max / min).toFixed(1)} 倍）；相邻页应保持相近的信息量`
			});
		}
	}

	findings.sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line || left.rule.localeCompare(right.rule));
	return { findings, available: true, decks: decks.size, pages: slides.length };
}

/**
 * Summarise a generated-deck audit in one line.
 *
 * @param {object} web - the result of {@link collectDeckWebFindings}.
 * @returns {string} The summary.
 */
export function describeDeckWeb(web) {
	if (!web.available) return '网页课件：尚未构建。';
	return `网页课件：${web.decks} 份、${web.pages} 页，密度/布局问题 ${web.findings.length} 条。`;
}
