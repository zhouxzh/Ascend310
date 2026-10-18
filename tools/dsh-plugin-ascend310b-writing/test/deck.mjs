/**
 * Presentation-deck layout checks.
 *
 * The failure this guards against is silent: the Marp theme pins slides to
 * 1280x720 with `overflow: hidden`, so overfull content is cropped rather than
 * scrolled. The fixture therefore pins the measurement thresholds, the slide
 * splitting (frontmatter and fenced `---` must not paginate), and the compute
 * tier rule.
 *
 * Usage: node test/deck.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditDeckText, collectDeckFindings, measureSlide, splitSlides } from '../lib/deck.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

// --- pagination -------------------------------------------------------------
const deck = [
	'---',
	'marp: true',
	'theme: ascend310',
	'---',
	'',
	'# 第一页',
	'',
	'```bash',
	'echo one',
	'---',
	'echo two',
	'```',
	'',
	'---',
	'',
	'# 第二页',
	'',
	'- 要点',
	''
].join('\n');
const slides = splitSlides(deck);
check(slides.length === 2, `frontmatter and fenced --- must not paginate, got ${slides.length} slides`);

// --- measurement ------------------------------------------------------------
const wideTable = measureSlide([
	'## 宽表',
	'',
	'| a | b | c | d | e | f | g | h |',
	'| --- | --- | --- | --- | --- | --- | --- | --- |',
	'| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |',
	''
]);
check(wideTable.tableColumns === 8, `eight table columns expected, got ${wideTable.tableColumns}`);

const longCode = measureSlide(['## 长代码', '', '```python', ...Array.from({ length: 30 }, (_v, i) => `x${i} = ${i}`), '```', '']);
check(longCode.codeLines === 30, `30 code lines expected, got ${longCode.codeLines}`);

// --- rules ------------------------------------------------------------------
const overflowFindings = auditDeckText({ file: 'x.md', text: longCode === null ? '' : ['---', '# 长代码', '', '```python', ...Array.from({ length: 30 }, (_v, i) => `x${i} = ${i}`), '```', ''].join('\n') });
check(overflowFindings.some((f) => f.rule === 'deck-slide-overflow'), 'a 30-line code slide must be reported as overflow');
check(overflowFindings.some((f) => f.rule === 'deck-code-too-long'), 'a 30-line code slide must be reported as too long');

const tableFindings = auditDeckText({
	file: 'x.md',
	text: ['---', '# 宽表', '', '| a | b | c | d | e | f | g | h |', '| --- | --- | --- | --- | --- | --- | --- | --- |', '| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |', ''].join('\n')
});
check(tableFindings.some((f) => f.rule === 'deck-table-too-wide'), 'an eight-column table must be reported');

const tierFindings = auditDeckText({ file: 'x.md', text: ['---', '# 口径', '', '发布口径是 Ascend 310B1 / 20T。', ''].join('\n') });
check(tierFindings.some((f) => f.rule === 'deck-tier-claim'), 'a 20T-only claim must be reported');
const scopedTier = auditDeckText({ file: 'x.md', text: ['---', '# 口径', '', '本项目是 Ascend 310B4 / 8T；20T 不继承本结论。', ''].join('\n') });
check(!scopedTier.some((f) => f.rule === 'deck-tier-claim'), 'a scoped tier claim must not be reported');

const clean = auditDeckText({ file: 'x.md', text: ['---', '# 干净的一页', '', '- 三条要点', '- 没有超长代码', ''].join('\n') });
check(clean.length === 0, `a clean slide must produce nothing, got ${clean.map((f) => f.rule).join(',')}`);

// --- collection and README order -------------------------------------------
const root = join(tmpdir(), `tw-deck-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};
write(
	'.tutorial-writing.json',
	JSON.stringify({
		sourceRoot: 'src',
		agentsFile: 'AGENTS.md',
		sidebarFile: 'src/.vuepress/sidebar.ts',
		parts: [{ key: 'presentation', label: '配套演示', dir: 'src/presentation', filePattern: '^\\d{2}-[a-z0-9-]+\\.md$', assets: true }]
	})
);
write('src/presentation/01-alpha.md', ['---', '# Alpha', '', '- 一', ''].join('\n'));
write('src/presentation/02-beta.md', ['---', '# Beta', '', '- 二', ''].join('\n'));
write(
	'src/presentation/README.md',
	[
		'# 演示',
		'',
		'## 课件目录',
		'',
		'| 单元 | 链接 |',
		'| --- | --- |',
		'| Alpha | [打开](https://example.test/presentation/02-beta.html) |',
		'| Beta | [打开](https://example.test/presentation/01-alpha.html) |',
		''
	].join('\n')
);
const collection = collectDeckFindings({ root, settings: resolveSettings({ root }) });
check(collection.decks === 2, `two decks expected, got ${collection.decks}`);
check(
	collection.findings.some((finding) => finding.rule === 'deck-readme-order'),
	'README order that disagrees with the file numbers must be reported'
);

write(
	'src/presentation/README.md',
	[
		'# 演示',
		'',
		'## 课件目录',
		'',
		'| Alpha | [打开](https://example.test/presentation/01-alpha.html) |',
		'| Beta | [打开](https://example.test/presentation/02-beta.html) |',
		'',
		'## 本地预览',
		'',
		'```text',
		'http://localhost/presentation/02-beta.html',
		'```',
		''
	].join('\n')
);
const reordered = collectDeckFindings({ root, settings: resolveSettings({ root }) });
check(
	!reordered.findings.some((finding) => finding.rule === 'deck-readme-order'),
	'the local-preview list must not be counted as catalogue order'
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('deck test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('deck test passed');
console.log('  分页：frontmatter 与围栏内的 --- 不计页；度量：列数/代码行数');
console.log('  规则：溢出 / 代码过长 / 表格过宽 / 算力口径 / README 顺序（本地预览不参与）');
