/**
 * Custom-layout test: the plugin must be able to audit a repository whose
 * layout is not this book's, and must keep every rule working when it does.
 *
 * The layout here shares nothing with the built-in one — a different source
 * root, directory, file pattern, title shape, frozen-name file, sidebar file
 * and generated-output directory — so a rule that still reads a literal path
 * shows up as a wrong finding instead of passing silently.
 *
 * Usage: node test/layout.mjs
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditRepo, resolveSettings } from '../lib/audit-core.js';
import { collectArtifactFindings, collectFrozenNameFindings } from '../lib/checks.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-layout-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};

/** A layout that shares no path with the built-in one. */
const LAYOUT = {
	sourceRoot: 'book-src',
	agentsFile: 'NAMES.md',
	sidebarFile: 'book-src/sidebar.ts',
	preface: { source: 'book-src/README.md', generated: 'out/lessons/preface.tex' },
	generatedDirs: { lesson: 'out/lessons' },
	latex: { log: 'out/build.log', pdf: 'out/book.pdf' },
	parts: [
		{
			key: 'lesson',
			label: '教程',
			dir: 'book-src/lessons',
			index: 'book-src/README.md',
			filePattern: '^lesson(\\d+)\\.md$',
			extras: [],
			titlePattern: '^Lesson (\\d+): \\S',
			titleHint: 'Lesson N: 标题',
			titleInFrontmatter: false
		}
	]
};

write('.tutorial-writing.json', JSON.stringify(LAYOUT, null, 2));
write('NAMES.md', ['| Source | Frozen title |', '| --- | --- |', '| `book-src/lessons/lesson1.md` | Lesson 1: 入门 |', '| `book-src/lessons/lesson2.md` | Lesson 3: 错号 |', ''].join('\n'));
write(
	'book-src/sidebar.ts',
	['export default {', '\tprefix: "/lesson/",', '\tchildren: [', '\t\t"lesson1.md",', '\t\t"lesson2.md"', '\t]', '};', ''].join('\n')
);
write('book-src/README.md', '---\ntitle: "教程"\n---\n\n前言。\n');
write('book-src/lessons/lesson1.md', '# Lesson 1: 入门 {#src-lesson-lesson1-h1}\n\n正文内容。\n');
// The file number and the title number disagree: the audit must still catch it
// under a layout that knows nothing about 第N章/案例 N.
write('book-src/lessons/lesson2.md', '# Lesson 3: 错号 {#src-lesson-lesson2-h1}\n\n正文内容。\n');
write('out/lessons/lesson1.tex', '\\section{Lesson 1: 入门}\\label{src-lesson-lesson1-h1}\n');
write('out/lessons/lesson2.tex', '\\section{Lesson 3: 错号}\\label{src-lesson-lesson2-h1}\n');

// --- the layout file is picked up ------------------------------------------
const settings = resolveSettings({ root });
check(settings.parts.length === 1 && settings.parts[0].key === 'lesson', 'the layout file must replace the built-in parts');
check(settings.parts[0].filePattern instanceof RegExp, 'pattern sources must be compiled');
check(settings.sidebarFile === 'book-src/sidebar.ts', 'the sidebar path must come from the layout');
check(settings.sourceRoot === 'book-src', 'the source root must come from the layout');

// --- plugin configuration is the highest layer ------------------------------
const overridden = resolveSettings({ root, layout: { sidebarFile: 'other.ts' } });
check(overridden.sidebarFile === 'other.ts', 'plugin configuration must win over the repository file');
check(overridden.parts[0].key === 'lesson', 'an override must not discard the rest of the layout');

// --- every rule works against the custom layout -----------------------------
const audit = auditRepo({ root });
check(audit.summary.files === 2, `the audit must find both lessons, got ${audit.summary.files}`);
const errors = audit.findings.filter((finding) => finding.severity === 'error');
check(errors.length === 1, `exactly one error expected, got ${errors.length}: ${errors.map((finding) => finding.rule).join(',')}`);
check(
	errors[0]?.rule === 'title-number-mismatch',
	`the file/title number disagreement must be caught under the custom shape, got ${errors[0]?.rule}`
);
check(
	!audit.findings.some((finding) => finding.rule === 'sidebar-missing'),
	'the custom sidebar file must satisfy the coverage rule'
);
check(
	!audit.findings.some((finding) => finding.rule === 'anchor-prefix'),
	'the declared anchor prefix must be accepted'
);

const frozen = collectFrozenNameFindings({ root });
check(frozen.rows === 2, `the custom frozen-name file must supply both rows, got ${frozen.rows}`);
check(frozen.findings.length === 0, `the frozen titles match, so no finding is expected, got ${frozen.findings.length}`);

const artifacts = collectArtifactFindings({ root });
// `pairs` counts only pairs whose two sides both exist, so the declared
// preface pair is skipped while its generated file is absent.
check(artifacts.pairs === 2, `the two lesson pairs must be compared, got ${artifacts.pairs}`);
check(artifacts.findings.length === 0, `generated labels match the declared anchors, got ${artifacts.findings.length}`);
check(
	artifacts.findings.every((finding) => !finding.file.startsWith('src/')),
	'no finding may name a path from the built-in layout'
);

// --- a broken layout fails loudly, not silently -----------------------------
write('.tutorial-writing.json', '{"parts": []}');
let emptyRejected = false;
try {
	resolveSettings({ root });
} catch (error) {
	emptyRejected = String(error.message).includes('至少一个 part');
}
check(emptyRejected, 'a layout with no parts must be rejected with a clear message');

write('.tutorial-writing.json', '{ not json');
let jsonRejected = false;
try {
	resolveSettings({ root });
} catch (error) {
	jsonRejected = String(error.message).includes('不是合法 JSON');
}
check(jsonRejected, 'a malformed layout file must be rejected with a clear message');

rmSync(root, { recursive: true, force: true });

// --- the built-in layout is still the default -------------------------------
const builtin = resolveSettings({ root: tmpdir() });
check(
	builtin.parts.map((part) => part.key).join(',') === 'book,experiment,appendix,misc',
	`the built-in layout must remain the default, got ${builtin.parts.map((part) => part.key).join(',')}`
);
check(builtin.sidebarFile === 'src/.vuepress/sidebar.ts', 'the built-in sidebar path must be unchanged');
check(builtin.latex.log === 'latex/book.log', 'the built-in build log path must be unchanged');

if (failures.length > 0) {
	console.error('layout test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('layout test passed');
console.log('  自定义布局：源目录/文件模式/标题形状/冻结名表/sidebar/产物目录全部生效，规则照常');
console.log('  三层优先级：内置默认 < 仓库 .tutorial-writing.json < 插件 config.layout');
