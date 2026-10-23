/**
 * VuePress wiring audit.
 *
 * A clean repository proves nothing about a checker: this test builds a fixture
 * with every defect the module claims to catch, then a clean fixture, and
 * asserts both directions. The git remote is real — the fixture initialises a
 * repository — because `repo`/`hostname` drift is compared against it.
 *
 * Usage: node test/vuepress.mjs
 */

import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectVuepressFindings } from '../lib/vuepress.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-vuepress-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};

write('src/README.md', '---\ntitle: "首页"\n---\n\n首页。\n');
write('src/portfolio.md', '---\ntitle: "作者"\n---\n\n作者页。\n');
write('src/book/README.md', '---\ntitle: "理论教程"\n---\n\n索引。\n');
write('src/book/chapter1.md', '---\ntitle: "第1章：唯一" \n---\n\n正文。\n');
write('src/presentation/README.md', '---\ntitle: "演示"\n---\n\n索引。\n');
write('src/presentation/00-reachable.md', '---\nmarp: true\n---\n\n# 第1章：唯一\n');
write('src/presentation/01-orphan.md', '---\nmarp: true\n---\n\n# 没人链接我\n');

// Every defect at once: a missing page, a broken deck link, an orphan deck, a
// missing logo and favicon, base drift, repo drift, a drifted frozen label.
write(
	'src/.vuepress/config.ts',
	['export default defineUserConfig({', '  base: "/",', '  head: [["link", { rel: "icon", href: "/favicon.ico" }]],', '});', ''].join('\n')
);
write(
	'src/.vuepress/theme.ts',
	[
		'export default hopeTheme({',
		'  hostname: "https://wrong.github.io",',
		'  logo: "logo.png",',
		'  repo: "someone/else",',
		'  docsDir: "src",',
		'});',
		''
	].join('\n')
);
write(
	'src/.vuepress/sidebar.ts',
	[
		'export default sidebar({',
		'  "/": [',
		'    "",',
		'    {',
		'      text: "理论教程",',
		'      link: "/book/",',
		'      prefix: "/book/",',
		'      children: [',
		'        "chapter1.md",',
		'        "chapter2.md"',
		'      ]',
		'    },',
		'    {',
		'      text: "案例 9：不存在的名字",',
		'      link: "/presentation/",',
		'      prefix: "/presentation/",',
		'      children: [',
		'        { text: "唯一", link: "/presentation/00-reachable.html" },',
		'        { text: "缺源", link: "/presentation/09-missing.html" }',
		'      ]',
		'    }',
		'  ]',
		'});',
		''
	].join('\n')
);
write(
	'src/.vuepress/navbar.ts',
	['export default navbar([', '  "/",', '  "nowhere.md",', '  { text: "理论教程", link: "/book/" },', ']);', ''].join('\n')
);
// Deliberately a different base, so the drift check has something to catch.
write('src/.vuepress/client.ts', 'const pattern = /^\\/OldBase\\/presentation\\//u;\n');

// A real git repository, so the remote comparison has something to compare.
execFileSync('git', ['init', '-q'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
execFileSync('git', ['remote', 'add', 'origin', 'https://github.com/zhouxzh/Ascend310.git'], {
	cwd: root,
	stdio: ['ignore', 'pipe', 'pipe']
});

const settings = resolveSettings({ root });
const frozenRows = [{ title: '第1章：唯一' }];
const report = collectVuepressFindings({ root, settings, frozenRows });
const rules = report.findings.map((finding) => finding.rule);
check(report.available === true, 'the audit must run when the config files exist');
check(rules.includes('vuepress-page-missing'), `a missing sidebar page must be reported, got ${rules.join(',')}`);
check(rules.includes('vuepress-nav-missing'), 'a missing navbar page must be reported');
check(rules.includes('vuepress-deck-link-broken'), 'a sidebar deck link without a source must be reported');
check(rules.includes('deck-unlinked'), 'a deck nothing links to must be reported');
check(rules.includes('vuepress-asset-missing'), 'a missing logo/favicon must be reported');
check(rules.includes('vuepress-frozen-mismatch'), 'a drifted frozen label must be reported');
check(rules.includes('vuepress-base-mismatch'), 'base drift between config.ts and client.ts must be reported');
check(rules.includes('vuepress-repo-mismatch'), 'repo drift against the git remote must be reported');
check(rules.includes('vuepress-hostname-mismatch'), 'hostname drift must be reported');
check(
	report.findings.some((finding) => finding.rule === 'vuepress-page-missing' && finding.message.includes('chapter2.md')),
	'the missing page must be named'
);
check(
	report.findings.every((finding) => finding.severity === 'error' || finding.severity === 'warning' || finding.severity === 'info'),
	'every finding needs a known severity'
);

// --- the clean direction ----------------------------------------------------
rmSync(root, { recursive: true, force: true });
const clean = join(tmpdir(), `tw-vuepress-clean-${Date.now()}`);
const writeClean = (relativePath, text) => {
	const full = join(clean, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};
writeClean('src/README.md', '---\ntitle: "首页"\n---\n\n首页。\n');
writeClean('src/book/README.md', '---\ntitle: "理论教程"\n---\n\n索引。\n');
writeClean('src/book/chapter1.md', '---\ntitle: "第1章：唯一"\n---\n\n正文。\n');
writeClean('src/presentation/README.md', '---\ntitle: "演示"\n---\n\n索引。\n');
writeClean('src/presentation/00-deck.md', '---\nmarp: true\n---\n\n# 第1章：唯一\n');
writeClean('src/.vuepress/public/logo.png', 'png');
writeClean('src/.vuepress/public/favicon.ico', 'ico');
writeClean('src/.vuepress/public/CNAME', '310b.cn\n');
writeClean('src/.vuepress/public/presentation/00-deck.html', '<html></html>');
writeClean('src/.vuepress/config.ts', 'export default defineUserConfig({ base: "/", head: [["link", { rel: "icon", href: "/favicon.ico" }]], });\n');
writeClean('src/.vuepress/theme.ts', ['export default hopeTheme({', '  hostname: "https://310b.cn",', '  logo: "logo.png",', '  repo: "zhouxzh/Ascend310",', '  docsDir: "src",', '});', ''].join('\n'));
writeClean('src/.vuepress/sidebar.ts', ['export default sidebar({', '  "/": [', '    "",', '    {',
	'      text: "理论教程",', '      link: "/book/",', '      prefix: "/book/",', '      children: ["chapter1.md"]',
	'    },', '    {', '      text: "演示",', '      link: "/presentation/",', '      prefix: "/presentation/",',
	'      children: [{ text: "唯一", link: "/presentation/00-deck.html" }]', '    }', '  ]', '});', ''].join('\n'));
writeClean('src/.vuepress/navbar.ts', 'export default navbar(["/", "portfolio.md", { text: "理论教程", link: "/book/" }]);\n');
writeClean('src/portfolio.md', '---\ntitle: "作者"\n---\n\n作者页。\n');
writeClean('src/.vuepress/client.ts', 'const pattern = /^\\/presentation\\//u;\n');
execFileSync('git', ['init', '-q'], { cwd: clean, stdio: ['ignore', 'pipe', 'pipe'] });
execFileSync('git', ['remote', 'add', 'origin', 'https://github.com/zhouxzh/Ascend310.git'], {
	cwd: clean,
	stdio: ['ignore', 'pipe', 'pipe']
});
const cleanReport = collectVuepressFindings({ root: clean, settings: resolveSettings({ root: clean }), frozenRows: [{ title: '第1章：唯一' }] });
check(
	cleanReport.findings.length === 0,
	`a consistent configuration must report nothing, got: ${cleanReport.findings.map((finding) => `${finding.rule}@${finding.file}`).join(', ')}`
);
check(cleanReport.decks === 1, `one linked deck expected, got ${cleanReport.decks}`);

rmSync(clean, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('vuepress test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('vuepress test passed');
console.log('  抓到：缺页面、缺导航、放映页链接断、文稿无链接、缺 logo/favicon、冻结标签漂移、base/repo/hostname 漂移；CNAME 自定义域名可正确匹配');
console.log('  干净配置：0 条');
