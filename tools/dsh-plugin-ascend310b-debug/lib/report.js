/**
 * Report assembly and sample indexing.
 *
 * Two responsibilities live here because both answer "what do we know and where
 * does it come from":
 *
 * - `assembleReport` renders the facts a board actually stated, each with the
 *   section it came from, plus the version-pairing verdict.
 * - `indexSampleIndex` / `indexQuery` turn `samples/` into a searchable list of
 *   runnable programs with their entry commands and debugging archetype.
 *
 * @module dsh-plugin-ascend310b-debug/report
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, join, relative, sep } from 'node:path';

/**
 * Field order and Chinese labels for the fact table.
 *
 * The order is the order a debugging session needs them: identity first, then
 * the software stack, then the runtime check that decides whether the stack is
 * usable at all.
 */
const FACT_FIELDS = Object.freeze([
	['hostname', '主机名'],
	['boardModel', '设备树型号'],
	['os', '操作系统'],
	['kernel', '内核'],
	['arch', '架构'],
	['soc', 'SoC / 芯片'],
	['chipName', 'npu-smi 芯片名'],
	['socTier', '算力档位'],
	['health', '设备健康'],
	['temperatureC', '温度 (C)'],
	['powerW', '功耗 (W)'],
	['memoryUsedMb', 'NPU 已用内存 (MB)'],
	['memoryTotalMb', 'NPU 总内存 (MB)'],
	['npuSmiVersion', 'npu-smi 版本'],
	['cannVersion', 'CANN 运行版本'],
	['cannPackageVersion', 'CANN 包版本'],
	['driverVersion', 'NPU 驱动版本'],
	['firmwareVersion', 'NPU 固件版本'],
	['cannLatestPath', 'Toolkit latest 指向'],
	['ascendDriverPath', '驱动目录'],
	['ascendFirmwarePath', '固件目录'],
	['setEnvPresent', 'set_env.sh 存在'],
	['atcOnPath', 'atc 在 PATH'],
	['aclImport', 'import acl'],
	['pythonExecutable', 'Python 解释器'],
	['pythonVersion', 'Python 版本'],
	['condaEnv', 'conda 环境'],
	['condaPrefix', 'conda 路径'],
	['pythonNoUserSite', 'PYTHONNOUSERSITE'],
	['deviceNodes', '设备节点'],
	['kernelModules', '内核模块'],
	['loginUser', '登录用户'],
	['probeTime', '探测时间']
]);

/** Human-readable value for one fact. */
function renderValue(fact) {
	if (fact === undefined || fact === null) return null;
	const value = fact.value;
	if (typeof value === 'boolean') return value ? '是' : '否';
	if (value === undefined || value === null || value === '') return null;
	return String(value);
}

/**
 * Assemble the report for one probed environment.
 *
 * @param {object} options - report options.
 * @param {object} options.environment - environment report from the probe parser.
 * @param {object} options.target - resolved target.
 * @param {string} options.source - `ssh`, `local` or `snapshot`.
 * @param {object} options.pairing - verdict from `assessVersionPairing`.
 * @returns {object} report with rendered text and JSON-safe parts.
 */
export function assembleReport({ environment, target, source, pairing }) {
	const facts = environment.facts ?? {};
	const scope = environment.scope ?? null;
	const lines = [];
	lines.push(`## 板端环境（来源：${source}，目标：${target?.label ?? '未指定'}）`);
	if (scope !== null && scope.applicable !== true) {
		lines.push('', `> ${scope.message}`);
	}
	if (environment.board === null || environment.board === undefined) {
		lines.push('', '未从输出中识别到 Ascend 310B 设备行。若这是本机运行，本机没有 NPU 运行时属于预期结果；板端请确认 npu-smi 可用。');
	} else {
		const board = environment.board;
		lines.push(
			'',
			`设备：${board.soc}（${board.chip}，${board.tier}）` +
				(board.health === null ? '' : `，Health ${board.health}`) +
				(board.temperatureC === null ? '' : `，${board.temperatureC} C`) +
				(board.memoryTotalMb === null ? '' : `，内存 ${board.memoryUsedMb}/${board.memoryTotalMb} MB`)
		);
	}

	lines.push('', '| 项目 | 板端实际值 | 来源节 |');
	lines.push('| --- | --- | --- |');
	let rendered = 0;
	for (const [key, label] of FACT_FIELDS) {
		const value = renderValue(facts[key]);
		if (value === null) continue;
		rendered += 1;
		lines.push(`| ${label} | ${value} | ${facts[key].source ?? ''} |`);
	}
	if (rendered === 0) lines.push('| （无） | 未采集到任何字段 | - |');

	const missing = environment.missingCore ?? [];
	lines.push('', '### 未采集的关键项');
	lines.push(
		missing.length === 0
			? '- 无：SoC、CANN、驱动三项都已采集。'
			: `- ${missing.join('、')}：保持为未采集，不要用推测值代替。`
	);

	lines.push('', '### 版本配套判定');
	lines.push(`- 结论：${pairing.verdict}（证据：${pairing.evidence}）`);
	for (const reason of pairing.reasons ?? []) lines.push(`- ${reason}`);
	lines.push('', '### 下一步');
	for (const action of pairing.nextActions ?? []) lines.push(`- ${action}`);

	return {
		text: lines.join('\n'),
		facts,
		board: environment.board ?? null,
		scope,
		pairing,
		missingCore: missing
	};
}

/** Archetype rules, most specific first. */
const ARCHETYPE_RULES = Object.freeze([
	{ id: 'atc-conversion', keywords: ['atc', 'onnx', 'om', 'convert', 'conversion', 'soc_version'], label: 'ATC ONNX→OM 转换' },
	{ id: 'quantization', keywords: ['quant', 'quantization', 'int8', 'calibration', '量化'], label: '量化与精度' },
	{ id: 'camera-v4l2', keywords: ['v4l2', 'camera', '摄像头', 'video'], label: '摄像头与 V4L2' },
	{ id: 'dvpp-video', keywords: ['dvpp', 'venc', 'vdec', 'vpc', 'video codec'], label: 'DVPP 视频处理' },
	{ id: 'custom-operator', keywords: ['operator', '算子', 'tbe', 'custom'], label: '自定义算子' },
	{ id: 'framework-migration', keywords: ['mindspore', 'torch_npu', 'npu training', '迁移'], label: '框架迁移与训练' },
	{ id: 'performance', keywords: ['performance', 'benchmark', 'latency', 'fps', '性能'], label: '性能与基准' },
	{ id: 'npu-service', keywords: ['fastapi', 'uvicorn', 'app.py', 'service', '服务', 'webui', 'web'], label: 'NPU 服务与前后端' },
	{ id: 'pyacl-inference', keywords: ['acl', 'pyacl', 'inference', '推理'], label: 'PyACL 推理' }
]);

/**
 * Canonical samples whose README necessarily covers several neighboring
 * topics. Keyword voting is useful for uncurated directories, but a sample
 * whose primary path is a service, camera pipeline or framework migration must
 * keep that primary archetype when its README also mentions ATC or OM.
 */
const SAMPLE_ARCHETYPE_OVERRIDES = Object.freeze({
	case4: Object.freeze({ id: 'npu-service', label: 'NPU 服务与前后端' }),
	case5: Object.freeze({ id: 'npu-service', label: 'NPU 服务与前后端' }),
	case6: Object.freeze({ id: 'npu-service', label: 'NPU 服务与前后端' }),
	case7: Object.freeze({ id: 'npu-service', label: 'NPU 服务与前后端' }),
	case8: Object.freeze({ id: 'camera-v4l2', label: '摄像头与 V4L2' }),
	case9: Object.freeze({ id: 'npu-service', label: 'NPU 服务与前后端' }),
	chapter3: Object.freeze({ id: 'framework-migration', label: '框架迁移与训练' }),
	chapter4: Object.freeze({ id: 'pyacl-inference', label: 'PyACL 推理' }),
	chapter5: Object.freeze({ id: 'framework-migration', label: '框架迁移与训练' }),
	chapter6: Object.freeze({ id: 'dvpp-video', label: 'DVPP 视频处理' }),
	chapter8: Object.freeze({ id: 'performance', label: '性能与基准' }),
	chapter9: Object.freeze({ id: 'quantization', label: '量化与精度' })
});

/**
 * Explicit primary run commands for samples whose first Python command is a
 * compile or asset check. The command is still verified against the README, so
 * a stale override cannot silently point at a command the sample no longer
 * documents.
 */
const SAMPLE_ENTRY_OVERRIDES = Object.freeze({
	case4: 'python -m palmprint_workbench.api --host 0.0.0.0 --port 7860'
});

/**
 * Curated Chinese and English search terms for tutorial samples.
 *
 * The README is not copied into the index: it contains board addresses and
 * credentials that must not leak into a distributable package. These terms are
 * the searchable vocabulary the book actually uses when a reader describes a
 * symptom, model or task.
 */
const SAMPLE_SEARCH_TERMS = Object.freeze({
	case1: ['人脸识别', '考勤', '摄像头', 'camera', 'PyACL', '服务', '考勤机', '检测'],
	case2: ['目标跟踪', '目标检测', '摄像头', 'camera', 'v4l2', 'DeepSORT', 'YOLO', '视频', '检测'],
	case3: ['电子琴', '音频', 'MIDI', 'Piano', '服务', '前端'],
	case4: ['掌纹', '掌纹识别', '摄像头', 'camera', 'v4l2', '服务', '推理', '模型'],
	case5: ['数据采集', '数据采集分析仪', 'DFT', '时频', '服务', '前端'],
	case6: ['小车', '智能小车', '车道线', '场景分类', '摄像头', 'camera', '服务'],
	case7: ['相册', '智能相册', '图像', '检索', '语义', '服务', '前端'],
	case8: ['手势', '手势识别', '摄像头', 'camera', 'v4l2', 'WebRTC', '视频', '推理'],
	case9: ['聊天', '聊天机器人', '大模型', '推理', '服务', '前端', 'Qwen', 'MindSpore', '超时'],
	chapter3: ['torch_npu', 'PyTorch', '迁移', '训练', '框架'],
	chapter4: ['PyACL', 'ACL', '推理', '模型', 'OM', 'ATC'],
	chapter5: ['MindSpore', '训练', '推理', 'MindIR', '框架'],
	chapter6: ['DVPP', 'VENC', 'VDEC', 'VPC', '视频', '摄像头', 'camera', '编解码'],
	chapter8: ['性能', 'benchmark', 'P50', 'P95', '优化', '延迟'],
	chapter9: ['量化', 'INT8', '精度', '校准']
});

/** Capability markers, detected from README text. */
const CAPABILITY_RULES = Object.freeze([
	{ id: 'ascend', pattern: /ascend|npu|cann|acl|atc|om\b|昇腾/i },
	{ id: 'camera', pattern: /camera|v4l2|摄像头|video\d/i },
	{ id: 'network', pattern: /download|http|api|remote|ssh|网络|下载/i },
	{ id: 'conda', pattern: /conda|miniconda/i },
	{ id: 'model-asset', pattern: /\.onnx|\.om\b|checkpoint|weights|模型权重/i },
	{ id: 'frontend', pattern: /npm|vite|react|frontend|前端/i },
	{ id: 'test', pattern: /pytest|npm test|unit test|测试/i }
]);

/** Extract the title from a README's first heading. */
function readmeTitle(text, fallback) {
	const match = /^#\s+(.+)$/m.exec(text ?? '');
	return match === null ? fallback : match[1].replace(/^案例\s*\d+[：:]\s*/u, '').trim();
}

/** Extract the first shell command inside fenced code blocks. */
function firstCommand(text) {
	if (typeof text !== 'string') return null;
	const blocks = [...text.matchAll(/```(?:bash|sh|shell|console)?\n([\s\S]*?)```/g)];
	for (const block of blocks) {
		for (const line of block[1].split(/\r?\n/)) {
			const candidate = line.trim().replace(/^\$\s*/, '');
			if (candidate === '' || candidate.startsWith('#')) continue;
			if (/^(python|python3|bash|sh|npm|pnpm|pytest|node|make|\.\/)\b/.test(candidate)) return candidate;
		}
	}
	return null;
}

/** Extract a test command, if the README names one. */
function firstTestCommand(text) {
	if (typeof text !== 'string') return null;
	for (const line of text.split(/\r?\n/)) {
		const candidate = line.trim().replace(/^\$\s*/, '');
		if (/^(pytest|python -m pytest|npm test|pnpm test|npm run test|bash .*test)/.test(candidate)) return candidate;
	}
	return null;
}

/** Keep only readme-verified commands: a command that no longer appears is dropped. */
function verifyCommand(command, text) {
	if (command === null || typeof text !== 'string') return null;
	return text.includes(command.replace(/^\$\s*/, '')) ? command : null;
}

/**
 * Detect the debugging archetype of one sample directory.
 *
 * @param {string} combined - id, title, keywords and README text.
 * @returns {{id: string, label: string}|null} archetype or null.
 */
export function detectArchetype(combined) {
	const lower = combined.toLowerCase();
	let best = null;
	for (const rule of ARCHETYPE_RULES) {
		const hits = rule.keywords.filter((keyword) => lower.includes(keyword.toLowerCase())).length;
		if (hits === 0) continue;
		if (best === null || hits > best.hits) best = { ...rule, hits };
	}
	return best === null ? null : { id: best.id, label: best.label };
}

/** Detect capabilities a sample needs. */
export function detectCapabilities(combined) {
	return CAPABILITY_RULES.filter((rule) => rule.pattern.test(combined)).map((rule) => rule.id);
}

/**
 * Scan `samples/` and build the debug index.
 *
 * The scan is structural (directories and READMEs), so it stays true when the
 * repository moves. Uncurated semantic fields use keyword rules; the canonical
 * textbook samples have explicit primary archetypes, entries and search terms
 * so a README that covers several topics cannot hide its main path.
 *
 * @param {object} options - scan options.
 * @param {string} options.root - repository root, or the `samples` directory.
 * @param {object} [options.caseIndex] - parsed `samples/case-index.json`.
 * @returns {object} index with `entries` and `archetypes`.
 */
export function indexSampleIndex({ root, caseIndex }) {
	const samplesRoot = basename(root) === 'samples' ? root : join(root, 'samples');
	const cases = new Map();
	for (const item of caseIndex?.cases ?? []) {
		cases.set(item.id, item);
	}
	const entries = [];
	let directories = [];
	try {
		directories = readdirSync(samplesRoot, { withFileTypes: true })
			.filter((item) => item.isDirectory())
			.map((item) => item.name)
			.sort();
	} catch {
		return { schemaVersion: 1, generatedFrom: 'samples', entries: [], archetypes: [] };
	}
	for (const name of directories) {
		const readmePath = join(samplesRoot, name, 'README.md');
		let text = null;
		try {
			if (!statSync(readmePath).isFile()) continue;
			text = readFileSync(readmePath, 'utf8');
		} catch {
			continue;
		}
		const known = cases.get(name);
		const keywords = known?.keywords ?? [];
		const title = known?.title_zh ?? readmeTitle(text, name);
		const combined = [name, title, keywords.join(' '), text].join('\n');
		const entry = verifyCommand(SAMPLE_ENTRY_OVERRIDES[name] ?? firstCommand(text), text);
		const test = verifyCommand(firstTestCommand(text), text);
		const archetype = SAMPLE_ARCHETYPE_OVERRIDES[name] ?? detectArchetype(combined);
		const searchTerms = [...new Set([...keywords, ...(SAMPLE_SEARCH_TERMS[name] ?? [])])];
		entries.push({
			id: name,
			title,
			readme: `samples/${name}/README.md`,
			keywords,
			searchTerms,
			archetype: archetype?.id ?? null,
			archetypeLabel: archetype?.label ?? null,
			entry,
			test,
			capabilities: detectCapabilities(combined)
		});
	}
	const archetypes = [...new Set(entries.map((entry) => entry.archetype).filter(Boolean))].sort();
	return { schemaVersion: 1, generatedFrom: 'samples', generatedAt: null, entries, archetypes };
}

/**
 * Load a previously generated index, tolerating a missing or partial file.
 *
 * @param {object} parsed - parsed JSON.
 * @returns {object} normalized index.
 */
export function loadSampleIndex(parsed) {
	if (parsed === null || typeof parsed !== 'object' || !Array.isArray(parsed.entries)) {
		return { schemaVersion: 1, entries: [], archetypes: [] };
	}
	return {
		schemaVersion: parsed.schemaVersion ?? 1,
		generatedFrom: parsed.generatedFrom ?? 'samples',
		generatedAt: parsed.generatedAt ?? null,
		entries: parsed.entries,
		archetypes: parsed.archetypes ?? [...new Set(parsed.entries.map((entry) => entry.archetype).filter(Boolean))]
	};
}

/**
 * Query the sample index.
 *
 * @param {object} index - index from {@link indexSampleIndex}.
 * @param {object} options - query options.
 * @param {string} [options.query] - free-text query.
 * @param {string} [options.archetype] - exact archetype filter.
 * @param {number} [options.limit] - maximum results.
 * @returns {object[]} matching entries.
 */
export function indexQuery(index, { query, archetype, limit = 12 } = {}) {
	const terms = typeof query === 'string'
		? query
				.toLowerCase()
				.split(/[\s,，、/]+/)
				.map((term) => term.trim())
				.filter((term) => term.length >= 2)
		: [];
	const scored = [];
	for (const entry of index.entries ?? []) {
		if (typeof archetype === 'string' && archetype !== '' && entry.archetype !== archetype) continue;
		const haystack = [
			entry.id,
			entry.title,
			(entry.keywords ?? []).join(' '),
			(entry.searchTerms ?? []).join(' '),
			entry.archetype ?? '',
			(entry.capabilities ?? []).join(' '),
			entry.entry ?? ''
		]
			.join(' ')
			.toLowerCase();
		let score = 0;
		for (const term of terms) {
			if (haystack.includes(term)) score += 2;
			if (String(entry.id).toLowerCase().includes(term)) score += 3;
		}
		if (terms.length === 0 || score > 0) scored.push({ entry, score });
	}
	scored.sort((left, right) => right.score - left.score || String(left.entry.id).localeCompare(String(right.entry.id)));
	return scored.slice(0, limit).map((item) => item.entry);
}

/** Relative, forward-slash path for display. */
export function displayPath(root, absolute) {
	return relative(root, absolute).split(sep).join('/');
}
