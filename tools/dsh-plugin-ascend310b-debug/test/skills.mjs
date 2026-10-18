/**
 * Validate the bundled 310B skills.
 *
 * This checks discovery/frontmatter and the expected 310B development
 * capability set. It does not pretend to evaluate CANN behavior; board tests
 * remain the source of runtime evidence.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const skillsRoot = join(root, 'skills');
const expected = [
	'ascend310b-api-best-practices',
	'ascend310b-cann-development',
	'ascend310b-code-review',
	'ascend310b-crash-debug',
	'ascend310b-debug',
	'ascend310b-env-check',
	'ascend310b-npu-arch',
	'ascend310b-precision-debug',
	'ascend310b-runtime-debug',
	'ascend310b-sync-audit'
];
const failures = [];

const actual = readdirSync(skillsRoot, { withFileTypes: true })
	.filter((entry) => entry.isDirectory())
	.map((entry) => entry.name)
	.sort();

for (const name of expected) {
	if (!actual.includes(name)) failures.push(`缺少 skill：${name}`);
}
for (const name of actual) {
	if (!expected.includes(name)) failures.push(`未登记的 skill：${name}`);
}

for (const name of expected) {
	const path = join(skillsRoot, name, 'SKILL.md');
	if (!statSync(path).isFile()) {
		failures.push(`不是文件：${path}`);
		continue;
	}
	const text = readFileSync(path, 'utf8');
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
	if (text.length < 400) failures.push(`${name}: 内容过短，无法作为可执行 skill`);
	if (!text.includes('310B')) failures.push(`${name}: 缺少 310B 范围声明`);
	if (!/^## /m.test(text)) failures.push(`${name}: 缺少工作流章节`);
}

if (failures.length > 0) {
	console.error(`skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`skills test passed (${expected.length} 个 310B skills)`);
console.log(`  directory: ${skillsRoot}`);
