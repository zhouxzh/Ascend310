/**
 * Validate the writing plugin's skill split.
 *
 * The umbrella skill routes common rules; each writing target has its own
 * specialized skill. This test keeps the frontmatter, scope and routing honest.
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const skillsRoot = join(root, 'skills');
const expected = [
	'ascend310b-writing',
	'ascend310b-code-docs',
	'ascend310b-curriculum-planning',
	'ascend310b-writing-academic',
	'ascend310b-writing-case',
	'ascend310b-writing-docs',
	'ascend310b-writing-dot',
	'ascend310b-writing-latex',
	'ascend310b-writing-markdown',
	'ascend310b-writing-presentation',
	'ascend310b-writing-theory',
	'ascend310b-writing-vuepress'
];
const external = ['scientific-writing', 'nature-polishing'];
const supportOnly = ['nature-shared'];
const allowed = new Set([...expected, ...external, ...supportOnly]);
const failures = [];
const actual = readdirSync(skillsRoot, { withFileTypes: true })
	.filter((entry) => entry.isDirectory())
	.map((entry) => entry.name)
	.sort();

for (const name of expected) if (!actual.includes(name)) failures.push(`缺少 writing skill：${name}`);
for (const name of [...external, ...supportOnly]) {
	if (!actual.includes(name)) failures.push(`缺少 vendored skill/support：${name}`);
}
for (const name of actual) if (!allowed.has(name)) failures.push(`未登记的 writing skill：${name}`);

for (const name of expected) {
	const path = join(skillsRoot, name, 'SKILL.md');
	let text = '';
	try {
		text = readFileSync(path, 'utf8');
	} catch {
		failures.push(`${name}: SKILL.md 不存在`);
		continue;
	}
	if (!text.startsWith('---\n')) failures.push(`${name}: 缺少 frontmatter`);
	const end = text.indexOf('\n---', 4);
	if (end < 0) {
		failures.push(`${name}: frontmatter 未闭合`);
		continue;
	}
	const frontmatter = text.slice(4, end);
	const declared = /^name:\s*(\S+)/m.exec(frontmatter)?.[1];
	if (declared !== name) failures.push(`${name}: name 不匹配（${declared}）`);
	if (!/^description:\s*\S+/m.test(frontmatter)) failures.push(`${name}: 缺少 description`);
	if (text.length < 500) failures.push(`${name}: 内容过短`);
	if (!text.includes('310B')) failures.push(`${name}: 缺少 310B 仓库范围`);
	if (!/^## /m.test(text)) failures.push(`${name}: 缺少工作流章节`);
}

for (const name of external) {
	const path = join(skillsRoot, name, 'SKILL.md');
	let text = '';
	try {
		text = readFileSync(path, 'utf8');
	} catch {
		failures.push(`${name}: SKILL.md 不存在`);
		continue;
	}
	if (!text.startsWith('---\n')) failures.push(`${name}: 缺少 frontmatter`);
	const end = text.indexOf('\n---', 4);
	if (end < 0) {
		failures.push(`${name}: frontmatter 未闭合`);
		continue;
	}
	const frontmatter = text.slice(4, end);
	const declared = /^name:\s*(\S+)/m.exec(frontmatter)?.[1];
	if (declared !== name) failures.push(`${name}: name 不匹配（${declared}）`);
	if (!/^description:\s*\S+/m.test(frontmatter)) failures.push(`${name}: 缺少 description`);
	if (text.length < 2000) failures.push(`${name}: 内容过短`);
	if (!existsSync(join(skillsRoot, name, 'SOURCE.md'))) {
		failures.push(`${name}: 缺少上游来源说明`);
	}
}
if (existsSync(join(skillsRoot, 'nature-shared', 'SKILL.md'))) {
	failures.push('nature-shared: 支持目录不能包含 SKILL.md，否则会暴露为独立 skill');
}
for (const required of ['core', 'journal-formats', 'scripts', 'manifest.yaml', 'LICENSE']) {
	if (!existsSync(join(skillsRoot, 'nature-shared', required))) {
		failures.push(`nature-shared: 缺少支持文件 ${required}`);
	}
}

const umbrella = readFileSync(join(skillsRoot, 'ascend310b-writing', 'SKILL.md'), 'utf8');
for (const name of expected.slice(1)) {
	if (!umbrella.includes(`\`${name}\``)) failures.push(`总 skill 未路由到 ${name}`);
}
const presentation = readFileSync(join(skillsRoot, 'ascend310b-writing-presentation', 'SKILL.md'), 'utf8');
if (!presentation.includes('GitHub Pages')) failures.push('Presentation skill 未规定 GitHub Pages 教材链接');
if (!presentation.includes('https://github.com/zhouxzh/Ascend310/tree/main/samples/')) {
	failures.push('Presentation skill 未规定 GitHub samples 链接');
}
if (!presentation.includes('../experiment/*.md')) failures.push('Presentation skill 未明确禁止本地教材相对链接');
if (!umbrella.includes('已发布 Presentation')) failures.push('总 skill 未同步已发布课件链接规则');

const humanReviewSkills = [
	'ascend310b-writing',
	'ascend310b-writing-theory',
	'ascend310b-writing-case',
	'ascend310b-writing-docs',
	'ascend310b-writing-markdown',
	'ascend310b-writing-presentation',
	'ascend310b-writing-vuepress'
];
for (const name of humanReviewSkills) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	if (!text.includes('human-review: pending')) failures.push(`${name}: 未声明 human-review: pending`);
	if (!text.includes('check_page_layout.mjs')) failures.push(`${name}: 未要求检查本地页面排版`);
}
for (const name of ['ascend310b-writing', 'ascend310b-writing-theory', 'ascend310b-writing-case', 'ascend310b-writing-docs', 'ascend310b-writing-markdown', 'ascend310b-writing-vuepress']) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	if (!text.includes('docs:dev')) failures.push(`${name}: 未要求启动 docs:dev 本地网页`);
}

if (failures.length > 0) {
	console.error(`writing skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`writing skills test passed (${expected.length} 个本地 skills + ${external.length} 个 vendored skills)`);
console.log('  umbrella: theory / case / presentation / docs / code-docs / academic / curriculum / vuepress / latex / markdown / dot');
console.log('  vendored: scientific-writing / nature-polishing（nature-shared 为支持目录）');
