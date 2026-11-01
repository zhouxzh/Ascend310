import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
	SERIAL_LOG_MAX_ENTRIES,
	SERIAL_LOG_MAX_TRANSCRIPT_BYTES,
	appendSerialSessionPhase,
	clearSerialSessionLogs,
	createSerialSessionLog,
	finishSerialSessionLog,
	listSerialSessionLogs,
	persistSerialSessionLog,
	readSerialSessionLog,
	redactSerialLogText,
	serialLogDirectory
} from '../lib/serial-log.js';

const failures = [];
const check = (condition, message) => { if (!condition) failures.push(message); };
const root = mkdtempSync(join(tmpdir(), 'ascend-serial-log-'));
const config = { serialLogDir: join(root, 'sessions') };

try {
	check(serialLogDirectory(config) === config.serialLogDir, 'configured serial log directory must win');
	const redacted = redactSerialLogText('password=secret token=abc\u0001 Bearer qwerty', ['secret']);
	check(!redacted.includes('secret') && !redacted.includes('qwerty') && !redacted.includes('\u0001'), 'secrets/control bytes must be redacted');

	const log = createSerialSessionLog({ operation: 'probe', settings: { port: 'COM23', baudRate: 115200, user: 'HwHiAiUser' }, commands: ['hostname'], now: '2026-11-01T00:00:00.000Z' });
	log.hostname = 'orangepiaipro';
	log.ipAddress = '198.51.100.20';
	log.macAddress = 'c0:74:2b:fd:f6:96';
	appendSerialSessionPhase(log, 'session', { status: 'ok', detail: 'token=abc' });
	finishSerialSessionLog(log, { status: 'success', transcript: `${'x'.repeat(SERIAL_LOG_MAX_TRANSCRIPT_BYTES + 1000)}\npassword=secret`, at: '2026-11-01T00:00:01.000Z' });
	const persisted = persistSerialSessionLog(config, log, { secrets: ['secret'] });
	check(existsSync(persisted.path) && existsSync(`${persisted.path}.bak`), 'log and backup must be written');
	const loaded = readSerialSessionLog(config, persisted.id);
	check(loaded?.status === 'success' && loaded?.durationMs === 1000, 'saved log must retain status/duration');
	check(Buffer.byteLength(loaded?.transcript ?? '', 'utf8') <= SERIAL_LOG_MAX_TRANSCRIPT_BYTES + 128, 'transcript must be bounded near 64 KiB');
	check(!JSON.stringify(loaded).includes('secret'), 'persisted log must not contain configured secret');
	const summary = listSerialSessionLogs(config)[0];
	check(summary?.hostname === 'orangepiaipro' && summary?.ipAddress === '198.51.100.20' && summary?.macAddress === 'c0:74:2b:fd:f6:96', 'summary must include hostname/ip/mac');

	writeFileSync(persisted.path, '{broken', 'utf8');
	check(readSerialSessionLog(config, persisted.id)?.status === 'success', 'corrupt primary must recover from .bak');
	rmSync(persisted.path, { force: true });
	check(listSerialSessionLogs(config).some((item) => item.id === persisted.id), 'backup-only log must remain enumerable');

	for (let index = 0; index < SERIAL_LOG_MAX_ENTRIES + 4; index += 1) {
		const entry = createSerialSessionLog({ operation: 'exec', settings: { port: 'COM7', user: 'HwHiAiUser' }, commands: ['uptime'], now: new Date(Date.UTC(2026, 10, 2, 0, 0, index)).toISOString() });
		finishSerialSessionLog(entry, { status: 'failed', error: 'test' });
		persistSerialSessionLog(config, entry);
	}
	check(listSerialSessionLogs(config).length <= SERIAL_LOG_MAX_ENTRIES, 'retention must cap logs at 50');
	let refused = false;
	try { clearSerialSessionLogs(config); } catch { refused = true; }
	check(refused, 'clear must require confirm=true');
	const cleared = clearSerialSessionLogs(config, { confirm: true });
	check(cleared.removed > 0 && listSerialSessionLogs(config).length === 0, 'confirmed clear must remove logs');
} finally {
	rmSync(root, { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error(`serial-log test FAILED (${failures.length})`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('serial-log test passed');
