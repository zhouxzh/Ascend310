/**
 * PDF chapter-map drift.
 *
 * `convert-vuepress.sh` turns each `src/book/chapterN.md` into
 * `latex/chapters/chapterN.tex`, but the `\chapter{...}` titles and their
 * `\input{}` order live in the hand-maintained `latex/book.tex`. When a new
 * chapter is inserted, that table can point one title at another chapter's
 * body, or drop a chapter, while every Markdown file still looks correct. That
 * is the defect class this check exists for: the manuscript audit cannot see it
 * because the mistake lives between the source and the generated PDF.
 *
 * Usage: node test/latex-map.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectLatexChapterFindings } from '../lib/checks.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-latex-map-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};

write('src/book/chapter1.md', ['---', 'title: "第1章：甲章"', '---', '', '## 1.1 内容', ''].join('\n'));
write('src/book/chapter2.md', ['---', 'title: "第2章：乙章"', '---', '', '## 2.1 内容', ''].join('\n'));

const settings = resolveSettings({ root });

// 1. The corrected table matches the source.
write('latex/book.tex', [
	'\\chapter{甲章}',
	'\\input{chapters/chapter1.tex}',
	'\\chapter{乙章}',
	'\\input{chapters/chapter2.tex}',
	''
].join('\n'));
const healthy = collectLatexChapterFindings({ root, settings });
check(healthy.available, 'the check must run when latex/book.tex exists');
check(healthy.chapters === 2, `two chapters expected, got ${healthy.chapters}`);
check(healthy.findings.length === 0, `a matching table must not report: ${healthy.findings.map((f) => f.rule).join(',')}`);

// 2. The historical defect: the title row points at the wrong chapter body.
write('latex/book.tex', [
	'\\chapter{乙章}',
	'\\input{chapters/chapter1.tex}',
	'\\chapter{乙章}',
	'\\input{chapters/chapter2.tex}',
	''
].join('\n'));
const drifted = collectLatexChapterFindings({ root, settings });
check(
	drifted.findings.some((finding) => finding.rule === 'latex-chapter-title' && finding.message.includes('第 1 章')),
	'a title attached to the wrong chapter body must be reported'
);

// 3. A dropped chapter.
write('latex/book.tex', ['\\chapter{甲章}', '\\input{chapters/chapter1.tex}', ''].join('\n'));
const dropped = collectLatexChapterFindings({ root, settings });
check(
	dropped.findings.some((finding) => finding.rule === 'latex-chapter-missing' && finding.message.includes('第 2 章')),
	'a chapter missing from book.tex must be reported'
);

// 4. A table entry with no source chapter.
write('latex/book.tex', [
	'\\chapter{甲章}',
	'\\input{chapters/chapter1.tex}',
	'\\chapter{乙章}',
	'\\input{chapters/chapter2.tex}',
	'\\chapter{丙章}',
	'\\input{chapters/chapter3.tex}',
	''
].join('\n'));
const extra = collectLatexChapterFindings({ root, settings });
check(
	extra.findings.some((finding) => finding.rule === 'latex-chapter-extra' && finding.message.includes('第 3 章')),
	'a table entry without a source chapter must be reported'
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('latex map test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('latex map test passed');
console.log('  一致 / 标题错位 / 缺章 / 多章 四种情形均已覆盖');
