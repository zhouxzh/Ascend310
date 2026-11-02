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
		'',
		'## 3. 手写章节编号',
		'',
		'### 3.1 手写子章节编号',
		''
	].join('\n')
);
const shaped = auditRepo({ root, maxFindings: 200 });
const longHeads = shaped.findings.filter((finding) => finding.rule === 'heading-too-long');
const linkHeads = shaped.findings.filter((finding) => finding.rule === 'heading-has-link');
const manualHeads = shaped.findings.filter(
	(finding) => finding.rule === 'heading-manual-number' && finding.file.endsWith('chapter2.md')
);
check(manualHeads.length === 1, `one aggregated manual-number finding expected, got ${manualHeads.length}`);
check(manualHeads[0]?.severity === 'error', 'manual numbering must block the audit');
check(
	manualHeads[0]?.message.includes('2 个标题手写章节编号'),
	`manual-number warning must aggregate both headings, got: ${manualHeads[0]?.message}`
);
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

// --- author spacing convention ---------------------------------------------
// The author's Chinese-academic style uses no literal space at a Han/Latin or
// Han/digit boundary. English word spaces and English/version spaces remain.
writeFileSync(
	join(root, 'src', 'book', 'chapter5.md'),
	[
		'---',
		'title: "第5章：中文 English"',
		'---',
		'',
		'使用 Python 3.9运行，共 5 个样本。',
		'正确写法：使用Python3.9运行，共5个样本。',
		'`使用 Python 3.9` 与 `Python 3.9` 保持原样。',
		'',
		'```text',
		'中文 English',
		'```',
		''
	].join('\n'),
	'utf8'
);
const spacing = auditRepo({ root, maxFindings: 300 });
const spacingFindings = spacing.findings.filter(
	(finding) => finding.file.endsWith('chapter5.md') && finding.rule === 'cjk-latin-space'
);
check(spacingFindings.length === 1, `one aggregated spacing finding expected, got ${spacingFindings.length}`);
check(
	spacingFindings[0]?.line === 5 && spacingFindings[0]?.message.includes('2 处'),
	`spacing finding must report two body occurrences, got: ${spacingFindings[0]?.message}`
);
check(
	spacingFindings[0]?.severity === 'warning',
	'the legacy-corpus spacing guard must remain a warning while new prose must fix it'
);

// --- table/list/prose selection --------------------------------------------
// A tutorial can use a matrix for shared columns, but a long run of tables is
// usually a sign that one-dimensional content should be lists or prose.
const tableLines = [
	'---',
	'title: "第6章：表格密度"',
	'---',
	'',
	'## 表格密度',
	'',
	'下面刻意放置多个表格，用来触发文件级和章节级密度检查。'
];
for (let index = 0; index < 13; index += 1) {
	tableLines.push(
		'',
		`段落${index + 1}。`,
		'',
		'| 项目 | 说明 |',
		'| --- | --- |',
		`| 项目${index + 1} | 单维说明 |`
	);
}
tableLines.push('');
writeFileSync(join(root, 'src', 'book', 'chapter6.md'), tableLines.join('\n'), 'utf8');
const tableDensity = auditRepo({ root, maxFindings: 400 }).findings.filter(
	(finding) => finding.file.endsWith('chapter6.md') && finding.rule.startsWith('table-')
);
check(
	tableDensity.some((finding) => finding.rule === 'table-density'),
	'a file above the table limit must request a table/list/prose review'
);
check(
	tableDensity.some((finding) => finding.rule === 'table-section-overuse'),
	'a section above the table limit must request a table/list/prose review'
);

// --- duplicate headings and labels -----------------------------------------
writeFileSync(
	join(root, 'src', 'book', 'chapter7.md'),
	[
		'---',
		'title: "第7章：重复章节"',
		'---',
		'',
		'## 参考资料',
		'',
		'第一节。',
		'',
		'## 参考资料',
		'',
		'第二节。',
		'',
		'参考资料：',
		'',
		'- 示例条目。',
		''
	].join('\n'),
	'utf8'
);
const duplicateSections = auditRepo({ root, maxFindings: 400 }).findings.filter(
	(finding) => finding.file.endsWith('chapter7.md')
);
check(
	duplicateSections.some((finding) => finding.rule === 'duplicate-heading'),
	'duplicate heading text must be reported'
);
check(
	duplicateSections.some((finding) => finding.rule === 'duplicate-section-label'),
	'a standalone label matching a section heading must be reported'
);

// --- conceptual list density ----------------------------------------------
const listLines = [
	'---',
	'title: "第8章：列表密度"',
	'---',
	'',
	'## 概念说明',
	'',
	'下面这些条目应当先判断是否存在连续解释关系。'
];
for (let index = 0; index < 11; index += 1) listLines.push(`- 概念条目${index + 1}`);
listLines.push('', '## 验证清单', '');
for (let index = 0; index < 15; index += 1) listLines.push(`- [ ] 检查项${index + 1}`);
listLines.push('');
writeFileSync(join(root, 'src', 'book', 'chapter8.md'), listLines.join('\n'), 'utf8');
const listDensity = auditRepo({ root, maxFindings: 400 }).findings.filter(
	(finding) => finding.file.endsWith('chapter8.md') && finding.rule === 'list-section-density'
);
check(listDensity.length === 1, `one conceptual list-density warning expected, got ${listDensity.length}`);
check(
	listDensity[0]?.line === 5,
	'checklist sections must be exempt from conceptual list-density warnings'
);

// --- reader-visible evidence details ----------------------------------------
// Reader prose should explain outcomes instead of exposing a local checkout,
// an exact artifact digest or a byte count. Commands inside fenced code remain
// legitimate executable instruction and must not be reported.
writeFileSync(
	join(root, 'src', 'book', 'chapter4.md'),
	[
		'---',
		'title: "第4章：读者证据边界"',
		'---',
		'',
		'结果图来自 C:\\Users\\author\\run.png，文件为 12345 bytes。',
		'完整 SHA-256 为 `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`。',
		'临时图位于 /tmp/appendix4-private/result.png。',
		'',
		'```bash',
		'sha256sum model.om',
		'cd /tmp/appendix4-private',
		'```',
		''
	].join('\n'),
	'utf8'
);
const readerEvidence = auditRepo({ root, maxFindings: 300 });
for (const rule of ['reader-evidence-digest', 'reader-evidence-bytes', 'reader-machine-path', 'reader-internal-path']) {
	check(
		readerEvidence.findings.some((finding) => finding.rule === rule && finding.file.endsWith('chapter4.md')),
		`reader-visible evidence must report ${rule}`
	);
}
check(
	!readerEvidence.findings.some((finding) => finding.file.endsWith('chapter4.md') && finding.line >= 10 && finding.rule.startsWith('reader-')),
	'commands in fenced blocks must remain exempt from reader-evidence warnings'
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
