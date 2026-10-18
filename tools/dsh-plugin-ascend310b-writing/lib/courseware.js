/**
 * Courseware standards: rules a teaching deck has to meet before it can be
 * presented, independent of whether it fits on a slide.
 *
 * `deck.js` answers "does this page fit". This module answers "is this page
 * traceable, accessible and self-describing", and its rules are taken from two
 * open-source projects rather than invented here:
 *
 * - **markdownlint** (`MD040`, `MD045`, `MD034`): a fenced block declares its
 *   language, an image carries alt text, and a URL is a link rather than bare
 *   text. Its `accessibility` tag groups `MD045` and `MD059`.
 * - **Marp CLI / Marp Core**: decks support presenter notes (`--notes`,
 *   `--pdf-notes`, presenter view), deck metadata through global directives,
 *   and a 16:9 slide is 1280x720. Marp's own docs warn that auto-scaling is
 *   horizontal only — "the scaled element still may stick out from bottom of
 *   slide" — which is why vertical fit is measured separately in `deck.js`.
 *
 * On top of those, this repository requires traceability: a deck that teaches a
 * chapter must say which chapter, and a code slide must say which file the code
 * came from, otherwise the courseware drifts away from the manuscript and the
 * samples.
 *
 * @module dsh-plugin-ascend310b-writing/courseware
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { splitSlides } from './deck.js';
import { join } from 'node:path';

/** Smallest content font that stays legible from the back of a classroom. */
export const MIN_CONTENT_FONT_PX = 20;
export const MIN_BODY_FONT_PX = 26;

/** A deck should teach at least this share of its slides with a note. */
const MIN_NOTE_SHARE = 0.2;

/**
 * Check one deck's courseware standards.
 *
 * @param {object} options - inputs.
 * @param {string} options.file - repository-relative deck path.
 * @param {string} options.text - deck source.
 * @returns {Array<object>} Findings.
 */
export function auditCoursewareText({ file, text }) {
	const findings = [];
	const lines = text.split(/\r?\n/u);
	const body = stripFrontmatter(text);
	const frontmatter = text.slice(0, text.length - body.length);

	// --- density and teaching structure ------------------------------------
	// A deck is not a document: every slide must carry one idea, and a slide
	// that carries almost nothing wastes a projector frame. Placeholder decks
	// (the `presentation-scaffold` marker) are reported as such instead of being
	// counted as finished teaching material.
	const isScaffold = /presentation-scaffold:/u.test(text);
	const isLegacy = /\/legacy-|archive/u.test(file);
	if (isScaffold) {
		findings.push({
			rule: 'deck-scaffold',
			severity: 'info',
			file,
			line: 1,
			message: '脚手架课件（presentation-scaffold）；补齐内容前不计为完成件'
		});
	} else if (!isLegacy) {
		const slides = splitSlides(text);
		const cjk = (text.match(/[\u4e00-\u9fff]/gu) ?? []).length;
		if (slides.length < 6 || cjk < 800) {
			findings.push({
				rule: 'deck-content-thin',
				severity: 'warning',
				file,
				line: 1,
				message: `课件只有 ${slides.length} 页、${cjk} 字；信息密度过低，补足工程细节或合并为更少的实质页`
			});
		}
		if (!/_class:\s*cover/u.test(text)) {
			findings.push({
				rule: 'deck-cover-missing',
				severity: 'warning',
				file,
				line: 1,
				message: '缺少封面页（<!-- _class: cover -->）'
			});
		}
		// A closing slide does not have to be titled "小结": "课堂交付",
		// "本讲结论边界" and "下一步" all close the lesson. Only the last two
		// slides are considered, so an early mention cannot satisfy the rule.
		const closingPattern = /小结|总结|回顾|课堂任务|思考题|练习|结论|交付|证据边界|下一步|检查清单|使用顺序/u;
		const closing = slides
			.slice(-2)
			.some((slide) => closingPattern.test(/^#{1,3}\s+(.+?)(?:\s+\{#[^}]+\})?\s*$/mu.exec(slide.lines.join('\n'))?.[1] ?? ''));
		if (!closing) {
			findings.push({
				rule: 'deck-summary-missing',
				severity: 'warning',
				file,
				line: 1,
				message: '最后两页没有小结、课堂交付或结论边界页，学生没有收束与自检环节'
			});
		}
		slides.forEach((slide, index) => {
			const body = slide.lines.join('\n');
			const className = /_class:\s*(\w+)/u.exec(body)?.[1] ?? '';
			if (['cover', 'section'].includes(className)) return;
			const heading = /^#{1,3}\s+(.+?)(?:\s+\{#[^}]+\})?\s*$/mu.exec(body)?.[1]?.trim() ?? '';
			if ([...heading].length > 30) {
				findings.push({
					rule: 'deck-title-too-long',
					severity: 'warning',
					file,
					line: slide.startLine,
					message: `第 ${index + 1} 页标题 ${[...heading].length} 字，超过 30 字：${heading.slice(0, 30)}`
				});
			}
			const hasCode = /^\s*(?:```|~~~)/mu.test(body);
			const hasTable = /^\s*\|/mu.test(body);
			const hasImage = /!\[|<img\b/iu.test(body);
			if (hasCode || hasTable || hasImage) return;
			const prose = body.replace(/^#{1,3}\s+.*$/mu, '').replace(/<!--[\s\S]*?-->/gu, '');
			const proseCjk = (prose.match(/[\u4e00-\u9fff]/gu) ?? []).length;
			if (proseCjk < 40) {
				findings.push({
					rule: 'deck-slide-underfull',
					severity: 'warning',
					file,
					line: slide.startLine,
					message: `第 ${index + 1} 页正文只有 ${proseCjk} 字且没有代码/表格/图片，信息密度过低：${heading.slice(0, 30)}`
				});
			}
		});
	}
	// --- visual identity ---------------------------------------------------
	// All decks share the Metropolis-inspired `ascend310` theme. A deck that
	// points at gaia/default will render with a different palette and footer,
	// which is exactly how the courseware set starts to look assembled from
	// unrelated templates.
	if (!isLegacy && !/^theme:\s*ascend310\s*$/mu.test(text)) {
		findings.push({
			rule: 'deck-theme-unsupported',
			severity: 'warning',
			file,
			line: 1,
			message: '课件没有使用统一主题 `ascend310`（Metropolis 风格）；请改回 theme: ascend310'
		});
	}
	// --- cover semantics ---------------------------------------------------
	// A cover should state what the audience will learn, not announce its own
	// administrative identity. "理论章节课件 · 教师授课版" tells a student
	// nothing and reads like a template left in the deck.
	if (!isLegacy) {
		const firstSlide = splitSlides(text)[0]?.lines.join('\n') ?? '';
		if (/教师授课版|教师版|授课版|理论章节课件|案例课件|附录课件|课程导览/u.test(firstSlide)) {
			findings.push({
				rule: 'deck-cover-meta-label',
				severity: 'warning',
				file,
				line: 1,
				message: '封面出现“教师授课版/理论章节课件”这类自我标注；封面应写内容承诺，不要告诉读者“这是课件”'
			});
		}
		if (!/class="byline"|[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}/u.test(firstSlide)) {
			findings.push({
				rule: 'deck-cover-byline-missing',
				severity: 'warning',
				file,
				line: 1,
				message: '封面没有作者与邮箱；请在标题下保留 byline'
			});
		}
	}
	// --- Marp metadata ------------------------------------------------------
	for (const required of ['marp', 'theme', 'paginate', 'size']) {
		if (!new RegExp(`^${required}:`, 'mu').test(frontmatter)) {
			findings.push({
				rule: 'deck-metadata-missing',
				severity: 'warning',
				file,
				line: 1,
				message: `frontmatter 缺少 ${required}：Marp 靠它确定渲染方式、主题、页码与画布尺寸`
			});
		}
	}

	// --- traceability -------------------------------------------------------
	const sources = [...body.matchAll(/(?:src\/(?:book|experiment|appendix)\/[\w-]+\.md|samples\/[\w./-]+)/gu)].map((match) => match[0]);
	if (sources.length === 0) {
		findings.push({
			rule: 'deck-source-missing',
			severity: 'error',
			file,
			line: 1,
			message: '课件没有标明对应的教材正文或样例路径；课件必须可追溯到 src/ 或 samples/ 的源'
		});
	}

	// --- per-slide accessibility -------------------------------------------
	let codeBlocks = 0;
	let codeWithSource = 0;
	let inFence = false;
	let fenceStart = 0;
	let lastFenceEnd = 0;
	for (const [index, line] of lines.entries()) {
		const lineNumber = index + 1;
		if (/^\s*(?:```|~~~)/u.test(line)) {
			if (!inFence) {
				inFence = true;
				fenceStart = lineNumber;
				const info = line.trim().replace(/^(?:```|~~~)\s*/u, '');
				if (info === '') {
					findings.push({
						rule: 'deck-code-no-language',
						severity: 'warning',
						file,
						line: lineNumber,
						message: '代码块没有语言标记（markdownlint MD040）；Marp 靠它选择高亮规则'
					});
				}
				codeBlocks += 1;
			} else {
				inFence = false;
				lastFenceEnd = lineNumber;
				// The three lines around the block are where a source path belongs.
				const window = lines.slice(Math.max(0, fenceStart - 4), Math.min(lines.length, lastFenceEnd + 3)).join('\n');
				if (/samples\/|src\/[\w-]+\/|\.`|\.[a-z]{2,4}`/u.test(window)) codeWithSource += 1;
			}
			continue;
		}
		if (inFence) continue;

		if (/!\[\]\(/u.test(line)) {
			findings.push({
				rule: 'deck-image-no-alt',
				severity: 'error',
				file,
				line: lineNumber,
				message: '图片缺少替代文字（markdownlint MD045）；放映页和 PDF 都需要 alt 才能被朗读与被搜索'
			});
		}
		// A URL inside inline code is a value to type, not a link to click
		// (`http://127.0.0.1:3080`), so check the prose with code spans removed.
		const proseOnly = line.replace(/`[^`]*`/gu, '');
		if (/(?<![\]("'<])https?:\/\/\S+/u.test(proseOnly) && !/\]\(/u.test(proseOnly)) {
			findings.push({
				rule: 'deck-bare-url',
				severity: 'warning',
				file,
				line: lineNumber,
				message: '裸 URL（markdownlint MD034）；改成有文字说明的链接，投影时才能读'
			});
		}
	}

	// --- presenter notes ----------------------------------------------------
	// Marp turns HTML comments into presenter notes, which is how a deck stays
	// teachable without putting the script on the slide.
	const comments = [...body.matchAll(/<!--([\s\S]*?)-->/gu)]
		.map((match) => match[1])
		.filter((comment) => !/^\s*_class:/u.test(comment) && comment.trim() !== '');
	const slideCount = (body.match(/^---\s*$/gmu) ?? []).length + 1;
	if (comments.length / slideCount < MIN_NOTE_SHARE) {
		findings.push({
			rule: 'deck-notes-sparse',
			severity: 'info',
			file,
			line: 1,
			message: `讲师备注覆盖 ${comments.length}/${slideCount} 页（Marp 支持演讲者视图）；低于 20% 时讲课时没有提词`
		});
	}

	if (codeBlocks > 0 && codeWithSource / codeBlocks < 0.5) {
		findings.push({
			rule: 'deck-code-untraceable',
			severity: 'info',
			file,
			line: 1,
			message: `只有 ${codeWithSource}/${codeBlocks} 个代码块标了来源文件；课件里的代码应能对应回 samples/`
		});
	}
	return findings;
}

/**
 * Check the theme's font sizes for classroom legibility.
 *
 * Declarations below `MIN_CONTENT_FONT_PX` are only reported for content
 * selectors: a 14px footer is fine, 13px code is not.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {string} options.themePath - repository-relative theme CSS.
 * @returns {Array<object>} Findings.
 */
export function auditTheme({ root, themePath }) {
	const absolute = join(root, themePath);
	if (!existsSync(absolute)) return [];
	let css;
	try {
		css = readFileSync(absolute, 'utf8');
	} catch {
		return [];
	}
	const findings = [];
	// The base section font is the projector body text; the content selector
	// below only covers code/table/compact rules.
	const sectionRule = /(?:^|[}])\s*section\s*[{]([^}]*)[}]/u.exec(css);
	const bodySize = sectionRule ? /font-size:\s*(\d+)px/u.exec(sectionRule[1]) : null;
	if (bodySize !== null && Number(bodySize[1]) < MIN_BODY_FONT_PX) {
		findings.push({
			rule: 'deck-font-too-small',
			severity: 'error',
			file: themePath,
			line: 1,
			message: `section 正文使用 ${bodySize[1]}px，低于投影正文下限 ${MIN_BODY_FONT_PX}px；应提高正文并重新分页`
		});
	}
	const contentSelector = /(?:^|[}])\s*([^{}]*\b(?:pre|table|section\.tight|section\.compact)\b[^{}]*)[{]([^}]*)[}]/gu;
	for (const match of css.matchAll(contentSelector)) {
		const selector = match[1].trim().replace(/\s+/gu, ' ');
		const size = /font-size:\s*(\d+)px/u.exec(match[2]);
		if (size === null) continue;
		const value = Number(size[1]);
		if (value >= MIN_CONTENT_FONT_PX) continue;
		findings.push({
			rule: 'deck-font-too-small',
			severity: 'error',
			file: themePath,
			line: 1,
			message: `${selector} 使用 ${value}px，低于教室投影可读下限 ${MIN_CONTENT_FONT_PX}px；应改字号并让内容分页，而不是缩小字体`
		});
	}
	return findings;
}

/**
 * Run every courseware rule over the presentation part.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {object} options.settings - resolved layout.
 * @returns {{ findings: Array<object>, decks: number }} Findings.
 */
export function collectCoursewareFindings({ root, settings }) {
	const part = settings.parts.find((entry) => entry.key === 'presentation');
	if (part === undefined) return { findings: [], decks: 0 };
	const directory = join(root, part.dir);
	if (!existsSync(directory)) return { findings: [], decks: 0 };

	const findings = auditTheme({ root, themePath: `${part.dir}/theme.css` });
	let decks = 0;
	for (const name of readdirSafe(directory)) {
		if (!/^\d{2}-.*\.md$/iu.test(name)) continue;
		let text;
		try {
			text = readFileSync(join(directory, name), 'utf8');
		} catch {
			continue;
		}
		decks += 1;
		findings.push(...auditCoursewareText({ file: `${part.dir}/${name}`, text }));
	}
	findings.sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line);
	return { findings, decks };
}

/** Strip YAML frontmatter from a deck, returning the slide body. */
function stripFrontmatter(text) {
	if (!text.startsWith('---')) return text;
	const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/u.exec(text);
	return match === null ? text : text.slice(match[0].length);
}

/** Read a directory's entries, or an empty list. */
function readdirSafe(directory) {
	try {
		return readdirSync(directory);
	} catch {
		return [];
	}
}
