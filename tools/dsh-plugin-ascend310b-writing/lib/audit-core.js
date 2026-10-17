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

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
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
export function readManuscript(file) {
	const text = readFileSync(file, 'utf8');
	const lines = text.split(/\r?\n/u);
	const headings = [];
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
		if (line.startsWith('>') || line.startsWith('    ')) continue;
		// Prose rules read the line with each inline code span replaced by a
		// sentinel, so a half-width mark inside `like(this)` is never reported
		// as a Chinese punctuation fault, and removing a span cannot invent a
		// run of spaces between the characters that surrounded it.
		const prose = line.replace(INLINE_CODE, INLINE_CODE_SENTINEL);
		if (HALF_WIDTH_IN_CJK.test(prose)) {
			push('cjk-halfwidth-punct', 'warning', i + 1, '中文之间出现半角标点，请改用全角标点');
		}
		if (HALF_WIDTH_BRACKET.test(prose)) {
			push('cjk-halfwidth-bracket', 'warning', i + 1, '中文之间出现半角括号，请改用全角括号');
		}
		if (DOUBLE_SPACE_IN_CJK.test(prose)) {
			push('cjk-double-space', 'info', i + 1, '中文之间出现连续空格');
		}
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
				if (sidebarFiles.size > 0 && !sidebarFiles.has(sidebarKey)) missingFromSidebar.push(rel);
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
