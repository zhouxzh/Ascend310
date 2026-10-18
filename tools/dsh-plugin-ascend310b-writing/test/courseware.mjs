/**
 * Courseware standards.
 *
 * The rules merged here come from two open-source projects, so the fixture
 * pins their intent rather than this repository's phrasing:
 *
 * - markdownlint: MD045 (image alt text), MD040 (fenced code language),
 *   MD034 (no bare URLs).
 * - Marp: presenter notes are a first-class feature (`--notes`, `--pdf-notes`,
 *   presenter view), deck metadata drives rendering, and auto-scaling is
 *   horizontal only.
 *
 * Usage: node test/courseware.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditCoursewareText, auditTheme, collectCoursewareFindings } from '../lib/courseware.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const deck = (body, frontmatter = 'marp: true\ntheme: ascend310\npaginate: true\nsize: 16:9') =>
	['---', frontmatter, '---', '', '# 标题', '', body, ''].join('\n');

// --- markdownlint-derived accessibility rules ------------------------------
const alt = auditCoursewareText({ file: 'x.md', text: deck('![](img/a.png)') });
check(alt.some((f) => f.rule === 'deck-image-no-alt'), 'MD045: an image without alt text must be reported');

const lang = auditCoursewareText({ file: 'x.md', text: deck(['```', 'echo hi', '```'].join('\n')) });
check(lang.some((f) => f.rule === 'deck-code-no-language'), 'MD040: a fenced block without a language must be reported');

const bare = auditCoursewareText({ file: 'x.md', text: deck('参考 https://example.test/docs 获取更多。') });
check(bare.some((f) => f.rule === 'deck-bare-url'), 'MD034: a bare URL must be reported');

const inlineCode = auditCoursewareText({ file: 'x.md', text: deck('等终端显示 `http://127.0.0.1:3080` 后再刷新。') });
check(
	!inlineCode.some((f) => f.rule === 'deck-bare-url'),
	'a URL inside inline code is a value to type, not a link, and must not be reported'
);

const linked = auditCoursewareText({ file: 'x.md', text: deck('参见 [Marp CLI 文档](https://example.test/marp)。') });
check(!linked.some((f) => f.rule === 'deck-bare-url'), 'a descriptive link must not be reported');

// --- traceability ----------------------------------------------------------
const noSource = auditCoursewareText({ file: 'x.md', text: deck('- 只看课件') });
check(noSource.some((f) => f.rule === 'deck-source-missing'), 'a deck with no manuscript or sample path must be reported');

const withSource = auditCoursewareText({ file: 'x.md', text: deck('对应教材：`src/experiment/case1.md`') });
check(!withSource.some((f) => f.rule === 'deck-source-missing'), 'a deck naming its source must pass');

// --- Marp metadata ---------------------------------------------------------
const noMeta = auditCoursewareText({ file: 'x.md', text: deck('对应教材：`src/book/chapter1.md`', 'title: 只有标题') });
check(
	noMeta.filter((f) => f.rule === 'deck-metadata-missing').length >= 2,
	'Marp metadata (theme/paginate/size) must be required'
);

// --- presenter notes -------------------------------------------------------
const notesDeck = auditCoursewareText({ file: 'x.md', text: deck('对应教材：`src/book/chapter1.md`') });
check(notesDeck.some((f) => f.rule === 'deck-notes-sparse'), 'a deck with no presenter notes must report sparse coverage');

// --- theme font floor ------------------------------------------------------
const root = join(tmpdir(), `tw-courseware-${Date.now()}`);
mkdirSync(join(root, 'src/presentation'), { recursive: true });
const write = (relativePath, text) => writeFileSync(join(root, relativePath), text, 'utf8');
write(
	'.tutorial-writing.json',
	JSON.stringify({
		sourceRoot: 'src',
		agentsFile: 'AGENTS.md',
		sidebarFile: 'src/.vuepress/sidebar.ts',
		parts: [{ key: 'presentation', label: '配套演示', dir: 'src/presentation', filePattern: '^\\d{2}-[a-z0-9-]+\\.md$', assets: true }]
	})
);
write('src/presentation/theme.css', ['section pre { font-size: 13px; }', 'section table { font-size: 18px; }', 'section::after { font-size: 14px; }'].join('\n'));
const small = auditTheme({ root, themePath: 'src/presentation/theme.css' });
check(small.length === 1, `only the content rule below 18px may be reported, got ${small.length}`);
check(small[0]?.message.includes('13px'), 'the finding must name the offending size');

write('src/presentation/theme.css', ['section pre { font-size: 18px; }', 'section table { font-size: 18px; }', 'section::after { font-size: 12px; }'].join('\n'));
check(auditTheme({ root, themePath: 'src/presentation/theme.css' }).length === 0, 'a 12px footer is peripheral and must not be reported');

write(
	'src/presentation/01-alpha.md',
	deck(['对应教材：`src/book/chapter1.md`', '', '```text', 'tree', '```'].join('\n'))
);
const collected = collectCoursewareFindings({ root, settings: resolveSettings({ root }) });
check(collected.decks === 1, `one deck expected, got ${collected.decks}`);
check(collected.findings.every((finding) => finding.file.startsWith('src/presentation/')), 'findings must point at the deck');

// --- density and teaching structure ---------------------------------------
const thin = auditCoursewareText({
	file: 'src/presentation/10-chapter1.md',
	text: ['---', 'marp: true', 'theme: ascend310', 'paginate: true', 'size: 16:9', '---', '', '<!-- _class: cover -->', '', '# 只有封面', '', '对应教材：`src/book/chapter1.md`', ''].join('\n')
});
check(thin.some((finding) => finding.rule === 'deck-content-thin'), 'a four-slide deck must be reported as thin');
check(thin.some((finding) => finding.rule === 'deck-summary-missing'), 'a deck without a summary must be reported');
check(!thin.some((finding) => finding.rule === 'deck-cover-missing'), 'a deck with a cover must not be reported as missing one');

const underfull = auditCoursewareText({
	file: 'src/presentation/11-chapter2.md',
	text: [
		'---', 'marp: true', 'theme: ascend310', 'paginate: true', 'size: 16:9', '---', '',
		'<!-- _class: cover -->', '', '# 封面', '', '对应教材：`src/book/chapter2.md`', '',
		'---', '', '## 只有一句话', '', '这一页几乎什么都没有。', '',
		'---', '', '## 小结', '', '- 回顾今天的内容', ''
	].join('\n')
});
check(underfull.some((finding) => finding.rule === 'deck-slide-underfull'), 'a prose slide under 40 characters must be reported');

const scaffold = auditCoursewareText({
	file: 'src/presentation/30-appendix1.md',
	text: ['---', 'marp: true', 'theme: ascend310', 'paginate: true', 'size: 16:9', '---', '', '<!-- presentation-scaffold: appendix-01 -->', '', '# 附录占位', '', '对应教材：`src/appendix/appendix1.md`', ''].join('\n')
});
check(scaffold.some((finding) => finding.rule === 'deck-scaffold'), 'a scaffold deck must be reported as such');
check(!scaffold.some((finding) => finding.rule === 'deck-content-thin'), 'a scaffold deck must not be counted as thin content');

const longTitle = auditCoursewareText({
	file: 'src/presentation/12-chapter3.md',
	text: [
		'---', 'marp: true', 'theme: ascend310', 'paginate: true', 'size: 16:9', '---', '',
		'<!-- _class: cover -->', '', '# 封面', '', '对应教材：`src/book/chapter3.md`', '',
		'---', '', '## 这是一个刻意写得很长很长并且明显超过三十个字符上限的幻灯片标题文案', '', '正文内容足够长，用来避免触发信息密度不足的规则，因此这里重复补充一些说明文字。', ''
	].join('\n')
});
check(longTitle.some((finding) => finding.rule === 'deck-title-too-long'), 'a slide title over 30 characters must be reported');

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('courseware test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('courseware test passed');
console.log('  markdownlint：alt（MD045）/ 语言标记（MD040）/ 裸 URL（MD034，行内代码除外）');
console.log('  Marp：元数据、讲师备注覆盖；本仓库：教材可追溯、主题字号下限 18px');
