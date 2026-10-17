/**
 * Debug-plan tests: matching, ordering, version notes and honest probe state.
 *
 * Usage: node test/planner.mjs
 */

import { buildPlan, matchEntries, probeState, renderPlan } from '../lib/plan.js';
import { detectArchetype, detectCapabilities, indexQuery, indexSampleIndex } from '../lib/report.js';
import { parseProbeOutput } from '../lib/probe.js';
import { PROBE_20T_CANN80, PROBE_8T_CANN80 } from './fixtures.mjs';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => {
	if (actual !== expected) failures.push(`${message}（期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}）`);
};

// --- matching -------------------------------------------------------------
equal(matchEntries({ symptom: 'import acl 失败' })[0].id, 'acl-import-failed', '应匹配 PyACL 不可用条目');
const venc = matchEntries({ symptom: 'venc_create_channel=507018' });
check(venc.length > 0, 'VENC 通道创建失败应匹配到 DVPP/摄像头条目');
check(venc[0].id === 'camera-v4l2', `VENC 失败应优先落到 camera-v4l2，实际 ${venc[0]?.id}`);
check(matchEntries({ symptom: 'camera v4l2 打不开' })[0].id === 'camera-v4l2', '应匹配摄像头条目');
check(matchEntries({ symptom: 'undefined symbol libmindspore' })[0].id === 'mixed-framework-symbols', '应匹配框架混装条目');
check(matchEntries({ area: 'atc' }).every((entry) => entry.area === 'atc'), '领域过滤应只返回该领域条目');
equal(matchEntries({ symptom: '完全无关的字符串 zzzz' }).length, 0, '无关输入不应匹配任何条目');

// --- probe state is honest ------------------------------------------------
const eight = parseProbeOutput(PROBE_8T_CANN80);
equal(probeState('device', eight.facts), 'collected', '已探测 SoC 时设备层算已采集');
equal(probeState('cann', eight.facts), 'collected', '有 CANN 与 import acl 时 CANN 层算已采集');
equal(probeState('numerical', eight.facts), 'untested', '数值层永远是未采集：探针不产生数值证据');
equal(probeState('performance', eight.facts), 'untested', '性能层永远是未采集');
equal(probeState('conversion', {}), 'untested', '没有探测时空手也必须是未采集');

// --- plan shape -----------------------------------------------------------
const plan = buildPlan({ symptom: 'import acl 失败', facts: eight.facts });
equal(plan.gate, 'cann', 'ACL 失败应从 CANN 门开始');
check(plan.steps.length > 0, '计划必须包含步骤');
check(
	plan.steps.every((step) => typeof step.stopWhen === 'string' && step.stopWhen.length > 0),
	'每个步骤都必须带停止条件'
);
check(plan.probes.some((probe) => probe.id === 'cann'), '计划应包含 CANN 探针');
check(
	plan.evidenceGates.some((gate) => gate.id === 'performance' && gate.requires.includes('P50/P95')),
	'性能证据门必须要求 P50/P95'
);

const text = renderPlan(plan);
check(text.includes('停止条件'), '渲染文本应包含停止条件');
check(text.includes('未采集(untested)') || text.includes('已采集'), '渲染文本应标明每步的采集状态');

const unmatched = buildPlan({ symptom: 'zzzz' });
check(unmatched.matchedNothing, '无匹配时 matchedNothing 应为 true');
check(renderPlan(unmatched).includes('没有匹配到已收录的症状'), '无匹配时应给出明确说明');

// --- version-scoped notes -------------------------------------------------
const twenty = parseProbeOutput(PROBE_20T_CANN80);
const versionPlan = buildPlan({ area: 'env', facts: twenty.facts });
check(versionPlan.versionNotes.length > 0, 'CANN 版本漂移条目应带版本提示');
check(
	versionPlan.versionNotes.every((note) => typeof note.applies === 'boolean'),
	'版本提示必须显式给出是否适用'
);

// --- sample index ---------------------------------------------------------
equal(detectArchetype('atc --framework=5 resnet50.onnx')?.id, 'atc-conversion', 'ATC 关键词应识别为转换原型');
equal(detectArchetype('uvicorn app.py fastapi service')?.id, 'npu-service', '服务关键词应识别为服务原型');
check(detectCapabilities('需要 npu-smi 与 acl 以及 camera').includes('ascend'), '应识别 ascend 依赖');
check(detectCapabilities('npm run build').includes('frontend'), '应识别前端依赖');

const repoRoot = process.argv[2] ?? process.env.DSH_ASCEND310B_ROOT ?? null;
const index = indexSampleIndex({ root: repoRoot ?? process.cwd() });
check(Array.isArray(index.entries), '样例索引应返回数组');
check(Array.isArray(index.archetypes), '样例索引应返回原型列表');
if (repoRoot !== null) {
	check(index.entries.length > 0, '给定仓库根时样例索引不应为空');
	check(indexQuery(index, { archetype: 'atc-conversion' }).length > 0, '仓库中应至少有一个 ATC 转换样例');
	check(indexQuery(index, { query: '摄像头' }).length > 0, '中文“摄像头”必须能检索到样例');
	check(indexQuery(index, { query: 'VENC' }).some((entry) => entry.id === 'chapter6'), 'VENC 必须能检索到 DVPP 样例');
	check(indexQuery(index, { archetype: 'camera-v4l2' }).some((entry) => entry.id === 'case8'), 'case8 的主类型应为 camera-v4l2');
	const case4 = index.entries.find((entry) => entry.id === 'case4');
	check(String(case4?.entry ?? '').includes('palmprint_workbench.api'), 'case4 必须暴露真实服务入口，而不是 compileall');
}

if (failures.length > 0) {
	console.error('planner tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('planner tests passed');
