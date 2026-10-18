/**
 * Generated-web deck audit.
 *
 * This checks the HTML Marp actually emits, not the Markdown source: each
 * `<section>` is measured for text volume, code lines, table shape and theme.
 * The fixture pins the academic density range, the acceptable table shape, the
 * per-deck evenness rule and the theme identity.
 *
 * Usage: node test/deck-web.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectDeckWebFindings } from '../lib/deck-web.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-deck-web-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};

const chinese = (count) => '内容'.repeat(Math.ceil(count / 2)).slice(0, count);
const prose = (theme, text, className = '') =>
	`<section data-theme="${theme}" data-marpit-pagination="2" class="${className}"><h2>标题</h2><p>${text}</p></section>`;
const table = (theme, cellText, rows = 3, columns = 3) => {
	const header = `<tr>${Array.from({ length: columns }, (_v, i) => `<th>列${i}</th>`).join('')}</tr>`;
	const body = Array.from(
		{ length: rows },
		() => `<tr>${Array.from({ length: columns }, () => `<td>${cellText}</td>`).join('')}</tr>`
	).join('');
	return `<section data-theme="${theme}" data-marpit-pagination="3" class=""><table>${header}${body}</table></section>`;
};

write('src/.vuepress/dist/index.html', '<html></html>');
write(
	'src/.vuepress/dist/presentation/10-good.html',
	[
		'<section data-theme="ascend310" data-marpit-pagination="1" class="cover"><h1>封面</h1></section>',
		prose('ascend310', chinese(100)),
		prose('ascend310', chinese(120)),
		prose('ascend310', chinese(130)),
		prose('ascend310', chinese(140)),
		table('ascend310', chinese(20)),
		'</html>'
	].join('\n')
);
write(
	'src/.vuepress/dist/presentation/11-bad.html',
	[
		prose('ascend310', chinese(30)),
		prose('ascend310', chinese(400)),
		table('gaia', chinese(3)),
		prose('ascend310', chinese(100)),
		prose('ascend310', chinese(100)),
		'</html>'
	].join('\n')
);

const settings = resolveSettings({ root });
const report = collectDeckWebFindings({ root, settings });
check(report.available, 'the audit must run when dist/presentation exists');
check(report.decks === 2, `two decks expected, got ${report.decks}`);
check(report.pages === 11, `eleven slides expected, got ${report.pages}`);
const good = report.findings.filter((finding) => finding.file.endsWith('10-good.html'));
check(good.length === 0, `the balanced deck must be clean: ${good.map((f) => f.rule).join(',')}`);
for (const rule of ['deck-web-density-low', 'deck-web-density-high', 'deck-web-density-uneven', 'deck-web-theme-drift']) {
	check(report.findings.some((finding) => finding.rule === rule), `${rule} must be reported`);
}

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('deck web test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('deck web test passed');
console.log('  均衡 / 过疏 / 过密 / 跨度失控 / 主题漂移 均已覆盖');
