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
	'ascend310b-camera-debug',
	'ascend310b-cann-development',
	'ascend310b-case-testing',
	'ascend310b-code-review',
	'ascend310b-crash-debug',
	'ascend310b-debug',
	'ascend310b-env-check',
	'ascend310b-microphone-debug',
	'ascend310b-npu-arch',
	'ascend310b-precision-debug',
	'ascend310b-runtime-debug',
	'ascend310b-speaker-debug',
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

const deviceDebugSkills = ['ascend310b-camera-debug', 'ascend310b-microphone-debug', 'ascend310b-speaker-debug'];
for (const name of deviceDebugSkills) {
	if (!actual.includes(name) || !name.endsWith('-debug')) failures.push(`设备调试 skill 命名不符合规范：${name}`);
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

const speakerSkill = readFileSync(join(skillsRoot, 'ascend310b-speaker-debug', 'SKILL.md'), 'utf8');
if (!speakerSkill.includes('25%')) failures.push('speaker skill: 首次音量测试必须明确从 25% 开始');
if (!/不要默认 100%|不要直接跳到 100%/.test(speakerSkill)) failures.push('speaker skill: 缺少禁止默认满音量测试的约束');

const caseTestingSkill = readFileSync(join(skillsRoot, 'ascend310b-case-testing', 'SKILL.md'), 'utf8');
for (const required of ['GitHub', 'Hugging Face', 'hf-mirror.com', '完整 commit', '固定 revision', 'scp', 'rsync', 'offline-bundle', 'SHA-256']) {
	if (!caseTestingSkill.includes(required)) failures.push(`case-testing skill: 缺少 ${required} 来源约束`);
}

if (failures.length > 0) {
	console.error(`skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`skills test passed (${expected.length} 个 310B skills)`);
console.log(`  directory: ${skillsRoot}`);
