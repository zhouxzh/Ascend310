/**
 * Regression tests for the manuscript rules whose answer needs a judgement
 * rather than a pattern: which trailing whitespace can be deleted, and which
 * run is a hard break the author has to decide about.
 *
 * Usage: node test/manuscript-rules.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditRepo } from '../lib/audit-core.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-rules-${Date.now()}`);
mkdirSync(join(root, 'src', 'book'), { recursive: true });
writeFileSync(
	join(root, 'src', 'book', 'chapter1.md'),
	[
		'---',
		'title: "第1章：分级测试"',
		'---',
		'',
		'1. 第一步，无硬换行',
		'   说明文字。',
		'',
		'2. 第二步，有硬换行  ',
		'   说明文字。',
		'',
		'3. 第三步，无硬换行',
		'   说明文字。',
		'',
		'整行只有空格的行：',
		'   ',
		'下一行是空行，所以硬换行无效：  ',
		'',
		'单空格不算硬换行： ',
		'',
		'下一个块是代码围栏，硬换行也无效：  ',
		'```bash',
		'echo hi',
		'```',
		'',
		'下面两项都写了硬换行，属于有意的一致写法：',
		'',
		'1. 甲项  ',
		'   说明。',
		'',
		'2. 乙项  ',
		'   说明。',
		''
	].join('\n'),
	'utf8'
);

const report = auditRepo({ root, maxFindings: 200 });
const effective = report.findings.filter((finding) => finding.rule === 'hard-break-effective');
const inert = report.findings.filter((finding) => finding.rule === 'trailing-whitespace');

check(effective.length === 3, `expected 3 effective hard breaks, got ${effective.length}`);
check(inert.length === 4, `expected 4 inert trailing runs, got ${inert.length}`);
check(
	report.findings.every((finding) => finding.severity === 'warning'),
	'both classifications are warnings'
);

const lone = effective.find((finding) => finding.line === 8);
check(lone !== undefined, 'the hard break on the lone list item must be reported');
check(
	lone !== undefined && lone.message.includes('另外 2 项都没有这样写'),
	`an inconsistent lone break must say so, got: ${lone?.message}`
);

const consistent = effective.filter((finding) => finding.message.includes('/'));
check(
	consistent.length === 2 && consistent.every((finding) => finding.message.includes('2/2')),
	`two sibling breaks must both report 2/2, got ${JSON.stringify(consistent.map((f) => f.line))}`
);

// A whitespace-only line is blank, so its run is inert, not a hard break.
check(
	inert.some((finding) => finding.line === 15),
	'a whitespace-only line must be classified as inert trailing whitespace'
);
check(
	inert.some((finding) => finding.line === 16),
	'a run before a blank line must be classified as inert'
);
check(
	inert.some((finding) => finding.line === 18),
	'a single space must be classified as inert'
);
check(
	inert.some((finding) => finding.line === 20),
	'a run before a fenced block must be classified as inert'
);
check(
	!effective.some((finding) => [15, 16, 18, 20].includes(finding.line)),
	'inert runs must not be reported as hard breaks'
);

// --- heading shape ----------------------------------------------------------
// A long heading is prose in disguise, a link in a heading breaks the bookmark
// text, and a YAML `#` comment is metadata rather than a heading.
writeFileSync(
	join(root, 'src', 'book', 'chapter2.md'),
	[
		'---',
		'title: "第2章：标题形状"',
		'# avatar: https://example.test/avatar.png',
		'---',
		'',
		'## 简短标题',
		'',
		'## 这是一个刻意写得很长很长用来触发标题长度规则的二级标题超过三十二个字符',
		'',
		'## 走读 [`check.py`](https://example.test/check.py)',
		''
	].join('\n')
);
const shaped = auditRepo({ root, maxFindings: 200 });
const longHeads = shaped.findings.filter((finding) => finding.rule === 'heading-too-long');
const linkHeads = shaped.findings.filter((finding) => finding.rule === 'heading-has-link');
check(longHeads.length === 1, `one long heading expected, got ${longHeads.length}`);
check(longHeads[0]?.file.endsWith('chapter2.md'), 'the long heading must be reported on chapter2');
check(linkHeads.length === 1, `one linked heading expected, got ${linkHeads.length}`);
check(
	!shaped.findings.some((finding) => finding.file.endsWith('chapter2.md') && finding.line === 3),
	'a YAML comment in frontmatter must not be parsed as a heading'
);
check(
	!shaped.findings.some((finding) => finding.file.endsWith('chapter2.md') && finding.rule === 'h1-count'),
	'a comment in frontmatter must not disturb the H1 count'
);

// --- layout: wide tables and very long code blocks --------------------------
// Eight columns cannot fit A4 or the web content column, and a 90-line block
// becomes a wall of code in both outputs.
writeFileSync(
	join(root, 'src', 'book', 'chapter3.md'),
	[
		'---',
		'title: "第3章：排版形状"',
		'---',
		'',
		'| a | b | c | d | e | f | g | h |',
		'| --- | --- | --- | --- | --- | --- | --- | --- |',
		'| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |',
		'',
		'```python',
		...Array.from({ length: 90 }, (_value, index) => `x${index} = ${index}`),
		'```',
		''
	].join('\n')
);
const layout = auditRepo({ root, maxFindings: 300 });
check(
	layout.findings.some((finding) => finding.rule === 'layout-table-wide' && finding.file.endsWith('chapter3.md')),
	'an eight-column table must be reported'
);
check(
	layout.findings.some((finding) => finding.rule === 'layout-code-long' && finding.file.endsWith('chapter3.md')),
	'a 90-line code block must be reported'
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('manuscript rule test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('manuscript rule test passed');
console.log(`  有效硬换行 ${effective.length} 条（含一条孤立项提示、两条 2/2 一致提示）`);
console.log(`  可安全删除 ${inert.length} 条（整行空白 / 空行前 / 单空格 / 代码围栏前）`);
