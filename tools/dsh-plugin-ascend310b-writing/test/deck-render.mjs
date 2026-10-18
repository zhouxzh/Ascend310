/**
 * Real browser render check.
 *
 * The plugin must be able to ask Puppeteer for the actual 1280x720 layout, not
 * only estimate from Markdown. This test runs a single built deck so it stays
 * fast, then checks that an unknown deck produces a structured no-decks issue.
 *
 * Usage: node test/deck-render.mjs
 */

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { collectDeckRenderFindings } from '../lib/deck-render.js';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const report = collectDeckRenderFindings({ root: repoRoot, deck: '10-chapter1' });
check(report.available, 'the renderer must be available');
check(report.decks === 1, `one deck expected, got ${report.decks}`);
check(report.pages >= 20, `Chapter 1 should have at least 20 slides, got ${report.pages}`);
check(
	report.findings.length === 0,
	`Chapter 1 must render cleanly: ${report.findings.map((finding) => finding.message).join('; ')}`
);

const missing = collectDeckRenderFindings({ root: repoRoot, deck: 'no-such-deck' });
check(missing.available, 'the renderer must still answer for an unknown deck');
check(
	missing.findings.some((finding) => finding.rule === 'deck-render-no-decks'),
	'an unknown deck must produce deck-render-no-decks'
);

if (failures.length > 0) {
	console.error('deck render test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('deck render test passed');
console.log(`  Chapter 1：${report.pages} 页，0 个真实渲染问题`);
