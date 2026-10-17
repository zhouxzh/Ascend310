/**
 * Ascend 310B debug assistant — Harness host half.
 *
 * Registers five model-facing tools plus two read-only HTTP routes the browser
 * panel uses, and stays useful in every composition: the tools work headless,
 * and only the routes need a Web server.
 *
 * The one design rule that shapes everything here: **the plugin never asserts a
 * board fact it did not read from that board.** Board model, driver, firmware
 * and CANN versions come from a live probe or a saved snapshot. Everything the
 * plugin knows ahead of time — version pairing rules, failure procedures,
 * sample entry points — is labelled `documented`, `inferred` or `untested`.
 *
 * @module dsh-plugin-ascend310b-debug
 */

import { readFileSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineTool } from '@deepseek-ai/dsh-tools';

import { assembleReport, indexQuery, indexSampleIndex, loadSampleIndex } from './report.js';
import {
	assessVersionPairing,
	CANN_RELEASE_LINES,
	OBSERVED_STACKS,
	PLUGIN_DISPLAY_NAME,
	SUPPORTED_SOCS,
	UPGRADE_ORDER
} from './compat.js';
import { buildPlan, renderPlan } from './plan.js';
import { AREAS, ENTRIES } from './knowledge.js';
import { buildProbeScript, parseProbeOutput } from './probe.js';
import { isSafeHost, runLocalScript, runRemoteScript, validateReadOnlyCommand } from './shell.js';

const require = createRequire(import.meta.url);

/** Cordis plugin name used by loader diagnostics. */
export const name = 'dsh-plugin-ascend310b-debug';

/** Host services this plugin needs before it may mount. */
export const inject = ['tools'];

/** Read-only panel route: current report, optionally freshly probed. */
const REPORT_PATH = '/ascend310b-debug/report.json';

/** Route that runs the probe against a configured target. */
const PROBE_PATH = '/ascend310b-debug/probe';

/** Maximum stdout kept in a tool response. */
const MAX_EXCERPT = 4000;

/** Cached report for the panel, keyed by target id. */
const reportCache = new Map();

/**
 * Resolve the repository root from the call argument, plugin config, or
 * environment. No checkout path is baked in: a hardcoded root would silently
 * index the wrong repository.
 *
 * @param {object} config - plugin configuration.
 * @param {string} [argument] - per-call override.
 * @returns {string} absolute repository root.
 * @throws {Error} when nothing is configured.
 */
function resolveRepoRoot(config, argument) {
	if (typeof argument === 'string' && argument.trim() !== '') return argument.trim();
	if (typeof config?.repoRoot === 'string' && config.repoRoot.trim() !== '') return config.repoRoot.trim();
	const fromEnv = process.env.DSH_ASCEND310B_ROOT;
	if (typeof fromEnv === 'string' && fromEnv.trim() !== '') return fromEnv.trim();
	throw new Error(
		'dsh-plugin-ascend310b-debug: 未配置教材仓库根目录。请任选其一——' +
			'① 给工具传 repoRoot 参数；' +
			'② 在 profile 的 cordis.patch.yml 里为 id `dsh-plugin-ascend310b-debug` 设置 config.repoRoot；' +
			'③ 设置环境变量 DSH_ASCEND310B_ROOT。'
	);
}

/** Normalize the configured target list into a lookup table. */
function targetTable(config) {
	const table = new Map();
	const configured = Array.isArray(config?.targets) ? config.targets : [];
	for (const entry of configured) {
		if (typeof entry?.id !== 'string' || entry.id.trim() === '') continue;
		const transport = entry.transport === 'local' ? 'local' : 'ssh';
		table.set(entry.id, {
			id: entry.id,
			label: typeof entry.label === 'string' && entry.label !== '' ? entry.label : entry.id,
			transport,
			host: transport === 'local' ? 'localhost' : String(entry.host ?? ''),
			user: typeof entry.user === 'string' && entry.user.trim() !== '' ? entry.user.trim() : null,
			port: Number.isInteger(entry.port) ? entry.port : null,
			identityFile: typeof entry.identityFile === 'string' ? entry.identityFile : null,
			workdir: typeof entry.workdir === 'string' ? entry.workdir : null,
			note: typeof entry.note === 'string' ? entry.note : null
		});
	}
	return table;
}

/**
 * Resolve which board to talk to.
 *
 * Priority: explicit argument, environment default, configured default, then a
 * single configured target. Two configured boards with no default is a
 * mistake the user must resolve, so the error names the candidates instead of
 * picking one.
 *
 * @param {object} config - plugin configuration.
 * @param {string} [argument] - configured id or configured `user@host` selector.
 * @returns {object} resolved target.
 * @throws {Error} when the selector is unknown or ambiguous.
 */
export function resolveTarget(config, argument) {
	const table = targetTable(config);
	const requested = typeof argument === 'string' && argument.trim() !== '' ? argument.trim() : null;
	if (requested !== null) {
		const named = table.get(requested);
		if (named !== undefined) return named;
		for (const candidate of table.values()) {
			if (`${candidate.user ? `${candidate.user}@` : ''}${candidate.host}` === requested) return candidate;
		}
		if (requested === 'local') return { id: 'local', label: '本机（无 NPU）', transport: 'local', host: 'localhost' };
		throw new Error(
			`dsh-plugin-ascend310b-debug: 未配置目标 ${requested}。已配置：${[...table.keys()].join('、') || '（无）'}；` +
				'也可以用 `local` 在本机执行（本机没有 NPU 运行时）。'
		);
	}
	const fromEnv = process.env.DSH_ASCEND310B_TARGET;
	if (typeof fromEnv === 'string' && fromEnv.trim() !== '') return resolveTarget(config, fromEnv.trim());
	const defaultId = typeof config?.defaultTarget === 'string' ? config.defaultTarget.trim() : '';
	if (defaultId !== '') return resolveTarget(config, defaultId);
	if (table.size === 1) return [...table.values()][0];
	if (table.size === 0) return { id: 'local', label: '本机（无 NPU）', transport: 'local', host: 'localhost' };
	throw new Error(
		'dsh-plugin-ascend310b-debug: 配置了多个开发板但没有默认值。请给工具传 target 参数，或在 profile 配置里设置 config.defaultTarget。' +
			`候选：${[...table.keys()].join('、')}`
	);
}

/** Targets for panel display, without secrets. */
function publicTargets(config) {
	const table = targetTable(config);
	const defaultId = typeof config?.defaultTarget === 'string' ? config.defaultTarget : null;
	return [...table.values()].map((target) => ({
		id: target.id,
		label: target.label,
		transport: target.transport,
		host: target.host,
		user: target.user,
		port: target.port,
		isDefault: target.id === defaultId,
		note: target.note
	}));
}

/** Flatten probe output into the JSON-safe shape the tools return. */
function serializeEnvironment(environment) {
	const facts = {};
	for (const [key, fact] of Object.entries(environment.facts ?? {})) {
		facts[key] = {
			value: fact.value === undefined || fact.value === null ? null : String(fact.value),
			source: fact.source ?? '',
			evidence: fact.evidence === undefined || fact.evidence === null ? '' : String(fact.evidence),
			evidenceLabel: fact.evidenceLabel ?? 'board-output'
		};
	}
	return { facts, board: environment.board ?? null, missingCore: environment.missingCore ?? [], sections: environment.sections ?? {} };
}

/** Read one file as text, returning null instead of throwing. */
function readText(path) {
	try {
		return readFileSync(path, 'utf8');
	} catch {
		return null;
	}
}

/** Resolve a path against the repository root without escaping it. */
function resolveInside(root, candidate) {
	if (typeof candidate !== 'string' || candidate.trim() === '') return null;
	const normalizedRoot = resolve(root);
	const absolute = isAbsolute(candidate) ? resolve(candidate) : resolve(normalizedRoot, candidate);
	const relative = absolute.slice(normalizedRoot.length);
	return absolute !== normalizedRoot && (relative.startsWith('\\') || relative.startsWith('/')) ? absolute : null;
}

/**
 * Load the generated sample index, falling back to a live scan of the samples
 * tree so a freshly cloned repository works before anyone runs the generator.
 */
function readSampleIndex(repoRoot) {
	const moduleRoot = dirname(fileURLToPath(import.meta.url));
	const candidates = [resolveInside(moduleRoot, join('..', 'data', 'sample-index.json'))];
	for (const candidate of candidates) {
		if (candidate === null) continue;
		const text = readText(candidate);
		if (text === null) continue;
		try {
			return loadSampleIndex(JSON.parse(text));
		} catch {
			// A malformed index should not take the tool down; fall through to a scan.
		}
	}
	return indexSampleIndex({ root: join(repoRoot, 'samples') });
}

/**
 * Build the sample index for a repository, degrading instead of throwing.
 *
 * The route handlers and the plan tool all want the index, but a composition
 * without `config.repoRoot` is still allowed to work: the shipped snapshot is
 * indexed from the package itself, so only the live rescan needs the checkout.
 *
 * @param {object} config - plugin configuration.
 * @param {string} [argument] - per-call root override.
 * @returns {{index: object, repoRoot: string|null, note: string|null}} index result.
 */
function sampleIndexFor(config, argument) {
	try {
		const repoRoot = resolveRepoRoot(config, argument);
		return { index: readSampleIndex(repoRoot), repoRoot, note: null };
	} catch (error) {
		return { index: readSampleIndex(process.cwd()), repoRoot: null, note: String(error?.message ?? error) };
	}
}

/** Build the environment report used by both routes and tools. */
function environmentReport({ environment, target, source }) {
	return assembleReport({
		environment,
		target,
		source,
		pairing: assessVersionPairing(environment.facts ?? {})
	});
}

/**
 * Run the probe: live (local or SSH) or from a saved snapshot.
 *
 * @param {object} options - probe options.
 * @returns {Promise<object>} report plus raw output.
 */
async function collectEnvironment({ config, targetSelector, rawText, timeoutMs, exec }) {
	if (typeof rawText === 'string' && rawText.trim() !== '') {
		const environment = parseProbeOutput(rawText);
		return {
			target: { id: 'snapshot', label: '已保存的探测输出', transport: 'snapshot', host: null, user: null, port: null },
			source: 'snapshot',
			environment,
			rawText,
			transport: { ok: true, reason: '已解析用户提供的探测输出。', display: 'snapshot' }
		};
	}
	const target = resolveTarget(config, targetSelector);
	const script = buildProbeScript();
	const transport =
		target.transport === 'local'
			? await runLocalScript({ script, cwd: target.workdir ?? undefined, timeoutMs, exec })
			: await runRemoteScript({ script, target, timeoutMs, exec });
	const environment = parseProbeOutput(transport.stdout ?? '');
	return { target, source: target.transport, environment, rawText: transport.stdout ?? '', transport };
}

/** Clamp an excerpt for tool output. */
function excerpt(text) {
	if (typeof text !== 'string') return '';
	return text.length <= MAX_EXCERPT ? text : `${text.slice(0, MAX_EXCERPT)}\n…（已截断 ${text.length - MAX_EXCERPT} 字符）`;
}

/** Sample lookup for the planner. */
function samplesFor(repoRoot, query, archetype, limit) {
	const index = readSampleIndex(repoRoot);
	return indexQuery(index, { query, archetype, limit });
}

/** JSON schema for one sample entry. */
const SAMPLE_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		id: { type: 'string', required: true },
		title: { type: 'string', required: true },
		readme: { type: 'string', required: true },
		archetype: { type: 'string' },
		entry: { type: 'string' },
		test: { type: 'string' },
		capabilities: { type: 'array', items: { type: 'string' } },
		keywords: { type: 'array', items: { type: 'string' } }
	}
};

/** JSON schema for one matched knowledge entry. */
const MATCH_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		id: { type: 'string', required: true },
		title: { type: 'string', required: true },
		area: { type: 'string', required: true },
		stage: { type: 'string', required: true },
		rootCause: { type: 'string', required: true },
		matchedSymptoms: { type: 'array', items: { type: 'string' } },
		commands: { type: 'array', items: { type: 'string' } },
		references: { type: 'array', items: { type: 'string' } }
	}
};

/** JSON schema for one plan step. */
const STEP_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		entryId: { type: 'string', required: true },
		entryTitle: { type: 'string', required: true },
		probe: { type: 'string', required: true },
		probeState: { type: 'string', required: true },
		do: { type: 'string', required: true },
		stopWhen: { type: 'string', required: true }
	}
};

/** Register the tools and, when present, the panel routes. */
export function apply(ctx, config) {
	ctx.tools.register(
		defineTool({
			name: 'ascend_probe',
			description:
				'Probe an Ascend 310B board and report the facts the board itself states: SoC/chip line, compute tier, ' +
				'health, temperature, memory, OS and kernel, CANN Toolkit version, NPU driver version, firmware version, ' +
				'Python interpreter with ACL import result, ATC availability, device nodes and kernel modules. ' +
				'It runs a read-only script over SSH or locally, or parses a saved snapshot/transcript passed as rawText. ' +
				'Use it before any debugging or version claim: the plugin never guesses a board version. ' +
				'A missing value stays missing instead of becoming a assumed version.',
			parameters: {
				target: {
					type: 'string',
					description:
						'Configured target id or a configured `user@host`, or `local`. Defaults to config.defaultTarget, then ' +
						'DSH_ASCEND310B_TARGET. With two boards configured and no default, the call fails and lists them.'
				},
				rawText: {
					type: 'string',
					description:
						'Probe output or saved board snapshot to parse instead of running anything. Use this when the ' +
						'board is unreachable or the snapshot was collected earlier.'
				},
				timeoutMs: {
					type: 'integer',
					description: 'Probe timeout in milliseconds. Defaults to 60000.'
				}
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						ok: { type: 'boolean', required: true },
						target: {
							type: 'object',
							additionalProperties: true,
							required: true,
							properties: {
								id: { type: 'string', required: true },
								label: { type: 'string', required: true },
								transport: { type: 'string', required: true },
								host: { type: 'string', required: true },
								user: { type: 'string', required: true }
							}
						},
						source: { type: 'string', required: true },
						board: {
							type: 'object',
							additionalProperties: true,
							properties: {
								soc: { type: 'string' },
								chip: { type: 'string' },
								tier: { type: 'string' },
								health: { type: 'string' },
								temperatureC: { type: 'number' },
								memoryUsedMb: { type: 'integer' },
								memoryTotalMb: { type: 'integer' }
							}
						},
						facts: { type: 'object', additionalProperties: true, required: true },
						missingCore: { type: 'array', items: { type: 'string' } },
						pairing: { type: 'object', additionalProperties: true, required: true },
						scope: { type: 'object', additionalProperties: true, required: true },
						rawExcerpt: { type: 'string' }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			async execute(args) {
				const result = await collectEnvironment({
					config,
					targetSelector: args.target,
					rawText: args.rawText,
					timeoutMs: Number.isInteger(args.timeoutMs) ? args.timeoutMs : 60000
				});
				const report = environmentReport(result);
				const text = [
					`目标：${result.target.label}（${result.target.transport}${result.target.host ? ` ${result.target.user ? `${result.target.user}@` : ''}${result.target.host}` : ''}）`,
					`传输结论：${result.transport.reason}`,
					report.scope?.applicable === true ? '' : `适用范围：${report.scope?.message ?? '未识别到 SoC。'}`,
					'',
					report.text,
					'',
					'原始输出节选：',
					'```text',
					excerpt(result.rawText),
					'```'
				].filter((line, index, all) => !(line === '' && index > 0 && all[index - 1] === '')).join('\n');
				return {
					text,
					ok: result.transport.ok !== false && report.board !== null,
					target: {
						id: result.target.id,
						label: result.target.label,
						transport: result.target.transport,
						host: result.target.host ?? '',
						user: result.target.user ?? ''
					},
					source: result.source,
					board: report.board ?? undefined,
					facts: serializeEnvironment(result.environment).facts,
					missingCore: result.environment.missingCore ?? [],
					pairing: {
						verdict: report.pairing.verdict,
						evidence: report.pairing.evidence,
						reasons: report.pairing.reasons
					},
					scope: report.scope ?? {
						applicable: 'unknown',
						message: '未识别到 SoC。',
						supportedSocs: SUPPORTED_SOCS
					},
					rawExcerpt: excerpt(result.rawText)
				};
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '昇腾310B环境探测',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_check',
			description:
				'Judge an Ascend 310B environment against the repository\'s documented version pairing and show which ' +
				'sample programs and documents are relevant. Pass `target` to probe a board first, or `rawText` to ' +
				'parse a saved snapshot. The verdict distinguishes `observed-match` (this repository recorded that ' +
				'exact combination passing), `documented-pairing`, `documented-unpaired`, `undocumented-cann` and ' +
				'`incomplete`; a version outside the recorded set is `untested`, never `unsupported`. ' +
				'Also returns the firmware -> driver -> CANN upgrade order and the five acceptance gates. ' +
				'The scope is Ascend 310B only: when the probed SoC is another chip, the result is marked ' +
				'out of scope and the 310B pairing/upgrade advice is withheld.',
			parameters: {
				target: { type: 'string', description: 'Configured target id, configured `user@host`, or `local`.' },
				rawText: { type: 'string', description: 'Probe output or saved snapshot to parse instead of probing.' },
				repoRoot: { type: 'string', description: 'Textbook repository root. Defaults to config.repoRoot.' }
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						verdict: { type: 'string', required: true },
						evidence: { type: 'string', required: true },
						reasons: { type: 'array', required: true, items: { type: 'string' } },
						nextActions: { type: 'array', required: true, items: { type: 'string' } },
						upgradeOrder: { type: 'array', required: true, items: { type: 'string' } },
						scope: { type: 'object', additionalProperties: true },
						releaseLine: {
							type: 'object',
							additionalProperties: true,
							properties: {
								line: { type: 'string' },
								documented: { type: 'string' },
								note: { type: 'string' }
							}
						}
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			async execute(args) {
				const repoRoot = resolveRepoRoot(config, args.repoRoot);
				const result = await collectEnvironment({
					config,
					targetSelector: args.target,
					rawText: args.rawText,
					timeoutMs: 60000
				});
				const report = environmentReport(result);
				const index = readSampleIndex(repoRoot);
				const soc = result.environment.facts?.soc?.value ?? null;
				const relevant = indexQuery(index, { query: [soc, 'atc', 'acl'].filter(Boolean).join(' '), limit: 8 });
				const docs = [
					'src/appendix/appendix7.md',
					'samples/chapter5/docs/01-board-validation-record-20261016.md',
					'samples/case8/docs/05-known-issues-and-version-compatibility.md'
				].map((relative) => ({ path: relative, exists: existsSync(join(repoRoot, relative)) }));
				const outOfScope = report.scope?.applicable === false;
				const sampleLines = outOfScope
					? ['- 设备不在昇腾 310B 范围内，本插件的样例索引不适用于该设备。']
					: relevant.length === 0
						? ['- 没有匹配到样例；先用 ascend_sample_index 查看索引。']
						: relevant.map((sample) => `- ${sample.id}：${sample.title}（${sample.readme}）`);
				const text = outOfScope
					? [
							report.text,
							'',
							'### 不适用范围',
							`- ${report.scope.message}`,
							'- 未应用 310B 的版本配套表、升级顺序和样例索引；请使用该芯片自己的工具链与文档。'
						].join('\n')
					: [
							report.text,
							'',
							'### 相关 samples 程序',
							...sampleLines,
							'',
							'### 版本文档',
							...docs.map((doc) => `- ${doc.exists ? '' : '[仓库中未找到] '}${doc.path}`),
							'',
							'### 升级顺序（覆盖升级）',
							...UPGRADE_ORDER.map((step, position) => `${position + 1}. ${step}`)
						].join('\n');
				return {
					text,
					verdict: outOfScope ? 'out-of-scope' : report.pairing.verdict,
					evidence: outOfScope ? 'documented' : report.pairing.evidence,
					reasons: outOfScope
						? [report.scope.message, '本插件只覆盖昇腾 310B1/310B4；其他芯片的调试程序不在此范围内。']
						: report.pairing.reasons,
					nextActions: outOfScope
						? ['改用检测到的芯片对应的工具链、驱动文档和样例。', '不要在本插件中套用 310B 的 CANN/驱动版本配套。']
						: report.pairing.nextActions,
					upgradeOrder: outOfScope ? [] : [...UPGRADE_ORDER],
					scope: report.scope ?? null,
					releaseLine:
						outOfScope || report.pairing.releaseLine === null || report.pairing.releaseLine === undefined
							? undefined
							: {
									line: report.pairing.releaseLine.line,
									documented: report.pairing.releaseLine.documented,
									note: report.pairing.releaseLine.note
								}
				};
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '昇腾310B版本配套检查',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_debug_plan',
			description:
				'Build a layer-ordered debugging plan for an Ascend 310B program from the failure text or the area ' +
				'being worked on. Pass `target` or `rawText` so version-scoped notes can be applied to the real ' +
				'environment. Each step names the probe that decides it and a stopping condition, so a failure is ' +
				'reported at the layer where it happened instead of being generalized to the whole board. ' +
				'Matched procedures cover ACL import failures, ATC conversion, OM loading, missing devices, the ' +
				'known Alarm health state, mixed MindSpore/Lite symbols, V4L2 cameras, services whose HTTP works but ' +
				'inference does not, large-model timeouts, CANN sub-package drift, memory growth, quantization ' +
				'accuracy and non-reproducible performance.',
			parameters: {
				symptom: {
					type: 'string',
					description: 'Error text, log line, command that failed, or the goal you are working on.'
				},
				area: {
					type: 'string',
					enum: Object.keys(AREAS),
					description: 'Optional function area filter when there is no error text yet.'
				},
				target: { type: 'string', description: 'Configured target id, configured `user@host`, or `local`.' },
				rawText: { type: 'string', description: 'Probe output or saved snapshot to parse instead of probing.' },
				repoRoot: { type: 'string', description: 'Textbook repository root. Defaults to config.repoRoot.' },
				maxEntries: { type: 'integer', description: 'Maximum matched procedures. Defaults to 3.' }
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						applicable: { type: 'string', required: true },
						scope: { type: 'object', additionalProperties: true },
						gate: { type: 'string', required: true },
						matched: { type: 'array', required: true, items: MATCH_SCHEMA },
						steps: { type: 'array', required: true, items: STEP_SCHEMA },
						probes: {
							type: 'array',
							required: true,
							items: {
								type: 'object',
								additionalProperties: false,
								properties: {
									id: { type: 'string', required: true },
									question: { type: 'string', required: true },
									state: { type: 'string', required: true },
									commands: { type: 'array', items: { type: 'string' } },
									passesWhen: { type: 'string', required: true },
									failsWhen: { type: 'string', required: true },
									note: { type: 'string', required: true }
								}
							}
						},
						facts: { type: 'object', additionalProperties: true },
						samples: { type: 'array', required: true, items: SAMPLE_SCHEMA }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			async execute(args) {
				// The plan is still useful without the textbook checkout: the sample
				// hits are a convenience, not a prerequisite for the diagnostic order.
				let index = null;
				let samplesNote = null;
				try {
					const repoRoot = resolveRepoRoot(config, args.repoRoot);
					index = readSampleIndex(repoRoot);
				} catch (error) {
					samplesNote = `未索引教材样例：${error?.message ?? error}`;
				}
				const needsProbe = typeof args.target === 'string' || typeof args.rawText === 'string';
				let facts = {};
				let scope = null;
				if (needsProbe) {
					const result = await collectEnvironment({
						config,
						targetSelector: args.target,
						rawText: args.rawText,
						timeoutMs: 60000
					});
					facts = serializeEnvironment(result.environment).facts;
					scope = result.environment.scope ?? null;
				}
				const queryText = [args.symptom, args.area].filter(Boolean).join(' ');
				const samples = index === null ? [] : indexQuery(index, { query: queryText, limit: 6 });
				const plan = buildPlan({
					symptom: args.symptom,
					area: args.area,
					facts,
					samples,
					maxEntries: args.maxEntries
				});
				const outOfScope = scope?.applicable === false;
				const scopeNote = outOfScope
					? `## 不适用范围\n\n${scope.message}\n\n下面的分层计划属于教材的昇腾 310B 调试程序，在 ${scope.soc} 上不应照做；请改用该芯片自己的工具链与文档。\n`
					: '';
				const body = outOfScope ? scopeNote.trimEnd() : renderPlan(plan);
				const text = samplesNote === null ? body : `${body}\n\n${samplesNote}`;
				return {
					text,
					applicable: scope?.applicable ?? 'unknown',
					scope,
					gate: plan.gate,
					matched: outOfScope ? [] : plan.matched.map((entry) => ({
						id: entry.id,
						title: entry.title,
						area: entry.area,
						stage: entry.stage,
						rootCause: entry.rootCause,
						matchedSymptoms: entry.matchedSymptoms,
						commands: entry.commands,
						references: entry.references
					})),
					steps: outOfScope ? [] : plan.steps,
					probes: outOfScope ? [] : plan.probes,
					facts,
					samples: outOfScope ? [] : samples
				};
			},
			presentCall: (args) => ({
				card: 'generic',
				title: `${PLUGIN_DISPLAY_NAME}计划`,
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_sample_index',
			description:
				'Search the repository\'s samples for the program that matches what you are debugging. Each hit ' +
				'carries the sample id, its README, the primary run command, the test command, the debugging ' +
				'archetype (ATC conversion, PyACL inference, NPU service, camera/V4L2, custom operator, ' +
				'quantization, performance, framework migration), and the capabilities it needs (ascend, om, ' +
				'camera, network, conda). Use it to point a debugging session at the same code path the book runs ' +
				'instead of inventing an example.',
			parameters: {
				query: { type: 'string', description: 'Free-text query: symptom, model, case name, or command.' },
				archetype: {
					type: 'string',
					description: 'Exact archetype filter, e.g. `atc-conversion`, `pyacl-inference`, `npu-service`.'
				},
				limit: { type: 'integer', description: 'Maximum entries. Defaults to 12.' },
				repoRoot: { type: 'string', description: 'Textbook repository root. Defaults to config.repoRoot.' }
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						matched: { type: 'array', required: true, items: SAMPLE_SCHEMA },
						total: { type: 'integer', required: true },
						archetypes: { type: 'array', required: true, items: { type: 'string' } }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				// Without a configured checkout the tool returns a refusal in its
				// normal shape; throwing here would surface as a harness error rather
				// than as something the caller can read and act on.
				let repoRoot = null;
				try {
					repoRoot = resolveRepoRoot(config, args.repoRoot);
				} catch (error) {
					return Promise.resolve({
						text: String(error?.message ?? error),
						matched: [],
						total: 0,
						archetypes: []
					});
				}
				const index = readSampleIndex(repoRoot);
				const matched = indexQuery(index, {
					query: args.query,
					archetype: args.archetype,
					limit: Number.isInteger(args.limit) && args.limit > 0 ? args.limit : 12
				});
				const text = [
					`共 ${index.entries.length} 个样例，匹配 ${matched.length} 个。`,
					...(matched.length === 0
						? ['没有匹配到样例：换个关键词，或省略 query 查看全部。']
						: matched.map((sample) =>
								[
									`- ${sample.id}：${sample.title}`,
									`  - README：${sample.readme}`,
									sample.entry ? `  - 运行：${sample.entry}` : null,
									sample.test ? `  - 测试：${sample.test}` : null,
									sample.archetype ? `  - 原型：${sample.archetype}` : null,
									(sample.capabilities ?? []).length > 0 ? `  - 依赖：${sample.capabilities.join('、')}` : null
								]
									.filter(Boolean)
									.join('\n')
							))
				].join('\n');
				return Promise.resolve({
					text,
					matched,
					total: index.entries.length,
					archetypes: index.archetypes ?? []
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: 'samples 程序索引',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_board_exec',
			description:
				'Run one read-only diagnostic command on the board over SSH (default) or locally. This is for ' +
				'inspection the probe does not cover, for example `npu-smi info -t proc-mem -i 0 -c 0`, ' +
				'`v4l2-ctl --list-devices`, `dmesg -T`, or `cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg`. ' +
				'The command must match a read-only allowlist: pipes, chained commands, redirection, command substitution, ' +
				'package installs, service restarts, device writes and repository mutations are rejected with a ' +
				'reason. Pass `run: false` to get the exact SSH invocation without executing it. This tool never ' +
				'changes board state; for a state change, show the user the command and ask them to run it. ' +
				'It is intended for the configured Ascend 310B targets; a device whose probe reports another ' +
				'chip is outside this plugin\'s scope.',
			parameters: {
				command: { type: 'string', description: 'Single read-only command line to execute.' },
				target: { type: 'string', description: 'Configured target id, configured `user@host`, or `local`.' },
				run: { type: 'boolean', description: 'Set false to validate and preview without executing. Defaults to true.' },
				timeoutMs: { type: 'integer', description: 'Execution timeout in milliseconds. Defaults to 30000.' }
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						allowed: { type: 'boolean', required: true },
						executed: { type: 'boolean', required: true },
						command: { type: 'string', required: true },
						target: { type: 'string', required: true },
						invocation: { type: 'string', required: true },
						reason: { type: 'string', required: true },
						exitCode: { type: 'integer', required: true },
						stdout: { type: 'string' },
						stderr: { type: 'string' }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			async execute(args) {
				const verdict = validateReadOnlyCommand(args.command);
				const target = resolveTarget(config, args.target);
				const invocation =
					target.transport === 'local'
						? `bash -lc ${JSON.stringify(args.command ?? '')}`
						: `ssh ${target.user ? `${target.user}@` : ''}${target.host} ${JSON.stringify(args.command ?? '')}`;
				if (!verdict.ok) {
					return {
						text: [`命令被拒绝：${verdict.reason}`, `目标：${target.label}`, `命令：${args.command ?? ''}`].join('\n'),
						allowed: false,
						executed: false,
						command: String(args.command ?? ''),
						target: target.label,
						invocation,
						reason: verdict.reason,
						exitCode: -1
					};
				}
				if (args.run === false) {
					return {
						text: [
							`校验通过：${verdict.reason}`,
							`目标：${target.label}`,
							`将要执行的命令：${args.command}`,
							`等价调用：${invocation}`,
							'未执行（run: false）。'
						].join('\n'),
						allowed: true,
						executed: false,
						command: args.command,
						target: target.label,
						invocation,
						reason: verdict.reason,
						exitCode: 0
					};
				}
				const script = args.command;
				const timeoutMs = Number.isInteger(args.timeoutMs) ? args.timeoutMs : 30000;
				const result =
					target.transport === 'local'
						? await runLocalScript({ script, cwd: target.workdir ?? undefined, timeoutMs })
						: await runRemoteScript({ script, target, timeoutMs });
				const stdoutText = excerpt(result.stdout ?? '');
				const stderrText = excerpt(result.stderr ?? '');
				return {
					text: [
						`目标：${target.label}（${result.display}）`,
						`命令：${args.command}`,
						`退出码：${result.code}${result.timedOut === true ? '（超时）' : ''}`,
						result.reason,
						'',
						'stdout:',
						'```text',
						stdoutText,
						'```',
						stderrText === '' ? '' : 'stderr:\n```text\n' + stderrText + '\n```'
					].join('\n'),
					allowed: true,
					executed: true,
					command: args.command,
					target: target.label,
					invocation,
					reason: result.reason,
					exitCode: Number.isInteger(result.code) ? result.code : 1,
					stdout: stdoutText,
					stderr: stderrText
				};
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '板端只读命令',
				kind: 'other',
				rawInput: args
			})
		})
	);

	// The browser panel reads the same report over HTTP. A headless composition
	// keeps the tools and simply skips these two routes.
	ctx.inject(['webServer'], (scope) => {
		const json = (response, status, value) => {
			const body = JSON.stringify(value);
			response.writeHead(status, {
				'content-type': 'application/json; charset=utf-8',
				'cache-control': 'no-store',
				'content-length': Buffer.byteLength(body)
			});
			response.end(body);
		};

		const reportHandler = async (request, response) => {
			try {
				const url = new URL(request.url ?? REPORT_PATH, 'http://localhost');
				const targetId = url.searchParams.get('target') ?? undefined;
				const fresh = url.searchParams.get('fresh') === '1';
				const samples = sampleIndexFor(config);
				const repoRoot = samples.repoRoot;
				const target = resolveTarget(config, targetId);
				const key = target.id;
				const cached = reportCache.get(key);
				if (!fresh && cached !== undefined && Date.now() - cached.at < 120000) {
					json(response, 200, cached.value);
					return;
				}
				const index = samples.index;
				const value = {
					ok: true,
					repoRoot,
					repoRootNote: samples.note,
					generatedAt: new Date().toISOString(),
					probedAt: null,
					source: 'not-probed',
					target,
					targets: publicTargets(config),
					board: null,
					facts: {},
					scope: {
						applicable: 'unknown',
						family: null,
						soc: null,
						supportedSocs: [...SUPPORTED_SOCS],
						message: '尚未探测，无法确认是否为昇腾 310B。'
					},
					pairing: {
						verdict: 'incomplete',
						evidence: 'untested',
						reasons: ['面板尚未探测。点击「运行探测」后才会读取板端版本。'],
						nextActions: []
					},
					missingCore: ['soc', 'cannVersion', 'driverVersion'],
					knowledge: ENTRIES.map((entry) => ({
						id: entry.id,
						title: entry.title,
						area: entry.area,
						stage: entry.stage,
						symptoms: entry.symptoms
					})),
					areas: Object.values(AREAS),
					samples: index.entries.map((sample) => ({
						id: sample.id,
						title: sample.title,
						readme: sample.readme,
						archetype: sample.archetype,
						entry: sample.entry,
						capabilities: sample.capabilities
					})),
					versionLines: CANN_RELEASE_LINES.map((line) => ({ line: line.line, note: line.note ?? '' })),
					observedStacks: OBSERVED_STACKS.map((stack) => ({
						id: stack.id,
						board: stack.board,
						soc: stack.soc,
						tier: stack.tier,
						driver: stack.driver,
						firmware: stack.firmware,
						cann: stack.cann,
						status: stack.status
					}))
				};
				if (cached?.value !== undefined) {
					// Keep the last successful probe visible until a new one runs.
					Object.assign(value, {
						probedAt: cached.value.probedAt,
						source: cached.value.source,
						target: cached.value.target,
						board: cached.value.board,
						facts: cached.value.facts,
						scope: cached.value.scope,
						pairing: cached.value.pairing,
						missingCore: cached.value.missingCore
					});
				}
				reportCache.set(key, { at: Date.now(), value });
				json(response, 200, value);
			} catch (error) {
				json(response, 500, { ok: false, error: String(error?.message ?? error) });
			}
		};

		const probeHandler = async (request, response) => {
			try {
				let body = '';
				for await (const chunk of request) {
					body += chunk;
					if (body.length > 1_000_000) {
						json(response, 413, { ok: false, error: '探测请求体超过 1 MB。' });
						return;
					}
				}
				const payload = body.trim() === '' ? {} : JSON.parse(body);
				const targetId = typeof payload.target === 'string' ? payload.target : undefined;
				const rawText = typeof payload.rawText === 'string' ? payload.rawText : undefined;
				const result = await collectEnvironment({ config, targetSelector: targetId, rawText, timeoutMs: 60000 });
				const report = environmentReport(result);
				const samples = sampleIndexFor(config);
				const repoRoot = samples.repoRoot;
				const index = samples.index;
				const value = {
					ok: result.transport.ok !== false && report.board !== null,
					repoRoot,
					repoRootNote: samples.note,
					generatedAt: new Date().toISOString(),
					probedAt: new Date().toISOString(),
					source: result.source,
					target: result.target,
					targets: publicTargets(config),
					board: report.board,
					facts: serializeEnvironment(result.environment).facts,
					scope: report.scope,
					pairing: report.pairing,
					missingCore: result.environment.missingCore ?? [],
					transport: { ok: result.transport.ok, reason: result.transport.reason, display: result.transport.display },
					rawExcerpt: excerpt(result.rawText),
					knowledge: ENTRIES.map((entry) => ({
						id: entry.id,
						title: entry.title,
						area: entry.area,
						stage: entry.stage,
						symptoms: entry.symptoms
					})),
					areas: Object.values(AREAS),
					samples: index.entries.map((sample) => ({
						id: sample.id,
						title: sample.title,
						readme: sample.readme,
						archetype: sample.archetype,
						entry: sample.entry,
						capabilities: sample.capabilities
					})),
					versionLines: CANN_RELEASE_LINES.map((line) => ({ line: line.line, note: line.note ?? '' })),
					observedStacks: OBSERVED_STACKS.map((stack) => ({
						id: stack.id,
						board: stack.board,
						soc: stack.soc,
						tier: stack.tier,
						driver: stack.driver,
						firmware: stack.firmware,
						cann: stack.cann,
						status: stack.status
					}))
				};
				reportCache.set(result.target.id, { at: Date.now(), value });
				json(response, 200, value);
			} catch (error) {
				json(response, 500, { ok: false, error: String(error?.message ?? error) });
			}
		};

		scope.effect(
			() => scope.webServer.register({ kind: 'exact', path: REPORT_PATH, handler: reportHandler }),
			'ascend310b-debug: report route'
		);
		scope.effect(
			() => scope.webServer.register({ kind: 'exact', path: PROBE_PATH, handler: probeHandler }),
			'ascend310b-debug: probe route'
		);
	});
}

/** Exported for tests and for the panel's own contract checks. */
export const internals = {
	resolveRepoRoot,
	resolveTarget,
	readSampleIndex,
	resolveInside,
	isSafeHost,
	require
};
