/**
 * Cross-artifact writing checks.
 *
 * The manuscript audit reads Markdown. These collectors read what it cannot:
 * the repository policy file, the LaTeX build log, the generated LaTeX tree,
 * and the Git object database. Each returns findings in the same shape the
 * manuscript audit uses, so a caller can merge them into one severity-ordered
 * report.
 *
 * @module dsh-plugin-ascend310b-writing/checks
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { basename, join, relative, sep } from 'node:path';

import { auditRepo, documentTitleOf, listPartFiles } from './audit-core.js';
import { DEFAULT_SETTINGS, resolveSettings } from './settings.js';
import { collectVuepressFindings } from './vuepress.js';

/** One row of a two-cell Markdown table whose first cell is a backticked path. */
const TABLE_ROW = /^\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|\s*$/u;
/** `Missing character: There is no X (U+NNNN) in font NAME...` */
const MISSING_CHARACTER = /^Missing character: There is no (.*) \((U\+[0-9A-Fa-f]{4,6})\) in font ([^/]+)/u;
/** `LaTeX Warning: Reference `key' on page N undefined ...` */
const UNDEFINED_REFERENCE = /LaTeX Warning: (Reference|Citation) `([^']*)'.*undefined/u;
/** `Overfull \hbox (12.3pt too wide) ...` */
const OVERFULL_BOX = /^Overfull \\(hbox|vbox) \(([0-9.]+)pt too wide\)/u;
/** `Underfull \hbox (badness ...)` — counted, never reported one by one. */
const UNDERFULL_BOX = /^Underfull \\(hbox|vbox)/u;
/**
 * An overfull box smaller than this is ordinary CJK typesetting noise. The
 * shipped book log carries 81 of them, most under a point, so reporting every
 * one would bury the findings that matter.
 */
const OVERFULL_REPORT_THRESHOLD_PT = 20;
/** How many overfull boxes to report, worst first. */
const OVERFULL_REPORT_LIMIT = 5;

/** The POSIX form of a part's directory, for comparing against table paths. */
const partDir = (part) => part.dir.split(sep).join('/');

/**
 * Read the frozen-name tables of `AGENTS.md`.
 *
 * Only rows whose first cell is a backticked `src/**.md` path qualify; the
 * repository-map table names `README.md` and is skipped by the prefix, so no
 * other table in the file can be mistaken for a frozen name.
 *
 * @param {string} root - absolute repository root.
 * @returns {{ path: string, rows: Array<{ source: string, title: string, line: number }>, available: boolean }}
 *   The frozen-name rows, or `available: false` when the file is absent.
 */
export function readFrozenNameTable(root, settings = DEFAULT_SETTINGS) {
	const path = join(root, settings.agentsFile);
	if (!existsSync(path)) return { path, rows: [], available: false };
	const rows = [];
	readFileSync(path, 'utf8')
		.split(/\r?\n/u)
		.forEach((line, index) => {
			const match = TABLE_ROW.exec(line);
			if (match === null) return;
			const source = match[1];
			// The frozen-name table names paths under the configured source root; on the
			// built-in layout that is src/, and a restated layout moves it.
			if (!source.startsWith(`${settings.sourceRoot}/`) || !source.endsWith('.md')) return;
			rows.push({ source, title: match[2], line: index + 1 });
		});
	return { path, rows, available: true };
}

/**
 * Compare the frozen-name table against the titles the manuscripts publish,
 * in both directions: a listed file whose title drifted, and a numbered
 * manuscript that was never registered.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - absolute repository root.
 * @returns {{ findings: Array<object>, available: boolean, rows: number }} The findings.
 */
export function collectFrozenNameFindings({ root, settings }) {
	const findings = [];
	const s = settings ?? resolveSettings({ root });
	const table = readFrozenNameTable(root, s);
	if (!table.available) return { findings, available: false, rows: 0, tableRows: [] };

	const listed = new Set(table.rows.map((row) => row.source));
	for (const row of table.rows) {
		const file = join(root, row.source);
		if (!existsSync(file)) {
			findings.push({
				rule: 'frozen-source-missing',
				severity: 'error',
				file: 'AGENTS.md',
				line: row.line,
				message: `冻结名表指向的文件不存在：${row.source}`
			});
			continue;
		}
		const part = s.parts.find((entry) => entry.assets !== true && row.source.startsWith(`${partDir(entry)}/`));
		if (part === undefined) continue;
		const actual = documentTitleOf(file, part);
		if (actual.title === row.title) continue;
		findings.push({
			rule: 'frozen-title-mismatch',
			severity: 'error',
			file: 'AGENTS.md',
			line: row.line,
			message:
				`冻结名表写「${row.title}」，但 ${row.source}:${actual.line} 的实际标题是「${actual.title}」。` +
				'改名时三处必须同步：正文标题、AGENTS.md 冻结名表、sidebar/索引。'
		});
	}

	for (const part of s.parts) {
		// A slide deck owns no frozen chapter title, so it cannot be unregistered.
		if (part.assets === true) continue;
		for (const { file, numbered } of listPartFiles(root, part)) {
			if (!numbered) continue;
			const source = relative(root, file).split(sep).join('/');
			if (listed.has(source)) continue;
			findings.push({
				rule: 'frozen-table-missing',
				severity: 'error',
				file: source,
				line: 1,
				message: `编号正文未登记在 AGENTS.md 冻结名表中：${source}`
			});
		}
	}
	// `rows` is the count a report prints; `tableRows` is the data a checker needs.
	return { findings, available: true, rows: table.rows.length, tableRows: table.rows };
}

/**
 * Find the newest manuscript modification time, used to judge whether a build
 * log still describes the current source.
 *
 * @param {string} root - absolute repository root.
 * @returns {{ file: string, mtimeMs: number } | null} The newest manuscript, or null.
 */
function newestManuscript(root, settings = DEFAULT_SETTINGS) {
	let newest = null;
	for (const part of settings.parts) {
		for (const { file } of listPartFiles(root, part)) {
			const mtimeMs = statSync(file).mtimeMs;
			if (newest === null || mtimeMs > newest.mtimeMs) {
				newest = { file: relative(root, file).split(sep).join('/'), mtimeMs };
			}
		}
	}
	return newest;
}

/**
 * Inspect a LaTeX build log for defects the Markdown audit cannot see: a font
 * without a glyph, an undefined reference, a hard error, and boxes that overrun
 * the page badly enough to matter.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - absolute repository root.
 * @param {string} [options.logPath] - explicit log path.
 * @returns {object} Findings plus the counters the rendered text reports.
 */
export function collectRenderFindings({ root, logPath, settings }) {
	const s = settings ?? resolveSettings({ root });
	const path = logPath ?? join(root, s.latex.log);
	if (!existsSync(path)) {
		return {
			findings: [],
			available: false,
			path,
			stats: null,
			text: `没有找到 LaTeX 构建日志：${path}\n先运行 ./convert-vuepress.sh，再回来做渲染体检。`
		};
	}
	const rel = relative(root, path).split(sep).join('/');
	const lines = readFileSync(path, 'utf8').split(/\r?\n/u);
	const findings = [];
	const missing = new Map();
	const overfull = [];
	let hardErrors = 0;
	let undefinedReferences = 0;
	let underfull = 0;

	lines.forEach((rawLine, index) => {
		const line = rawLine.trim();
		const number = index + 1;
		if (line.startsWith('!')) {
			hardErrors += 1;
			findings.push({
				rule: 'render-hard-error',
				severity: 'error',
				file: rel,
				line: number,
				message: `LaTeX 硬错误：${line.slice(0, 200)}`
			});
			return;
		}
		const missingMatch = MISSING_CHARACTER.exec(line);
		if (missingMatch !== null) {
			const key = `${missingMatch[2]}|${missingMatch[3]}`;
			const entry = missing.get(key) ?? {
				char: missingMatch[1],
				code: missingMatch[2],
				font: missingMatch[3],
				count: 0,
				line: number
			};
			entry.count += 1;
			missing.set(key, entry);
			return;
		}
		const referenceMatch = UNDEFINED_REFERENCE.exec(line);
		if (referenceMatch !== null) {
			undefinedReferences += 1;
			findings.push({
				rule: 'render-undefined-reference',
				severity: 'error',
				file: rel,
				line: number,
				message: `${referenceMatch[1] === 'Reference' ? '交叉引用' : '文献引用'}未定义：${referenceMatch[2]}`
			});
			return;
		}
		const overfullMatch = OVERFULL_BOX.exec(line);
		if (overfullMatch !== null) {
			overfull.push({ kind: overfullMatch[1], size: Number(overfullMatch[2]), line: number });
			return;
		}
		if (UNDERFULL_BOX.test(line)) underfull += 1;
	});

	for (const entry of missing.values()) {
		findings.push({
			rule: 'render-missing-character',
			severity: 'error',
			file: rel,
			line: entry.line,
			message:
				`字体缺字形：${entry.code}（${entry.char}）在「${entry.font}」中不存在，出现 ${entry.count} 次。` +
				'PDF 里该字符会缺失或落到替换字形上；改用数学模式（如 $\\to$）或换到有该字形的字体。'
		});
	}

	const reported = overfull
		.filter((entry) => entry.size >= OVERFULL_REPORT_THRESHOLD_PT)
		.sort((left, right) => right.size - left.size)
		.slice(0, OVERFULL_REPORT_LIMIT);
	for (const entry of reported) {
		findings.push({
			rule: 'render-overfull-box',
			severity: 'warning',
			file: rel,
			line: entry.line,
			message: `${entry.kind} 超出页宽 ${entry.size.toFixed(1)}pt（约 ${(entry.size / 28.45).toFixed(1)}mm）`
		});
	}

	const newest = newestManuscript(root, s);
	const logTime = statSync(path).mtimeMs;
	const stale = newest !== null && logTime < newest.mtimeMs;
	if (stale) {
		findings.push({
			rule: 'render-log-stale',
			severity: 'warning',
			file: rel,
			line: 1,
			message:
				`日志早于最新正文改动（${newest.file}），它描述的是旧构建；` +
				'在重新构建之前不要据它下结论。'
		});
	}

	const pageLine = lines.find((line) => line.startsWith('Output written on ')) ?? '';
	const stats = {
		logPath: rel,
		logTime: new Date(logTime).toISOString(),
		newestSource: newest === null ? null : newest.file,
		newestSourceTime: newest === null ? null : new Date(newest.mtimeMs).toISOString(),
		stale,
		hardErrors,
		missingCharacters: [...missing.values()].reduce((sum, entry) => sum + entry.count, 0),
		missingCharacterKinds: missing.size,
		undefinedReferences,
		overfull: overfull.length,
		overfullReported: reported.length,
		underfull,
		pageLine: pageLine.trim()
	};
	const text = [
		`渲染体检 · ${rel}`,
		`日志时间 ${stats.logTime}${stale ? '（早于最新正文，已标记为过期）' : ''}`,
		`硬错误 ${hardErrors} · 缺字形 ${stats.missingCharacters}（${stats.missingCharacterKinds} 种）· ` +
			`未定义引用 ${undefinedReferences} · Overfull ${overfull.length}（报告最大的 ${reported.length} 个）· Underfull ${underfull}`,
		pageLine.trim() === '' ? '' : pageLine.trim(),
		findings.length === 0 ? '渲染层没有发现问题。' : ''
	]
		.filter((line) => line !== '')
		.join('\n');
	return { findings, available: true, path, stats, text };
}

/**
 * Check that every relative Markdown reference inside a committed tree points
 * at a path that commit also contains.
 *
 * The manuscript audit reads the working tree, where a file excluded from the
 * commit still exists. This reads the commit itself, which is what a reader of
 * the published repository sees.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - absolute repository root.
 * @param {string} options.rev - the revision to inspect, e.g. `HEAD`.
 * @returns {object} Findings plus the counters the caller may report.
 */
export function collectCommitFindings({ root, rev }) {
	// `stdio` is pinned so a failing git does not echo its own stderr into the
	// caller's output; the reason is turned into a finding instead.
	const git = (args, options = {}) =>
		execFileSync('git', ['-C', root, ...args], {
			maxBuffer: 512 * 1024 * 1024,
			stdio: ['pipe', 'pipe', 'pipe'],
			...options
		});

	let listing;
	try {
		listing = git(['ls-tree', '-r', '-z', rev], { encoding: 'utf8' });
	} catch (error) {
		const stderr = String(error?.stderr ?? '');
		const reason =
			stderr
				.split('\n')
				.map((line) => line.trim())
				.find((line) => line.startsWith('fatal:') || line.startsWith('error:')) ??
			String(error.message).split('\n')[0];
		return {
			findings: [
				{
					rule: 'commit-unreadable',
					severity: 'error',
					file: '.git',
					line: 1,
					message: `无法读取提交 ${rev}：${reason}`
				}
			],
			checked: 0,
			files: 0,
			manuscripts: 0
		};
	}

	const pathToSha = new Map();
	for (const entry of listing.split('\0')) {
		if (entry === '') continue;
		const tab = entry.indexOf('\t');
		if (tab === -1) continue;
		pathToSha.set(entry.slice(tab + 1), entry.slice(0, tab).split(' ')[2]);
	}
	const present = new Set(pathToSha.keys());
	const manuscripts = [...pathToSha.keys()].filter((name) => /^src\/.*\.md$/u.test(name));

	// Paths come from a NUL-separated listing and contents from the object
	// database, so a non-ASCII name is never mangled by git's path quoting.
	const contents = new Map();
	if (manuscripts.length > 0) {
		const batch = git(['cat-file', '--batch'], {
			input: `${manuscripts.map((name) => pathToSha.get(name)).join('\n')}\n`,
			encoding: null
		});
		let cursor = 0;
		for (const name of manuscripts) {
			const headerEnd = batch.indexOf(0x0a, cursor);
			if (headerEnd === -1) break;
			const size = Number(batch.toString('utf8', cursor, headerEnd).split(' ')[2]);
			const start = headerEnd + 1;
			contents.set(name, batch.toString('utf8', start, start + size));
			cursor = start + size + 1;
		}
	}

	const findings = [];
	let checked = 0;
	for (const [file, text] of contents) {
		const lines = text.split('\n');
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
			for (const match of stripped.matchAll(/!?\[[^\]]*\]\(([^)\s]+)/gu)) {
				const target = match[1];
				if (
					target === '' ||
					target.startsWith('#') ||
					/^[A-Za-z][\w+.-]*:/u.test(target) ||
					target.startsWith('//')
				) {
					continue;
				}
				const clean = decodeURI(target.split('#')[0].split('?')[0]);
				if (clean === '') continue;
				const segments = clean.startsWith('/') ? [] : file.split('/').slice(0, -1);
				for (const part of clean.replace(/^\//u, '').split('/')) {
					if (part === '' || part === '.') continue;
					if (part === '..') segments.pop();
					else segments.push(part);
				}
				const resolved = segments.join('/');
				checked += 1;
				if (present.has(resolved)) continue;
				findings.push({
					rule: 'commit-link-missing',
					severity: 'error',
					file,
					line: i + 1,
					message: `提交 ${rev} 内引用的目标不存在：${resolved}（发布后即为断链）`
				});
			}
		}
	}
	return { findings, checked, files: present.size, manuscripts: manuscripts.length };
}

/**
 * Describe a frozen-name check for a rendered report.
 *
 * @param {{ available: boolean, rows: number, findings: Array<object> }} result - the check result.
 * @returns {string} One summary line.
 */
export function describeFrozenNames(result) {
	if (!result.available) return 'AGENTS.md 不存在，跳过冻结名表交叉校验。';
	const errors = result.findings.filter((finding) => finding.severity === 'error').length;
	return `冻结名表 ${result.rows} 行，与正文比对：${errors} 处不一致。`;
}

/**
 * Describe a commit-tree check for a rendered report.
 *
 * @param {object} result - the check result from {@link collectCommitFindings}.
 * @param {string} rev - the revision that was inspected.
 * @returns {string} One summary line.
 */
export function describeCommit(result, rev) {
	return (
		`提交 ${rev}：${result.files} 个文件、${result.manuscripts} 篇正文，` +
		`检查 ${result.checked} 处本地引用，${result.findings.length} 处无法解析。`
	);
}

/**
 * Pair each numbered manuscript with the LaTeX file the converter writes for
 * it, plus the book front matter, which the converter builds as the preface.
 *
 * @param {string} root - absolute repository root.
 * @returns {Array<{ source: string, generated: string }>} Source/generated pairs.
 */
function generatedPairs(root, settings = DEFAULT_SETTINGS) {
	const pairs = [];
	for (const part of settings.parts) {
		for (const { file, numbered } of listPartFiles(root, part)) {
			if (!numbered) continue;
			const name = basename(file, '.md');
			const directory = settings.generatedDirs[part.key];
			// A part the converter does not emit has no artifact to compare against.
			if (directory === undefined) continue;
			pairs.push({ source: file, generated: join(root, directory, `${name}.tex`) });
		}
	}
	pairs.push({
		source: join(root, settings.preface.source),
		generated: join(root, settings.preface.generated)
	});
	return pairs;
}

/**
 * Compare the generated LaTeX tree against the manuscript it was built from.
 *
 * Only one direction is sound. Every explicit `{#id}` in the source becomes a
 * `\label{id}`, so a declared anchor missing from the generated file proves the
 * artifact is wrong. The reverse is not evidence: pandoc also invents labels
 * for headings that declare no id, so extra labels are normal.
 *
 * A missing anchor has two very different causes, and they need opposite
 * advice, so each one is classified before it is reported:
 *
 * - the generated file escaped the whole heading (`\#\#\#\# … \{\#id\}`), which
 *   means pandoc never saw a heading — usually the line before it was not blank;
 * - the generated file invented a label ending in the declared id, which means
 *   the attribute block was swallowed into the heading text — usually an
 *   unescaped `_` opened emphasis;
 * - otherwise the source is simply newer than the artifact.
 *
 * Telling the author to rebuild when the converter cannot express the anchor
 * would send them in a circle, so `artifact-anchor-unparsed` says to fix the
 * source instead.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - absolute repository root.
 * @returns {{ findings: Array<object>, pairs: number, anchors: number }} The findings.
 */
export function collectArtifactFindings({ root, settings }) {
	const findings = [];
	let pairs = 0;
	let anchors = 0;
	let newestTex = null;
	const s = settings ?? resolveSettings({ root });
	for (const pair of generatedPairs(root, s)) {
		if (!existsSync(pair.source) || !existsSync(pair.generated)) continue;
		pairs += 1;
		const sourceTime = statSync(pair.source).mtimeMs;
		const texTime = statSync(pair.generated).mtimeMs;
		const relSource = relative(root, pair.source).split(sep).join('/');
		const relGenerated = relative(root, pair.generated).split(sep).join('/');
		if (newestTex === null || texTime > newestTex.mtimeMs) newestTex = { file: relGenerated, mtimeMs: texTime };

		const sourceText = readFileSync(pair.source, 'utf8');
		const generatedText = readFileSync(pair.generated, 'utf8');
		const declared = [...sourceText.matchAll(/\{#([^}\s]+)\}/gu)].map((match) => match[1]);
		if (declared.length === 0) continue;
		anchors += declared.length;
		const labels = [...generatedText.matchAll(/\\label\{([^}]+)\}/gu)].map((match) => match[1]);
		const present = new Set(labels);
		const missing = declared.filter((id) => !present.has(id));
		if (missing.length === 0) continue;

		const unparsed = [];
		const stale = [];
		const unexplained = [];
		for (const id of missing) {
			if (generatedText.includes(`\\{\\#${id}\\}`) || labels.some((label) => label.endsWith(`-${id}`))) {
				unparsed.push(id);
			} else if (sourceTime > texTime) {
				stale.push(id);
			} else {
				unexplained.push(id);
			}
		}
		const sample = (ids) => ids.slice(0, 3).join('、') + (ids.length > 3 ? ' …' : '');
		if (unparsed.length > 0) {
			findings.push({
				rule: 'artifact-anchor-unparsed',
				severity: 'error',
				file: relSource,
				line: 1,
				message:
					`${unparsed.length}/${declared.length} 个源锚点没能进入 ${relGenerated}（如 ${sample(unparsed)}）：` +
					'转换器没有把它们当作标题属性——常见原因是标题行前面缺少空行，' +
					'或标题文字里有未转义的特殊字符（如 `_` 被当成强调起始）。' +
					'修源 Markdown 后重新构建；重新构建本身修不好这一条。'
			});
		}
		if (stale.length > 0) {
			findings.push({
				rule: 'artifact-anchor-stale',
				severity: 'warning',
				file: relSource,
				line: 1,
				message:
					`${relGenerated} 里缺少 ${stale.length} 个源锚点（如 ${sample(stale)}），` +
					'且源比产物新：生成物落后于源，重新运行 ./convert-vuepress.sh。'
			});
		}
		if (unexplained.length > 0) {
			findings.push({
				rule: 'artifact-anchor-missing',
				severity: 'warning',
				file: relSource,
				line: 1,
				message:
					`${relGenerated} 里缺少 ${unexplained.length} 个源锚点（如 ${sample(unexplained)}），` +
					'源并不比产物新：先重新运行 ./convert-vuepress.sh，仍缺失再检查这些标题的写法。'
			});
		}
	}

	const pdf = join(root, s.latex.pdf);
	const newestSource = newestManuscript(root, s);
	const newest = [newestSource, newestTex]
		.filter((entry) => entry !== null)
		.sort((left, right) => right.mtimeMs - left.mtimeMs)[0];
	if (existsSync(pdf) && newest !== undefined && statSync(pdf).mtimeMs < newest.mtimeMs) {
		findings.push({
			rule: 'artifact-pdf-stale',
			severity: 'warning',
			file: s.latex.pdf,
			line: 1,
			message: `book.pdf 早于 ${newest.file}，PDF 已过期，需要重新构建`
		});
	}
	return { findings, pairs, anchors };
}

/**
 * List the `src/` files with uncommitted changes.
 *
 * A push publishes a commit, so a manuscript edited after that commit is not
 * what gets published — the failure this reports.
 *
 * @param {string} root - absolute repository root.
 * @returns {string[]} Repository-relative paths, or an empty list when the
 *   status cannot be read.
 */
export function dirtyManuscripts(root, settings = DEFAULT_SETTINGS) {
	try {
		const status = execFileSync('git', ['-C', root, 'status', '--porcelain', '--', settings.sourceRoot], {
			encoding: 'utf8',
			stdio: ['pipe', 'pipe', 'pipe'],
			maxBuffer: 64 * 1024 * 1024
		});
		return status
			.split('\n')
			.map((line) => line.replace(/\s+$/u, ''))
			.filter((line) => line !== '')
			.map((line) => {
				const path = line.slice(3);
				const renamed = path.split(' -> ');
				return renamed[renamed.length - 1].replace(/^"|"$/gu, '');
			})
			.filter((path) => path.endsWith('.md'));
	} catch {
		return [];
	}
}

/**
 * Decide whether the repository may be pushed, separating what can be checked
 * mechanically from what only a person can assert.
 *
 * A push publishes a commit, so the blocking items are the ones a commit can
 * contradict: the manuscript audit and the referenced-file coherence of that
 * commit. Everything else is advisory, because it describes local artifacts
 * that a push does not carry.
 *
 * @param {object} options - gate inputs.
 * @param {string} options.root - absolute repository root.
 * @param {string} [options.rev] - the revision that would be pushed.
 * @returns {object} The verdict, its items, and the rendered text.
 */
export function evaluatePushGate({ root, rev = 'HEAD', settings }) {
	const s = settings ?? resolveSettings({ root });
	const frozen = collectFrozenNameFindings({ root, settings: s });
	const assets = collectAssetFindings({ root, settings: s });
	const vuepress = collectVuepressFindings({ root, settings: s, frozenRows: frozen.tableRows });
	const audit = auditRepo({
		root,
		maxFindings: 400,
		extraFindings: [...frozen.findings, ...assets.findings, ...vuepress.findings],
		settings: s
	});
	const commit = collectCommitFindings({ root, rev });
	const dirty = dirtyManuscripts(root, s);
	const render = collectRenderFindings({ root, settings: s });
	const artifacts = collectArtifactFindings({ root, settings: s });

	const renderErrors = render.findings.filter((finding) => finding.severity === 'error').length;
	const items = [
		{
			id: 'manuscript-audit',
			blocking: true,
			ok: audit.summary.errors === 0,
			detail: `${audit.summary.errors} 个 error / ${audit.summary.warnings} 个 warning（含冻结名表交叉校验）`
		},
		{
			id: 'commit-tree',
			blocking: true,
			ok: commit.findings.length === 0,
			detail: describeCommit(commit, rev)
		},
		{
			id: 'uncommitted-manuscripts',
			blocking: false,
			ok: dirty.length === 0,
			detail:
				dirty.length === 0
					? 'src/ 下没有未提交的正文改动'
					: `src/ 下 ${dirty.length} 个文件未提交（${dirty.slice(0, 4).join('、')}${dirty.length > 4 ? ' …' : ''}）：` +
						'推上去的是它们上一次提交的版本'
		},
		{
			id: 'render',
			blocking: false,
			ok: !render.available || renderErrors === 0,
			detail: !render.available
				? '没有 LaTeX 构建日志，跳过（LaTeX 不参与 Pages 发布）'
				: render.stats.stale
					? `日志早于 ${render.stats.newestSource}，先重新构建再据它下结论`
					: `${renderErrors} 个渲染错误`
		},
		{
			id: 'artifacts',
			blocking: false,
			ok: artifacts.findings.length === 0,
			detail:
				artifacts.findings.length === 0
					? `${artifacts.pairs} 对源/产物的锚点一致`
					: `${artifacts.findings.length} 条产物问题（用 ascend310b_writing_render 看原因）`
		}
	];

	const blockingFailed = items.filter((item) => item.blocking && !item.ok).map((item) => item.id);
	const human = ['站点预览是否已由用户确认（AGENTS.md 规定：未经确认不要 push）'];
	const text = [
		`推送前检查 · ${root} · rev=${rev}`,
		...items.map((item) => {
			const mark = item.ok ? 'OK  ' : item.blocking ? '阻塞' : '注意';
			return `  [${mark}] ${item.id}：${item.detail}`;
		}),
		'',
		blockingFailed.length === 0
			? '机械检查通过。'
			: `机械检查未通过，阻塞项：${blockingFailed.join('、')}。`,
		...human.map((entry) => `只有人能确认：${entry}`)
	].join('\n');

	return { root, rev, ready: blockingFailed.length === 0, items, blockingFailed, human, text };
}

/**
 * A frozen-name shape the presentation decks are expected to use when they name
 * a unit: `第N章：…`, `案例 N：…`, `附录 N：…`.
 */
const FROZEN_SHAPE = /^(?:第\d+章|案例 \d+|附录 \d+)：/u;

/**
 * Check the two artifacts the manuscript audit does not cover: the built
 * presentation decks and the built VuePress site.
 *
 * Both are *freshness* questions — a deck whose built HTML predates it, or a
 * site whose `dist` predates its sources, is what makes a preview show the old
 * thing. The decks also carry the textbook's frozen unit names in their cover
 * heading and footer, so a renamed unit that was updated in the manuscript but
 * not in the deck is reported here.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - absolute repository root.
 * @param {object} [options.settings] - the resolved layout, for the paths.
 * @returns {{ findings: Array<object>, decks: number, slidesMissing: number, slidesStale: number, site: object }} The findings and counters.
 */
export function collectAssetFindings({ root, settings }) {
	const s = settings ?? resolveSettings({ root });
	const findings = [];
	let decks = 0;
	let slidesMissing = 0;
	let slidesStale = 0;

	const presentation = s.parts.find((part) => part.assets === true);
	const frozenTitleRows = readFrozenNameTable(root, s);
	const frozenTitles = new Set(frozenTitleRows.rows.map((row) => row.title));
	const isFrozenShape = (value) => typeof value === 'string' && FROZEN_SHAPE.test(value);

	if (presentation !== undefined) {
		const builtDir = s.slides?.builtDir;
		for (const { file, numbered } of listPartFiles(root, presentation)) {
			// README.md is the deck index, not a deck.
			if (!numbered) continue;
			decks += 1;
			const relSource = relative(root, file).split(sep).join('/');
			const sourceTime = statSync(file).mtimeMs;

			// A deck names a unit in its cover heading and its footer.
			const text = readFileSync(file, 'utf8');
			const cover = /^#\s+(.+)$/mu.exec(text)?.[1]?.trim() ?? '';
			const footer = /^footer:\s*"?([^"\n]+)"?\s*$/mu.exec(text)?.[1]?.trim() ?? '';
			for (const value of [cover, footer]) {
				if (!isFrozenShape(value) || frozenTitles.size === 0 || frozenTitles.has(value)) continue;
				findings.push({
					rule: 'deck-frozen-mismatch',
					severity: 'warning',
					file: relSource,
					line: 1,
					message: `演示文稿里的「${value}」不是冻结名表里的名字：改名时 AGENTS.md、sidebar 与 deck 必须同步`
				});
			}

			if (builtDir === undefined) continue;
			const built = join(root, builtDir, `${basename(file, '.md')}.html`);
			const relBuilt = relative(root, built).split(sep).join('/');
			if (!existsSync(built)) {
				slidesMissing += 1;
				findings.push({
					rule: 'slide-missing',
					severity: 'info',
					file: relSource,
					line: 1,
					message: `还没有构建出的放映页 ${relBuilt}：运行 pnpm run docs:slides`
				});
				continue;
			}
			if (statSync(built).mtimeMs < sourceTime) {
				slidesStale += 1;
				findings.push({
					rule: 'slide-stale',
					severity: 'warning',
					file: relSource,
					line: 1,
					message: `放映页 ${relBuilt} 早于源文稿，重新运行 pnpm run docs:slides`
				});
			}
		}
	}

	// The site build: `dist` older than the newest source page means a preview
	// would show what the source no longer says.
	let site = { available: false, stale: false, newestSource: null };
	const distIndex = s.site?.distIndex;
	if (distIndex !== undefined) {
		const distPath = join(root, distIndex);
		let newest = null;
		for (const part of s.parts) {
			for (const { file } of listPartFiles(root, part)) {
				const mtimeMs = statSync(file).mtimeMs;
				if (newest === null || mtimeMs > newest.mtimeMs) newest = { file: relative(root, file).split(sep).join('/'), mtimeMs };
			}
		}
		site = { available: existsSync(distPath), stale: false, newestSource: newest };
		if (!site.available) {
			findings.push({
				rule: 'site-missing',
				severity: 'info',
				file: distIndex,
				line: 1,
				message: '还没有构建站点（src/.vuepress/dist）：本地预览前先运行 pnpm run docs:build'
			});
		} else if (newest !== null && statSync(distPath).mtimeMs < newest.mtimeMs) {
			site.stale = true;
			findings.push({
				rule: 'site-stale',
				severity: 'warning',
				file: distPath,
				line: 1,
				message: `站点产物早于 ${newest.file}：重新运行 pnpm run docs:build 后再预览或推送`
			});
		}
	}

	findings.sort((left, right) => left.file.localeCompare(right.file) || left.rule.localeCompare(right.rule));
	return { findings, decks, slidesMissing, slidesStale, site };
}
