/**
 * Validate the writing plugin's skill split.
 *
 * The umbrella skill routes common rules; each writing target has its own
 * specialized skill. This test keeps the frontmatter, scope and routing honest.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const skillsRoot = join(root, 'skills');
const expected = [
	'ascend310b-writing',
	'ascend310b-writing-case',
	'ascend310b-writing-docs',
	'ascend310b-writing-presentation',
	'ascend310b-writing-theory'
];
const failures = [];
const actual = readdirSync(skillsRoot, { withFileTypes: true })
	.filter((entry) => entry.isDirectory())
	.map((entry) => entry.name)
	.sort();

for (const name of expected) if (!actual.includes(name)) failures.push(`缺少 writing skill：${name}`);
for (const name of actual) if (!expected.includes(name)) failures.push(`未登记的 writing skill：${name}`);

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

const umbrella = readFileSync(join(skillsRoot, 'ascend310b-writing', 'SKILL.md'), 'utf8');
for (const name of expected.slice(1)) {
	if (!umbrella.includes(`\`${name}\``)) failures.push(`总 skill 未路由到 ${name}`);
}

if (failures.length > 0) {
	console.error(`writing skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`writing skills test passed (${expected.length} 个写作 skills)`);
console.log('  umbrella: theory / case / presentation / docs');
