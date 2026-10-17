/**
 * Prose-style analysis.
 *
 * Two properties matter and are easy to lose. First, the lexicon must not
 * report ordinary procedure words: `首先` appeared 44 times in the real book and
 * every one was a legitimate step word, so reporting it would drown the signal.
 * Second, a phrase repeated on one line is one thing to read, not five.
 *
 * Usage: node test/style.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { analyzeStyle, analyzeText, formatStyle, proseParagraphs } from '../lib/style.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-style-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};

write(
	'.tutorial-writing.json',
	JSON.stringify(
		{
			sourceRoot: 'src',
			agentsFile: 'AGENTS.md',
			sidebarFile: 'src/.vuepress/sidebar.ts',
			parts: [
				{
					key: 'book',
					label: '理论教程',
					dir: 'src/book',
					filePattern: '^chapter(\\d+)\\.md$',
					extras: ['README.md'],
					titlePattern: '^第(\\d+)章：\\S',
					titleHint: '第N章：标题',
					titleInFrontmatter: true
				}
			]
		},
		null,
		2
	)
);
write('src/book/chapter1.md', [
	'---',
	'title: "第1章：测试"',
	'---',
	'',
	'# 第1章：测试',
	'',
	'这款芯片提供极致的性能，极致的能效。',
	'',
	'综上所述，它很出色。',
	'',
	'本文将介绍边缘计算的背景。',
	'',
	'正如您所指出的，功耗是核心约束。',
	'',
	'它不仅要跑得快，而且要省电。',
	'',
	'首先调用 `acl.init`，随后创建 Context。这是一句正常的步骤说明，不应被报告。',
	''
].join('\n'));

const text = [
	'这款芯片提供极致的性能，极致的能效。',
	'',
	'综上所述，它很出色。',
	'',
	'本文将介绍边缘计算的背景。',
	'',
	'首先调用 `acl.init`，随后创建 Context。'
].join('\n');
const single = analyzeText({ file: 'x.md', text });

// --- what the lexicon must catch -------------------------------------------
check(
	single.hits.some((hit) => hit.group === '营销腔' && hit.phrase === '极致'),
	'marketing adjectives must be reported'
);
check(single.hits.some((hit) => hit.group === '空转套话' && hit.phrase === '综上所述'), 'filler must be reported');
check(single.hits.some((hit) => hit.group === '元话语' && hit.phrase === '本文将'), 'meta-discourse must be reported');

// --- what it must NOT report ----------------------------------------------
check(
	!single.hits.some((hit) => hit.phrase === '首先'),
	`a procedure word must not be reported as a cliche (首次出现会把工具变成噪音源)`
);

// --- one line is one thing to read ----------------------------------------
const repeated = single.hits.filter((hit) => hit.phrase === '极致');
check(repeated.length === 1, `a repeated phrase on one line must be one hit, got ${repeated.length}`);
check(repeated[0]?.count === 2, `the hit must carry its occurrence count, got ${repeated[0]?.count}`);

// --- shape ------------------------------------------------------------------
check(single.paragraphs.count >= 3, `paragraphs must be counted, got ${single.paragraphs.count}`);
check(single.sentences.count >= 3, `sentences must be counted, got ${single.sentences.count}`);
const paragraphs = proseParagraphs(['```bash', 'echo hi', '```', '', '第一段。', '', '第二段。', ''].join('\n'));
check(paragraphs.length === 2, `a code block must not count as prose, got ${paragraphs.length}`);
check(paragraphs.includes('第一段。'), 'prose paragraphs must survive the fence filtering');

// --- the whole analysis ----------------------------------------------------
const analysis = analyzeStyle({ root, settings: resolveSettings({ root }) });
check(analysis.totals.files === 1, `one manuscript expected, got ${analysis.totals.files}`);
check(analysis.totals.hits > 0, 'the fixture must produce hits');
check(analysis.totals.byGroup['对话残留'] === 1, `the chat leftover must be reported, got ${analysis.totals.byGroup['对话残留']}`);
const pairedNames = analysis.files[0].paired.map((rule) => rule.name);
check(pairedNames.includes('不仅…而且'), `the paired construction must be counted, got ${pairedNames.join(',')}`);

const report = formatStyle({ analysis });
check(report.includes('只提示，不判定对错'), 'the report must say it does not judge');
check(report.includes('不是错误'), 'the report must repeat that a hit is not an error');
check(report.includes('src/book/chapter1.md'), 'the report must name the file');

// --- a clean text produces nothing ---------------------------------------
const clean = analyzeText({ file: 'clean.md', text: ['直接调用 `acl.init` 完成初始化。', '', '再创建 Context 与 Stream。', ''].join('\n') });
check(clean.hits.length === 0, `a clean text must produce no hits, got ${clean.hits.length}`);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('style test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('style test passed');
console.log('  四类命中：营销腔 / 空转套话 / 元话语 / 对话残留；步骤词「首先」不报；同一行重复只报一次并带次数');
console.log('  段落、句子长度统计排除代码块；报告明示"不是错误"');
