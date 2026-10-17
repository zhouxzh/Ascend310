/**
 * Presentation decks and the site build: the two artifacts the manuscript
 * audit does not cover.
 *
 * Two halves matter. First, a Marp deck is Markdown but not a manuscript — a
 * deck with thirty-odd H1s must produce *no* title, H1 or sidebar findings, or
 * the rule set is unusable on it. Second, both built artifacts are freshness
 * questions: a preview that shows yesterday's deck is the failure these rules
 * exist to catch.
 *
 * Usage: node test/site-assets.mjs
 */

import { mkdirSync, rmSync, unlinkSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditRepo, resolveSettings } from '../lib/audit-core.js';
import { collectAssetFindings } from '../lib/checks.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-assets-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
	return full;
};
const now = Date.now();

write(
	'.tutorial-writing.json',
	JSON.stringify(
		{
			sourceRoot: 'src',
			agentsFile: 'NAMES.md',
			sidebarFile: 'src/.vuepress/sidebar.ts',
			site: { distIndex: 'out/site/index.html' },
			slides: { builtDir: 'out/slides' },
			parts: [
				{
					key: 'presentation',
					label: '配套演示',
					dir: 'src/presentation',
					filePattern: '^\\d{2}-[a-z0-9-]+\\.md$',
					extras: ['README.md'],
					assets: true
				},
				{ key: 'misc', label: '仓库级文档', dir: 'src', files: ['README.md'], titleInFrontmatter: true }
			]
		},
		null,
		2
	)
);
write('NAMES.md', ['| Source | Frozen title |', '| --- | --- |', '| `src/book/chapter1.md` | 第1章：测试章节 |', ''].join('\n'));
write('src/README.md', '---\ntitle: "首页"\n---\n\n首页内容。\n');
write('src/.vuepress/sidebar.ts', 'export default { prefix: "/", children: [] };\n');
// A deck behaves like the real ones: many H1s, one per slide, and a frozen unit
// name in the cover heading and the footer.
const deck = [
	'---',
	'marp: true',
	'footer: "第1章：测试章节"',
	'---',
	'',
	'# 第1章：测试章节',
	'',
	'## 第一节',
	'',
	'正文。',
	'',
	'---',
	'',
	'# 另一个幻灯片',
	'',
	'## 细节',
	'',
	'正文。',
	''
].join('\n');
write('src/presentation/00-intro.md', deck);
write('src/presentation/README.md', '---\neditLink: false\n---\n\n# 演示索引\n');
write('src/presentation/theme.css', '.slide { }\n');

// --- a deck is not a manuscript --------------------------------------------
const settings = resolveSettings({ root });
check(settings.parts.some((part) => part.key === 'presentation' && part.assets === true), 'the assets part must be loaded');
const audit = auditRepo({ root, settings });
const deckFindings = audit.findings.filter((finding) => finding.file.includes('presentation'));
check(
	deckFindings.every((finding) => !['title-missing', 'title-format', 'title-number-mismatch', 'h1-count', 'heading-jump', 'sidebar-missing'].includes(finding.rule)),
	`a deck must not be judged as a manuscript, got: ${deckFindings.map((finding) => finding.rule).join(',')}`
);
check(
	audit.findings.every((finding) => !finding.message.includes('第1章：测试章节') || finding.rule === 'deck-frozen-mismatch'),
	'a matching frozen name must not be reported'
);

// --- freshness -------------------------------------------------------------
write('out/slides/00-intro.html', '<html></html>');
write('out/site/index.html', '<html></html>');
const fresh = collectAssetFindings({ root, settings });
check(fresh.decks === 1, `one deck expected, got ${fresh.decks}`);
check(fresh.slidesMissing === 0 && fresh.slidesStale === 0, 'fresh output must report nothing stale');
check(fresh.findings.every((finding) => !finding.rule.startsWith('slide-')), 'fresh slides must not be reported');
check(fresh.site.available === true && fresh.site.stale === false, 'a fresh dist must not be reported stale');

// A deck edited after its built HTML means the preview shows the old deck.
const deckFile = join(root, 'src/presentation/00-intro.md');
const builtFile = join(root, 'out/slides/00-intro.html');
utimesSync(builtFile, new Date(now - 120_000), new Date(now - 120_000));
utimesSync(deckFile, new Date(now), new Date(now));
const staleSlides = collectAssetFindings({ root, settings });
check(staleSlides.slidesStale === 1, `one stale deck expected, got ${staleSlides.slidesStale}`);
check(staleSlides.findings.some((finding) => finding.rule === 'slide-stale'), 'the stale deck must be reported');

// A dist older than the newest source means the preview shows the old site.
const distFile = join(root, 'out/site/index.html');
utimesSync(distFile, new Date(now - 300_000), new Date(now - 300_000));
const staleSite = collectAssetFindings({ root, settings });
check(staleSite.site.stale === true, 'an old dist must be reported stale');
check(
	staleSite.findings.some((finding) => finding.rule === 'site-stale' && finding.message.includes('docs:build')),
	'the stale site must say how to rebuild it'
);

unlinkSync(builtFile);
const missing = collectAssetFindings({ root, settings });
check(missing.slidesMissing === 1, 'a deck without built HTML must be reported missing');
check(missing.findings.some((finding) => finding.rule === 'slide-missing'), 'the missing deck must be named');

// --- frozen-name drift in a deck -------------------------------------------
write('src/presentation/00-intro.md', deck.replace('第1章：测试章节', '第9章：不存在的章节'));
const drift = collectAssetFindings({ root, settings });
check(
	drift.findings.some((finding) => finding.rule === 'deck-frozen-mismatch'),
	'a frozen-shaped name that is not in the frozen table must be reported'
);
check(
	drift.findings.filter((finding) => finding.rule === 'deck-frozen-mismatch').length === 1,
	'one drifted name in both the cover and the footer must be reported once'
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('site-assets test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('site-assets test passed');
console.log('  演示文稿：不被当正文审（多 H1 不报错），放映页缺失/过期可查，冻结名漂移可查');
console.log('  站点产物：dist 缺失/落后于源可查');
