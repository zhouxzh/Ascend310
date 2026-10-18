/**
 * Split overfull Marp slides so nothing is cropped by `overflow: hidden`.
 *
 * The theme pins each slide to 1280x720. A slide whose content exceeds that is
 * not scrollable — the rest is simply invisible. This script reflows a deck by:
 *
 * 1. splitting any single element that cannot fit (a code block longer than 10
 *    lines, a bullet run longer than 6, a table longer than 7 body rows);
 * 2. greedily packing the resulting blocks into pages under a height budget,
 *    repeating the heading with 「（续）」 on continuation pages.
 *
 * It never rewrites prose and never touches a fenced block's content beyond
 * dividing it. Run with `--write` to apply; without it, it reports only.
 *
 * Usage: node scripts/split-decks.mjs [--write] [deck.md ...]
 */

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

// Use the checker's own measurement so the fixer and the gate can never
// disagree about whether a slide fits.
const { measureSlide } = await import(
	new URL('../tools/dsh-plugin-ascend310b-writing/lib/deck.js', import.meta.url).href
);

const PRESENTATION_DIR = 'src/presentation';
const BUDGET = 560;
const MAX_CODE_LINES = 16;
const MAX_BULLETS = 6;
const MAX_TABLE_ROWS = 7;

function isFence(line) {
	return /^\s*(?:```|~~~)/u.test(line);
}

/** Split a deck into frontmatter, slides, keeping blank lines significant. */
function parseDeck(text) {
	const lines = text.split(/\r?\n/u);
	let cursor = 0;
	let frontmatter = [];
	if (lines[0]?.trim() === '---') {
		for (let index = 1; index < lines.length; index += 1) {
			if (lines[index].trim() === '---') {
				frontmatter = lines.slice(0, index + 1);
				cursor = index + 1;
				break;
			}
		}
	}
	const slides = [];
	let current = [];
	let inFence = false;
	for (let index = cursor; index < lines.length; index += 1) {
		const line = lines[index];
		if (isFence(line)) inFence = !inFence;
		if (!inFence && line.trim() === '---') {
			slides.push(current);
			current = [];
			continue;
		}
		current.push(line);
	}
	slides.push(current);
	return { frontmatter, slides };
}

/** Group a slide's body into atomic blocks separated by blank lines. */
function parseBlocks(lines) {
	const blocks = [];
	let current = [];
	let inFence = false;
	for (const line of lines) {
		if (isFence(line)) inFence = !inFence;
		if (!inFence && line.trim() === '') {
			if (current.length > 0) {
				blocks.push(current);
				current = [];
			}
			continue;
		}
		current.push(line);
	}
	if (current.length > 0) blocks.push(current);
	return blocks;
}

const isBullet = (line) => /^\s*(?:[-*+]|\d+[.)])\s+/u.test(line);
const isTableRow = (line) => /^\s*\|/u.test(line);

/** Measure a page with the same rules the audit uses. */
function estimateHeight(lines) {
	return measureSlide(lines).height;
}

/** Break one block that cannot fit into smaller blocks of the same kind. */
function splitOversized(block) {
	if (isFence(block[0]) && block.length - 2 > MAX_CODE_LINES) {
		const open = block[0];
		const close = block[block.length - 1];
		const body = block.slice(1, -1);
		const chunks = [];
		for (let index = 0; index < body.length; index += MAX_CODE_LINES) {
			chunks.push([open, ...body.slice(index, index + MAX_CODE_LINES), close]);
		}
		return chunks;
	}
	if (block.every((line) => isBullet(line)) && block.length > MAX_BULLETS) {
		const chunks = [];
		for (let index = 0; index < block.length; index += MAX_BULLETS) chunks.push(block.slice(index, index + MAX_BULLETS));
		return chunks;
	}
	if (block.length > 2 && block.every((line) => isTableRow(line)) && block.length - 2 > MAX_TABLE_ROWS) {
		const header = block.slice(0, 2);
		const rows = block.slice(2);
		const chunks = [];
		for (let index = 0; index < rows.length; index += MAX_TABLE_ROWS) chunks.push([...header, ...rows.slice(index, index + MAX_TABLE_ROWS)]);
		return chunks;
	}
	return [block];
}

/** Reflow one slide into one or more slides. */
function reflowSlide(slide) {
	const lines = slide;
	const headingIndex = lines.findIndex((line) => /^\s*#{1,3}\s+/u.test(line));
	if (headingIndex === -1) return [lines];
	const prelude = lines.slice(0, headingIndex).filter((line) => line.trim() !== '');
	const headingLine = lines[headingIndex];
	const headingMatch = /^(\s*)(#{1,3})\s+(.+?)\s*$/u.exec(headingLine);
	if (headingMatch === null) return [lines];
	const [, indent, hashes, title] = headingMatch;
	const paragraphs = parseBlocks(lines.slice(headingIndex + 1));
	const needsBlockSplit = paragraphs.some((block) => block.length - 2 > MAX_CODE_LINES && isFence(block[0]));
	if (estimateHeight(lines) <= BUDGET && !needsBlockSplit) return [lines];

	const blocks = paragraphs.flatMap((block) => splitOversized(block));
	const pages = [];
	let page = [...prelude, headingLine];
	let hasContent = false;
	for (const block of blocks) {
		const candidate = [...page, '', ...block];
		if (hasContent && estimateHeight(candidate) > BUDGET) {
			pages.push(page);
			page = [`${indent}${hashes} ${title}（续）`];
			hasContent = false;
		}
		page = [...page, '', ...block];
		hasContent = true;
	}
	pages.push(page);
	return pages.map((entry) => entry.filter((line, index, all) => !(line === '' && all[index - 1] === '')));
}

function reflowDeck(text) {
	const { frontmatter, slides } = parseDeck(text);
	const reflowed = slides.flatMap((slide) => reflowSlide(slide));
	const body = reflowed.map((page) => page.join('\n').replace(/^\n+|\n+$/gu, '')).join('\n\n---\n\n');
	if (frontmatter.length === 0) return `${body}\n`;
	return `${frontmatter.join('\n')}\n\n${body}\n`;
}

const args = process.argv.slice(2);
const write = args.includes('--write');
const includeArchive = args.includes('--include-archive');
const explicit = args.filter((argument) => !argument.startsWith('--'));
const defaultPattern = includeArchive ? /^(?:\d{2}-|legacy-\d{2}-).*\.md$/u : /^\d{2}-.*\.md$/u;
const files = explicit.length > 0 ? explicit : readdirSync(PRESENTATION_DIR).filter((name) => defaultPattern.test(name)).map((name) => join(PRESENTATION_DIR, name));

let changed = 0;
for (const file of files) {
	const before = readFileSync(file, 'utf8');
	const after = reflowDeck(before);
	if (after === before) continue;
	const beforePages = parseDeck(before).slides.length;
	const afterPages = parseDeck(after).slides.length;
	changed += 1;
	console.log(`${file}: ${beforePages} 页 -> ${afterPages} 页`);
	if (write) writeFileSync(file, after, 'utf8');
}
console.log(`${write ? '已写入' : '（预览，未写入）'} ${changed} 份`);
