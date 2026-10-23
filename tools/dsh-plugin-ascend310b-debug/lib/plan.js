/**
 * Debug-plan synthesis for Ascend 310B programs.
 *
 * A plan is built from three inputs and never from a guess:
 *
 * 1. the user's description of the failure or goal,
 * 2. the knowledge entries whose symptoms or area match,
 * 3. the facts that were actually probed from the board.
 *
 * The third input only ever *narrows* a plan. A step whose probe has not been
 * collected is emitted as `untested` with the exact command needed to collect
 * it — it is never silently marked as passing.
 *
 * @module dsh-plugin-ascend310b-debug/plan
 */

import { AREAS, ENTRIES, GATE_ORDER, PROBE_BUCKETS, VERSION_PREDICATES } from './knowledge.js';

/**
 * Match knowledge entries against a request.
 *
 * Matching is intentionally literal: a symptom phrase or a function area. A
 * request that matches nothing returns an empty list so the caller can say so,
 * instead of attaching an unrelated procedure to the failure.
 *
 * @param {object} request - matching input.
 * @param {string} [request.symptom] - user description, error text, or goal.
 * @param {string} [request.area] - optional area filter.
 * @returns {object[]} matched entries, most specific first.
 */
export function matchEntries(request = {}) {
	const symptom = typeof request.symptom === 'string' ? request.symptom.toLowerCase() : '';
	const area = typeof request.area === 'string' ? request.area : null;
	const scored = [];
	for (const entry of ENTRIES) {
		if (area !== null && entry.area !== area) continue;
		let score = area !== null ? 1 : 0;
		const hits = [];
		for (const pattern of entry.symptoms) {
			if (symptom !== '' && symptom.includes(pattern.toLowerCase())) {
				score += 3;
				hits.push(pattern);
			}
		}
		if (symptom !== '' && entry.title.toLowerCase().includes(symptom)) score += 2;
		if (symptom !== '' && score > 0) scored.push({ entry, score, hits });
		else if (symptom === '' && area !== null) scored.push({ entry, score, hits });
	}
	return scored
		.sort((left, right) => right.score - left.score || left.entry.id.localeCompare(right.entry.id))
		.map((item) => ({ ...item.entry, matchedSymptoms: item.hits, score: item.score }));
}

/** The gate a matched entry fails at, used to order the plan. */
export function earliestGate(entries) {
	let index = GATE_ORDER.length - 1;
	for (const entry of entries) {
		const position = GATE_ORDER.indexOf(entry.stage);
		if (position >= 0 && position < index) index = position;
	}
	return GATE_ORDER[index];
}

/**
 * Turn a probe bucket into a collection step whose state is honest.
 *
 * @param {string} bucketId - probe bucket id.
 * @param {object} facts - probed facts map.
 * @returns {object} probe step with `state` of collected/untested.
 */
export function probeStep(bucketId, facts = {}) {
	const bucket = PROBE_BUCKETS[bucketId];
	if (bucket === undefined) return null;
	const state = probeState(bucketId, facts);
	return {
		id: bucket.id,
		question: bucket.question,
		commands: [...bucket.commands],
		passesWhen: bucket.passes,
		failsWhen: bucket.fails,
		state,
		note:
			state === 'collected'
				? '本次报告已包含该层的探测结果，按上面的通过/失败判据核对。'
				: `尚未采集：先执行上面的命令并解析输出，再下结论（当前状态为 untested）。`
	};
}

/**
 * Decide whether a probe bucket has been collected.
 *
 * @param {string} bucketId - probe bucket id.
 * @param {object} facts - probed facts map.
 * @returns {'collected'|'untested'} probe state.
 */
export function probeState(bucketId, facts = {}) {
	const has = (key) => facts[key] !== undefined;
	switch (bucketId) {
		case 'device':
			return has('soc') ? 'collected' : 'untested';
		case 'cann':
			return has('cannVersion') && has('aclImport') ? 'collected' : 'untested';
		case 'conversion':
		case 'load':
		case 'contract':
		case 'numerical':
		case 'task':
		case 'performance':
			return 'untested';
		case 'service':
			return has('serviceHealth') || has('serviceProcess') ? 'collected' : 'untested';
		case 'sanity':
			return 'untested';
		default:
			return 'untested';
	}
}

/**
 * The four evidence labels this repository uses, restated so a plan can require
 * them explicitly. A result must be labelled; an unlabelled result is the most
 * common way a debugging session ends up with a wrong conclusion.
 */
export const EVIDENCE_GATES = Object.freeze([
	{
		id: 'artifact-integrity',
		question: '工件是完整的吗？',
		requires: ['文件路径', '字节数', 'SHA-256'],
		notProvenBy: '文件存在或名字正确'
	},
	{
		id: 'numerical',
		question: '数值与参考实现一致吗？',
		requires: ['输入哈希', '参考输出', '容差', '最大绝对误差'],
		notProvenBy: '导入成功或 shape 正确'
	},
	{
		id: 'task-metric',
		question: '任务指标达标吗？',
		requires: ['数据集', '样本数', '预处理协议', '指标值'],
		notProvenBy: '数值烟测'
	},
	{
		id: 'performance',
		question: '性能可复现吗？',
		requires: ['硬件档位', '模型与精度', 'warmup', '计时次数', '重复次数', '线程数', 'P50/P95'],
		notProvenBy: '单次最好值或另一档位（20T）的数字'
	}
]);

/** Render matched entries as ordered steps. */
function renderSteps(entries, facts) {
	const steps = [];
	const seen = new Set();
	for (const entry of entries) {
		for (const step of entry.steps) {
			const key = `${entry.id}:${step.probe}:${step.do}`;
			if (seen.has(key)) continue;
			seen.add(key);
			steps.push({
				entryId: entry.id,
				entryTitle: entry.title,
				probe: step.probe,
				probeState: probeState(step.probe, facts),
				do: step.do,
				stopWhen: step.stop
			});
		}
	}
	return steps;
}

/** Version-scoped notes that apply to the probed environment. */
function versionNotes(entries, facts) {
	const ids = new Set();
	for (const entry of entries) {
		for (const id of entry.versionPredicates ?? []) ids.add(id);
	}
	const notes = [];
	for (const id of ids) {
		const predicate = VERSION_PREDICATES[id];
		if (predicate === undefined) continue;
		notes.push({
			id,
			applies: predicate.test(facts),
			when: predicate.describe,
			note: predicate.note
		});
	}
	return notes;
}

/**
 * Build a debugging plan.
 *
 * @param {object} request - plan request.
 * @param {string} [request.symptom] - symptom text, error message, or goal.
 * @param {string} [request.area] - area filter id.
 * @param {object} [request.facts] - probed facts map.
 * @param {object[]} [request.samples] - sample entries to attach as runnable references.
 * @param {number} [request.maxEntries] - cap on matched entries.
 * @returns {object} plan with steps, probes, gates, notes and samples.
 */
export function buildPlan(request = {}) {
	const facts = request.facts ?? {};
	const maxEntries = Number.isInteger(request.maxEntries) && request.maxEntries > 0 ? request.maxEntries : 3;
	const matched = matchEntries({ symptom: request.symptom, area: request.area }).slice(0, maxEntries);
	const area = typeof request.area === 'string' ? request.area : null;
	const gate = matched.length > 0 ? earliestGate(matched) : area === null ? GATE_ORDER[1] : earliestGate(ENTRIES.filter((entry) => entry.area === area));

	const probes = [];
	for (const entry of matched) {
		for (const step of entry.steps) {
			if (!probes.some((probe) => probe.id === step.probe)) probes.push(probeStep(step.probe, facts));
		}
	}
	if (probes.length === 0) probes.push(probeStep('device', facts), probeStep('cann', facts));

	const notes = versionNotes(matched, facts);
	const samples = Array.isArray(request.samples) ? request.samples.slice(0, 8) : [];

	return {
		matched,
		gate,
		gateOrder: GATE_ORDER,
		steps: renderSteps(matched, facts),
		probes: probes.filter(Boolean),
		versionNotes: notes,
		evidenceGates: EVIDENCE_GATES,
		samples,
		matchedNothing: request.symptom !== undefined && request.symptom !== '' && matched.length === 0,
		areas: Object.values(AREAS)
	};
}

/**
 * Render a plan as the Markdown-ish text the tools return.
 *
 * The header states the stopping rule first, because the most expensive failure
 * mode in this domain is not a wrong command — it is continuing past a layer
 * that already failed.
 *
 * @param {object} plan - plan from {@link buildPlan}.
 * @returns {string} human-readable plan.
 */
export function renderPlan(plan) {
	const lines = [];
	lines.push('## 昇腾310B调试计划');
	if (plan.matchedNothing) {
		lines.push('', '没有匹配到已收录的症状。请补充报错原文（错误码、日志行或失败命令），或指定功能领域后重试。');
		lines.push('可用的领域：' + plan.areas.map((area) => `${area.id}（${area.label}）`).join('、'));
		return lines.join('\n');
	}
	lines.push('', `起点门：${plan.gate}（按固定顺序推进，前一门未过不要进入下一门）`);
	lines.push('', '### 归因（按层记录，不要把一层的失败写成整机不支持）');
	for (const entry of plan.matched) {
		lines.push(`- **${entry.title}**（领域 ${entry.area}，阶段 ${entry.stage}）`);
		lines.push(`  - 机制假设：${entry.rootCause}`);
		if (entry.matchedSymptoms.length > 0) lines.push(`  - 命中症状：${entry.matchedSymptoms.join('、')}`);
	}
	lines.push('', '### 步骤（每步都有停止条件）');
	for (const [index, step] of plan.steps.entries()) {
		const state = step.probeState === 'collected' ? '已采集' : '未采集(untested)';
		lines.push(`${index + 1}. [${step.probe} / ${state}] ${step.do}`);
		lines.push(`   - 停止条件：${step.stopWhen}`);
	}
	lines.push('', '### 需要采集的证据');
	for (const probe of plan.probes) {
		lines.push(`- **${probe.id}**：${probe.question}`);
		for (const command of probe.commands) lines.push(`  - \`${command}\``);
		lines.push(`  - 通过：${probe.passesWhen}`);
		lines.push(`  - 失败：${probe.failsWhen}`);
		lines.push(`  - ${probe.note}`);
	}
	if (plan.versionNotes.length > 0) {
		lines.push('', '### 版本相关提示');
		for (const note of plan.versionNotes) {
			lines.push(`- [${note.applies ? '适用本次探测版本' : '暂不适用'}] ${note.when}：${note.note}`);
		}
	}
	lines.push('', '### 证据分级（每项结果都必须带标签）');
	for (const gate of plan.evidenceGates) {
		lines.push(`- ${gate.question} 需要：${gate.requires.join('、')}；不能由「${gate.notProvenBy}」替代。`);
	}
	if (plan.samples.length > 0) {
		lines.push('', '### 可参考的 samples 程序');
		for (const sample of plan.samples) {
			lines.push(`- ${sample.id}：${sample.title}（${sample.readme}）`);
			if (sample.entry !== undefined && sample.entry !== null) lines.push(`  - 入口命令：\`${sample.entry}\``);
			if (sample.archetype !== undefined && sample.archetype !== null) lines.push(`  - 调试原型：${sample.archetype}`);
		}
	}
	return lines.join('\n');
}
