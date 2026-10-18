/**
 * Keep the plugin's documentation honest.
 *
 * Tool names drift, rule tables rot, and a skill can promise a tool that no
 * longer exists. These checks are deliberately mechanical:
 *  - every ascend310b_writing_* name mentioned by the skill exists in index.js;
 *  - every deck-* / ci-* rule implemented in lib is listed in README;
 *  - README has no literal backslash-n table corruption;
 *  - the stale deck counts and old 18px floor do not return.
 *
 * Usage: node test/docs.mjs
 */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const index = readFileSync(join(root, 'lib/index.js'), 'utf8');
const skill = readdirSync(join(root, 'skills'), { withFileTypes: true })
	.filter((entry) => entry.isDirectory())
	.map((entry) => readFileSync(join(root, 'skills', entry.name, 'SKILL.md'), 'utf8'))
	.join('\n');
const readme = readFileSync(join(root, 'README.md'), 'utf8');

const toolNames = new Set(
	[...index.matchAll(/name: '(ascend310b_writing_[a-z_]+)'/gu)].map((match) => match[1])
);
const skillTools = new Set([...skill.matchAll(/ascend310b_writing_[a-z_]+/gu)].map((match) => match[0]));
const rules = new Set();
for (const file of readdirSync(join(root, 'lib')).filter((name) => name.endsWith('.js'))) {
	const text = readFileSync(join(root, 'lib', file), 'utf8');
	for (const match of text.matchAll(/rule: '((?:deck|ci)-[a-z0-9-]+)'/gu)) rules.add(match[1]);
}

const failures = [];
for (const tool of skillTools) {
	if (!toolNames.has(tool)) failures.push(`SKILL 引用不存在的工具：${tool}`);
}
for (const rule of rules) {
	if (!readme.includes(`\`${rule}\``)) failures.push(`README 缺少规则：${rule}`);
}
if (/`n\|/u.test(readme)) failures.push('README 出现字面 `n|（表格换行损坏）');
if (readme.includes('18px')) failures.push('README 仍写旧字号 18px');
if (skill.includes('28 份课件') || skill.includes('9 份 **4 页脚手架**')) {
	failures.push('SKILL 仍写旧的 28 份 / 9 份脚手架数量');
}
if (!readme.includes('ascend310b_writing_deck_render')) {
	failures.push('README 未记录 ascend310b_writing_deck_render');
}

if (failures.length > 0) {
	console.error('docs test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('docs test passed');
console.log(`  ${toolNames.size} 个工具、${rules.size} 条 deck/ci 规则与 SKILL/README 一致`);
