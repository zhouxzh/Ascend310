/**
 * Generated-site audit.
 *
 * The manuscript audit sees Markdown and the render audit sees the PDF log;
 * neither can tell that the built site dropped a page, kept a link to a page it
 * did not generate, or shipped an anchor that no longer exists. The fixture
 * pins each of those failures plus the healthy case.
 *
 * Usage: node test/site.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectSiteFindings } from '../lib/site.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-site-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};
const body = (text) => `<html><head><title>t</title></head><body><h1>标题</h1><p>${text}</p></body></html>`;
const longText = '这是一段用于满足正文长度阈值的说明文字。'.repeat(20);

write('src/book/chapter1.md', ['---', 'title: "第1章：甲章"', '---', '', '## 1.1 内容', '', '正文', ''].join('\n'));
write('src/.vuepress/config.ts', 'export default { base: "/T/", title: "测试" };');
write('src/.vuepress/dist/index.html', `<html><body><h1>首页</h1><p>${longText}</p><a href="/T/book/chapter1.html">甲章</a></body></html>`);
write('src/.vuepress/dist/book/chapter1.html', `<html><body><h1>甲章</h1><div id="sec"></div><p>${longText}</p></body></html>`);
write('src/.vuepress/dist/robots.txt', 'User-agent: *');
write('src/.vuepress/dist/sitemap.xml', '<urlset></urlset>');

const settings = resolveSettings({ root });
const healthy = collectSiteFindings({ root, settings });
check(healthy.available, 'the site audit must run when index.html exists');
check(healthy.pages === 2, `two pages expected, got ${healthy.pages}`);
check(healthy.findings.length === 0, `healthy site must be clean: ${healthy.findings.map((f) => f.rule).join(',')}`);

// A source page with no generated counterpart.
write('src/book/chapter2.md', ['---', 'title: "第2章：乙章"', '---', '', '## 2.1 内容', '', '正文', ''].join('\n'));
const missingPage = collectSiteFindings({ root, settings });
check(
	missingPage.findings.some((finding) => finding.rule === 'site-page-missing' && finding.message.includes('chapter2.html')),
	'a source page without a generated page must be reported'
);

// Broken page link, missing asset, missing fragment, unrendered marker.
write(
	'src/.vuepress/dist/index.html',
	[
		'<html><body><h1>首页</h1>',
		'<a href="/T/book/missing.html">缺页</a>',
		'<img src="/T/assets/none.png">',
		'<a href="/T/book/chapter1.html#nope">锚点</a>',
		'<p>{#src-book-chapter1-h1}</p>',
		'</body></html>'
	].join('\n')
);
const broken = collectSiteFindings({ root, settings });
for (const rule of ['site-link-broken', 'site-asset-missing', 'site-fragment-missing', 'site-unrendered']) {
	check(broken.findings.some((finding) => finding.rule === rule), `${rule} must be reported`);
}

// A page that only carries the navigation shell.
write('src/.vuepress/dist/book/chapter1.html', '<html><body><nav>菜单</nav></body></html>');
const empty = collectSiteFindings({ root, settings });
check(empty.findings.some((finding) => finding.rule === 'site-empty-page'), 'an empty page must be reported');

// Missing delivery metadata.
rmSync(join(root, 'src/.vuepress/dist/sitemap.xml'));
rmSync(join(root, 'src/.vuepress/dist/robots.txt'));
const meta = collectSiteFindings({ root, settings });
check(meta.findings.filter((finding) => finding.rule === 'site-sitemap-missing').length === 2, 'sitemap and robots must be required');

// A site that was never built.
rmSync(join(root, 'src/.vuepress/dist'), { recursive: true, force: true });
const absent = collectSiteFindings({ root, settings });
check(!absent.available, 'an absent dist must be marked unavailable');
check(absent.findings.some((finding) => finding.rule === 'site-not-built'), 'an absent dist must be reported');

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('site test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('site test passed');
console.log('  页面缺失 / 断链 / 缺资源 / 缺锚点 / 未渲染 / 空页 / 缺 sitemap / 未构建 均已覆盖');
