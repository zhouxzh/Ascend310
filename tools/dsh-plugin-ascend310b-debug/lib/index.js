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
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineTool } from '@deepseek-ai/dsh-tools';

import { assembleReport, indexQuery, indexSampleIndex, loadSampleIndex } from './report.js';
import {
	assessVersionPairing,
	ACCEPTANCE_GATES,
	CANN_RELEASE_LINES,
	OBSERVED_STACKS,
	PLUGIN_DISPLAY_NAME,
	SUPPORTED_SOCS,
	UPGRADE_ORDER
} from './compat.js';
import { buildPlan, renderPlan } from './plan.js';
import { AREAS, ENTRIES } from './knowledge.js';
import { buildProbeScript, parseProbeOutput } from './probe.js';
import { inventoryPath, inventoryTargets, observeBoard, readInventory, saveInventory, systemFromFacts } from './inventory.js';
import { captureScreenshot, findBrowser } from './capture.js';
import { discoverNetwork, registerDiscoveryTool } from './discovery.js';
import { isSafeHost, runLocalScript, runLocalShell, runRemoteScript, runRemoteShell, shellQuote, validateReadOnlyCommand, validateTaskCommand } from './shell.js';

const require = createRequire(import.meta.url);

/** Cordis plugin name used by loader diagnostics. */
export const name = 'dsh-plugin-ascend310b-debug';

/** Host services this plugin needs before it may mount. */
export const inject = ['tools'];

/** Read-only panel route: current report, optionally freshly probed. */
const REPORT_PATH = '/ascend310b-debug/report.json';

/** Route that runs the probe against a configured target. */
const PROBE_PATH = '/ascend310b-debug/probe';

/** Route that discovers boards and refreshes the local inventory. */
const DISCOVER_PATH = '/ascend310b-debug/discover';

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

/** Normalize a host for target/inventory matching. */
function normalizedHost(value) {
	return String(value ?? '').trim().toLowerCase().replace(/\.$/, '');
}

/** Merge discovered metadata into one configured target. */
function attachDiscovered(target, entry) {
	target.mac = target.mac ?? entry.mac ?? null;
	target.discoveredId = target.discoveredId ?? entry.id ?? null;
	target.observedIp = target.observedIp ?? entry.host ?? null;
	target.hostname = target.hostname ?? entry.hostname ?? null;
	return target;
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
			hostname: null,
			mac: null,
			discoveredId: null,
			observedIp: null,
			user: typeof entry.user === 'string' && entry.user.trim() !== '' ? entry.user.trim() : null,
			port: Number.isInteger(entry.port) ? entry.port : null,
			identityFile: typeof entry.identityFile === 'string' ? entry.identityFile : null,
			workdir: typeof entry.workdir === 'string' ? entry.workdir : null,
			note: typeof entry.note === 'string' ? entry.note : null
		});
	}
	const discovered = inventoryTargets(
		readInventory(inventoryPath(config)),
		typeof config?.discovery?.user === 'string' ? config.discovery.user.trim() : null
	);
	const merged = new Set();
	for (const entry of discovered) {
		if (entry.configuredId && table.has(entry.configuredId)) {
			attachDiscovered(table.get(entry.configuredId), entry);
			merged.add(entry.configuredId);
			continue;
		}
		const hostNames = [entry.host, entry.hostname, entry.hostname ? `${entry.hostname}.local` : null]
			.map(normalizedHost)
			.filter(Boolean);
		const target = [...table.values()].find(
			(candidate) => candidate.transport === 'ssh' && !merged.has(candidate.id) && hostNames.includes(normalizedHost(candidate.host))
		);
		if (target !== undefined) {
			attachDiscovered(target, entry);
			merged.add(target.id);
		} else if (!table.has(entry.id)) {
			table.set(entry.id, entry);
		}
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
		hostname: target.hostname ?? null,
		mac: target.mac ?? null,
		discoveredId: target.discoveredId ?? null,
		observedIp: target.observedIp ?? null,
		user: target.user,
		port: target.port,
		isDefault: target.id === defaultId,
		note: target.note
	}));
}

/** Public inventory summaries for the Assets tab (never expose SSH secrets). */
function publicInventory(config) {
	const inventory = readInventory(inventoryPath(config));
	return Object.values(inventory.boards ?? {})
		.filter((board) => board !== null && typeof board === 'object')
		.map((board) => {
			const latestChange = Array.isArray(board.changes) && board.changes.length > 0
				? board.changes[board.changes.length - 1]
				: null;
			return {
				id: board.id,
				mac: board.mac ?? null,
				hostname: board.hostname ?? null,
				lastIp: board.lastIp ?? null,
				model: board.system?.boardModel ?? null,
				soc: board.system?.soc ?? null,
				tier: board.system?.tier ?? null,
				driver: board.system?.driverVersion ?? null,
				cann: board.system?.cannVersion ?? null,
				firmware: board.system?.firmwareVersion ?? null,
				firstSeen: board.firstSeen ?? null,
				lastSeen: board.lastSeen ?? null,
				lastChangeAt: board.lastChangeAt ?? null,
				configuredId: board.configuredId ?? null,
				changesCount: Array.isArray(board.changes) ? board.changes.length : 0,
				latestChange: latestChange === null
					? null
					: {
							at: latestChange.at,
							fromIp: latestChange.fromIp ?? null,
							toIp: latestChange.toIp ?? null,
							fields: (latestChange.fields ?? []).map((field) => ({
								name: field.name,
								from: field.from ?? null,
								to: field.to ?? null
							}))
						}
			};
		})
		.sort((left, right) => String(right.lastSeen ?? '').localeCompare(String(left.lastSeen ?? '')));
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

/** Read one JSON file, returning null instead of throwing. */
function readJson(path) {
	const text = readText(path);
	if (text === null) return null;
	try {
		return JSON.parse(text);
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
 * Load the sample index.
 *
 * A configured checkout is authoritative: scanning its `samples/` tree keeps
 * `ascend_sample_index` and `ascend_debug_plan` aligned with the code the user
 * is actually reading. The packaged JSON remains the fallback for a copied
 * installation without a checkout, so the plugin still works after a file-copy
 * install.
 */
function readSampleIndex(repoRoot) {
	const moduleRoot = dirname(fileURLToPath(import.meta.url));
	if (typeof repoRoot === 'string' && repoRoot.trim() !== '') {
		const samplesRoot = join(resolve(repoRoot), 'samples');
		if (existsSync(samplesRoot)) {
			const live = indexSampleIndex({
				root: samplesRoot,
				caseIndex: readJson(join(samplesRoot, 'case-index.json'))
			});
			if (live.entries.length > 0) return live;
		}
	}
	const candidates = [resolve(join(moduleRoot, '..', 'data', 'sample-index.json'))];
	for (const candidate of candidates) {
		if (candidate === null) continue;
		const parsed = readJson(candidate);
		if (parsed !== null) {
			try {
				return loadSampleIndex(parsed);
			} catch {
				// A malformed index should not take the tool down; fall through to a scan.
			}
		}
	}
	return indexSampleIndex({ root: join(repoRoot ?? process.cwd(), 'samples') });
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

/** Resolve the default remote workspace for a target. */
function defaultTaskCwd(target) {
	if (typeof target?.workdir === 'string' && target.workdir.trim() !== '') return target.workdir.trim();
	if (target?.transport === 'local') return process.cwd();
	if (target?.user) return `/home/${target.user}`;
	return '/tmp';
}

/** Check whether a POSIX path is inside an allowed remote root. */
function isRemotePathInside(root, candidate) {
	const normalizedRoot = root.replace(/\/+$/, '');
	const normalizedCandidate = candidate.replace(/\/+$/, '');
	return normalizedCandidate === normalizedRoot || normalizedCandidate.startsWith(`${normalizedRoot}/`);
}

/** Resolve a task working directory without allowing escape outside the user's workspace. */
function resolveTaskCwd(target, requested) {
	const defaultCwd = defaultTaskCwd(target);
	if (typeof requested !== 'string' || requested.trim() === '') return defaultCwd;
	const candidate = requested.trim();
	if (target?.transport === 'local') return candidate;
	if (!candidate.startsWith('/') || candidate.split('/').includes('..')) {
		throw new Error('任务 cwd 必须是开发板上的绝对 POSIX 路径，且不能包含 ..。');
	}
	const base = defaultTaskCwd(target);
	const allowed =
		isRemotePathInside(base, candidate) ||
		isRemotePathInside('/tmp', candidate) ||
		(target?.user ? isRemotePathInside(`/home/${target.user}`, candidate) : false);
	if (!allowed) throw new Error(`任务 cwd 必须位于 ${base}、/home/${target?.user ?? '<user>'} 或 /tmp 下。`);
	return candidate;
}

/** Join a remote POSIX path with a relative destination. */
function remotePath(cwd, value) {
	if (typeof value !== 'string' || value.trim() === '') throw new Error('下载目标路径为空。');
	const candidate = value.trim();
	if (candidate.split('/').includes('..')) throw new Error('下载目标不能包含 ..。');
	return candidate.startsWith('/') ? candidate : `${cwd.replace(/\/+$/, '')}/${candidate}`;
}

/** Resolve a host-side screenshot path under the configured artifact root. */
function resolveCapturePath(config, output) {
	if (typeof output !== 'string' || output.trim() === '') throw new Error('截图输出路径为空。');
	const root = resolve(config?.artifactRoot ?? process.cwd());
	const candidate = resolve(root, output.trim());
	const rel = relative(root, candidate);
	if (rel === '' || rel.startsWith('..') || isAbsolute(rel)) throw new Error('截图输出必须位于 artifactRoot 内。');
	if (!candidate.toLowerCase().endsWith('.png')) throw new Error('截图输出必须是 .png 文件。');
	return candidate;
}

/** Encode one URL path segment, keeping it usable by wget/curl. */
function encodePathPart(value) {
	return encodeURIComponent(value).replaceAll('%2F', '/');
}

/** Normalize a model download request and return the actual URL and command. */
function normalizeDownload({ source, url, repoId, filename, revision = 'main', githubProxy }) {
	if (source === 'hf') {
		if (typeof repoId !== 'string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repoId)) {
			throw new Error('HF 下载需要合法的 repoId，例如 `org/model`。');
		}
		if (typeof filename !== 'string' || filename.trim() === '') throw new Error('HF 下载需要 filename。');
		if (typeof revision !== 'string' || !/^[A-Za-z0-9_.\/-]+$/.test(revision) || revision.split('/').includes('..')) {
			throw new Error('HF revision 不合法。');
		}
		const repo = repoId.split('/').map(encodePathPart).join('/');
		const file = filename.split('/').map(encodePathPart).join('/');
		return {
			url: `https://hf-mirror.com/${repo}/resolve/${encodePathPart(revision)}/${file}`,
			commandUrl: null
		};
	}
	if (typeof url !== 'string' || url.trim() === '') throw new Error(`${source} 下载需要 url。`);
	let parsed;
	try {
		parsed = new URL(url.trim());
	} catch {
		throw new Error('下载 URL 不合法。');
	}
	if (!/^https?:$/.test(parsed.protocol)) throw new Error('下载 URL 必须是 HTTP(S)。');
	if (source === 'huawei') {
		if (!/(^|\.)myhuaweicloud\.com$|(^|\.)huaweicloud\.com$/i.test(parsed.hostname)) {
			throw new Error('华为云下载只接受 huaweicloud.com 或 myhuaweicloud.com 域名。');
		}
		return { url: parsed.toString(), commandUrl: null };
	}
	if (source === 'github') {
		if (!/(^|\.)github\.com$|(^|\.)githubusercontent\.com$/i.test(parsed.hostname)) {
			throw new Error('GitHub 下载只接受 github.com 或 githubusercontent.com URL。');
		}
		const prefix = typeof githubProxy === 'string' && githubProxy.trim() !== ''
			? githubProxy.trim()
			: 'https://gh-proxy.com/';
		if (!/^https?:\/\//i.test(prefix)) throw new Error('GitHub 网页代理必须以 http:// 或 https:// 开头。');
		return { url: `${prefix.replace(/\/?$/, '/')}${parsed.toString()}`, commandUrl: null };
	}
	return { url: parsed.toString(), commandUrl: null };
}

/** Build a wget/curl command for a normalized download. */
function downloadCommand(url, destination) {
	const directory = destination.slice(0, destination.lastIndexOf('/')) || '/tmp';
	const quotedDirectory = shellQuote(directory);
	const quotedDestination = shellQuote(destination);
	const quotedUrl = shellQuote(url);
	return `mkdir -p ${quotedDirectory} && if command -v wget >/dev/null 2>&1; then wget -q -O ${quotedDestination} ${quotedUrl}; else curl -fL --retry 3 -o ${quotedDestination} ${quotedUrl}; fi`;
}

/**
 * Add the board's common tutorial environment without hiding the user's
 * command. `base` makes `python`, `atc` and conda activation available; `none`
 * runs the command in the bare SSH shell.
 */
function prepareTaskCommand(command, environment) {
	if (environment !== 'base') return command;
	return [
		'set +u',
		'export PATH=/usr/local/miniconda3/bin:$PATH',
		'if [ -r /usr/local/Ascend/ascend-toolkit/set_env.sh ]; then . /usr/local/Ascend/ascend-toolkit/set_env.sh >/dev/null 2>&1 || true; fi',
		'if [ -r /usr/local/miniconda3/etc/profile.d/conda.sh ]; then . /usr/local/miniconda3/etc/profile.d/conda.sh >/dev/null 2>&1 || true; fi',
		command
	].join('; ');
}

/**
 * Run an explicitly approved task command on a configured board.
 *
 * @returns {Promise<object>} normalized task result.
 */
async function runTask({ config, targetSelector, command, cwd, timeoutMs = 120000, allowStateChange, allowPrivileged = false, allowDestructive = false, dryRun = false, environment = 'base' }) {
	if (allowStateChange !== true) {
		return {
			allowed: false,
			executed: false,
			command: String(command ?? ''),
			target: '未执行',
			cwd: '',
			exitCode: -1,
			durationMs: 0,
			reason: '任务执行需要显式 allowStateChange=true；只读诊断请继续使用 ascend_board_exec。',
			stdout: '',
			stderr: ''
		};
	}
	const taskCommand = prepareTaskCommand(command, environment);
	const verdict = validateTaskCommand(taskCommand, { allowPrivileged, allowDestructive });
	if (!verdict.ok) {
		return {
			allowed: false,
			executed: false,
			command: taskCommand,
			target: '未执行',
			cwd: '',
			exitCode: -1,
			durationMs: 0,
			reason: verdict.reason,
			stdout: '',
			stderr: ''
		};
	}
	const target = resolveTarget(config, targetSelector);
	const workingDir = resolveTaskCwd(target, cwd);
	const started = Date.now();
	if (dryRun) {
		return {
			allowed: true,
			executed: false,
			command: taskCommand,
			target: target.label,
			cwd: workingDir,
			exitCode: 0,
			durationMs: 0,
			reason: `预检通过：${verdict.reason} 未执行（dryRun）。`,
			stdout: '',
			stderr: ''
		};
	}
	const result =
		target.transport === 'local'
			? await runLocalShell({ script: taskCommand, cwd: workingDir, timeoutMs })
			: await runRemoteShell({ script: taskCommand, target, cwd: workingDir, timeoutMs });
	const durationMs = Date.now() - started;
	return {
		allowed: true,
		executed: true,
		command: taskCommand,
		target: target.label,
		cwd: workingDir,
		exitCode: Number.isInteger(result.code) ? result.code : result.ok ? 0 : 1,
		durationMs,
		reason: result.reason,
		stdout: excerpt(result.stdout ?? ''),
		stderr: excerpt(result.stderr ?? '')
	};
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
 * Record a probe result in the local inventory when it came from a real SSH
 * board. Snapshots and the local host are not board identities.
 */
function recordEnvironment(config, result) {
	if (result?.target?.transport !== 'ssh' || typeof result.target.host !== 'string') return null;
	const facts = result.environment?.facts ?? {};
	const mac = facts.macAddress?.value ?? null;
	if (typeof mac !== 'string' || mac === '') return null;
	const path = inventoryPath(config);
	const inventory = readInventory(path);
	const system = systemFromFacts(facts);
	system.tier = system.tier ?? (facts.socTier?.value ? String(facts.socTier.value) : null);
	const ipAddress = facts.ipAddress?.value ?? (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(result.target.host) ? result.target.host : null);
	const observed = observeBoard(inventory, {
		ip: ipAddress,
		user: result.target.user,
		mac,
		hostname: facts.hostname?.value ?? null,
		configuredId: result.target.id,
		system
	});
	if (observed.status !== 'unchanged') {
		try {
			saveInventory(path, observed.inventory);
		} catch {
			// A read-only probe must still succeed if the local inventory path is
			// not writable; the report indicates the probe result, not the cache.
			return null;
		}
	}
	return { path, status: observed.status, changes: observed.changes };
}

/** Render the local inventory change summary for a text response. */
function inventoryNote(change) {
	if (change === null) return '';
	if (change.status === 'system-changed' && change.changes.length > 0) {
		return [
			'',
			'### 本地资产表检测到系统变化',
			...change.changes.map((field) => `- ${field.name}: ${field.from ?? '未记录'} -> ${field.to ?? '未记录'}`),
			`- 资产表：${change.path}`
		].join('\n');
	}
	if (change.status === 'new') return ['', '### 本地资产表', `- 新板已记录：${change.path}`].join('\n');
	return '';
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
		archetypeLabel: { type: 'string' },
		entry: { type: 'string' },
		test: { type: 'string' },
		capabilities: { type: 'array', items: { type: 'string' } },
		keywords: { type: 'array', items: { type: 'string' } }
	}
};

/** Remove null/undefined optional sample fields rejected by the tool schema. */
function publicSample(sample) {
	const output = {
		id: sample.id,
		title: sample.title,
		readme: sample.readme
	};
	for (const key of ['archetype', 'archetypeLabel', 'entry', 'test']) {
		if (typeof sample[key] === 'string' && sample[key] !== '') output[key] = sample[key];
	}
	for (const key of ['capabilities', 'keywords']) {
		if (Array.isArray(sample[key])) output[key] = sample[key].filter((value) => typeof value === 'string');
	}
	return output;
}

/** Keep the panel payload small while preserving its search vocabulary. */
function panelSample(sample) {
	return {
		id: sample.id,
		title: sample.title,
		readme: sample.readme,
		archetype: sample.archetype,
		entry: sample.entry,
		capabilities: sample.capabilities,
		keywords: sample.keywords,
		searchTerms: sample.searchTerms
	};
}

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

/** JSON schema shared by explicit task and download execution. */
const TASK_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		text: { type: 'string', required: true },
		allowed: { type: 'boolean', required: true },
		executed: { type: 'boolean', required: true },
		command: { type: 'string', required: true },
		target: { type: 'string', required: true },
		cwd: { type: 'string', required: true },
		exitCode: { type: 'integer', required: true },
		durationMs: { type: 'integer', required: true },
		reason: { type: 'string', required: true },
		stdout: { type: 'string' },
		stderr: { type: 'string' }
	}
};

const DOWNLOAD_SCHEMA = {
	...TASK_SCHEMA,
	properties: {
		...TASK_SCHEMA.properties,
		source: { type: 'string', required: true },
		resolvedUrl: { type: 'string', required: true },
		destination: { type: 'string', required: true }
	}
};

const CAPTURE_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		text: { type: 'string', required: true },
		allowed: { type: 'boolean', required: true },
		executed: { type: 'boolean', required: true },
		url: { type: 'string', required: true },
		path: { type: 'string', required: true },
		width: { type: 'integer', required: true },
		height: { type: 'integer', required: true },
		bytes: { type: 'integer', required: true },
		durationMs: { type: 'integer', required: true },
		browser: { type: 'string', required: true },
		stderr: { type: 'string' },
		reason: { type: 'string', required: true }
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
				const inventoryChange = recordEnvironment(config, result);
				const text = [
					`目标：${result.target.label}（${result.target.transport}${result.target.host ? ` ${result.target.user ? `${result.target.user}@` : ''}${result.target.host}` : ''}）`,
					`传输结论：${result.transport.reason}`,
					report.scope?.applicable === true ? '' : `适用范围：${report.scope?.message ?? '未识别到 SoC。'}`,
					'',
					report.text,
					inventoryNote(inventoryChange),
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
						acceptanceGates: { type: 'array', required: true, items: { type: 'string' } },
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
				let repoRoot = null;
				let repoNote = null;
				try {
					repoRoot = resolveRepoRoot(config, args.repoRoot);
				} catch (error) {
					repoNote = `未配置教材仓库根目录：样例来自包内快照，版本文档路径未校验。${String(error?.message ?? error)}`;
				}
				const result = await collectEnvironment({
					config,
					targetSelector: args.target,
					rawText: args.rawText,
					timeoutMs: 60000
				});
				const report = environmentReport(result);
				const inventoryChange = recordEnvironment(config, result);
				const index = readSampleIndex(repoRoot);
				const soc = result.environment.facts?.soc?.value ?? null;
				const relevant = indexQuery(index, { query: [soc, 'atc', 'acl'].filter(Boolean).join(' '), limit: 8 });
				const docs = repoRoot === null
					? []
					: [
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
							inventoryNote(inventoryChange),
							'',
							'### 不适用范围',
							`- ${report.scope.message}`,
							'- 未应用 310B 的版本配套表、升级顺序和样例索引；请使用该芯片自己的工具链与文档。'
						].join('\n')
					: [
							report.text,
							inventoryNote(inventoryChange),
							...(repoNote === null ? [] : ['', `> ${repoNote}`]),
							'',
							'### 相关 samples 程序',
							...sampleLines,
							...(docs.length === 0
								? []
								: [
										'',
										'### 版本文档',
										...docs.map((doc) => `- ${doc.exists ? '' : '[仓库中未找到] '}${doc.path}`)
									]),
							'',
							'### 升级顺序（覆盖升级）',
							...UPGRADE_ORDER.map((step, position) => `${position + 1}. ${step}`),
							'',
							'### 五道验收门',
							...ACCEPTANCE_GATES.map((gate, position) => `${position + 1}. ${gate}`)
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
					acceptanceGates: outOfScope ? [] : [...ACCEPTANCE_GATES],
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
					index = readSampleIndex(null);
					samplesNote = `未配置教材仓库根目录：样例来自包内快照。${error?.message ?? error}`;
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
				const applicable =
					scope?.applicable === true ? 'yes' : scope?.applicable === false ? 'no' : 'unknown';
				const scopeNote = outOfScope
					? `## 不适用范围\n\n${scope.message}\n\n下面的分层计划属于教材的昇腾 310B 调试程序，在 ${scope.soc} 上不应照做；请改用该芯片自己的工具链与文档。\n`
					: '';
				const body = outOfScope ? scopeNote.trimEnd() : renderPlan(plan);
				const text = samplesNote === null ? body : `${body}\n\n${samplesNote}`;
				return {
					text,
					applicable,
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
					samples: outOfScope ? [] : samples.map(publicSample)
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
				// A copied installation ships the generated sample snapshot, so the
				// tool can still search it without a checkout. When a checkout is
				// configured, readSampleIndex uses that tree instead of the snapshot.
				let repoRoot = null;
				let note = null;
				try {
					repoRoot = resolveRepoRoot(config, args.repoRoot);
				} catch (error) {
					note = `未配置教材仓库根目录：样例来自包内快照。${String(error?.message ?? error)}`;
				}
				const index = readSampleIndex(repoRoot);
				const matched = indexQuery(index, {
					query: args.query,
					archetype: args.archetype,
					limit: Number.isInteger(args.limit) && args.limit > 0 ? args.limit : 12
				});
				const text = [
					...(note === null ? [] : [note, '']),
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
					matched: matched.map(publicSample),
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

	ctx.tools.register(
		defineTool({
			name: 'ascend_board_run',
			description:
				'Run an explicitly approved task command on an Ascend 310B board: downloading models, creating a workspace, ' +
				'converting ONNX with ATC, running Python/pytest/msame, or executing a tutorial shell script. Unlike ' +
				'`ascend_board_exec`, this tool changes files and may execute build/test code. It requires ' +
				'`allowStateChange:true`; `sudo`, recursive deletion and board reset/upgrade commands require separate ' +
				'explicit switches. Use `dryRun:true` first. The command runs in a constrained board workspace and returns ' +
				'exit code, duration, stdout and stderr.',
			parameters: {
				target: { type: 'string', description: 'Configured target id, configured `user@host`, or `local`.' },
				command: { type: 'string', description: 'Shell command to run, for example `python sample.py` or `bash run.sh`.' },
				cwd: { type: 'string', description: 'Absolute board working directory. Defaults to the target workdir/home.' },
				timeoutMs: { type: 'integer', description: 'Execution timeout in milliseconds. Defaults to 120000.' },
				allowStateChange: {
					type: 'boolean',
					description: 'Required true to acknowledge that this task writes files or executes sample code.'
				},
				allowPrivileged: { type: 'boolean', description: 'Allow sudo. Defaults to false.' },
				allowDestructive: { type: 'boolean', description: 'Allow rm -r/-f. Defaults to false.' },
				environment: {
					type: 'string',
					enum: ['base', 'none'],
					description: 'Use the board base Python/CANN/conda environment, or a bare shell. Defaults to base.'
				},
				dryRun: { type: 'boolean', description: 'Validate and show the task without executing it.' }
			},
			output: { schema: TASK_SCHEMA, render: (_args, value) => [{ type: 'text', text: value.text }] },
			async execute(args) {
				const task = await runTask({
					config,
					targetSelector: args.target,
					command: args.command,
					cwd: args.cwd,
					timeoutMs: Number.isInteger(args.timeoutMs) && args.timeoutMs > 0 ? args.timeoutMs : 120000,
					allowStateChange: args.allowStateChange === true,
					allowPrivileged: args.allowPrivileged === true,
					allowDestructive: args.allowDestructive === true,
					environment: args.environment === 'none' ? 'none' : 'base',
					dryRun: args.dryRun === true
				});
				const text = [
					`目标：${task.target}${task.cwd === '' ? '' : `（${task.cwd}）`}`,
					`状态：${task.allowed ? (task.executed ? (task.exitCode === 0 ? '已执行' : '已执行（失败）') : '未执行') : '已拒绝'}`,
					`命令：${task.command}`,
					`说明：${task.reason}`,
					...(task.executed
						? [
								`退出码：${task.exitCode}`,
								`耗时：${task.durationMs} ms`,
								'',
								'stdout:',
								'```text',
								task.stdout ?? '',
								'```',
								...(task.stderr ? ['stderr:', '```text', task.stderr, '```'] : [])
							]
						: [])
				].join('\n');
				return { ...task, text };
			},
			presentCall: (args) => ({ card: 'generic', title: '板端任务执行', kind: 'other', rawInput: args })
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_model_download',
			description:
				'Download a tutorial asset to a configured Ascend 310B board. Huawei OBS URLs download directly. ' +
				'Hugging Face downloads use `https://hf-mirror.com` instead of huggingface.co. GitHub URLs are rewritten ' +
				'through a web proxy (default `https://gh-proxy.com/`). Set `allowStateChange:true`; use `dryRun:true` to ' +
				'inspect the resolved URL and command without writing. The destination must be a file path under the board ' +
				'workspace or /tmp.',
			parameters: {
				target: { type: 'string', description: 'Configured target id, configured `user@host`, or `local`.' },
				source: {
					type: 'string',
					enum: ['huawei', 'hf', 'github', 'direct'],
					description: 'Download source policy.'
				},
				url: { type: 'string', description: 'Huawei cloud, GitHub, or direct HTTP(S) URL.' },
				repoId: { type: 'string', description: 'Hugging Face repository id, for example `org/model`.' },
				filename: { type: 'string', description: 'Hugging Face file path to download.' },
				revision: { type: 'string', description: 'Hugging Face revision. Defaults to main.' },
				destination: { type: 'string', description: 'Destination file path on the board.' },
				cwd: { type: 'string', description: 'Board working directory used to resolve a relative destination.' },
				githubProxy: { type: 'string', description: 'GitHub web proxy prefix. Defaults to https://gh-proxy.com/.' },
				timeoutMs: { type: 'integer', description: 'Download timeout in milliseconds. Defaults to 300000.' },
				allowStateChange: { type: 'boolean', description: 'Required true to write the destination file.' },
				dryRun: { type: 'boolean', description: 'Validate and show the resolved URL without downloading.' }
			},
			output: { schema: DOWNLOAD_SCHEMA, render: (_args, value) => [{ type: 'text', text: value.text }] },
			async execute(args) {
				try {
					const target = resolveTarget(config, args.target);
					const workingDir = resolveTaskCwd(target, args.cwd);
					const destination = remotePath(workingDir, args.destination);
					resolveTaskCwd(target, destination);
					const resolved = normalizeDownload({
						source: args.source,
						url: args.url,
						repoId: args.repoId,
						filename: args.filename,
						revision: args.revision ?? 'main',
						githubProxy: args.githubProxy ?? config?.githubProxy
					});
					const command = downloadCommand(resolved.url, destination);
					const task = await runTask({
						config,
						targetSelector: args.target,
						command,
						cwd: workingDir,
						timeoutMs: Number.isInteger(args.timeoutMs) && args.timeoutMs > 0 ? args.timeoutMs : 300000,
						allowStateChange: args.allowStateChange === true,
						environment: 'none',
						dryRun: args.dryRun === true
					});
					const output = {
						...task,
						source: args.source,
						resolvedUrl: resolved.url,
						destination,
						text: [
							`下载源：${args.source}`,
							`解析 URL：${resolved.url}`,
							`目标文件：${destination}`,
							`状态：${task.allowed ? (task.executed ? (task.exitCode === 0 ? '已下载' : '下载失败') : '未执行') : '已拒绝'}`,
							`命令：${command}`,
							`说明：${task.reason}`,
							...(task.executed
								? [`退出码：${task.exitCode}`, `耗时：${task.durationMs} ms`, 'stdout:', '```text', task.stdout ?? '', '```', ...(task.stderr ? ['stderr:', '```text', task.stderr, '```'] : [])]
								: [])
						].join('\n')
					};
					return output;
				} catch (error) {
					const reason = String(error?.message ?? error);
					return {
						text: `下载请求被拒绝：${reason}`,
						allowed: false,
						executed: false,
						command: '',
						target: '未执行',
						cwd: '',
						exitCode: -1,
						durationMs: 0,
						reason,
						source: String(args.source ?? ''),
						resolvedUrl: '',
						destination: String(args.destination ?? '')
					};
				}
			},
			presentCall: (args) => ({ card: 'generic', title: '模型下载', kind: 'other', rawInput: args })
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend_ui_capture',
			description:
				'Capture an Ascend tutorial frontend URL to a PNG using the host machine\'s Edge/Chrome headless browser. ' +
				'This is host-side output: start the board service first with `ascend_board_run`, then capture its HTTP URL. ' +
				'Requires `allowStateChange:true` to write the PNG. The output must be under artifactRoot (default: current ' +
				'workspace) and must end in .png. Returns path, dimensions, bytes and elapsed time.',
			parameters: {
				url: { type: 'string', description: 'HTTP(S) frontend URL, for example http://192.0.2.10:7860/.' },
				output: { type: 'string', description: 'PNG path under artifactRoot.' },
				width: { type: 'integer', description: 'Viewport width. Defaults to 1440.' },
				height: { type: 'integer', description: 'Viewport height. Defaults to 900.' },
				waitMs: { type: 'integer', description: 'Virtual time budget before screenshot. Defaults to 1000.' },
				timeoutMs: { type: 'integer', description: 'Browser timeout in milliseconds. Defaults to 30000.' },
				browserPath: { type: 'string', description: 'Optional Edge/Chrome executable path.' },
				allowStateChange: { type: 'boolean', description: 'Required true to write the screenshot file.' }
			},
			output: { schema: CAPTURE_SCHEMA, render: (_args, value) => [{ type: 'text', text: value.text }] },
			async execute(args) {
				const width = Number.isInteger(args.width) ? args.width : 1440;
				const height = Number.isInteger(args.height) ? args.height : 900;
				const path = String(args.output ?? '');
				if (args.allowStateChange !== true) {
					return {
						text: '截图需要显式 allowStateChange=true。',
						allowed: false,
						executed: false,
						url: String(args.url ?? ''),
						path,
						width,
						height,
						bytes: 0,
						durationMs: 0,
						browser: '',
						reason: '截图会写入宿主机文件，需要显式确认。'
					};
				}
				try {
					const output = resolveCapturePath(config, args.output);
					const result = await captureScreenshot({
						url: args.url,
						output,
						width,
						height,
						waitMs: Number.isInteger(args.waitMs) ? args.waitMs : 1000,
						timeoutMs: Number.isInteger(args.timeoutMs) && args.timeoutMs > 0 ? args.timeoutMs : 30000,
						browserPath: args.browserPath ?? config?.browserPath
					});
					return {
						text: [
							`页面：${result.url}`,
							`截图：${result.path}`,
							`尺寸：${result.width}x${result.height}`,
							`大小：${result.bytes} bytes`,
							`浏览器：${result.browser}`,
							`耗时：${result.durationMs} ms`
						].join('\n'),
						allowed: true,
						executed: true,
						...result,
						reason: '截图完成。'
					};
				} catch (error) {
					const reason = String(error?.message ?? error);
					return {
						text: `截图失败：${reason}`,
						allowed: false,
						executed: false,
						url: String(args.url ?? ''),
						path,
						width,
						height,
						bytes: 0,
						durationMs: 0,
						browser: findBrowser({ browserPath: args.browserPath ?? config?.browserPath }) ?? '',
						reason
					};
				}
			},
			presentCall: (args) => ({ card: 'generic', title: '前端截图', kind: 'other', rawInput: args })
		})
	);

	registerDiscoveryTool(ctx, config, defineTool);

	// The browser panel reads the same report over HTTP. A headless composition
	// keeps the tools and simply skips these two routes.
	ctx.inject(['webServer'], (scope) => {		const json = (response, status, value) => {
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
					boards: publicInventory(config),
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
					samples: index.entries.map(panelSample),
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
				const inventoryChange = recordEnvironment(config, result);
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
					boards: publicInventory(config),
					board: report.board,
					facts: serializeEnvironment(result.environment).facts,
					scope: report.scope,
					pairing: report.pairing,
					missingCore: result.environment.missingCore ?? [],
					inventoryChange: inventoryChange ?? null,
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
					samples: index.entries.map(panelSample),
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

		const discoverHandler = async (request, response) => {
			try {
				let body = '';
				for await (const chunk of request) {
					body += chunk;
					if (body.length > 1_000_000) {
						json(response, 413, { ok: false, error: '发现请求体超过 1 MB。' });
						return;
					}
				}
				const payload = body.trim() === '' ? {} : JSON.parse(body);
				const discovery = config?.discovery ?? {};
				const cidrs = Array.isArray(payload.cidrs) && payload.cidrs.length > 0
					? payload.cidrs
					: discovery.cidrs;
				const result = await discoverNetwork({
					cidrs,
					user: typeof payload.user === 'string' ? payload.user : discovery.user ?? config?.targets?.[0]?.user ?? null,
					port: Number.isInteger(payload.port) ? payload.port : discovery.port ?? 22,
					scanConcurrency: Number.isInteger(payload.concurrency) ? payload.concurrency : discovery.scanConcurrency ?? 24,
					probeConcurrency: Number.isInteger(discovery.probeConcurrency) ? discovery.probeConcurrency : 4,
					scanTimeoutMs: Number.isInteger(discovery.scanTimeoutMs) ? discovery.scanTimeoutMs : 500,
					probeTimeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : discovery.probeTimeoutMs ?? 60000,
					path: typeof payload.inventoryPath === 'string' ? payload.inventoryPath : discovery.inventoryPath,
					config
				});
				json(response, 200, { ok: true, ...result, targets: publicTargets(config), boards: publicInventory(config) });
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
		scope.effect(
			() => scope.webServer.register({ kind: 'exact', path: DISCOVER_PATH, handler: discoverHandler }),
			'ascend310b-debug: discover route'
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
