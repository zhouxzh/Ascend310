/**
 * Persistent board inventory for discovered Ascend 310B boards.
 *
 * The network address is deliberately not the identity. A board is keyed by
 * its physical MAC address, so a DHCP address change, reinstall or temporary
 * offline state does not create a second board. The inventory records the
 * latest observed system tuple (OS, kernel, driver, firmware, CANN, Python and
 * machine-id) and appends a change record when that tuple moves.
 *
 * The inventory is a local deployment file, not part of the repository. It is
 * written below `DSH_HOME` by default and contains MAC addresses, so it must
 * not be committed or copied into a public package.
 *
 * @module dsh-plugin-ascend310b-debug/inventory
 */

import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { homedir } from 'node:os';

/** System fields that describe the installed software stack on one board. */
export const SYSTEM_FIELDS = Object.freeze([
	'hostname',
	'boardModel',
	'soc',
	'tier',
	'os',
	'kernel',
	'driverVersion',
	'cannVersion',
	'npuSmiVersion',
	'pythonVersion',
	'machineId',
	'aclImport',
	'memoryUsedMb',
	'memoryTotalMb'
]);

/** Maximum retained change records per board. */
const MAX_CHANGES = 50;

/** Maximum retained IP/system history entries per board. */
const MAX_HISTORY = 20;

/**
 * Resolve the local inventory file.
 *
 * Priority: per-call path, config.inventoryPath, config.discovery.inventoryPath,
 * DSH_HOME, then the operating-system home directory.
 *
 * @param {object} [config] - plugin configuration.
 * @param {object} [env] - environment map (injectable for tests).
 * @returns {string} absolute inventory path.
 */
export function inventoryPath(config = {}, env = process.env) {
	const configured = config?.inventoryPath ?? config?.discovery?.inventoryPath;
	if (typeof configured === 'string' && configured.trim() !== '') return resolve(configured.trim());
	const home =
		(typeof env?.DSH_HOME === 'string' && env.DSH_HOME.trim() !== '' && env.DSH_HOME) ||
		(typeof env?.USERPROFILE === 'string' && env.USERPROFILE.trim() !== '' && env.USERPROFILE) ||
		(typeof env?.HOME === 'string' && env.HOME.trim() !== '' && env.HOME) ||
		homedir();
	return join(home, 'ascend310b-inventory.json');
}

/** Normalize a MAC address to lower-case colon form, or null. */
export function normalizeMac(value) {
	if (typeof value !== 'string') return null;
	const match = /([0-9a-f]{2}(?::[0-9a-f]{2}){5})/i.exec(value.trim());
	return match === null ? null : match[1].toLowerCase();
}

/** Stable target id derived from a MAC address. */
export function boardIdForMac(mac) {
	const normalized = normalizeMac(mac);
	if (normalized === null) return null;
	return `ascend-${normalized.replaceAll(':', '')}`;
}

/** Read the inventory, tolerating absence or a partially written file. */
export function readInventory(path) {
	try {
		const parsed = JSON.parse(readFileSync(path, 'utf8'));
		if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
			return { version: 1, boards: {}, updatedAt: null };
		}
		const boards = parsed.boards !== null && typeof parsed.boards === 'object' && !Array.isArray(parsed.boards)
			? parsed.boards
			: {};
		return { version: 1, boards, updatedAt: parsed.updatedAt ?? null };
	} catch {
		return { version: 1, boards: {}, updatedAt: null };
	}
}

/** Persist the inventory atomically; the file stays local to this machine. */
export function saveInventory(path, inventory) {
	mkdirSync(dirname(path), { recursive: true });
	const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
	const lock = `${path}.lock`;
	const started = Date.now();
	while (true) {
		try {
			mkdirSync(lock);
			break;
		} catch (error) {
			if (error?.code !== 'EEXIST') throw error;
			if (Date.now() - started > 5000) throw new Error(`inventory lock timeout: ${lock}`);
			Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
		}
	}
	const body = `${JSON.stringify({ ...inventory, version: 1 }, null, 2)}\n`;
	try {
		writeFileSync(temporary, body, 'utf8');
		try {
			renameSync(temporary, path);
		} catch (error) {
			// Windows cannot replace an existing file with rename; remove only the
			// destination after the complete temp file exists, then retry.
			if (error?.code !== 'EEXIST' && error?.code !== 'EPERM') throw error;
			rmSync(path, { force: true });
			renameSync(temporary, path);
		}
	} finally {
		rmSync(temporary, { force: true });
		rmSync(lock, { recursive: true, force: true });
	}
	return inventory;
}

/** Normalize one observation value to a string or null. */
function text(value) {
	if (value === undefined || value === null) return null;
	const normalized = String(value).trim();
	return normalized === '' || /^(?:N\/?A|UNKNOWN|NOT_FOUND)$/i.test(normalized) ? null : normalized;
}

/** Flatten a probe facts map into the fields inventory compares. */
export function systemFromFacts(facts = {}) {
	const value = (key) => text(facts?.[key]?.value);
	const system = {};
	for (const key of SYSTEM_FIELDS) system[key] = value(key);
	return system;
}

/** Describe the changed fields between two system tuples. */
export function diffSystem(previous, next) {
	const fields = [];
	for (const key of SYSTEM_FIELDS) {
		const before = previous?.[key] ?? null;
		const after = next?.[key] ?? null;
		if (before !== after) fields.push({ name: key, from: before, to: after });
	}
	return fields;
}

/**
 * Merge one discovery observation into the inventory.
 *
 * Identity is the MAC address. A board with no MAC is retained as a
 * `hostname` fallback for diagnosis, but it is marked unstable because it
 * cannot survive a DHCP change or a reinstall reliably.
 *
 * @param {object} inventory - inventory object from {@link readInventory}.
 * @param {object} observation - observed board fields.
 * @param {string} [now] - ISO timestamp (injectable for tests).
 * @returns {{inventory: object, board: object, status: string, changes: object[]}}
 */
export function observeBoard(inventory, observation, now = new Date().toISOString()) {
	const mac = normalizeMac(observation?.mac);
	const hostname = text(observation?.hostname);
	const identity = mac === null ? `hostname:${hostname ?? 'unknown'}` : mac;
	const id = mac === null ? `hostname-${hostname ?? 'unknown'}` : boardIdForMac(mac);
	const previous = inventory.boards?.[identity] ?? null;
	const system = {};
	for (const key of SYSTEM_FIELDS) {
		system[key] = text(observation?.system?.[key]) ?? previous?.system?.[key] ?? null;
	}
	const changes = previous === null ? [] : diffSystem(previous.system, system);
	const ip = text(observation?.ip);
	const ipChanged = ip !== null && previous !== null && previous.lastIp !== null && previous.lastIp !== ip;
	const status = previous === null ? 'new' : changes.length > 0 ? 'system-changed' : ipChanged ? 'ip-changed' : 'unchanged';
	const board = previous ?? {
		id,
		identity,
		mac,
		label: null,
		user: null,
		configuredId: null,
		firstSeen: now,
		lastSeen: now,
		lastChangeAt: null,
		lastIp: null,
		hostname: null,
		port: null,
		system: {},
		serial: null,
		changes: [],
		history: []
	};
	board.id = id;
	board.identity = identity;
	board.mac = mac;
	board.hostname = hostname;
	board.lastIp = ip ?? previous?.lastIp ?? null;
	board.port = Number.isInteger(observation?.port) ? observation.port : board.port ?? null;
	board.user = text(observation?.user) ?? board.user;
	board.label = text(observation?.label) ?? board.label;
	board.configuredId = text(observation?.configuredId) ?? board.configuredId ?? null;
	if (observation?.serial !== null && typeof observation?.serial === 'object') {
		board.serial = {
			status: text(observation.serial.status) ?? 'reachable',
			port: text(observation.serial.port),
			user: text(observation.serial.user),
			ip: text(observation.serial.ip),
			hostname: text(observation.serial.hostname) ?? hostname,
			lastSeen: now
		};
	}
	board.system = system;
	board.lastSeen = now;
	if (previous === null) {
		board.lastChangeAt = now;
		board.history.push({ at: now, ip, system });
	} else {
		if (changes.length > 0) {
			board.changes.push({ at: now, fields: changes, fromIp: previous.lastIp, toIp: ip });
			board.changes = board.changes.slice(-MAX_CHANGES);
			board.lastChangeAt = now;
		}
		if (changes.length > 0 || ipChanged) board.history.push({ at: now, ip, system });
		board.history = board.history.slice(-MAX_HISTORY);
	}
	if (previous === null) board.changes = [];
	inventory.boards[identity] = board;
	inventory.updatedAt = now;
	return { inventory, board, status, changes };
}

/**
 * Convert inventory boards into the target shape consumed by the host half.
 * Configured targets win when their id collides with a discovered id.
 */
export function inventoryTargets(inventory, defaultUser = null) {
	const targets = [];
	for (const board of Object.values(inventory?.boards ?? {})) {
		if (board === null || typeof board !== 'object') continue;
		// A hostname-only record or a serial console that has not proved a 310B
		// SoC must never become an executable SSH target.
		if (normalizeMac(board.mac) === null || !/^Ascend310B[14]$/iu.test(String(board.system?.soc ?? ''))) continue;
		const host = text(board.lastIp) ?? text(board.hostname);
		if (host === null) continue;
		targets.push({
			id: board.id,
			label: board.label ?? `${board.system?.boardModel ?? 'Ascend 310B'} ${board.mac?.slice(-8) ?? board.hostname ?? ''}`.trim(),
			transport: 'ssh',
			host,
			hostname: board.hostname ?? null,
			mac: board.mac ?? null,
			configuredId: board.configuredId ?? null,
			lastSeen: board.lastSeen ?? null,
			user: board.user ?? defaultUser,
			port: Number.isInteger(board.port) ? board.port : null,
			identityFile: null,
			workdir: null,
			note: `发现板；MAC ${board.mac ?? '未读取'}`
		});
	}
	return targets;
}
