/**
 * Cross-check between the manuscript and the code it talks about.
 *
 * The book is a tutorial: almost every case study tells the reader to run a
 * script that lives under `samples/`. Those references rot faster than anything
 * else in the repository — a script is renamed, a directory is reorganised, and
 * the prose keeps pointing at the old name. Nothing about the Markdown is
 * wrong; it is the *join* between prose and code that broke.
 *
 * The hard part is not detecting a missing file, it is resolving what the text
 * means. A code block that says `scripts/inference_npu.py` is relative to the
 * case's own directory (or to one of its sub-projects), not to the repository
 * root. So a token is accepted when it resolves at any of these:
 *
 * 1. the repository root — `samples/case9/scripts/x.sh`
 * 2. the manuscript's own sample directory — `samples/case9` + `scripts/x.sh`
 * 3. anywhere under that sample directory, as a suffix — the chapter-4 examples
 *    live in `samples/chapter4/SSDLite/scripts/inference_npu.py`, and the prose
 *    writes it as `scripts/inference_npu.py`
 *
 * Resolving permissively is deliberate: a false "missing" would train the
 * author to ignore the rule, and the failure this exists to catch — the file is
 * gone from everywhere — survives the permissive rule.
 *
 * A reference into a gitignored path is not a defect: `outputs/env-report.json`
 * is produced by running the sample. Those are reported as information so the
 * author can confirm the reader is told how to produce it.
 *
 * @module dsh-plugin-ascend310b-writing/code
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { basename, join, relative, sep } from 'node:path';

/** A repository path token: `samples/…`, `scripts/…`, `tools/…`. */
const PATH_TOKEN = /(?<![A-Za-z0-9_./-])((?:samples|scripts|tools)\/[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})/gu;

/** Directories a walk must never descend into. */
const SKIP_DIRS = new Set(['node_modules', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.cache']);

/**
 * The sample directory a manuscript is about, when it exists.
 *
 * @param {string} root - the repository root.
 * @param {string} manuscriptRel - the manuscript's repository-relative path.
 * @returns {string | null} A repository-relative sample directory, or null.
 */
export function sampleDirFor(root, manuscriptRel) {
	const name = basename(manuscriptRel, '.md');
	const match = /^(chapter|case|appendix)(\d+)$/u.exec(name);
	if (match === null) return null;
	for (const candidate of [`samples/${match[1]}${match[2]}`, `samples/${match[2]}`]) {
		if (existsSync(join(root, candidate))) return candidate;
	}
	return null;
}

/**
 * List every file under a directory as a normalised relative path.
 *
 * Unreadable directories (a sandbox denial, a broken ACL on this machine's
 * `samples/case5/.pytest-*` trees) are skipped rather than aborting the walk.
 *
 * @param {string} directory - the absolute directory.
 * @param {string} [prefix] - the relative prefix accumulated so far.
 * @param {Set<string>} [out] - the accumulator.
 * @returns {Set<string>} File paths relative to the walk root, with `/`.
 */
export function listFiles(directory, prefix = '', out = new Set()) {
	let entries;
	try {
		entries = readdirSync(directory, { withFileTypes: true });
	} catch {
		return out;
	}
	for (const entry of entries) {
		const rel = prefix === '' ? entry.name : `${prefix}/${entry.name}`;
		if (entry.isDirectory()) {
			if (SKIP_DIRS.has(entry.name)) continue;
			listFiles(join(directory, entry.name), rel, out);
		} else if (entry.isFile()) {
			out.add(rel);
		}
	}
	return out;
}

/**
 * Ask git which of these paths are ignored, in one call.
 *
 * @param {string} root - the repository root.
 * @param {string[]} paths - repository-relative paths.
 * @returns {Set<string>} The ignored ones.
 */
export function ignoredPaths(root, paths) {
	if (paths.length === 0) return new Set();
	try {
		const output = execFileSync('git', ['-C', root, 'check-ignore', '--stdin', '-z'], {
			input: `${paths.join('\0')}\0`,
			encoding: 'utf8',
			stdio: ['pipe', 'pipe', 'pipe'],
			maxBuffer: 16 * 1024 * 1024
		});
		return new Set(String(output).split('\0').filter((entry) => entry !== ''));
	} catch {
		// git exits 1 when nothing matches, and is absent in a plain directory.
		return new Set();
	}
}

/**
 * Resolve one path token against a manuscript's context.
 *
 * @param {object} context - the resolution context.
 * @param {string} context.root - the repository root.
 * @param {string | null} context.sampleDir - the manuscript's sample directory.
 * @param {Map<string, Set<string>>} context.walks - per-directory file cache.
 * @param {string} token - the path token.
 * @returns {boolean} True when the token names something that exists.
 */
export function resolves({ root, sampleDir, walks }, token) {
	if (existsSync(join(root, token))) return true;
	if (sampleDir === null) return false;
	if (existsSync(join(root, sampleDir, token))) return true;
	const key = sampleDir;
	let files = walks.get(key);
	if (files === undefined) {
		files = listFiles(join(root, sampleDir));
		walks.set(key, files);
	}
	for (const file of files) {
		if (file === token || file.endsWith(`/${token}`)) return true;
	}
	return false;
}

/**
 * Find the code references in one manuscript and check they still resolve.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - the repository root.
 * @param {string} options.manuscriptRel - the manuscript's repository-relative path.
 * @param {string} options.text - the manuscript source.
 * @param {string} [options.lineOffset] - added to reported line numbers.
 * @returns {{ findings: Array<object>, tokens: number }} The findings.
 */
export function checkManuscriptReferences({ root, manuscriptRel, text, lineOffset = 0 }) {
	const findings = [];
	const tokens = [];
	text.split(/\r?\n/u).forEach((line, index) => {
		for (const match of line.matchAll(PATH_TOKEN)) {
			tokens.push({ token: match[1], line: index + 1 + lineOffset });
		}
	});
	if (tokens.length === 0) return { findings, tokens: 0 };

	const sampleDir = sampleDirFor(root, manuscriptRel);
	// A token is only judgeable when the text means a repository path:
	//   - samples/... always names the repository, so it is always checked
	//   - scripts/... and 	ools/... are relative to a sample directory, so they
	//     are checked only when this manuscript has one. The Ubuntu appendix and
	//     the topic-named decks teach generic commands, and judging those against
	//     the repository root would report every one of them.
	// A template placeholder such as samples/caseN/README.md is not a path.
	const judgeable = (token) => {
		if (/[{}<>]/u.test(token) || /(?:^|\/)(?:case|chapter|appendix)N\//u.test(token)) return false;
		if (token.startsWith('samples/')) return true;
		return sampleDir !== null;
	};
	const context = { root, sampleDir, walks: new Map() };
	const unresolved = [];
	for (const { token, line } of tokens) {
		if (!judgeable(token)) continue;
		if (resolves(context, token)) continue;
		unresolved.push({ token, line });
	}
	if (unresolved.length === 0) return { findings, tokens: tokens.length };

	const ignored = ignoredPaths(root, [...new Set(unresolved.map((entry) => entry.token))]);
	for (const { token, line } of unresolved) {
		if (ignored.has(token)) {
			findings.push({
				rule: 'sample-path-generated',
				severity: 'info',
				file: manuscriptRel,
				line,
				message: `${token} 是运行时产物（被 git 忽略），确认正文写清了怎么生成它`
			});
			continue;
		}
		const where = sampleDir === null ? '仓库根' : `仓库根与 ${sampleDir}`;
		findings.push({
			rule: 'sample-path-missing',
			severity: 'error',
			file: manuscriptRel,
			line,
			message: `正文引用的代码路径不存在：${token}（在${where}下都没找到）`
		});
	}
	return { findings, tokens: tokens.length };
}

/**
 * Cross-check every manuscript part against the code it references.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.settings - the resolved layout.
 * @returns {{ findings: Array<object>, manuscripts: number, tokens: number }} The findings.
 */
export function collectCodeFindings({ root, settings }) {
	const findings = [];
	let manuscripts = 0;
	let tokens = 0;
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		const directory = join(root, part.dir);
		if (!existsSync(directory)) continue;
		for (const entry of readdirNames(directory)) {
			if (!entry.endsWith('.md')) continue;
			if (part.filePattern !== undefined && !part.filePattern.test(entry)) {
				if (!(part.extras ?? []).includes(entry)) continue;
			}
			const rel = `${part.dir}/${entry}`;
			let text;
			try {
				text = readFileSync(join(root, rel), 'utf8');
			} catch {
				continue;
			}
			manuscripts += 1;
			const result = checkManuscriptReferences({ root, manuscriptRel: rel, text });
			findings.push(...result.findings);
			tokens += result.tokens;
		}
	}
	findings.sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line);
	return { findings, manuscripts, tokens };
}

/**
 * Report code that changed after the chapter that describes it.
 *
 * This walks recent commit history once, so it is cheap enough for the push
 * gate and too slow for the audit's five-second panel refresh. The window is
 * deliberately finite: it answers "did this change since the prose was last
 * touched?" for recent work, which is when the drift actually happens.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - the repository root.
 * @param {Array<{ file: string, sample: string }>} options.pairs - manuscript/code pairs to compare.
 * @param {number} [options.commits] - how many commits of history to read.
 * @returns {Array<object>} The findings.
 */
export function collectCodeDrift({ root, pairs, commits = 300 }) {
	if (pairs.length === 0) return [];
	let log;
	try {
		log = execFileSync('git', ['-C', root, 'log', `-n${commits}`, '--format=@%ct', '--name-only'], {
			encoding: 'utf8',
			stdio: ['pipe', 'pipe', 'pipe'],
			maxBuffer: 64 * 1024 * 1024
		});
	} catch {
		return [];
	}
	const lastChange = new Map();
	let stamp = null;
	for (const rawLine of String(log).split(/\r?\n/u)) {
		const line = rawLine.trim();
		if (line === '') continue;
		if (line.startsWith('@')) {
			stamp = Number(line.slice(1));
			continue;
		}
		if (stamp === null || lastChange.has(line)) continue;
		lastChange.set(line, stamp);
	}
	const findings = [];
	for (const { file, sample } of pairs) {
		const docStamp = lastChange.get(file);
		const sampleStamp = lastChange.get(sample);
		if (docStamp === undefined || sampleStamp === undefined) continue;
		if (sampleStamp <= docStamp) continue;
		const days = Math.round((sampleStamp - docStamp) / 86400);
		findings.push({
			rule: 'doc-code-drift',
			severity: 'warning',
			file,
			line: 1,
			message: `${sample} 在这段正文之后改过（约 ${days} 天），确认正文描述与代码仍然一致`
		});
	}
	return findings;
}

/** List a directory's entry names, or an empty array. */
function readdirNames(directory) {
	try {
		return readdirSync(directory);
	} catch {
		return [];
	}
}

/**
 * Turn a resolved reference into a drift pair.
 *
 * @param {string} manuscriptRel - the manuscript's repository-relative path.
 * @param {string} token - the path token as written.
 * @param {{ root: string, sampleDir: string | null }} context - resolution context.
 * @returns {{ file: string, sample: string } | null} The pair, when the token resolves to a real file.
 */
export function driftPairFor(manuscriptRel, token, context) {
	if (existsSync(join(context.root, token))) return { file: manuscriptRel, sample: token };
	if (context.sampleDir === null) return null;
	const candidate = `${context.sampleDir}/${token}`;
	if (existsSync(join(context.root, candidate))) return { file: manuscriptRel, sample: candidate };
	return null;
}

/**
 * Collect the manuscript/code pairs the drift check compares.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.settings - the resolved layout.
 * @returns {Array<{ file: string, sample: string }>} Unique pairs.
 */
export function collectDriftPairs({ root, settings }) {
	const pairs = new Map();
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		const directory = join(root, part.dir);
		if (!existsSync(directory)) continue;
		for (const entry of readdirNames(directory)) {
			if (!entry.endsWith('.md')) continue;
			if (part.filePattern !== undefined && !part.filePattern.test(entry) && !(part.extras ?? []).includes(entry)) continue;
			const rel = `${part.dir}/${entry}`;
			let text;
			try {
				text = readFileSync(join(root, rel), 'utf8');
			} catch {
				continue;
			}
			const context = { root, sampleDir: sampleDirFor(root, rel) };
			for (const match of text.matchAll(PATH_TOKEN)) {
				const pair = driftPairFor(rel, match[1], context);
				if (pair !== null) pairs.set(`${pair.file}|${pair.sample}`, pair);
			}
		}
	}
	return [...pairs.values()];
}
