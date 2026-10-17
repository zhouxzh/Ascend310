/**
 * Regression tests and a real-repository run for the cross-artifact checks.
 *
 * Every new rule is exercised against a synthetic fixture whose answer is
 * known, so a future edit cannot quietly turn a rule into a false-positive
 * generator — the failure mode this plugin already suffered once with
 * `cjk-double-space`.
 *
 * Usage: node test/checks.mjs [repoRoot]
 */

import { mkdirSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
	collectArtifactFindings,
	collectCommitFindings,
	collectFrozenNameFindings,
	collectRenderFindings,
	evaluatePushGate
} from '../lib/checks.js';

// Default to the working directory; no checkout path is baked in.
const repoRoot = process.argv[2] ?? process.cwd();
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

// --- fixture: frozen-name table --------------------------------------------
const fixture = join(tmpdir(), `tw-checks-${Date.now()}`);
mkdirSync(join(fixture, 'src', 'book'), { recursive: true });
mkdirSync(join(fixture, 'src', 'experiment'), { recursive: true });
writeFileSync(
	join(fixture, 'AGENTS.md'),
	[
		'| Source | Frozen title |',
		'| --- | --- |',
		'| `src/book/chapter1.md` | 第1章：表里写的标题 |',
		'| `src/book/chapter9.md` | 第9章：文件并不存在 |',
		'',
		'| Area | Purpose | Source of truth |',
		'| --- | --- | --- |',
		'| `README.md` | Repository entry point | Repository root |',
		''
	].join('\n'),
	'utf8'
);
// Actual title differs from the table on purpose.
writeFileSync(join(fixture, 'src', 'book', 'chapter1.md'), '---\ntitle: "第1章：实际标题"\n---\n\n## 内容\n', 'utf8');
// Numbered manuscript absent from the table on purpose.
writeFileSync(join(fixture, 'src', 'book', 'chapter2.md'), '---\ntitle: "第2章：未登记"\n---\n\n## 内容\n', 'utf8');

const frozen = collectFrozenNameFindings({ root: fixture });
const frozenRules = frozen.findings.map((finding) => finding.rule).sort();
check(frozen.available, 'fixture AGENTS.md should be found');
check(frozen.rows === 2, `expected 2 frozen-name rows (the README table row must be skipped), got ${frozen.rows}`);
check(frozenRules.includes('frozen-title-mismatch'), 'a drifted title must be reported');
check(frozenRules.includes('frozen-source-missing'), 'a table row with no file must be reported');
check(frozenRules.includes('frozen-table-missing'), 'an unregistered numbered manuscript must be reported');
check(frozen.findings.length === 3, `expected exactly 3 fixture findings, got ${frozen.findings.length}`);

// --- fixture: render log ----------------------------------------------------
const logPath = join(fixture, 'book.log');
writeFileSync(
	logPath,
	[
		'Missing character: There is no → (U+2192) in font Noto Serif Bold/OT:script=latn;!',
		'Missing character: There is no → (U+2192) in font Noto Serif Bold/OT:script=latn;!',
		'! Undefined control sequence.',
		"LaTeX Warning: Reference `fig:missing' on page 3 undefined on input line 42.",
		'Overfull \\hbox (301.32pt too wide) in paragraph at lines 282--287',
		'Overfull \\hbox (0.64pt too wide) detected at line 423',
		'Underfull \\hbox (badness 10000) in alignment at lines 63--75',
		'Output written on book.xdv (632 pages, 12775588 bytes).'
	].join('\n'),
	'utf8'
);
const previous = new Date(Date.now() - 60_000);
utimesSync(logPath, previous, previous);

const render = collectRenderFindings({ root: fixture, logPath });
const renderRules = render.findings.map((finding) => finding.rule);
check(render.available, 'fixture log should be found');
check(render.stats.hardErrors === 1, `expected 1 hard error, got ${render.stats.hardErrors}`);
check(render.stats.missingCharacters === 2, `expected 2 missing characters, got ${render.stats.missingCharacters}`);
check(render.stats.missingCharacterKinds === 1, `expected missing characters deduped to 1 kind, got ${render.stats.missingCharacterKinds}`);
check(render.stats.undefinedReferences === 1, `expected 1 undefined reference, got ${render.stats.undefinedReferences}`);
check(render.stats.overfull === 2, `expected 2 overfull boxes counted, got ${render.stats.overfull}`);
check(render.stats.overfullReported === 1, `expected only the box above the threshold reported, got ${render.stats.overfullReported}`);
check(render.stats.underfull === 1, `expected 1 underfull box counted, got ${render.stats.underfull}`);
check(renderRules.filter((rule) => rule === 'render-overfull-box').length === 1, 'small overfull boxes must stay out of the findings');
check(render.stats.stale, 'a log older than the newest manuscript must be flagged stale');
check(renderRules.includes('render-log-stale'), 'staleness must be a finding');

// A log newer than every manuscript must not be called stale.
const fresh = new Date(Date.now() + 60_000);
utimesSync(logPath, fresh, fresh);
check(
	collectRenderFindings({ root: fixture, logPath }).stats.stale === false,
	'a fresh log must not be flagged stale'
);

// --- real repository --------------------------------------------------------
const realFrozen = collectFrozenNameFindings({ root: repoRoot });
console.log(`冻结名表: ${realFrozen.rows} 行, ${realFrozen.findings.length} 处不一致`);
for (const finding of realFrozen.findings.slice(0, 5)) {
	console.log(`  ${finding.severity[0].toUpperCase()} ${finding.file}:${finding.line} [${finding.rule}] ${finding.message}`);
}

const realRender = collectRenderFindings({ root: repoRoot });
console.log(`\n${realRender.text}`);

const realCommit = collectCommitFindings({ root: repoRoot, rev: 'HEAD' });
console.log(
	`\n提交树: ${realCommit.files} 文件 / ${realCommit.manuscripts} 正文 / ` +
		`${realCommit.checked} 引用 / ${realCommit.findings.length} 断链`
);
for (const finding of realCommit.findings.slice(0, 5)) {
	console.log(`  ${finding.file}:${finding.line} ${finding.message}`);
}

const badRev = collectCommitFindings({ root: repoRoot, rev: 'no-such-revision-xyz' });
check(
	badRev.findings.length === 1 && badRev.findings[0].rule === 'commit-unreadable',
	'a bad revision must produce exactly one commit-unreadable finding'
);

// --- fixture: artifact freshness -------------------------------------------
// Four numbered chapters, one per outcome, so the classifier is pinned on each
// cause rather than only on "something is missing".
const generatedDir = join(fixture, 'latex', 'chapters');
mkdirSync(generatedDir, { recursive: true });
const now = Date.now();
const write = (path, text, mtime) => {
	writeFileSync(path, text, 'utf8');
	if (mtime !== undefined) utimesSync(path, new Date(mtime), new Date(mtime));
};
write(join(fixture, 'src', 'book', 'chapter1.md'), '---\ntitle: "第1章：t"\n---\n\n## 甲 {#src-book-chapter1-h1}\n', now);
write(join(generatedDir, 'chapter1.tex'), '\\section{甲}\n', now - 120_000); // stale: no label, source newer
write(join(fixture, 'src', 'book', 'chapter2.md'), '---\ntitle: "第2章：t"\n---\n\n## 乙 {#src-book-chapter2-h1}\n', now);
write(join(generatedDir, 'chapter2.tex'), '正文 \\#\\# 乙 \\{\\#src-book-chapter2-h1\\}\n', now); // heading escaped
write(join(fixture, 'src', 'book', 'chapter3.md'), '---\ntitle: "第3章：t"\n---\n\n## 丙 {#src-book-chapter3-h1}\n', now);
write(join(generatedDir, 'chapter3.tex'), '\\section{丙}\\label{x-src-book-chapter3-h1}\n', now); // attribute swallowed
write(join(fixture, 'src', 'book', 'chapter4.md'), '---\ntitle: "第4章：t"\n---\n\n## 丁 {#src-book-chapter4-h1}\n', now);
write(join(generatedDir, 'chapter4.tex'), '\\section{丁}\\label{src-book-chapter4-h1}\n', now); // control: fine
write(join(fixture, 'latex', 'book.pdf'), 'pdf', now - 300_000);

const artifacts = collectArtifactFindings({ root: fixture });
const byRule = (rule) => artifacts.findings.filter((finding) => finding.rule === rule);
check(artifacts.pairs === 4, `expected 4 source/artifact pairs, got ${artifacts.pairs}`);
check(byRule('artifact-anchor-stale').length === 1, `expected 1 stale anchor, got ${byRule('artifact-anchor-stale').length}`);
check(
	byRule('artifact-anchor-stale')[0]?.file === 'src/book/chapter1.md',
	'only the source newer than its artifact is stale'
);
check(
	byRule('artifact-anchor-unparsed').length === 2,
	`expected 2 unparsed anchors (escaped heading + swallowed attribute), got ${byRule('artifact-anchor-unparsed').length}`
);
check(
	byRule('artifact-anchor-unparsed').every((finding) => finding.severity === 'error'),
	'a converter that cannot express the anchor is a source defect, not a stale artifact'
);
check(
	byRule('artifact-anchor-unparsed').every((finding) => finding.message.includes('修源 Markdown')),
	'the advice for an unparsed anchor must be to fix the source, not to rebuild'
);
check(byRule('artifact-pdf-stale').length === 1, 'a PDF older than the newest source must be reported stale');
check(
	artifacts.findings.every((finding) => !finding.file.includes('chapter4')),
	'a chapter whose anchor survived generation must not be reported'
);
check(artifacts.findings.length === 4, `expected exactly 4 artifact findings, got ${artifacts.findings.length}`);

// --- real repository: push gate --------------------------------------------
const gate = evaluatePushGate({ root: repoRoot, rev: 'HEAD' });
console.log(`\n${gate.text}`);
check(typeof gate.ready === 'boolean', 'the gate must report a boolean verdict');
check(
	gate.items.map((item) => item.id).join(',') ===
		'manuscript-audit,commit-tree,uncommitted-manuscripts,render,artifacts',
	`unexpected gate items: ${gate.items.map((item) => item.id).join(',')}`
);
check(
	gate.items.filter((item) => item.blocking).length === 2,
	'only the manuscript audit and the commit tree may block a push'
);
check(gate.human.length === 1 && gate.human[0].includes('预览'), 'the gate must name the preview confirmation it cannot verify');

const blocked = evaluatePushGate({ root: repoRoot, rev: 'no-such-revision-xyz' });
check(blocked.ready === false, 'an unreadable revision must block the push');
check(blocked.blockingFailed.includes('commit-tree'), 'the blocking item must name the commit tree');

rmSync(fixture, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('\nchecks test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('\nchecks test passed (fixtures + real repository)');
