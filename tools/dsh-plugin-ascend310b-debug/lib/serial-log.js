import {
	copyFileSync,
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	renameSync,
	rmSync,
	statSync,
	writeFileSync
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export const SERIAL_LOG_SCHEMA_VERSION = 1;
export const SERIAL_LOG_MAX_TRANSCRIPT_BYTES = 64 * 1024;
export const SERIAL_LOG_MAX_ENTRIES = 50;
export const SERIAL_LOG_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
export const SERIAL_LOG_MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const LOG_ID = /^[0-9TZ.-]+-[0-9a-f-]{36}$/iu;

export function serialLogDirectory(config = {}, env = process.env) {
	if (typeof config?.serialLogDir === 'string' && config.serialLogDir.trim() !== '') return resolve(config.serialLogDir.trim());
	const home =
		(typeof env?.DSH_HOME === 'string' && env.DSH_HOME.trim() !== '' && env.DSH_HOME) ||
		(typeof env?.USERPROFILE === 'string' && env.USERPROFILE.trim() !== '' && env.USERPROFILE) ||
		(typeof env?.HOME === 'string' && env.HOME.trim() !== '' && env.HOME) ||
		homedir();
	return join(home, 'ascend310b-debug', 'serial-sessions');
}

function stripControls(value) {
	return String(value ?? '')
		.replace(/\u001b\[[0-?]*[ -/]*[@-~]/gu, '')
		.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu, '');
}

export function redactSerialLogText(value, secrets = []) {
	let text = stripControls(value);
	for (const secret of secrets) {
		if (typeof secret !== 'string' || secret === '') continue;
		text = text.split(secret).join('<redacted>');
	}
	text = text
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/giu, 'Bearer <redacted>')
		.replace(/\b(password|passwd|token|secret|api[_-]?key|authorization|credential)\b(\s*[:=]\s*)([^\s,;]+)/giu, '$1$2<redacted>')
		.replace(/\bASC[A-Z0-9]{8,}\b/gu, '<session-marker>');
	return text;
}

function truncateUtf8(value, limit = SERIAL_LOG_MAX_TRANSCRIPT_BYTES) {
	const text = String(value ?? '');
	const bytes = Buffer.from(text, 'utf8');
	if (bytes.length <= limit) return text;
	const half = Math.floor((limit - 64) / 2);
	return `${bytes.subarray(0, half).toString('utf8')}\n…<truncated ${bytes.length - limit} bytes>…\n${bytes.subarray(bytes.length - half).toString('utf8')}`;
}

function sanitizeValue(value, secrets) {
	if (typeof value === 'string') return redactSerialLogText(value, secrets);
	if (Array.isArray(value)) return value.map((item) => sanitizeValue(item, secrets));
	if (value !== null && typeof value === 'object') {
		const output = {};
		for (const [key, item] of Object.entries(value)) {
			if (/password|passwd|token|secret|api[_-]?key|authorization|credential/iu.test(key)) output[key] = '<redacted>';
			else output[key] = sanitizeValue(item, secrets);
		}
		return output;
	}
	return value;
}

export function createSerialSessionLog({ operation, settings = {}, commands = [], now = new Date().toISOString() } = {}) {
	return {
		schemaVersion: SERIAL_LOG_SCHEMA_VERSION,
		id: `${now.replace(/[:]/gu, '-')}-${randomUUID()}`,
		operation: String(operation ?? 'serial'),
		status: 'running',
		startedAt: now,
		endedAt: null,
		durationMs: null,
		port: String(settings.port ?? ''),
		baudRate: Number.isInteger(settings.baudRate) ? settings.baudRate : null,
		user: String(settings.user ?? ''),
		commands: Array.isArray(commands) ? commands.map(String) : [],
		timeline: [{ at: now, phase: 'start', status: 'running', detail: 'serial session requested' }],
		transcript: '',
		error: null
	};
}

export function appendSerialSessionPhase(log, phase, { status = 'ok', detail = '', at = new Date().toISOString(), data = null } = {}) {
	if (!Array.isArray(log.timeline)) log.timeline = [];
	log.timeline.push({ at, phase: String(phase), status: String(status), detail: String(detail ?? ''), ...(data === null ? {} : { data }) });
	return log;
}

export function finishSerialSessionLog(log, { status, transcript = '', error = null, at = new Date().toISOString() } = {}) {
	log.status = status ?? 'failed';
	log.endedAt = at;
	const start = Date.parse(log.startedAt ?? '');
	const end = Date.parse(at);
	log.durationMs = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null;
	log.transcript = transcript;
	log.error = error === null ? null : String(error);
	appendSerialSessionPhase(log, 'finish', { status: log.status, detail: log.error ?? log.status, at });
	return log;
}

function atomicWrite(path, body) {
	mkdirSync(dirname(path), { recursive: true });
	const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
	const backup = `${path}.bak`;
	writeFileSync(temporary, body, 'utf8');
	try {
		if (existsSync(path)) copyFileSync(path, backup);
		try {
			renameSync(temporary, path);
		} catch (error) {
			if (error?.code !== 'EEXIST' && error?.code !== 'EPERM') throw error;
			let movedPrevious = false;
			if (existsSync(path)) {
				rmSync(backup, { force: true });
				renameSync(path, backup);
				movedPrevious = true;
			}
			try {
				renameSync(temporary, path);
			} catch (replacementError) {
				if (movedPrevious && !existsSync(path) && existsSync(backup)) renameSync(backup, path);
				throw replacementError;
			}
		}
		copyFileSync(path, backup);
	} finally {
		rmSync(temporary, { force: true });
	}
}

function parseLog(path) {
	for (const candidate of [path, `${path}.bak`]) {
		try {
			const parsed = JSON.parse(readFileSync(candidate, 'utf8'));
			if (parsed?.schemaVersion === SERIAL_LOG_SCHEMA_VERSION && typeof parsed?.id === 'string') return parsed;
		} catch {
			// Fall through to the backup.
		}
	}
	return null;
}

function logFiles(directory) {
	try {
		const paths = new Set();
		for (const name of readdirSync(directory)) {
			if (name.endsWith('.json')) paths.add(join(directory, name));
			else if (name.endsWith('.json.bak')) paths.add(join(directory, name.slice(0, -4)));
		}
		return [...paths];
	} catch {
		return [];
	}
}

export function pruneSerialSessionLogs(config = {}, now = Date.now()) {
	const directory = serialLogDirectory(config);
	let entries = logFiles(directory).map((path) => {
		const log = parseLog(path);
		let bytes = 0;
		let mtimeMs = 0;
		try {
			const stat = statSync(existsSync(path) ? path : `${path}.bak`);
			bytes = stat.size;
			mtimeMs = stat.mtimeMs;
		} catch {}
		return { path, log, bytes, at: Date.parse(log?.startedAt ?? '') || mtimeMs };
	}).sort((left, right) => right.at - left.at);
	const remove = new Set(entries.filter((entry) => now - entry.at > SERIAL_LOG_MAX_AGE_MS).map((entry) => entry.path));
	entries.filter((entry) => !remove.has(entry.path)).slice(SERIAL_LOG_MAX_ENTRIES).forEach((entry) => remove.add(entry.path));
	let total = 0;
	for (const entry of entries) {
		if (remove.has(entry.path)) continue;
		total += entry.bytes;
		if (total > SERIAL_LOG_MAX_TOTAL_BYTES) remove.add(entry.path);
	}
	for (const path of remove) {
		rmSync(path, { force: true });
		rmSync(`${path}.bak`, { force: true });
	}
	return { removed: remove.size };
}

export function persistSerialSessionLog(config, log, { secrets = [] } = {}) {
	const directory = serialLogDirectory(config);
	const sanitized = sanitizeValue(log, secrets);
	sanitized.transcript = truncateUtf8(sanitized.transcript);
	sanitized.timeline = Array.isArray(sanitized.timeline) ? sanitized.timeline.slice(-200) : [];
	const path = join(directory, `${sanitized.id}.json`);
	atomicWrite(path, `${JSON.stringify(sanitized, null, 2)}\n`);
	pruneSerialSessionLogs(config);
	return { id: sanitized.id, path, log: sanitized };
}

function summary(log) {
	return {
		id: log.id,
		operation: log.operation,
		status: log.status,
		startedAt: log.startedAt,
		endedAt: log.endedAt,
		durationMs: log.durationMs,
		port: log.port,
		user: log.user,
		hostname: log.hostname ?? null,
		ipAddress: log.ipAddress ?? null,
		macAddress: log.macAddress ?? null,
		error: log.error
	};
}

export function listSerialSessionLogs(config = {}) {
	const directory = serialLogDirectory(config);
	pruneSerialSessionLogs(config);
	return logFiles(directory)
		.map(parseLog)
		.filter(Boolean)
		.sort((left, right) => String(right.startedAt).localeCompare(String(left.startedAt)))
		.map(summary);
}

export function readSerialSessionLog(config, id) {
	if (typeof id !== 'string' || !LOG_ID.test(id)) return null;
	return parseLog(join(serialLogDirectory(config), `${id}.json`));
}

export function clearSerialSessionLogs(config, { confirm = false } = {}) {
	if (confirm !== true) throw new Error('清理串口日志需要 confirm=true。');
	const directory = serialLogDirectory(config);
	let removed = 0;
	for (const path of logFiles(directory)) {
		rmSync(path, { force: true });
		rmSync(`${path}.bak`, { force: true });
		removed += 1;
	}
	return { removed, directory };
}
