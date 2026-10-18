/**
 * Figure audit: raster images and their Graphviz `.dot` sources.
 *
 * The rules protect both outputs at once. A heavy or oversized image slows the
 * site and bloats the PDF; an unreferenced image is dead weight; a `.dot` whose
 * PNG was not rebuilt shows an old diagram; and a Chinese Graphviz label without
 * a CJK font renders as boxes in the book.
 *
 * Usage: node test/figures.mjs
 */

import { mkdirSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectFigureFindings, imageSize } from '../lib/figures.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-figures-${Date.now()}`);
const write = (relativePath, data) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, data);
};

/** Minimal PNG header with a chosen size; enough for the header reader. */
const pngHeader = (width, height, padding = 0) => {
	const buffer = Buffer.alloc(33 + padding);
	Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(buffer, 0);
	buffer.writeUInt32BE(13, 8);
	buffer.write('IHDR', 12, 'ascii');
	buffer.writeUInt32BE(width, 16);
	buffer.writeUInt32BE(height, 20);
	return buffer;
};

write('src/book/chapter1.md', [
	'---',
	'title: "第1章：图表测试"',
	'---',
	'',
	'![](img1/no-alt.png)',
	'',
	'![大图](img1/heavy.png)',
	'',
	'![宽图](img1/wide.png)',
	'',
	'![结构图](img1/diagram.png)',
	''
].join('\n'));
write('src/book/img1/no-alt.png', pngHeader(10, 10));
write('src/book/img1/heavy.png', pngHeader(800, 600, 1_600_000));
write('src/book/img1/wide.png', pngHeader(4000, 3000));
write('src/book/img1/orphan.png', pngHeader(20, 20));
write('src/book/img1/diagram.png', pngHeader(800, 600));
write(
	'src/book/img1/diagram.dot',
	['digraph G {', '  label="这是一个非常非常长的中文标签，用来触发结构图标签的长度规则，确保它明显超过四十四个字符的长度上限要求";', '  a -> b;', '}'].join('\n')
);
// The PNG is older than its source, i.e. the diagram was edited but not rebuilt.
const stale = new Date(Date.now() - 86_400_000);
utimesSync(join(root, 'src/book/img1/diagram.png'), stale, stale);
write('src/book/img1/broken.dot', ['a -> b;', ''].join('\n'));

const settings = resolveSettings({ root });
const report = collectFigureFindings({ root, settings });
const rules = new Set(report.findings.map((finding) => finding.rule));
for (const rule of [
	'image-alt-missing',
	'image-too-heavy',
	'image-too-wide',
	'image-orphan',
	'dot-png-stale',
	'dot-cjk-font',
	'dot-label-too-long',
	'dot-invalid',
	'dot-pair-missing'
]) {
	check(rules.has(rule), `${rule} must be reported`);
}
check(imageSize(join(root, 'src/book/img1/wide.png'))?.width === 4000, 'PNG width must be read from the header');

// A clean fixture produces no warnings or errors (info-level orphans may stay).
utimesSync(join(root, 'src/book/img1/diagram.png'), new Date(Date.now() + 60_000), new Date(Date.now() + 60_000));
rmSync(join(root, 'src/book/img1/broken.dot'));
write('src/book/img1/orphan.png', pngHeader(20, 20));
write('src/book/img1/clean.dot', ['digraph G {', '  label="中文图";', '  fontname="Noto Sans SC";', '}'].join('\n'));
write('src/book/img1/clean.png', pngHeader(400, 300));
const future = new Date(Date.now() + 60_000);
utimesSync(join(root, 'src/book/img1/clean.png'), future, future);
write('src/book/chapter2.md', ['---', 'title: "第2章：干净图表"', '---', '', '![干净](img1/clean.png)', ''].join('\n'));
const cleanReport = collectFigureFindings({ root, settings });
check(
	!cleanReport.findings.some((finding) => finding.severity === 'error'),
	`a clean figure set must have no errors: ${cleanReport.findings.filter((f) => f.severity === 'error').map((f) => f.rule).join(',')}`
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('figure test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('figure test passed');
console.log('  图片：alt / 体积 / 尺寸 / 未被引用；结构图：缺 PNG / PNG 过期 / 中文字体 / 超长标签 / 语法');
