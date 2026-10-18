/**
 * Local-network discovery for Ascend 310B boards.
 *
 * Discovery is deliberately explicit: the caller supplies private IPv4 CIDRs.
 * The scanner first checks whether SSH is reachable, then runs the same
 * read-only probe used by `ascend_probe` and keys every board by MAC address.
 * A board that moves to another DHCP address is therefore the same inventory
 * record, while an OS/driver/CANN change produces a change record.
 *
 * This module never writes files except the local inventory file chosen by the
 * caller. It never scans public ranges by default.
 *
 * @module dsh-plugin-ascend310b-debug/discovery
 */

import { Socket } from 'node:net';

import { assessBoardScope } from './compat.js';
import { buildProbeScript, parseProbeOutput } from './probe.js';
import { runRemoteScript } from './shell.js';
import {
	inventoryPath,
	normalizeMac,
	observeBoard,
	readInventory,
	saveInventory,
	systemFromFacts
} from './inventory.js';

/** Maximum number of addresses a single discovery call may scan. */
export const MAX_SCAN_HOSTS = 4096;

/** Parse a dotted IPv4 address to an unsigned integer, or null. */
export function parseIPv4(text) {
	if (typeof text !== 'string') return null;
	const parts = text.trim().split('.');
	if (parts.length !== 4) return null;
	const values = parts.map((part) => (/^\d{1,3}$/.test(part) ? Number(part) : NaN));
	if (values.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) return null;
	return ((values[0] << 24) >>> 0) + (values[1] << 16) + (values[2] << 8) + values[3];
}

/** Format an unsigned IPv4 integer. */
function formatIPv4(value) {
	return [24, 16, 8, 0].map((shift) => Math.floor(value / 2 ** shift) % 256).join('.');
}

/** Whether an IPv4 integer belongs to a private or link-local range. */
export function isPrivateIPv4(value) {
	const first = Math.floor(value / 2 ** 24);
	const second = Math.floor(value / 2 ** 16) % 256;
	return (
		first === 10 ||
		(first === 172 && second >= 16 && second <= 31) ||
		(first === 192 && second === 168) ||
		(first === 169 && second === 254)
	);
}

/**
 * Expand one CIDR into candidate host addresses.
 *
 * Public ranges and oversized scans are rejected instead of being silently
 * clamped: a typo such as `0.0.0.0/0` must not turn into a network scan.
 *
 * @param {string} cidr - IPv4 CIDR such as `10.0.0.0/24`.
 * @param {number} [maxHosts] - hard cap.
 * @returns {string[]} host addresses.
 */
export function expandCidr(cidr, maxHosts = MAX_SCAN_HOSTS) {
	if (typeof cidr !== 'string') throw new Error('CIDR 必须是字符串。');
	const match = /^([0-9.]+)\/([0-9]{1,2})$/.exec(cidr.trim());
	if (match === null) throw new Error(`CIDR 格式无效：${cidr}`);
	const address = parseIPv4(match[1]);
	const prefix = Number(match[2]);
	if (address === null || !Number.isInteger(prefix) || prefix < 16 || prefix > 30) {
		throw new Error(`CIDR 必须是 /16 到 /30 的 IPv4 私有网段：${cidr}`);
	}
	const mask = (0xffffffff << (32 - prefix)) >>> 0;
	const network = (address & mask) >>> 0;
	if (!isPrivateIPv4(network)) throw new Error(`只允许扫描私有 IPv4 网段：${cidr}`);
	const count = 2 ** (32 - prefix);
	if (count - 2 > maxHosts) throw new Error(`CIDR ${cidr} 包含 ${count - 2} 个地址，超过上限 ${maxHosts}。`);
	const hosts = [];
	for (let offset = 1; offset < count - 1; offset += 1) hosts.push(formatIPv4(network + offset));
	return hosts;
}

/** Expand and validate a list of CIDRs, de-duplicating addresses. */
export function expandCidrs(cidrs, maxHosts = MAX_SCAN_HOSTS) {
	if (!Array.isArray(cidrs) || cidrs.length === 0) {
		throw new Error('没有提供 discovery.cidrs；请在配置或工具参数中给出私有网段。');
	}
	const seen = new Set();
	const hosts = [];
	for (const cidr of cidrs) {
		for (const host of expandCidr(cidr, maxHosts)) {
			if (!seen.has(host)) {
				seen.add(host);
				hosts.push(host);
			}
		}
	}
	if (hosts.length > maxHosts) throw new Error(`展开后的地址数 ${hosts.length} 超过上限 ${maxHosts}。`);
	return hosts;
}

/** Check whether one TCP port accepts a connection. */
export function tcpOpen(host, port = 22, timeoutMs = 500) {
	return new Promise((resolve) => {
		const socket = new Socket();
		let settled = false;
		const finish = (open) => {
			if (settled) return;
			settled = true;
			socket.destroy();
			resolve(open);
		};
		socket.setTimeout(timeoutMs, () => finish(false));
		socket.once('error', () => finish(false));
		socket.connect(port, host, () => finish(true));
	});
}

/** Run async functions with a fixed concurrency. */
async function mapLimit(items, limit, worker) {
	const results = new Array(items.length);
	let cursor = 0;
	const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, async () => {
		while (cursor < items.length) {
			const index = cursor;
			cursor += 1;
			results[index] = await worker(items[index], index);
		}
	});
	await Promise.all(workers);
	return results;
}

/**
 * Probe one candidate and turn the probe into an inventory observation.
 *
 * A reachable SSH port is not enough: the identity must come from the board's
 * own output. Non-310B hosts stay in the discovery report but are not inserted
 * into the 310B inventory.
 *
 * @param {object} options - probe options.
 * @returns {Promise<object>} candidate result.
 */
export async function probeCandidate({ host, user = null, port = 22, timeoutMs = 30000, exec } = {}) {
	const result = await runRemoteScript({
		script: buildProbeScript(),
		target: { host, user, port },
		timeoutMs,
		exec
	});
	if (result.ok !== true) {
		return { ip: host, ok: false, error: result.reason ?? result.stderr ?? 'SSH 探测失败' };
	}
	const environment = parseProbeOutput(result.stdout ?? '');
	const scope = assessBoardScope(environment);
	const facts = environment.facts ?? {};
	const mac = normalizeMac(facts.macAddress?.value);
	const system = systemFromFacts(facts);
	system.tier = system.tier ?? (facts.socTier?.value ? String(facts.socTier.value) : null);
	return {
		ip: host,
		ok: true,
		soc: facts.soc?.value ?? null,
		hostname: facts.hostname?.value ?? null,
		mac: mac ?? null,
		system,
		scope,
		environment
	};
}

/**
 * Discover boards in the supplied private CIDRs.
 *
 * @param {object} options - discovery options.
 * @param {string[]} options.cidrs - private IPv4 CIDRs.
 * @param {string|null} [options.user] - SSH user.
 * @param {number} [options.port] - SSH port.
 * @param {number} [options.scanConcurrency] - TCP scan concurrency.
 * @param {number} [options.probeConcurrency] - SSH probe concurrency.
 * @param {number} [options.scanTimeoutMs] - per-address TCP timeout.
 * @param {number} [options.probeTimeoutMs] - SSH probe timeout.
 * @param {string} [options.path] - inventory file.
 * @param {object} [options.config] - plugin configuration.
 * @param {function} [options.exec] - injectable executor for tests.
 * @returns {Promise<object>} discovery result.
 */
export async function discoverNetwork({
	cidrs,
	user = null,
	port = 22,
	scanConcurrency = 24,
	probeConcurrency = 4,
	scanTimeoutMs = 500,
	probeTimeoutMs = 60000,
	path,
	config = {},
	exec
} = {}) {
	const targets = expandCidrs(cidrs);
	const open = [];
	await mapLimit(targets, scanConcurrency, async (host) => {
		if (await tcpOpen(host, port, scanTimeoutMs)) open.push(host);
	});
	const probed = await mapLimit(open, probeConcurrency, (host) => probeCandidate({
		host,
		user,
		port,
		timeoutMs: probeTimeoutMs,
		exec
	}));
	const inventoryFile = path ?? inventoryPath(config);
	const inventory = readInventory(inventoryFile);
	const found = [];
	const errors = [];
	const others = [];
	for (const candidate of probed) {
		if (!candidate.ok) {
			errors.push(`${candidate.ip}: ${candidate.error}`);
			continue;
		}
		if (candidate.scope?.applicable !== true) {
			others.push({ ip: candidate.ip, soc: candidate.soc, reason: candidate.scope?.message ?? '不是昇腾 310B' });
			continue;
		}
		const result = observeBoard(inventory, {
			ip: candidate.ip,
			user,
			mac: candidate.mac,
			hostname: candidate.hostname,
			system: candidate.system
		});
		found.push({
			id: result.board.id,
			mac: result.board.mac,
			ip: result.board.lastIp,
			hostname: result.board.hostname,
			model: result.board.system.boardModel,
			soc: result.board.system.soc,
			tier: result.board.system.tier,
			driver: result.board.system.driverVersion,
			cann: result.board.system.cannVersion,
			status: result.status,
			changes: result.changes,
			identity: candidate.mac === null ? 'hostname-fallback' : 'mac'
		});
	}
	if (found.length > 0) saveInventory(inventoryFile, inventory);
	return { scanned: targets.length, open: open.length, found, others, errors, inventoryPath: inventoryFile };
}

/** Remove null/undefined fields before a tool schema sees them. */
function compactBoard(board) {
	const output = { id: board.id, ip: board.ip, status: board.status };
	for (const key of ['mac', 'hostname', 'model', 'soc', 'tier', 'driver', 'cann', 'firmware', 'identity']) {
		if (typeof board[key] === 'string' && board[key] !== '') output[key] = board[key];
	}
	output.changes = (Array.isArray(board.changes) ? board.changes : []).map((change) => ({
		...(typeof change.at === 'string' ? { at: change.at } : {}),
		...(typeof change.fromIp === 'string' ? { fromIp: change.fromIp } : {}),
		...(typeof change.toIp === 'string' ? { toIp: change.toIp } : {}),
		fields: (Array.isArray(change.fields) ? change.fields : []).map((field) => ({
			name: field.name,
			...(typeof field.from === 'string' ? { from: field.from } : {}),
			...(typeof field.to === 'string' ? { to: field.to } : {})
		}))
	}));
	return output;
}

/** Render a short human-readable discovery report. */
function renderDiscovery(result) {
	const lines = [
		`扫描地址：${result.scanned} 个；SSH 开放：${result.open} 个。`,
		'',
		'### 已识别开发板',
		...(result.found.length === 0
			? ['- 没有发现新的 310B 开发板。']
			: result.found.map((board) => {
					const detail = [
						`- ${board.id}：${board.ip}${board.hostname ? `（${board.hostname}）` : ''}`,
						board.mac ? `  - MAC：${board.mac}` : null,
						board.model || board.soc ? `  - 型号：${board.model ?? board.soc}${board.tier ? ` / ${board.tier}` : ''}` : null,
						board.driver || board.cann ? `  - 驱动：${board.driver ?? '未采集'}；CANN：${board.cann ?? '未采集'}` : null,
						`  - 状态：${board.status}`,
						...(board.changes ?? []).map((change) => `  - 变更：${change.fields.map((field) => `${field.name} ${field.from ?? 'null'} -> ${field.to ?? 'null'}`).join('；')}`)
					].filter(Boolean);
					return detail.join('\n');
				})),
		'',
		`资产表：${result.inventoryPath}`
	];
	if (result.others.length > 0) {
		lines.push('', '### 非 310B 主机');
		for (const other of result.others) lines.push(`- ${other.ip}：${other.soc ?? '未知芯片'}（${other.reason}）`);
	}
	if (result.errors.length > 0) {
		lines.push('', '### 未完成探测');
		for (const error of result.errors) lines.push(`- ${error}`);
	}
	return lines.join('\n');
}

/**
 * Register `ascend_discover` on the host half.
 *
 * @param {object} ctx - plugin context with a `tools` service.
 * @param {object} config - plugin configuration.
 * @param {function} defineTool - host `defineTool` helper.
 */
export function registerDiscoveryTool(ctx, config, defineTool) {
	ctx.tools.register(
		defineTool({
			name: 'ascend_discover',
			description:
				'Discover Ascend 310B boards on explicitly provided private IPv4 CIDRs. It scans SSH, runs the same ' +
				'read-only probe as ascend_probe, identifies each board by MAC address, stores a local inventory and ' +
				'records OS/kernel/driver/firmware/CANN/Python changes across reinstalls. It never scans public ranges ' +
				'and never changes a board. Discovered boards become usable as target ids without editing the static ' +
				'target list.',
			parameters: {
				cidrs: {
					type: 'array',
					items: { type: 'string' },
					description: 'Private IPv4 CIDRs to scan, for example ["10.0.0.0/24"]. Defaults to config.discovery.cidrs.'
				},
				user: { type: 'string', description: 'SSH user. Defaults to config.discovery.user, then the first configured target user.' },
				port: { type: 'integer', description: 'SSH port. Defaults to config.discovery.port, then 22.' },
				concurrency: { type: 'integer', description: 'TCP scan concurrency. Defaults to config.discovery.scanConcurrency, then 24.' },
				timeoutMs: { type: 'integer', description: 'SSH probe timeout in milliseconds. Defaults to config.discovery.probeTimeoutMs, then 60000.' },
				inventoryPath: { type: 'string', description: 'Inventory JSON path. Defaults to config location or DSH_HOME/ascend310b-inventory.json.' }
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						scanned: { type: 'integer', required: true },
						open: { type: 'integer', required: true },
						found: {
							type: 'array',
							required: true,
							items: {
								type: 'object',
								additionalProperties: false,
								properties: {
									id: { type: 'string', required: true },
									ip: { type: 'string', required: true },
									status: { type: 'string', required: true },
									mac: { type: 'string' },
									hostname: { type: 'string' },
									model: { type: 'string' },
									soc: { type: 'string' },
									tier: { type: 'string' },
									driver: { type: 'string' },
									cann: { type: 'string' },
									firmware: { type: 'string' },
									identity: { type: 'string' },
									changes: {
										type: 'array',
										items: {
											type: 'object',
											additionalProperties: false,
											properties: {
												at: { type: 'string' },
												fromIp: { type: 'string' },
												toIp: { type: 'string' },
												fields: {
													type: 'array',
													items: {
														type: 'object',
														additionalProperties: false,
														properties: {
															name: { type: 'string' },
															from: { type: 'string' },
															to: { type: 'string' }
														}
													}
												}
											}
										}
									}
								}
							}
						},
						others: {
							type: 'array',
							required: true,
							items: {
								type: 'object',
								additionalProperties: false,
								properties: {
									ip: { type: 'string' },
									soc: { type: 'string' },
									reason: { type: 'string' }
								}
							}
						},
						errors: { type: 'array', required: true, items: { type: 'string' } },
						inventoryPath: { type: 'string', required: true }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			async execute(args) {
				const discovery = config?.discovery ?? {};
				const cidrs = Array.isArray(args.cidrs) && args.cidrs.length > 0
					? args.cidrs
					: discovery.cidrs;
				const user = typeof args.user === 'string'
					? args.user
					: discovery.user ?? config?.targets?.[0]?.user ?? null;
				let result;
				try {
					result = await discoverNetwork({
						cidrs,
						user,
						port: Number.isInteger(args.port) ? args.port : discovery.port ?? 22,
						scanConcurrency: Number.isInteger(args.concurrency) ? args.concurrency : discovery.scanConcurrency ?? 24,
						probeConcurrency: Number.isInteger(discovery.probeConcurrency) ? discovery.probeConcurrency : 4,
						scanTimeoutMs: Number.isInteger(discovery.scanTimeoutMs) ? discovery.scanTimeoutMs : 500,
						probeTimeoutMs: Number.isInteger(args.timeoutMs) ? args.timeoutMs : discovery.probeTimeoutMs ?? 60000,
						path: typeof args.inventoryPath === 'string' ? args.inventoryPath : discovery.inventoryPath,
						config
					});
				} catch (error) {
					const message = String(error?.message ?? error);
					return {
						text: `发现失败：${message}`,
						scanned: 0,
						open: 0,
						found: [],
						others: [],
						errors: [message],
						inventoryPath: inventoryPath(config)
					};
				}
				const found = result.found.map(compactBoard);
				const others = result.others.map((other) => ({
					...(typeof other.ip === 'string' ? { ip: other.ip } : {}),
					...(typeof other.soc === 'string' ? { soc: other.soc } : {}),
					...(typeof other.reason === 'string' ? { reason: other.reason } : {})
				}));
				return {
					text: renderDiscovery({ ...result, found }),
					scanned: result.scanned,
					open: result.open,
					found,
					others,
					errors: result.errors,
					inventoryPath: result.inventoryPath
				};
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '发现昇腾310B开发板',
				kind: 'other',
				rawInput: args
			})
		})
	);
}
