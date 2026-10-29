/**
 * Validate the bundled 310B skills.
 *
 * This checks discovery/frontmatter and the expected 310B development
 * capability set. It does not pretend to evaluate CANN behavior; board tests
 * remain the source of runtime evidence.
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ENTRIES } from '../lib/knowledge.js';

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
const cameraSkill = readFileSync(join(skillsRoot, 'ascend310b-camera-debug', 'SKILL.md'), 'utf8');
const microphoneSkill = readFileSync(join(skillsRoot, 'ascend310b-microphone-debug', 'SKILL.md'), 'utf8');
if (!speakerSkill.includes('25%')) failures.push('speaker skill: 首次音量测试必须明确从 25% 开始');
if (!/不要默认 100%|不要直接跳到 100%/.test(speakerSkill)) failures.push('speaker skill: 缺少禁止默认满音量测试的约束');
for (const [label, text, tool] of [
	['camera', cameraSkill, 'ascend_camera_capture'],
	['microphone', microphoneSkill, 'ascend_microphone_record'],
	['speaker', speakerSkill, 'ascend_speaker_test']
]) {
	if (!text.includes(tool)) failures.push(`${label} skill: must name fixed tool ${tool}`);
}
for (const [label, text] of [
	['camera', cameraSkill],
	['microphone', microphoneSkill],
	['speaker', speakerSkill]
]) {
	if (!text.includes('/tmp/ascend310b-debug/peripherals')) failures.push(`${label} skill: must name fixed evidence root`);
}
if (!speakerSkill.includes('heard=null')) failures.push('speaker skill: command success must remain distinct from audible confirmation');
if (!microphoneSkill.includes('ascend_play_recording')) failures.push('microphone skill: recorded audio must be replayed for human verification');
if (!microphoneSkill.includes('独立测试') || !speakerSkill.includes('独立测试')) failures.push('audio skills: microphone and speaker must remain independently testable');
if (!microphoneSkill.includes('不需要先完成扬声器测试')) failures.push('microphone skill: speaker confirmation must not gate microphone recording');

const caseTestingSkill = readFileSync(join(skillsRoot, 'ascend310b-case-testing', 'SKILL.md'), 'utf8');
for (const required of ['GitHub', 'Hugging Face', 'hf-mirror.com', '完整 commit', '固定 revision', 'scp', 'rsync', 'offline-bundle', 'SHA-256']) {
	if (!caseTestingSkill.includes(required)) failures.push(`case-testing skill: 缺少 ${required} 来源约束`);
}

// Keep the panel's dependency hints tied to live case setup metadata.
const repositoryRoot = resolve(root, '..', '..');
const dependencyPath = join(root, 'data', 'case-dependencies.json');
const dependencyData = JSON.parse(readFileSync(dependencyPath, 'utf8'));
const cases = Array.isArray(dependencyData.cases) ? dependencyData.cases : [];
const casesById = new Map(cases.map((entry) => [entry.id, entry]));
for (const entry of cases) {
	for (const source of [entry.source, ...(entry.apt ?? []).map((item) => item.source)]) {
		if (typeof source !== 'string' || source.trim() === '') {
			failures.push(`${entry.id}: dependency metadata must provide a source path`);
			continue;
		}
		const sourcePath = resolve(repositoryRoot, source);
		const relative = sourcePath.slice(repositoryRoot.length);
		if (!(relative.startsWith('\\') || relative.startsWith('/')) || !existsSync(sourcePath)) {
			failures.push(`${entry.id}: dependency source does not resolve inside the repository: ${source}`);
		}
	}
	for (const pkg of entry.apt ?? []) {
		if (!['required', 'recommended', 'optional', 'conditional'].includes(pkg.level)) {
			failures.push(`${entry.id}: invalid apt dependency level for ${pkg.name}: ${pkg.level}`);
		}
	}
}

const case6 = casesById.get('case6');
if (!case6) {
	failures.push('case-dependencies: missing case6');
} else {
	const aptNames = (case6.apt ?? []).map((item) => item.name);
	if (aptNames.length > 0) failures.push('case6 metadata: apt dependencies must match setup.sh, which installs no system packages');
	for (const stale of ['python3-dev', 'python3-pip']) {
		if (aptNames.includes(stale)) failures.push(`case6 metadata: ${stale} is stale; Python dependencies belong in conda ascend`);
	}
	const pythonHints = (case6.python ?? []).join(' ').toLowerCase();
	if (!pythonHints.includes('cann') || !pythonHints.includes('acl/atc')) {
		failures.push('case6 metadata: system-provided CANN/ACL/ATC boundary must be visible');
	}
	if (!pythonHints.includes('ascend') || !pythonHints.includes('pythonnousersite=1')) {
		failures.push('case6 metadata: Python hint must name ascend and PYTHONNOUSERSITE=1');
	}
	if (!pythonHints.includes('requirements.txt') || !pythonHints.includes('opencv-python-headless')) {
		failures.push('case6 metadata: Python hint must match current requirements.txt and headless OpenCV package');
	}
	const setupPath = resolve(repositoryRoot, 'samples/case6/setup.sh');
	const requirementsPath = resolve(repositoryRoot, 'samples/case6/requirements.txt');
	const setup = readFileSync(setupPath, 'utf8');
	const requirements = readFileSync(requirementsPath, 'utf8');
	if (!/conda activate ascend/.test(setup) || !/PYTHONNOUSERSITE=1/.test(setup)) {
		failures.push('case6 setup: must activate ascend and disable user-site');
	}
	if (/sudo apt|pip3 install|python3 prepare_models/.test(setup)) {
		failures.push('case6 setup: must not install into base or use system pip/python');
	}
	if (!/^opencv-python-headless==/m.test(requirements) || /^opencv-python==/m.test(requirements)) {
		failures.push('case6 requirements: must use opencv-python-headless, not GUI OpenCV');
	}
}

const deviceMissing = ENTRIES.find((entry) => entry.id === 'device-missing');
const serviceInference = ENTRIES.find((entry) => entry.id === 'service-npu-unavailable');
if (!deviceMissing?.rootCause?.includes('候选原因')) {
	failures.push('device-missing: root cause must be phrased as a hypothesis pending board evidence');
}
if (deviceMissing?.steps?.some((step) => /升级顺序固定/.test(step.stop ?? ''))) {
	failures.push('device-missing: must not prescribe a fixed firmware/driver/CANN upgrade order');
}
if (!deviceMissing?.steps?.some((step) => /用户授权/.test(step.stop ?? '') && /不升级/.test(step.stop ?? ''))) {
	failures.push('device-missing: firmware/driver/CANN changes must require evidence and user authorization');
}
if (!serviceInference?.rootCause?.includes('候选原因') || !serviceInference?.rootCause?.includes('OM')) {
	failures.push('service-npu-unavailable: distinguish CANN environment hypothesis from model/runtime failures');
}

if (failures.length > 0) {
	console.error(`skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`skills test passed (${expected.length} 个 310B skills)`);
console.log(`  directory: ${skillsRoot}`);
