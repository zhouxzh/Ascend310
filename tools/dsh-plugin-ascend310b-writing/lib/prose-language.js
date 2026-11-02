/**
 * Detect repository-authored prose that has drifted from Chinese into English.
 *
 * Ascend310 learner material keeps commands, API names, paths, model names and
 * other technical tokens in their original form. Those tokens are therefore
 * removed where possible before counting prose. The remaining Chinese and
 * English words decide whether a document is written in the repository's
 * learner-facing language rather than merely containing technical English.
 */

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/gu;
const LATIN_WORD = /[A-Za-z]+(?:['\u2019-][A-Za-z]+)*/gu;
const FENCE = /^\s*(`{3,}|~{3,})/u;
const INLINE_CODE = /`[^`\n]*`/gu;
const IMAGE_OR_LINK = /!?\[([^\]]*)\]\([^)]*\)/gu;
const BARE_URL = /(?:https?:\/\/|www\.)[^\s)>]+/giu;
const AUTOLINK = /<https?:\/\/[^>]+>/giu;
const HTML_TAG = /<[^>\n]+>/gu;
const TABLE_DIVIDER = /^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/u;

/** Count Han characters in a string. */
function countCjk(text) {
	return (String(text).match(CJK) ?? []).length;
}

/** Count English words in a string. */
function countLatinWords(text) {
	return (String(text).match(LATIN_WORD) ?? []).length;
}

/**
 * Remove non-prose Markdown while preserving the human-visible link and image
 * labels. Fenced code is the most important exclusion: an English command is
 * not evidence that the surrounding explanation is English.
 *
 * @param {string} text - Markdown source.
 * @returns {{lines: string[], lineNumbers: number[]}} Visible prose lines.
 */
export function markdownProseLines(text) {
	const source = String(text ?? '').replace(/\r\n?/gu, '\n').split('\n');
	const lines = [];
	const lineNumbers = [];
	let start = 0;
	if (source[0]?.trim() === '---') {
		for (let i = 1; i < source.length; i += 1) {
			if (source[i].trim() === '---') {
				start = i + 1;
				break;
			}
		}
	}

	let fence = '';
	let inComment = false;
	for (let i = start; i < source.length; i += 1) {
		let line = source[i];
		const fenceMatch = FENCE.exec(line);
		if (fence !== '') {
			if (fenceMatch !== null && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) {
				fence = '';
			}
			continue;
		}
		if (fenceMatch !== null) {
			fence = fenceMatch[1];
			continue;
		}
		if (inComment) {
			const end = line.indexOf('-->');
			if (end < 0) continue;
			line = line.slice(end + 3);
			inComment = false;
		}
		for (;;) {
			const begin = line.indexOf('<!--');
			if (begin < 0) break;
			const end = line.indexOf('-->', begin + 4);
			if (end >= 0) {
				line = line.slice(0, begin) + line.slice(end + 3);
				continue;
			}
			line = line.slice(0, begin);
			inComment = true;
			break;
		}
		if (line.trim() === '' || TABLE_DIVIDER.test(line)) continue;
		line = line
			.replace(INLINE_CODE, ' ')
			.replace(AUTOLINK, ' ')
			.replace(IMAGE_OR_LINK, '$1')
			.replace(BARE_URL, ' ')
			.replace(HTML_TAG, ' ')
			.replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/u, '')
			.replace(/\|/gu, ' ')
			.replace(/\s+/gu, ' ')
			.trim();
		if (line === '') continue;
		lines.push(line);
		lineNumbers.push(i + 1);
	}
	return { lines, lineNumbers };
}

/**
 * Decide whether a Markdown document is English-dominant learner prose.
 *
 * The thresholds deliberately tolerate dense English identifiers in Chinese
 * technical text. They reject files that contain no meaningful Chinese prose,
 * or whose Chinese content is only a token fraction of a much larger English
 * body.
 *
 * @param {string} text - Markdown source.
 * @returns {{
 *   cjkChars: number,
 *   latinWords: number,
 *   chineseShare: number,
 *   englishParagraphs: number,
 *   firstEnglishLine: number | null,
 *   isEnglishDominant: boolean
 * }} Language statistics.
 */
export function analyzeProseLanguage(text) {
	const { lines, lineNumbers } = markdownProseLines(text);
	let cjkChars = 0;
	let latinWords = 0;
	let englishParagraphs = 0;
	let firstEnglishLine = null;

	for (let i = 0; i < lines.length; i += 1) {
		const cjk = countCjk(lines[i]);
		const latin = countLatinWords(lines[i]);
		cjkChars += cjk;
		latinWords += latin;
		if (cjk === 0 && latin >= 20) {
			englishParagraphs += 1;
			firstEnglishLine ??= lineNumbers[i];
		}
	}

	const chineseShare = cjkChars + latinWords > 0 ? cjkChars / (cjkChars + latinWords) : 0;
	const noChineseProse = cjkChars === 0 && latinWords >= 20;
	const overwhelminglyEnglish = latinWords >= 50 && chineseShare < 0.08;
	const isEnglishDominant = noChineseProse || overwhelminglyEnglish;

	return {
		cjkChars,
		latinWords,
		chineseShare,
		englishParagraphs,
		firstEnglishLine: firstEnglishLine ?? lineNumbers[0] ?? null,
		isEnglishDominant
	};
}

/**
 * Build one actionable language finding for a repository-authored document.
 *
 * @param {string} file - repository-relative file path.
 * @param {string} text - Markdown source.
 * @param {number} [lineOffset] - line offset when `text` is only the body.
 * @returns {object | null} A finding, or null when the prose is Chinese-dominant.
 */
export function proseLanguageFinding(file, text, lineOffset = 0) {
	const stats = analyzeProseLanguage(text);
	if (!stats.isEnglishDominant) return null;
	const percent = Math.round(stats.chineseShare * 100);
	return {
		rule: 'prose-language',
		severity: 'error',
		file,
		line: (stats.firstEnglishLine ?? 1) + lineOffset,
		message:
			`正文以英文为主：剔除代码、行内代码和链接目标后，有${stats.cjkChars}个中文字符、` +
			`${stats.latinWords}个英文词，中文占比${percent}%。仓库自有的学生文档必须使用简体中文；` +
			'命令、API、路径、型号、日志原文和外部引用可保留英文。'
	};
}
