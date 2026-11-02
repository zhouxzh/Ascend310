/**
 * Manuscript audit and progress engine for a VuePress tutorial book.
 *
 * Pure Node ESM with no Harness imports, so it can be unit-tested and run
 * standalone. It knows the Ascend310 book layout (`src/book`, `src/experiment`,
 * `src/appendix`) and the repository's frozen naming rules, and reports
 * findings that a human author can act on without guessing.
 *
 * @module dsh-plugin-ascend310b-writing/audit-core
 */

import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, posix, relative, resolve, sep, win32 } from 'node:path';
import { proseLanguageFinding } from './prose-language.js';
import { DEFAULT_SETTINGS, resolveSettings } from './settings.js';

/**
 * The manuscript parts, their directory, and their frozen title shape.
 *
 * A part lists its numbered manuscripts through `filePattern`. `extras` names
 * part-owned documents that carry no frozen number — the section's own README,
 * which the sidebar reaches through the section `link:` rather than through a
 * child entry. Files that match no `filePattern` are audited for hygiene and
 * links but are exempt from the frozen-title and sidebar-coverage rules.
 */
export { DEFAULT_LAYOUT, DEFAULT_SETTINGS, LAYOUT_FILE, compileLayout, resolveSettings } from './settings.js';

/**
 * The built-in part list.
 *
 * It is a value from {@link ./settings.js} rather than a literal here, so a
 * repository can restate the layout (its own directories, file patterns and
 * title shapes) without editing this package. `PARTS` stays exported because
 * it is the default a caller gets when it passes no settings.
 */
export const PARTS = DEFAULT_SETTINGS.parts;

/** Half-width marks that are almost always wrong inside Chinese prose. */
const HALF_WIDTH_IN_CJK = /[\u4e00-\u9fff][,;:!?][\u4e00-\u9fff]/u;
/** Half-width brackets hugging Chinese characters. */
const HALF_WIDTH_BRACKET = /[\u4e00-\u9fff][()][\u4e00-\u9fff]/u;
/** Two spaces that should be one inside Chinese prose. */
const DOUBLE_SPACE_IN_CJK = /[\u4e00-\u9fff] {2,}[\u4e00-\u9fff]/u;
/** The author's Chinese-academic style forbids literal spaces at CJK/Latin boundaries. */
const CJK_LATIN_SPACE = /[\u4e00-\u9fff][\u0020\u3000]+[A-Za-z0-9]|[A-Za-z0-9][\u0020\u3000]+[\u4e00-\u9fff]/gu;

/** Count CJK/Latin boundary spaces without involving line-end whitespace. */
function countCjkLatinSpaces(text) {
	return [...String(text ?? '').matchAll(CJK_LATIN_SPACE)].length;
}

/** A file with this many tables needs a table/list/prose review. */
const TABLE_FILE_LIMIT = 12;
/** One H2 section with this many tables is usually reference-heavy. */
const TABLE_SECTION_LIMIT = 8;
/** A conceptual H2 section with this many list items is probably over-fragmented. */
const LIST_SECTION_LIMIT = 10;
/** Sections whose primary form is legitimately a checklist or procedure. */
const LIST_ORIENTED_SECTION = /(?:参考|文献|验证|检查|作业|练习|评分|常见错误|安装|配置|步骤|流程|安全|提示词|功能详解|操作)/u;

/** Whether a line is the delimiter row of a pipe table. */
function isTableDelimiter(line) {
	const cells = String(line ?? '')
		.trim()
		.replace(/^\|/u, '')
		.replace(/\|$/u, '')
		.split('|');
	return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/u.test(cell.trim()));
}

/** Count pipe tables by file and H2 section, ignoring fenced code. */
function collectTableStats(lines) {
	let fenced = false;
	let fence = '';
	let section = '(preface)';
	let sectionLine = 1;
	const bySection = new Map();
	const starts = [];
	for (let i = 0; i < lines.length; i += 1) {
		const fenceMatch = /^\s*(`{3,}|~{3,})/u.exec(lines[i]);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;
		if (/^## /u.test(lines[i])) {
			section = lines[i].replace(/\s*\{#[^}]+\}\s*$/u, '');
			sectionLine = i + 1;
		}
		if (!/^\s*\|/u.test(lines[i]) || !isTableDelimiter(lines[i + 1])) continue;
		starts.push(i + 1);
		const current = bySection.get(section) ?? { line: sectionLine, count: 0 };
		current.count += 1;
		bySection.set(section, current);
	}
	return { total: starts.length, firstLine: starts[0] ?? 1, sections: [...bySection].map(([name, value]) => ({ name, ...value })) };
}

/** Count list items by H2 section, ignoring fenced code. */
function collectListStats(lines) {
	let fenced = false;
	let fence = '';
	let section = '(preface)';
	let sectionLine = 1;
	const bySection = new Map();
	for (let i = 0; i < lines.length; i += 1) {
		const fenceMatch = /^\s*(`{3,}|~{3,})/u.exec(lines[i]);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;
		if (/^## /u.test(lines[i])) {
			section = lines[i].replace(/\s*\{#[^}]+\}\s*$/u, '');
			sectionLine = i + 1;
		}
		if (!/^\s*(?:[-*+]|\d+[.)])\s+/u.test(lines[i])) continue;
		const current = bySection.get(section) ?? { line: sectionLine, count: 0 };
		current.count += 1;
		bySection.set(section, current);
	}
	return [...bySection].map(([name, value]) => ({ name, ...value }));
}
/** A full cryptographic digest belongs in engineering evidence, not reader prose. */
const FULL_SHA256 = /\b[a-f0-9]{64}\b/iu;
/** Exact artifact bytes belong in an evidence manifest or engineering record. */
const ARTIFACT_BYTES = /\b\d{4,}[\s\u00a0]*(?:bytes|字节)\b/iu;
/** A machine-specific Windows home/checkout path is not portable reader guidance. */
const MACHINE_WINDOWS_PATH = /\b[A-Za-z]:\\(?:Users|Github|Documents)\\(?!<)/u;
/** Internal scratch/build paths expose an author workflow rather than a reader task. */
const INTERNAL_TEMP_PATH = /(?:^|[\s`])\/(?:tmp\/(?:appendix|writing|presentation|package|test|debug)[\w./-]*|var\/folders\/[^\s`]+)/u;
/** Inline code spans, removed before a prose rule reads a line. */
const INLINE_CODE = /`[^`]*`/gu;
/**
 * Stands in for a removed inline code span. It is deliberately neither CJK nor
 * whitespace nor a bracket: deleting a span outright glues its neighbours
 * together, so `整个 `known_hosts` 文件` would otherwise read as two spaces
 * between Chinese characters and be reported as a fault the raw line never had.
 */
const INLINE_CODE_SENTINEL = '\u0000';
/**
 * A line that opens a new Markdown block. A hard break immediately before one
 * of these cannot render, because the block boundary already ends the line.
 * That is what separates a trailing run worth deleting from a real line break.
 */
const STARTS_BLOCK = /^\s*(?:`{3,}|~{3,}|\$\$|#{1,6}\s|>|<!--|\||[-*+]\s|\d+[.)]\s)/u;

/** Characters counted as one "word" each in the progress report. */
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/gu;
/** Latin word runs, used with the CJK count for the reading estimate. */
const LATIN_WORD = /[A-Za-z0-9][A-Za-z0-9'’._+-]*/gu;
/**
 * Safety bound on findings collected before sorting. It exists so one very
 * noisy rule cannot exhaust memory; the caller's `maxFindings` slices the
 * sorted list afterwards.
 */
const HARD_FINDING_LIMIT = 20000;

/**
 * Compare a hard break against the other items of the list it belongs to.
 *
 * An author who wants a line break writes it on every item of a list, so a
 * break that appears on exactly one item is far more often a leftover than an
 * intent. This turns that judgement into evidence the finding can carry.
 *
 * @param {string[]} lines - the file's lines.
 * @param {number} index - the 0-based index of the line carrying the break.
 * @returns {string} A clause to append to the finding's message, or `''`.
 */
function listBreakConsistency(lines, index) {
	const here = /^(\s*)(?:([-*+])|(\d+)[.)])\s/u.exec(lines[index] ?? '');
	if (here === null) return '';
	const indent = here[1];
	const ordered = here[3] !== undefined;
	// Walk out to the actual boundary of this list block: a blank line, an
	// indented continuation and another list item are all inside it, while a
	// top-level paragraph or heading ends it. A fixed line window would merge
	// two separate lists and report a ratio that belongs to neither.
	const inside = (line) =>
		line === undefined ||
		line.trim() === '' ||
		/^\s/u.test(line) ||
		/^\s*(?:[-*+]|\d+[.)])\s/u.test(line);
	let from = index;
	while (from > 0 && inside(lines[from - 1])) from -= 1;
	let to = index;
	while (to + 1 < lines.length && inside(lines[to + 1])) to += 1;

	const siblings = [];
	for (let i = from; i <= to; i += 1) {
		const marker = /^(\s*)(?:([-*+])|(\d+)[.)])\s/u.exec(lines[i]);
		if (marker === null) continue;
		if (marker[1] !== indent) continue;
		if ((marker[3] !== undefined) !== ordered) continue;
		siblings.push(i);
	}
	if (siblings.length < 2) return '';
	const withBreak = siblings.filter((i) => /[ \t]{2,}$/u.test(lines[i]) && lines[i].trim() !== '');
	if (withBreak.length === 1) {
		return `；同一列表的另外 ${siblings.length - 1} 项都没有这样写，先确认是不是误留`;
	}
	return `；同一列表有 ${withBreak.length}/${siblings.length} 项这样写`;
}

/**
 * Split a manuscript file into frontmatter and body without a YAML dependency.
 * Only the top-level `key: value` lines the book uses are read.
 *
 * @param {string} text - the whole file, decoded as UTF-8.
 * @returns {{ frontmatter: Record<string, string>, body: string, bodyLine: number }}
 *   Parsed frontmatter, the body after it, and the 1-based line the body starts on.
 */
export function splitFrontmatter(text) {
	if (!text.startsWith('---')) return { frontmatter: {}, body: text, bodyLine: 1 };
	const lines = text.split(/\r?\n/u);
	let end = -1;
	for (let i = 1; i < lines.length; i += 1) {
		if (lines[i].trim() === '---') {
			end = i;
			break;
		}
	}
	if (end === -1) return { frontmatter: {}, body: text, bodyLine: 1 };
	const frontmatter = {};
	for (const line of lines.slice(1, end)) {
		const match = /^([A-Za-z_][\w-]*):\s*(.*)$/u.exec(line);
		if (match === null) continue;
		frontmatter[match[1]] = match[2].trim().replace(/^"(.*)"$/u, '$1');
	}
	return { frontmatter, body: lines.slice(end + 1).join('\n'), bodyLine: end + 2 };
}

/**
 * Read one Markdown file and split it into numbered lines plus its headings.
 *
 * @param {string} file - absolute path to the Markdown file.
 * @returns {{ text: string, lines: string[], headings: Array<{ level: number, text: string, line: number, anchor?: string }> }}
 *   The raw text, its lines, and every ATX heading with its explicit anchor id.
 */
const MANUAL_SECTION_NUMBER = /^(?:\d+\.\d+(?:\.\d+)*\.?|\d+[.、)])\s+/u;

/**
 * Detect a manually numbered section heading. Chapter/case/appendix numbers
 * live in the frozen H1 or frontmatter; H2-H6 must stay semantic so the site
 * and LaTeX can assign chapter-local numbers without duplicated prefixes.
 *
 * @param {string} text - heading text without the ATX marker or anchor.
 * @returns {boolean} Whether the visible heading starts with manual numbering.
 */
export function hasManualSectionNumber(text) {
	const visible = text.replace(/\{#[^}\s]+\}/u, '').trim();
	// An IPv4 address or decimal measurement is content, not section numbering.
	if (/^\d{1,3}(?:\.\d{1,3}){3}(?:\s|$)/u.test(visible)) return false;
	return MANUAL_SECTION_NUMBER.test(visible);
}

/** Normalize heading and standalone-label text for duplicate detection. */
function sectionTextKey(text) {
	return String(text ?? '')
		.replace(/\[([^\]]+)\]\([^)]*\)/gu, '$1')
		.replace(/`([^`]*)`/gu, '$1')
		.replace(/^\s*\*\*(.*?)\*\*\s*$/u, '$1')
		.replace(/^\s*__(.*?)__\s*$/u, '$1')
		.replace(/[\s*_~]+/gu, '')
		.replace(/[：:]+$/u, '')
		.trim()
		.toLowerCase();
}

export function readManuscript(file) {
	const text = readFileSync(file, 'utf8');
	const lines = text.split(/\r?\n/u);
	const headings = [];
	// A leading YAML block is metadata, not Markdown: a `#` comment inside it
	// (portfolio.md carries one) is not a heading and must not be audited as one.
	let start = 0;
	if (lines[0]?.trim() === '---') {
		for (let i = 1; i < lines.length; i += 1) {
			if (lines[i].trim() === '---') {
				start = i + 1;
				break;
			}
		}
	}
	let fenced = false;
	let fence = '';
	for (let i = start; i < lines.length; i += 1) {
		const line = lines[i];
		const fenceMatch = /^\s*(`{3,}|~{3,})/u.exec(line);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;
		const heading = /^(#{1,6})\s+(.*)$/u.exec(line);
		if (heading === null) continue;
		const anchor = /\{#([^}\s]+)\}/u.exec(heading[2]);
		headings.push({
			level: heading[1].length,
			text: heading[2].replace(/\{#([^}\s]+)\}/u, '').trim(),
			line: i + 1,
			...(anchor === null ? {} : { anchor: anchor[1] })
		});
	}
	return { text, lines, headings };
}

/**
 * Walk one directory and return the manuscript files it directly contains, in
 * numeric reading order (`chapter2.md` before `chapter10.md`).
 *
 * @param {string} dir - absolute directory path.
 * @param {RegExp} pattern - the file-name shape that identifies a manuscript.
 * @returns {string[]} Absolute file paths, ordered by their embedded number.
 */
export function listManuscripts(dir, pattern) {
	if (!existsSync(dir)) return [];
	return readdirSync(dir)
		.filter((name) => pattern.test(name))
		.sort((left, right) => {
			const a = Number(pattern.exec(left)?.[1] ?? 0);
			const b = Number(pattern.exec(right)?.[1] ?? 0);
			return a - b;
		})
		.map((name) => join(dir, name));
}

/**
 * List every document one part owns: its explicitly named pages first, then
 * its numbered manuscripts in reading order, then its part-owned extras.
 *
 * @param {string} root - absolute repository root.
 * @param {object} part - a descriptor from {@link PARTS}.
 * @returns {Array<{ file: string, numbered: boolean }>} Owned documents and
 *   whether each one carries a frozen number.
 */
export function listPartFiles(root, part) {
	const dir = join(root, part.dir);
	const named = (part.files ?? [])
		.map((name) => join(dir, name))
		.filter((file) => existsSync(file));
	const numbered = part.filePattern ? listManuscripts(dir, part.filePattern) : [];
	const extras = (part.extras ?? [])
		.map((name) => join(dir, name))
		.filter((file) => existsSync(file));
	return [
		...named.map((file) => ({ file, numbered: false })),
		...numbered.map((file) => ({ file, numbered: true })),
		...extras.map((file) => ({ file, numbered: false }))
	];
}

/**
 * Collect the relative link and image targets of one manuscript, skipping
 * fenced code blocks, external URLs, and pure anchors.
 *
 * @param {string[]} lines - the file's lines.
 * @returns {Array<{ kind: 'image'|'link', target: string, line: number }>}
 *   Every local target with the 1-based line it appears on.
 */
export function collectTargets(lines) {
	const targets = [];
	let fenced = false;
	let fence = '';
	for (let i = 0; i < lines.length; i += 1) {
		const line = lines[i];
		const fenceMatch = /^\s*(`{3,}|~{3,})/u.exec(line);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;
		const stripped = line.replace(/`[^`]*`/gu, '');
		for (const match of stripped.matchAll(/!\[[^\]]*\]\(([^)\s]+)/gu)) {
			targets.push({ kind: 'image', target: match[1], line: i + 1 });
		}
		for (const match of stripped.matchAll(/(?<!!)\[[^\]]*\]\(([^)\s]+)/gu)) {
			targets.push({ kind: 'link', target: match[1], line: i + 1 });
		}
	}
	return targets;
}

/**
 * Decide whether a Markdown target names a file inside the repository.
 *
 * @param {string} target - the raw target text.
 * @returns {boolean} True when the target must resolve to a local file.
 */
export function isLocalTarget(target) {
	if (target === '' || target.startsWith('#')) return false;
	if (/^[A-Za-z][\w+.-]*:/u.test(target)) return false;
	return !target.startsWith('//');
}

/**
 * Resolve a Markdown target to an absolute path, dropping its anchor and query.
 *
 * @param {string} fromFile - the file the target appears in.
 * @param {string} target - the raw target text.
 * @returns {string} The absolute path the target names.
 */
export function resolveTarget(fromFile, target) {
	const clean = decodeURI(target.split('#')[0].split('?')[0]);
	return resolve(dirname(fromFile), clean);
}

/**
 * Pick the frozen document title a part publishes, from pieces the caller has
 * already parsed. This is the single place the choice between a frontmatter
 * `title` and the first H1 lives, so the audit and the AGENTS.md cross-check
 * cannot drift apart.
 *
 * @param {object} part - a descriptor from {@link PARTS}.
 * @param {object} parsed - the file's parsed pieces.
 * @param {Record<string, string>} parsed.frontmatter - frontmatter keys.
 * @param {Array<{ level: number, text: string, line: number }>} parsed.headings - ATX headings.
 * @returns {{ title: string, line: number }} The title text and the 1-based line it sits on.
 */
export function pickDocumentTitle(part, { frontmatter, headings }) {
	if (part.titleInFrontmatter) return { title: frontmatter.title ?? '', line: 1 };
	const heading = headings.find((entry) => entry.level === 1);
	return { title: heading?.text ?? '', line: heading?.line ?? 1 };
}

/**
 * Read one manuscript from disk and pick the title it publishes. A caller that
 * already parsed the file should call {@link pickDocumentTitle} instead.
 *
 * @param {string} file - absolute path to the Markdown file.
 * @param {object} part - a descriptor from {@link PARTS}.
 * @returns {{ title: string, line: number }} The title text and the 1-based line it sits on.
 */
export function documentTitleOf(file, part) {
	const { frontmatter } = splitFrontmatter(readFileSync(file, 'utf8'));
	return pickDocumentTitle(part, { frontmatter, headings: readManuscript(file).headings });
}

/**
 * Check one manuscript file and append its findings.
 *
 * @param {object} context - the running audit context.
 * @param {object} context.part - the part descriptor from {@link PARTS}.
 * @param {string} context.file - absolute path of the manuscript.
 * @param {object} context.root - absolute repository root.
 * @param {Array<object>} context.findings - the finding sink.
 * @param {number} context.maxFindings - total finding cap.
 * @param {Map<string, number>} context.sidebarFiles - sidebar-listed files by relative path.
 */
function auditFile(context) {
	const { part, file, numbered, root, findings, maxFindings, sidebarFiles, sidebarFile } = context;
	// A Marp deck is Markdown but not a manuscript: the title, H1 and sidebar
	// rules describe book chapters, and applying them to a slide run reports
	// dozens of findings that are all correct by design.
	const assetPart = part.assets === true;
	const rel = relative(root, file).split(sep).join('/');
	const push = (rule, severity, line, message) => {
		if (findings.length >= maxFindings) return;
		findings.push({ rule, severity, file: rel, line, message });
	};

	const text = readFileSync(file, 'utf8');
	const { frontmatter, body, bodyLine } = splitFrontmatter(text);
	const { lines, headings } = readManuscript(file);
	const stem = basename(file, '.md');
	const expectedNumber = Number(part.filePattern?.exec(basename(file))?.[1] ?? 0);

	// --- frozen title -------------------------------------------------------
	// Only a numbered manuscript owns a frozen chapter/case/appendix title: a
	// section README and a site-level page must not be judged against it.
	const { title: documentTitle, line: titleLine } = pickDocumentTitle(part, { frontmatter, headings });
	if (!assetPart && numbered && documentTitle === '') {
		push('title-missing', 'error', titleLine, `缺少标题：${part.titleHint}`);
	} else if (!assetPart && numbered) {
		const match = part.titlePattern.exec(documentTitle);
		if (match === null) {
			push('title-format', 'error', titleLine, `标题「${documentTitle}」不符合「${part.titleHint}」`);
		} else if (Number(match[1]) !== expectedNumber) {
			push(
				'title-number-mismatch',
				'error',
				titleLine,
				`标题编号 ${match[1]} 与文件名 ${basename(file)} 不一致`
			);
		}
	}

	// --- heading structure --------------------------------------------------
	const levelOne = headings.filter((heading) => heading.level === 1);
	if (!assetPart && !part.titleInFrontmatter && levelOne.length !== 1) {
		push('h1-count', 'error', 1, `正文应恰好有一个一级标题，实际 ${levelOne.length} 个`);
	}
	let previous = 0;
	// Slides deliberately jump between contexts; only a manuscript has a spine.
	for (const heading of assetPart ? [] : headings) {
		if (previous !== 0 && heading.level > previous + 1) {
			push(
				'heading-jump',
				'warning',
				heading.line,
				`标题层级从 H${previous} 跳到 H${heading.level}：${heading.text}`
			);
		}
		previous = heading.level;
	}

	const headingKeys = new Map();
	for (const heading of assetPart ? [] : headings) {
		const key = sectionTextKey(heading.text);
		if (key === '') continue;
		const first = headingKeys.get(key);
		if (first === undefined) {
			headingKeys.set(key, heading);
			continue;
		}
		push(
			'duplicate-heading',
			'warning',
			heading.line,
			`标题「${heading.text}」与第${first.line}行标题重复；合并章节或改成不同语义标题。`
		);
	}

	// Section numbers are rendered by VuePress/LaTeX. Keeping them in the
	// source duplicates the chapter/case number and drifts when sections move.
	if (!assetPart && ['book', 'experiment', 'appendix'].includes(part.key)) {
		const manual = headings.filter(
			(heading) => heading.level >= 2 && heading.level <= 6 && hasManualSectionNumber(heading.text)
		);
		if (manual.length > 0) {
			push(
				'heading-manual-number',
				'error',
				manual[0].line,
				`H2-H6 有 ${manual.length} 个标题手写章节编号；保留语义标题，由站点/PDF 自动编号，首个：「${manual[0].text}」`
			);
		}
	}

	// --- heading length and shape -------------------------------------------
	// A heading is a signpost: it has to stay scannable in the sidebar, the
	// table of contents and the PDF bookmarks. The budgets are calibrated on
	// this manuscript (H2 p90 is 25 characters); a heading that exceeds them is
	// almost always carrying prose that belongs in the paragraph below it.
	// A link is never part of a heading: it breaks the bookmark text.
	const HEADING_LIMITS = { 1: 24, 2: 32, 3: 33, 4: 40, 5: 40, 6: 40 };
	if (!assetPart) {
		for (const heading of headings) {
			const visible = heading.text.replace(/\[([^\]]*)\]\([^)]*\)/gu, '$1');
			const length = [...visible].length;
			const limit = HEADING_LIMITS[heading.level] ?? 40;
			if (length > limit) {
				push(
					'heading-too-long',
					'warning',
					heading.line,
					`H${heading.level} 标题 ${length} 字，超过 ${limit} 字上限：${visible.slice(0, 40)}`
				);
			}
			if (/\]\(https?:/u.test(heading.text)) {
				push(
					'heading-has-link',
					'warning',
					heading.line,
					`标题里含链接，链接应放在正文：${visible.slice(0, 40)}`
				);
			}
		}
	}

	// --- layout: wide tables and very long code blocks ----------------------
	// These two shapes are what break the PDF page and the web column: a table
	// with eight columns cannot fit A4 or a 1440px content column, and a
	// hundred-line block becomes a wall of text in both outputs.
	const TABLE_COLUMN_LIMIT = 7;
	const CODE_LINE_LIMIT = 80;
	if (!assetPart) {
		let inFence = false;
		let blockStart = 0;
		let blockLines = 0;
		for (let i = 0; i < lines.length; i += 1) {
			const line = lines[i];
			if (/^\s*(?:```|~~~)/u.test(line)) {
				if (!inFence) {
					inFence = true;
					blockStart = i + 1;
					blockLines = 0;
				} else {
					inFence = false;
					if (blockLines > CODE_LINE_LIMIT) {
						push(
							'layout-code-long',
							'warning',
							blockStart,
							`代码块 ${blockLines} 行，超过 ${CODE_LINE_LIMIT} 行；PDF 与网页都会变成整页代码，建议拆段或链接到 samples/`
						);
					}
				}
				continue;
			}
			if (inFence) {
				blockLines += 1;
				continue;
			}
			if (!/^\s*\|/u.test(line)) continue;
			const columns = (line.match(/\|/gu) ?? []).length - 1;
			if (columns > TABLE_COLUMN_LIMIT) {
				push(
					'layout-table-wide',
					'warning',
					i + 1,
					`表格 ${columns} 列，超过 ${TABLE_COLUMN_LIMIT} 列；A4 与网页内容列都放不下`
				);
			}
		}
	}

	// --- explicit anchors ---------------------------------------------------
	// An anchor that carries the `src-` convention must name this file's own
	// part and stem; appendix files reusing a copied `src-experiment-caseN-`
	// prefix is the defect class this rule exists for.
	const anchorPrefix = `src-${part.key}-${stem}-`;
	for (const heading of headings) {
		if (heading.anchor === undefined || !heading.anchor.startsWith('src-')) continue;
		if (heading.anchor.startsWith(anchorPrefix)) continue;
		push(
			'anchor-prefix',
			'error',
			heading.line,
			`锚点 {#${heading.anchor}} 应以 ${anchorPrefix} 开头`
		);
	}

	// --- local targets ------------------------------------------------------
	for (const entry of collectTargets(lines)) {
		if (!isLocalTarget(entry.target)) continue;
		const absolute = resolveTarget(file, entry.target);
		if (existsSync(absolute)) continue;
		push(
			entry.kind === 'image' ? 'image-missing' : 'link-missing',
			'error',
			entry.line,
			`${entry.kind === 'image' ? '图片' : '链接'}目标不存在：${entry.target}`
		);
	}

	// --- prose hygiene ------------------------------------------------------
	let fenced = false;
	let fence = '';
	let cjkLatinSpaceCount = 0;
	let firstCjkLatinSpaceLine = null;
	for (let i = 0; i < lines.length; i += 1) {
		const line = lines[i];
		const fenceMatch = /^\s*(`{3,}|~{3,})/u.exec(line);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;
		const trailing = /[ \t]+$/u.exec(line);
		if (trailing !== null) {
			const next = lines[i + 1];
			// A run is inert when it cannot render as a line break: a single
			// space is never one, a whitespace-only line is already blank, and a
			// following blank line or block boundary ends the line anyway.
			const inert =
				trailing[0].length === 1 ||
				line.trim() === '' ||
				next === undefined ||
				next.trim() === '' ||
				STARTS_BLOCK.test(next);
			if (inert) {
				push('trailing-whitespace', 'warning', i + 1, '行尾有多余空白（删除不会改变渲染）');
			} else {
				push(
					'hard-break-effective',
					'warning',
					i + 1,
					`行尾 ${trailing[0].length} 个空格是 Markdown 硬换行，渲染时会断行` +
						listBreakConsistency(lines, i) +
						'；要保留就留着，要删就先确认渲染效果可以接受'
				);
			}
		}
		if (/^\t/u.test(line)) push('tab-indent', 'warning', i + 1, '使用制表符缩进，请改为空格');
		if (line.startsWith('    ')) continue;
		// Prose rules read the line with each inline code span replaced by a
		// sentinel, so a half-width mark inside `like(this)` is never reported
		// as a Chinese punctuation fault, and removing a span cannot invent a
		// run of spaces between the characters that surrounded it.
		const prose = line.replace(INLINE_CODE, INLINE_CODE_SENTINEL);
		if (i + 1 >= bodyLine) {
			const count = countCjkLatinSpaces(prose);
			if (count > 0) {
				cjkLatinSpaceCount += count;
				firstCjkLatinSpaceLine ??= i + 1;
			}
			const labelLine = line.trim();
			if (!/^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)/u.test(labelLine)) {
				const label = /^([^:：\n]{1,30})[:：]$/u.exec(labelLine)?.[1]?.trim() ?? '';
				const heading = label === '' ? undefined : headingKeys.get(sectionTextKey(label));
				if (heading !== undefined) {
					push(
						'duplicate-section-label',
						'warning',
						i + 1,
						`正文出现与第${heading.line}行章节标题同名的独立标签「${label}」；删除标签并合并到该章节，或改成普通引导语。`
					);
				}
			}
		}
		if (line.startsWith('>')) continue;
		if (HALF_WIDTH_IN_CJK.test(prose)) {
			push('cjk-halfwidth-punct', 'warning', i + 1, '中文之间出现半角标点，请改用全角标点');
		}
		if (HALF_WIDTH_BRACKET.test(prose)) {
			push('cjk-halfwidth-bracket', 'warning', i + 1, '中文之间出现半角括号，请改用全角括号');
		}
		if (DOUBLE_SPACE_IN_CJK.test(prose)) {
			push('cjk-double-space', 'info', i + 1, '中文之间出现连续空格');
		}
		// Reader-facing chapters explain what a result means. Full digests, byte
		// counts and author-machine paths are useful for engineering evidence, but
		// make a tutorial look like an internal run log and cannot be reused by a
		// learner on another machine. Fenced commands remain exempt above.
		if (['book', 'experiment', 'appendix'].includes(part.key)) {
			if (FULL_SHA256.test(line)) {
				push('reader-evidence-digest', 'warning', i + 1, '读者可见正文出现完整 SHA-256；保留可理解的结果和边界，把完整哈希放到工程证据或学习记录。');
			}
			if (ARTIFACT_BYTES.test(line)) {
				push('reader-evidence-bytes', 'warning', i + 1, '读者可见正文出现精确字节数；除非它直接解释学习结果，否则移到工程证据或学习记录。');
			}
			if (MACHINE_WINDOWS_PATH.test(line)) {
				push('reader-machine-path', 'warning', i + 1, '读者可见正文出现个人 Windows 路径；改为“教材仓库根目录”或读者自己的工作区。');
			}
			if (INTERNAL_TEMP_PATH.test(line)) {
				push('reader-internal-path', 'warning', i + 1, '读者可见正文出现内部临时路径；改为“个人临时学习目录”或教师指定目录。');
			}
		}
	}
	if (cjkLatinSpaceCount > 0) {
		push(
			'cjk-latin-space',
			'warning',
			firstCjkLatinSpaceLine,
			`本文件有 ${cjkLatinSpaceCount} 处中西文边界空格；按作者规范删除，例如“使用Python3.9”“共5个”。英文单词之间及英文与数字的常规空格保留。`
		);
	}
	const tableStats = collectTableStats(lines);
	if (tableStats.total > TABLE_FILE_LIMIT) {
		push(
			'table-density',
			'warning',
			tableStats.firstLine,
			`本文件有 ${tableStats.total} 个表格，超过建议上限 ${TABLE_FILE_LIMIT}；逐表判断是否存在多个共享字段的横向比较，顺序、分类和单维清单改用列表或段落。`
		);
	}
	for (const section of tableStats.sections.filter((entry) => entry.count > TABLE_SECTION_LIMIT)) {
		push(
			'table-section-overuse',
			'warning',
			section.line,
			`${section.name.replace(/^##\s*/u, '')}有 ${section.count} 个表格，单节超过 ${TABLE_SECTION_LIMIT} 个；只有参数、兼容性或证据矩阵适合连续用表。`
		);
	}
	if (!assetPart) {
		for (const section of collectListStats(lines).filter(
			(entry) => entry.count > LIST_SECTION_LIMIT && !LIST_ORIENTED_SECTION.test(entry.name)
		)) {
			push(
				'list-section-density',
				'warning',
				section.line,
				`${section.name.replace(/^##\s*/u, '')}有 ${section.count} 个列表项，超过概念说明建议上限 ${LIST_SECTION_LIMIT}；存在因果、比较或连续解释关系的内容应改写为段落，只保留真正的步骤、条件和并列枚举。`
			);
		}
		const language = proseLanguageFinding(rel, body, bodyLine - 1);
		if (language !== null && findings.length < maxFindings) findings.push(language);
	}
	if (text.includes('\r\n')) push('crlf', 'warning', 1, '文件包含 CRLF 行尾，仓库统一使用 LF');
	if (text !== '' && !text.endsWith('\n')) push('no-final-newline', 'warning', lines.length, '文件末尾缺少换行');
	// A numbered manuscript carries prose after its title. A site-level page may
	// legitimately be all frontmatter, so only a numbered document is judged.
	if (numbered && body.trim() === '') push('empty-body', 'error', bodyLine, '正文为空');

	// --- index and sidebar coverage -----------------------------------------
	// A numbered manuscript must be reachable from the sidebar. A section
	// README is reached through its section `link:`, and a site-level page
	// through the root entry or a home action, so neither is required to
	// appear as a child entry.
	if (numbered) {
		const sidebarKey = `${part.key}/${basename(file)}`;
		if (!assetPart && sidebarFiles.size > 0 && !sidebarFiles.has(sidebarKey)) {
			push('sidebar-missing', 'error', 1, `未出现在 ${sidebarFile ?? 'src/.vuepress/sidebar.ts'}：${sidebarKey}`);
		}
	}
}

/**
 * Read every file name the VuePress sidebar lists, keyed as `<part>/<file>`.
 *
 * @param {string} root - absolute repository root.
 * @returns {Map<string, number>} Sidebar-listed file names by part-relative key.
 */
export function readSidebarEntries(root, settings = DEFAULT_SETTINGS) {
	const sidebarPath = join(root, settings.sidebarFile);
	if (!existsSync(sidebarPath)) return new Map();
	const listed = new Map();
	let part = '';
	for (const line of readFileSync(sidebarPath, 'utf8').split(/\r?\n/u)) {
		const prefix = /prefix:\s*"\/([\w-]+)\/"/u.exec(line);
		if (prefix !== null) {
			part = prefix[1];
			continue;
		}
		const child = /^\s*"([\w.-]+\.md)"/u.exec(line);
		if (child !== null && part !== '') listed.set(`${part}/${child[1]}`, 0);
	}
	return listed;
}

/**
 * Order findings by severity, then by file, line and rule.
 *
 * This is the one ordering the whole plugin uses, so a report merged from
 * several collectors reads the same way as a manuscript-only audit.
 *
 * @param {Array<object>} findings - findings to sort in place.
 * @returns {Array<object>} The same array, sorted.
 */
export function sortFindings(findings) {
	const order = { error: 0, warning: 1, info: 2 };
	return findings.sort(
		(left, right) =>
			order[left.severity] - order[right.severity] ||
			left.file.localeCompare(right.file) ||
			left.line - right.line ||
			left.rule.localeCompare(right.rule)
	);
}

/**
 * Return whether an absolute path is a descendant of a canonical root.
 *
 * @param {string} root - canonical repository root.
 * @param {string} candidate - canonical or resolved candidate path.
 * @returns {boolean} Whether the candidate is strictly inside root.
 */
function isInsideRoot(root, candidate) {
	const pathFromRoot = relative(root, candidate);
	return (
		pathFromRoot !== '' &&
		pathFromRoot !== '..' &&
		!pathFromRoot.startsWith(`..${sep}`) &&
		!isAbsolute(pathFromRoot)
	);
}

/**
 * Validate and canonicalize the one learner record a focused audit may read.
 *
 * This intentionally accepts only POSIX-shaped paths below `tmp/`. Resolving
 * both the root and the file through the filesystem catches a final-file or
 * intermediate-directory symlink that points outside the repository.
 *
 * @param {object} options - record location inputs.
 * @param {string} options.root - repository root.
 * @param {string} options.file - repository-relative record path.
 * @returns {{ root: string, file: string, path: string }} Canonical root, display path and canonical file path.
 * @throws {Error} When the requested record is outside the allowed scope.
 */
export function resolveLearnerRecord({ root, file }) {
	if (typeof root !== 'string' || root.trim() === '') throw new Error('记录审计需要有效的仓库根目录。');
	if (typeof file !== 'string' || file.trim() === '') throw new Error('记录审计需要 file 参数。');
	if (
		isAbsolute(file) ||
		win32.isAbsolute(file) ||
		posix.isAbsolute(file) ||
		file.includes('\\') ||
		!file.startsWith('tmp/') ||
		!file.endsWith('.md')
	) {
		throw new Error('记录审计只允许仓库相对的 tmp/*.md 路径。');
	}
	const segments = file.split('/');
	if (segments.some((segment) => segment === '' || segment === '.' || segment === '..')) {
		throw new Error('记录审计拒绝包含空段、当前目录或上级目录的 file 路径。');
	}

	let canonicalRoot;
	try {
		canonicalRoot = realpathSync(root);
	} catch {
		throw new Error(`记录审计的仓库根目录不存在或不可读取：${root}`);
	}
	if (!statSync(canonicalRoot).isDirectory()) throw new Error('记录审计的 root 必须是目录。');

	const requested = resolve(canonicalRoot, ...segments);
	if (!isInsideRoot(canonicalRoot, requested)) throw new Error('记录审计拒绝仓库外的 file 路径。');
	if (!existsSync(requested)) throw new Error(`记录审计文件不存在：${file}`);

	let canonicalFile;
	try {
		canonicalFile = realpathSync(requested);
	} catch {
		throw new Error(`记录审计无法解析文件：${file}`);
	}
	if (!isInsideRoot(canonicalRoot, canonicalFile)) {
		throw new Error(`记录审计拒绝指向仓库外的符号链接：${file}`);
	}
	if (!statSync(canonicalFile).isFile()) throw new Error(`记录审计目标不是普通文件：${file}`);
	return { root: canonicalRoot, file: segments.join('/'), path: canonicalFile };
}

/**
 * Read one learner record as UTF-8 without silently replacing invalid bytes.
 *
 * @param {string} path - canonical record path.
 * @returns {string} The decoded Markdown text.
 * @throws {TypeError} When the file is not valid UTF-8.
 */
export function readUtf8Strict(path) {
	return new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(path));
}

/**
 * Audit one learner experiment record below `tmp/` without applying book-level
 * publication conventions such as frozen names, sidebar membership or anchors.
 *
 * @param {object} options - record audit inputs.
 * @param {string} options.root - repository root.
 * @param {string} options.file - repository-relative `tmp/*.md` path.
 * @param {number} [options.maxFindings] - cap on returned findings.
 * @returns {object} Structured record audit report.
 */
export function auditLearnerRecord({ root, file, maxFindings = 400 }) {
	const record = resolveLearnerRecord({ root, file });
	const findings = [];
	const push = (rule, severity, line, message) => {
		if (findings.length < HARD_FINDING_LIMIT) findings.push({ rule, severity, file: record.file, line, message });
	};
	let text;
	try {
		text = readUtf8Strict(record.path);
	} catch (error) {
		if (error instanceof TypeError) {
			push('utf8-invalid', 'error', 1, '文件不是可严格解码的 UTF-8 文本。');
		} else {
			push('record-unreadable', 'error', 1, `无法读取文件：${error.message}`);
		}
		return finalizeRecordAudit({ root: record.root, file: record.file, findings, maxFindings });
	}

	const lines = text.split(/\r?\n/u);
	const { body, bodyLine } = splitFrontmatter(text);
	const { headings } = readManuscript(record.path);
	let previous = 0;
	const headingKeys = new Map();
	for (const heading of headings) {
		if (previous !== 0 && heading.level > previous + 1) {
			push('heading-jump', 'warning', heading.line, `标题层级从 H${previous} 跳到 H${heading.level}：${heading.text}`);
		}
		previous = heading.level;
		if (heading.level >= 2 && hasManualSectionNumber(heading.text)) {
			push('heading-manual-number', 'error', heading.line, `标题手写章节编号：${heading.text}`);
		}
		const key = sectionTextKey(heading.text);
		if (key === '') continue;
		const first = headingKeys.get(key);
		if (first === undefined) {
			headingKeys.set(key, heading);
		} else {
			push('duplicate-heading', 'warning', heading.line, `标题「${heading.text}」与第${first.line}行标题重复；合并章节或改成不同语义标题。`);
		}
	}

	let fenced = false;
	let fence = '';
	let cjkLatinSpaceCount = 0;
	let firstCjkLatinSpaceLine = null;
	for (let i = 0; i < lines.length; i += 1) {
		const line = lines[i];
		const fenceMatch = /^\s*(`{3,}|~{3,})(.*)$/u.exec(line);
		if (fenceMatch !== null) {
			if (!fenced) {
				fenced = true;
				fence = fenceMatch[1][0];
				if (fenceMatch[2].trim() === '') {
					push('code-fence-language', 'error', i + 1, '围栏代码块缺少语言标记。');
				}
			} else if (fenceMatch[1][0] === fence) {
				fenced = false;
			}
			continue;
		}
		if (fenced) continue;

		const trailing = /[ \t]+$/u.exec(line);
		if (trailing !== null) push('trailing-whitespace', 'warning', i + 1, '行尾有多余空白。');
		if (line.startsWith('    ')) continue;
		const prose = line.replace(INLINE_CODE, INLINE_CODE_SENTINEL);
		if (i + 1 >= bodyLine) {
			const count = countCjkLatinSpaces(prose);
			if (count > 0) {
				cjkLatinSpaceCount += count;
				firstCjkLatinSpaceLine ??= i + 1;
			}
			const labelLine = line.trim();
			if (!/^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)/u.test(labelLine)) {
				const label = /^([^:：\n]{1,30})[:：]$/u.exec(labelLine)?.[1]?.trim() ?? '';
				const heading = label === '' ? undefined : headingKeys.get(sectionTextKey(label));
				if (heading !== undefined) {
					push(
						'duplicate-section-label',
						'warning',
						i + 1,
						`正文出现与第${heading.line}行章节标题同名的独立标签「${label}」；删除标签并合并到该章节，或改成普通引导语。`
					);
				}
			}
		}
		if (line.startsWith('>')) continue;
		if (HALF_WIDTH_IN_CJK.test(prose)) {
			push('cjk-halfwidth-punct', 'warning', i + 1, '中文之间出现半角标点，请改用全角标点。');
		}
		if (HALF_WIDTH_BRACKET.test(prose)) {
			push('cjk-halfwidth-bracket', 'warning', i + 1, '中文之间出现半角括号，请改用全角括号。');
		}
	}
	if (cjkLatinSpaceCount > 0) {
		push(
			'cjk-latin-space',
			'warning',
			firstCjkLatinSpaceLine,
			`本记录有 ${cjkLatinSpaceCount} 处中西文边界空格；按作者规范删除，例如“使用Python3.9”“共5个”。英文单词之间及英文与数字的常规空格保留。`
		);
	}
	const language = proseLanguageFinding(record.file, body, bodyLine - 1);
	if (language !== null) push(language.rule, language.severity, language.line, language.message);
	if (text.includes('\r\n')) push('crlf', 'warning', 1, '文件包含 CRLF 行尾，记录应使用 LF。');
	if (text !== '' && !text.endsWith('\n')) push('no-final-newline', 'warning', lines.length, '文件末尾缺少换行。');

	for (const entry of collectTargets(lines)) {
		if (!isLocalTarget(entry.target)) continue;
		let target;
		try {
			target = resolveTarget(record.path, entry.target);
		} catch {
			push('target-invalid', 'error', entry.line, `链接目标无法解码：${entry.target}`);
			continue;
		}
		if (!isInsideRoot(record.root, target)) {
			push(
				entry.kind === 'image' ? 'image-outside-root' : 'link-outside-root',
				'error',
				entry.line,
				`${entry.kind === 'image' ? '图片' : '链接'}目标超出仓库：${entry.target}`
			);
			continue;
		}
		if (!existsSync(target)) {
			push(
				entry.kind === 'image' ? 'image-missing' : 'link-missing',
				'error',
				entry.line,
				`${entry.kind === 'image' ? '图片' : '链接'}目标不存在：${entry.target}`
			);
			continue;
		}
		try {
			if (!isInsideRoot(record.root, realpathSync(target))) {
				push(
					entry.kind === 'image' ? 'image-outside-root' : 'link-outside-root',
					'error',
					entry.line,
					`${entry.kind === 'image' ? '图片' : '链接'}目标的符号链接超出仓库：${entry.target}`
				);
			}
		} catch {
			push('target-unreadable', 'error', entry.line, `无法解析链接目标：${entry.target}`);
		}
	}
	return finalizeRecordAudit({ root: record.root, file: record.file, findings, maxFindings });
}

/**
 * Sort, cap and render one learner record's findings with the normal schema.
 *
 * @param {object} report - report pieces.
 * @param {string} report.root - canonical repository root.
 * @param {string} report.file - repository-relative record file.
 * @param {Array<object>} report.findings - raw findings.
 * @param {number} report.maxFindings - returned finding cap.
 * @returns {object} The structured and rendered report.
 */
function finalizeRecordAudit({ root, file, findings, maxFindings }) {
	sortFindings(findings);
	const cap = Number.isInteger(maxFindings) && maxFindings > 0 ? maxFindings : 400;
	const kept = findings.slice(0, cap);
	const count = (severity) => findings.filter((finding) => finding.severity === severity).length;
	const summary = {
		files: 1,
		errors: count('error'),
		warnings: count('warning'),
		infos: count('info'),
		truncated: kept.length < findings.length
	};
	return {
		root,
		file,
		generatedAt: new Date().toISOString(),
		summary,
		findings: kept,
		text: formatRecordAudit({ root, file, summary, findings: kept })
	};
}

/**
 * Render one learner record audit using the ordinary finding presentation.
 *
 * @param {object} report - the record report to render.
 * @returns {string} Human-readable audit report.
 */
export function formatRecordAudit({ root, file, summary, findings }) {
	const lines = [
		`学习实验记录审计 · ${file} · ${root}`,
		`扫描 1 个记录文件：${summary.errors} 个错误，${summary.warnings} 个警告，${summary.infos} 个提示`
	];
	if (findings.length === 0) lines.push('没有发现问题。');
	else {
		for (const finding of findings) {
			const mark = finding.severity === 'error' ? 'x' : finding.severity === 'warning' ? '!' : '-';
			lines.push(`  ${mark} L${finding.line} [${finding.rule}] ${finding.message}`);
		}
	}
	if (summary.truncated) lines.push('结果已达到上限，请提高 maxFindings。');
	return lines.join('\n');
}

/**
 * Run the full manuscript audit.
 *
 * @param {object} options - audit inputs.
 * @param {string} options.root - absolute repository root.
 * @param {'all'|'book'|'experiment'|'appendix'} [options.scope] - part filter.
 * @param {number} [options.maxFindings] - cap on returned findings.
 * @param {Array<object>} [options.extraFindings] - findings from a sibling
 *   check (cross-artifact rules) merged into the same sort, cap and summary.
 * @returns {object} The structured audit report, including its rendered text.
 */
export function auditRepo({ root, scope = 'all', maxFindings = 400, extraFindings = [], settings }) {
	const findings = [...extraFindings];
	const s = settings ?? resolveSettings({ root });
	const sidebarFiles = readSidebarEntries(root, s);
	const parts = s.parts.filter((part) => scope === 'all' || part.key === scope);
	let files = 0;
	for (const part of parts) {
		for (const { file, numbered } of listPartFiles(root, part)) {
			files += 1;
			auditFile({ part, file, numbered, root, findings, maxFindings: HARD_FINDING_LIMIT, sidebarFiles, sidebarFile: s.sidebarFile });
		}
	}
	sortFindings(findings);
	// Severity order is applied before the cap, so a flood of one informational
	// rule can never hide an error in a later file. The summary counts every
	// finding, not just the returned slice: a caller must be able to see that
	// the list was shortened without re-running the audit.
	const kept = findings.slice(0, maxFindings);
	const count = (severity) => findings.filter((finding) => finding.severity === severity).length;
	const summary = {
		files,
		errors: count('error'),
		warnings: count('warning'),
		infos: count('info'),
		truncated: kept.length < findings.length
	};
	return {
		root,
		scope,
		generatedAt: new Date().toISOString(),
		summary,
		findings: kept,
		text: formatAudit({ root, scope, summary, findings: kept })
	};
}

/**
 * Render an audit report as the text a model or a person reads.
 *
 * @param {object} report - the report from {@link auditRepo}.
 * @returns {string} The rendered report.
 */
export function formatAudit(report) {
	const { root, scope, summary, findings } = report;
	const lines = [
		`写作审计 · ${scope} · ${root}`,
		`扫描 ${summary.files} 个正文文件：${summary.errors} 个错误，${summary.warnings} 个警告，${summary.infos} 个提示`
	];
	if (findings.length === 0) {
		lines.push('没有发现问题。');
		return lines.join('\n');
	}
	const grouped = new Map();
	for (const finding of findings) {
		const bucket = grouped.get(finding.file) ?? [];
		bucket.push(finding);
		grouped.set(finding.file, bucket);
	}
	for (const [file, bucket] of grouped) {
		lines.push('', `${file}（${bucket.length}）`);
		for (const finding of bucket) {
			const mark = finding.severity === 'error' ? 'x' : finding.severity === 'warning' ? '!' : '-';
			lines.push(`  ${mark} L${finding.line} [${finding.rule}] ${finding.message}`);
		}
	}
	if (summary.truncated) lines.push('', '结果已达到上限，请缩小 scope 或提高 maxFindings。');
	return lines.join('\n');
}

/**
 * Count the reading units of one manuscript.
 *
 * @param {string} text - the whole file, decoded as UTF-8.
 * @returns {{ cjk: number, latinWords: number, words: number }} The counts.
 */
export function countWords(text) {
	const cjk = (text.match(CJK) ?? []).length;
	const latinWords = (text.match(LATIN_WORD) ?? []).length;
	return { cjk, latinWords, words: cjk + latinWords };
}

/**
 * Build the per-file progress report for every manuscript part.
 *
 * @param {object} options - report inputs.
 * @param {string} options.root - absolute repository root.
 * @param {'all'|'book'|'experiment'|'appendix'} [options.scope] - part filter.
 * @returns {object} The structured progress report, including its rendered text.
 */
export function progressReport({ root, scope = 'all', settings }) {
	const s = settings ?? resolveSettings({ root });
	const sidebarFiles = readSidebarEntries(root, s);
	const parts = [];
	const missingFromSidebar = [];
	const totals = { files: 0, cjk: 0, words: 0, figures: 0, headings: 0, codeBlocks: 0, readingMinutes: 0 };
	for (const part of s.parts) {
		if (scope !== 'all' && scope !== part.key) continue;
		const files = [];
		for (const { file, numbered } of listPartFiles(root, part)) {
			const { text, lines, headings } = readManuscript(file);
			const { cjk, words } = countWords(text);
			const figures = (text.match(/!\[[^\]]*\]\(/gu) ?? []).length;
			const codeBlocks = Math.floor((text.match(/^\s*(?:`{3,}|~{3,})/gmu) ?? []).length / 2);
			const readingMinutes = Math.max(1, Math.round(words / 300));
			const rel = relative(root, file).split(sep).join('/');
			const entry = {
				path: rel,
				bytes: statSync(file).size,
				lines: lines.length,
				cjk,
				words,
				headings: headings.length,
				figures,
				codeBlocks,
				readingMinutes,
				modifiedAt: statSync(file).mtime.toISOString()
			};
			files.push(entry);
			totals.files += 1;
			totals.cjk += cjk;
			totals.words += words;
			totals.figures += figures;
			totals.headings += headings.length;
			totals.codeBlocks += codeBlocks;
			totals.readingMinutes += readingMinutes;
			if (numbered) {
				const sidebarKey = `${part.key}/${basename(file)}`;
				// A deck reaches the site through its built .html link in the sidebar, not
		// as a VuePress page, so the page-coverage rule must not judge it.
		if (part.assets !== true && sidebarFiles.size > 0 && !sidebarFiles.has(sidebarKey)) missingFromSidebar.push(rel);
			}
		}
		parts.push({
			key: part.key,
			label: part.label,
			files,
			cjk: files.reduce((sum, entry) => sum + entry.cjk, 0),
			words: files.reduce((sum, entry) => sum + entry.words, 0)
		});
	}
	return {
		root,
		scope,
		generatedAt: new Date().toISOString(),
		totals,
		parts,
		missingFromSidebar,
		text: formatProgress({ root, totals, parts, missingFromSidebar })
	};
}

/**
 * Render a progress report as text.
 *
 * @param {object} report - the report from {@link progressReport}.
 * @returns {string} The rendered report.
 */
export function formatProgress(report) {
	const lines = [
		`写作进度 · ${report.root}`,
		`共 ${report.totals.files} 个正文文件，${report.totals.cjk} 个中文字，约 ${report.totals.readingMinutes} 分钟阅读量`
	];
	for (const part of report.parts) {
		lines.push('', `${part.label}（${part.files.length} 篇，${part.cjk} 字）`);
		for (const file of part.files) {
			lines.push(
				`  ${file.path}  ${String(file.cjk).padStart(6)} 字  ${String(file.headings).padStart(3)} 节  ` +
					`${String(file.figures).padStart(3)} 图  ${String(file.codeBlocks).padStart(3)} 代码块`
			);
		}
	}
	if (report.missingFromSidebar.length > 0) {
		lines.push('', `未加入 sidebar.ts：${report.missingFromSidebar.join('、')}`);
	}
	return lines.join('\n');
}
