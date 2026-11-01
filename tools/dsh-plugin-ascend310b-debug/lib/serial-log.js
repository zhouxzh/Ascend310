import {
	closeSync,
	constants,
	fstatSync,
	lstatSync,
	mkdirSync,
	openSync,
	readdirSync,
	readFileSync,
	renameSync,
	rmSync,
	writeFileSync
} from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export const SERIAL_LOG_SCHEMA_VERSION = 1;
export const SERIAL_LOG_MAX_TRANSCRIPT_BYTES = 64 * 1024;
export const SERIAL_LOG_MAX_ENTRIES = 50;
export const SERIAL_LOG_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
export const SERIAL_LOG_MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const LOG_ID = /^(\d{4}-\d{2}-\d{2}T\d{2})-(\d{2})-(\d{2}\.\d{3}Z)-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

function logTime(id) {
	if (typeof id !== 'string') return null;
	const match = LOG_ID.exec(id);
	if (!match) return null;
	const timestamp = `${match[1]}:${match[2]}:${match[3]}`;
	const time = Date.parse(timestamp);
	return Number.isFinite(time) && new Date(time).toISOString() === timestamp ? time : null;
}

export function serialLogDirectory(config = {}, env = process.env) {
	if (typeof config?.serialLogDir === 'string' && config.serialLogDir.trim() !== '') return resolve(config.serialLogDir.trim());
	const home = typeof env?.DSH_HOME === 'string' && env.DSH_HOME.trim() !== '' ? env.DSH_HOME.trim() : join(homedir(), '.dsh');
	return join(home, 'ascend310b-debug', 'serial-sessions');
}

function stripControls(value) {
	return String(value ?? '')
		// Drop complete and unfinished terminal control strings, including OSC titles/links.
		.replace(/(?:\u001b\]|\u009d)[\s\S]*?(?:\u0007|\u001b\\|\u009c|$)/gu, '')
		.replace(/(?:\u001b[PX^_]|[\u0090\u0098\u009e\u009f])[\s\S]*?(?:\u001b\\|\u009c|$)/gu, '')
		.replace(/(?:\u001b\[|\u009b)[0-?]*[ -/]*(?:[@-~]|$)/gu, '')
		.replace(/\u001b[ -/]*[0-~]/gu, '')
		.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/gu, '');
}

function secretKey(key) {
	const normalized = key.replace(/([a-z0-9])([A-Z])/gu, '$1_$2').replace(/([A-Z])([A-Z][a-z])/gu, '$1_$2');
	return /(?:^|[_.-])(?:password|passwd|passphrase|token|secret|apikey|key|authorization|credentials?)\d*(?:$|[_.-])/iu.test(normalized);
}

function redactAssignments(text) {
	const labels = /(?<![\w.-])(?:(["'])([A-Za-z_][\w.-]*)\1[ \t]*[:=]\s*|([A-Za-z_][\w.-]*)[ \t]*[:=]\s*|--([A-Za-z_][\w.-]*)(?:[ \t]*=[ \t]*|[ \t]+))/gu;
	const parts = [];
	let cursor = 0;
	for (const match of text.matchAll(labels)) {
		const key = match[2] ?? match[3] ?? match[4];
		if (match.index < cursor || !secretKey(key)) continue;
		const start = match.index + match[0].length;
		const rest = text.slice(start);
		const quoted = rest[0] === '"' || rest[0] === "'";
		const value = !quoted && /authorization/iu.test(key)
			? /^[^\r\n]+(?:\r?\n[ \t]+[^\r\n]+)*/u.exec(rest)?.[0]
			: /^(?:"(?:\\[\s\S]?|[^"\\])*(?:"|$)|'(?:\\[\s\S]?|[^'\\])*(?:'|$)|\\[\s\S]|[^\s,;&|<>"'`{}\[\]#\\])+/u.exec(rest)?.[0];
		if (!value) continue;
		const replacement = quoted ? `${rest[0]}<redacted>${value.endsWith(rest[0]) && value.length > 1 ? rest[0] : ''}` : '<redacted>';
		parts.push(text.slice(cursor, start), replacement);
		cursor = start + value.length;
	}
	return parts.join('') + text.slice(cursor);
}

export function redactSerialLogText(value, secrets = []) {
	const known = [...new Set(secrets.filter((secret) => typeof secret === 'string' && secret !== ''))].sort((left, right) => right.length - left.length);
	let text = String(value ?? '');
	for (const secret of known) text = text.split(secret).join('<redacted>');
	text = stripControls(text);
	const cleaned = [...new Set(known.map(stripControls).filter(Boolean))].sort((left, right) => right.length - left.length);
	for (const secret of cleaned) text = text.split(secret).join('<redacted>');
	text = text.replace(/-----BEGIN ((?:[A-Z0-9]+ )?PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)/giu, '<redacted private key>');
	return redactAssignments(text)
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/giu, 'Bearer <redacted>')
		.replace(/\bASC[A-Z0-9]{8,}\b/gu, '<session-marker>');
}

function truncateUtf8(value, limit = SERIAL_LOG_MAX_TRANSCRIPT_BYTES) {
	const text = String(value ?? '');
	const bytes = Buffer.from(text, 'utf8');
	if (bytes.length <= limit) return text;
	const marker = '\n...<truncated>...\n';
	const budget = limit - Buffer.byteLength(marker, 'utf8');
	let head = Math.floor(budget / 2);
	let tail = bytes.length - (budget - head);
	while ((bytes[head] & 0xc0) === 0x80) head -= 1;
	while ((bytes[tail] & 0xc0) === 0x80) tail += 1;
	return bytes.subarray(0, head).toString('utf8') + marker + bytes.subarray(tail).toString('utf8');
}

function sanitizeValue(value, secrets) {
	if (typeof value === 'string') return redactSerialLogText(value, secrets);
	if (Array.isArray(value)) return value.map((item) => sanitizeValue(item, secrets));
	if (value !== null && typeof value === 'object') {
		const output = Object.create(null);
		for (const [key, item] of Object.entries(value)) {
			output[key] = secretKey(key) ? '<redacted>' : sanitizeValue(item, secrets);
		}
		return output;
	}
	return value;
}

export function createSerialSessionLog({ operation, settings = {}, commands = [], now = new Date().toISOString() } = {}) {
	now = new Date(now).toISOString();
	const id = `${now.replace(/:/gu, '-')}-${randomUUID()}`;
	if (logTime(id) === null) throw new Error('Invalid serial session timestamp.');
	return {
		schemaVersion: SERIAL_LOG_SCHEMA_VERSION,
		id,
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

export function appendSerialSessionPhase(log, phase, { status = 'ok', detail = '', at = new Date().toISOString(), data = null, durationMs = null } = {}) {
	if (!Array.isArray(log.timeline)) log.timeline = [];
	log.timeline.push({
		at,
		phase: String(phase),
		status: String(status),
		detail: String(detail ?? ''),
		...(Number.isFinite(durationMs) && durationMs >= 0 ? { durationMs } : {}),
		...(data === null ? {} : { data })
	});
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

function pathStat(path) {
	try {
		return lstatSync(path);
	} catch (error) {
		if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') return null;
		throw error;
	}
}

function regularFile(path) {
	const stat = pathStat(path);
	return stat?.isFile() ? stat : null;
}

function writableFile(path) {
	const stat = pathStat(path);
	if (stat && !stat.isFile()) throw new Error('Serial log path is not a regular file.');
	return stat;
}

function atomicWrite(path, body) {
	// Install the backup first so replacement failures still leave a recoverable record.
	for (const destination of [`${path}.bak`, path]) {
		writableFile(destination);
		const temporary = `${destination}.${randomUUID()}.tmp`;
		const fd = openSync(temporary, 'wx', 0o600);
		try {
			try {
				writeFileSync(fd, body, 'utf8');
			} finally {
				closeSync(fd);
			}
			writableFile(destination);
			try {
				renameSync(temporary, destination);
			} catch (error) {
				if (error?.code !== 'EEXIST' && error?.code !== 'EPERM') throw error;
				writableFile(destination);
				rmSync(destination, { force: true });
				renameSync(temporary, destination);
			}
		} finally {
			rmSync(temporary, { force: true });
		}
	}
}

function parseLog(path, id) {
	for (const candidate of [path, `${path}.bak`]) {
		let fd;
		try {
			const stat = regularFile(candidate);
			if (!stat || stat.size > SERIAL_LOG_MAX_TOTAL_BYTES / 2) continue;
			fd = openSync(candidate, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
			const opened = fstatSync(fd);
			if (!opened.isFile() || opened.dev !== stat.dev || opened.ino !== stat.ino || opened.size > SERIAL_LOG_MAX_TOTAL_BYTES / 2) continue;
			const parsed = JSON.parse(readFileSync(fd, 'utf8'));
			if (parsed?.schemaVersion === SERIAL_LOG_SCHEMA_VERSION && parsed?.id === id) return parsed;
		} catch {
			// Fall through to the backup without following a symlink.
		} finally {
			if (fd !== undefined) closeSync(fd);
		}
	}
	return null;
}

function logFiles(directory) {
	if (!pathStat(directory)?.isDirectory()) return [];
	const entries = new Map();
	for (const name of readdirSync(directory)) {
		const match = /^(.*)\.json(?:\.bak)?$/u.exec(name);
		const id = match?.[1];
		const at = logTime(id);
		if (at === null) continue;
		const file = join(directory, name);
		const stat = regularFile(file);
		if (!stat) continue;
		const entry = entries.get(id) ?? { id, path: join(directory, `${id}.json`), files: [], bytes: 0, at };
		entry.files.push(file);
		entry.bytes += stat.size;
		entries.set(id, entry);
	}
	return [...entries.values()];
}

function removeEntry(entry) {
	for (const file of entry.files) {
		if (regularFile(file)) rmSync(file, { force: true });
	}
}

function pruneLogs(directory, now, reservation = null) {
	const entries = logFiles(directory).sort((left, right) => right.at - left.at);
	let total = reservation?.bytes ?? 0;
	let kept = reservation ? 1 : 0;
	let removed = 0;
	for (const entry of entries) {
		if (entry.id === reservation?.id) continue;
		if (now - entry.at > SERIAL_LOG_MAX_AGE_MS || kept >= SERIAL_LOG_MAX_ENTRIES || total + entry.bytes > SERIAL_LOG_MAX_TOTAL_BYTES) {
			removeEntry(entry);
			removed += 1;
		} else {
			total += entry.bytes;
			kept += 1;
		}
	}
	return { removed };
}

export function pruneSerialSessionLogs(config = {}, now = Date.now()) {
	return pruneLogs(serialLogDirectory(config), now);
}

export function persistSerialSessionLog(config, log, { secrets = [] } = {}) {
	const at = logTime(log?.id);
	if (at === null) throw new Error('Invalid serial session log id.');
	const now = Date.now();
	if (now - at > SERIAL_LOG_MAX_AGE_MS) throw new Error('Serial session log is older than the retention limit.');
	const sanitized = sanitizeValue(log, secrets);
	if (logTime(sanitized.id) === null) throw new Error('Invalid sanitized serial session log id.');
	sanitized.transcript = truncateUtf8(sanitized.transcript);
	sanitized.timeline = Array.isArray(sanitized.timeline) ? sanitized.timeline.slice(-200) : [];
	const body = `${JSON.stringify(sanitized, null, 2)}\n`;
	const bytes = Buffer.byteLength(body, 'utf8');
	if (bytes * 2 > SERIAL_LOG_MAX_TOTAL_BYTES) throw new Error('Serial session log exceeds the storage limit.');
	const directory = serialLogDirectory(config);
	const path = join(directory, `${sanitized.id}.json`);
	const primary = writableFile(path);
	const backup = writableFile(`${path}.bak`);
	// Account for both retained copies and the peak temporary file before writing anything.
	const reserved = Math.max((primary?.size ?? 0) + (backup?.size ?? 0) + bytes, (primary?.size ?? 0) + bytes * 2);
	if (reserved > SERIAL_LOG_MAX_TOTAL_BYTES) throw new Error('Serial session log replacement exceeds the storage limit.');
	mkdirSync(directory, { recursive: true, mode: 0o700 });
	if (!pathStat(directory)?.isDirectory()) throw new Error('Serial log directory must not be a symlink.');
	pruneLogs(directory, now, { id: sanitized.id, bytes: reserved });
	atomicWrite(path, body);
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
		.map((entry) => parseLog(entry.path, entry.id))
		.filter(Boolean)
		.sort((left, right) => String(right.startedAt).localeCompare(String(left.startedAt)))
		.map(summary);
}

export function readSerialSessionLog(config, id) {
	const at = logTime(id);
	if (at === null || Date.now() - at > SERIAL_LOG_MAX_AGE_MS) return null;
	const directory = serialLogDirectory(config);
	if (!pathStat(directory)?.isDirectory()) return null;
	return parseLog(join(directory, `${id}.json`), id);
}

export function clearSerialSessionLogs(config, { confirm = false } = {}) {
	if (confirm !== true) throw new Error('清理串口日志需要 confirm=true。');
	const directory = serialLogDirectory(config);
	let removed = 0;
	for (const entry of logFiles(directory)) {
		removeEntry(entry);
		removed += 1;
	}
	return { removed, directory };
}
