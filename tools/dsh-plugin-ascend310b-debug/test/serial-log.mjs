import fs, { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';

import {
	SERIAL_LOG_MAX_AGE_MS,
	SERIAL_LOG_MAX_ENTRIES,
	SERIAL_LOG_MAX_TOTAL_BYTES,
	SERIAL_LOG_MAX_TRANSCRIPT_BYTES,
	appendSerialSessionPhase,
	clearSerialSessionLogs,
	createSerialSessionLog,
	finishSerialSessionLog,
	listSerialSessionLogs,
	persistSerialSessionLog,
	pruneSerialSessionLogs,
	readSerialSessionLog,
	redactSerialLogText,
	serialLogDirectory
} from '../lib/serial-log.js';

const failures = [];
let checks = 0;
let skipped = 0;
const check = (condition, message) => { checks += 1; if (!condition) failures.push(message); };
const throws = (fn, message) => {
	let error;
	try { fn(); } catch (caught) { error = caught; }
	check(error instanceof Error, message);
};
const root = mkdtempSync(join(tmpdir(), 'ascend-serial-log-'));
const configFor = (name) => ({ serialLogDir: join(root, name) });
const now = Date.now();
const iso = (time) => new Date(time).toISOString();
const session = (time = now) => createSerialSessionLog({ operation: 'exec', settings: { port: 'COM23', baudRate: 115200, user: 'HwHiAiUser' }, commands: ['hostname'], now: iso(time) });

function fixture(config, log, { size = 0, backupSize = size, primary = true, backup = true } = {}) {
	mkdirSync(config.serialLogDir, { recursive: true });
	const path = join(config.serialLogDir, `${log.id}.json`);
	const body = JSON.stringify(log);
	if (primary) writeFileSync(path, body + ' '.repeat(Math.max(0, size - Buffer.byteLength(body))), 'utf8');
	if (backup) writeFileSync(`${path}.bak`, body + ' '.repeat(Math.max(0, backupSize - Buffer.byteLength(body))), 'utf8');
	return path;
}

function diskUsage(config) {
	const ids = new Set();
	let bytes = 0;
	for (const name of readdirSync(config.serialLogDir)) {
		const stat = lstatSync(join(config.serialLogDir, name));
		if (!stat.isFile()) continue;
		bytes += stat.size;
		ids.add(name.split('.json')[0]);
	}
	return { bytes, entries: ids.size };
}

function observeWrites(config, fn) {
	const original = fs.writeFileSync;
	let writes = 0;
	const verify = () => {
		const usage = diskUsage(config);
		check(usage.bytes <= SERIAL_LOG_MAX_TOTAL_BYTES, 'storage must fit 20 MiB even while staging a write');
		check(usage.entries <= SERIAL_LOG_MAX_ENTRIES, 'entry capacity must be reserved before a write');
	};
	fs.writeFileSync = (...args) => {
		writes += 1;
		verify();
		const result = original(...args);
		verify();
		return result;
	};
	syncBuiltinESMExports();
	try {
		return fn();
	} finally {
		fs.writeFileSync = original;
		syncBuiltinESMExports();
		check(writes === 2, 'persistence must write exactly the primary and backup');
	}
}

function link(target, path, type = 'file') {
	try {
		symlinkSync(target, path, type);
		return true;
	} catch (error) {
		if (!['EPERM', 'EACCES', 'ENOSYS'].includes(error?.code)) throw error;
		skipped += 1;
		console.log(`serial-log: ${type} symlink checks skipped (${error.code})`);
		return false;
	}
}

try {
	const config = configFor('sessions');
	check(serialLogDirectory(config) === config.serialLogDir, 'configured serial log directory must win');
	check(serialLogDirectory({}, { DSH_HOME: root }) === join(root, 'ascend310b-debug', 'serial-sessions'), 'DSH_HOME must own the default directory');
	check(serialLogDirectory({}, { HOME: root, USERPROFILE: root }) === join(homedir(), '.dsh', 'ascend310b-debug', 'serial-sessions'), 'fallback must use homedir/.dsh, not HOME or USERPROFILE directly');
	check(serialLogDirectory({}, { DSH_HOME: '  ' }) === join(homedir(), '.dsh', 'ascend310b-debug', 'serial-sessions'), 'blank DSH_HOME must use the .dsh fallback');

	const cases = [
		['password=VALUE_PASSWORD passwd: VALUE_PASSWD', ['VALUE_PASSWORD', 'VALUE_PASSWD']],
		['{"credentials":"VALUE_CREDENTIALS","token":"VALUE_JSON_TOKEN","key":"VALUE_JSON_KEY"}', ['VALUE_CREDENTIALS', 'VALUE_JSON_TOKEN', 'VALUE_JSON_KEY']],
		['{"password":\n "VALUE_JSON_MULTILINE", "apiKey": "VALUE_ESCAPED\\\"TAIL"}', ['VALUE_JSON_MULTILINE', 'VALUE_ESCAPED', 'TAIL']],
		["TOKEN_VALUE='VALUE_SHELL with spaces\nand a second line' API_KEY_ID=VALUE_SUFFIX", ['VALUE_SHELL', 'second line', 'VALUE_SUFFIX']],
		['clientSecret=VALUE_CAMEL accessTokenValue=VALUE_ACCESS AWS_SECRET_ACCESS_KEY=VALUE_AWS', ['VALUE_CAMEL', 'VALUE_ACCESS', 'VALUE_AWS']],
		['https://board.test/?token=VALUE_URL&key=VALUE_URL_KEY&hostname=orangepiaipro#network', ['VALUE_URL', 'VALUE_URL_KEY']],
		['--api-key "VALUE_CLI with spaces" --token=VALUE_FLAG', ['VALUE_CLI', 'VALUE_FLAG']],
		['TOKEN=VALUE_ESCAPED_SHELL\\ tail API_KEY=\'VALUE_JOINED\'"TAIL_JOINED"', ['VALUE_ESCAPED_SHELL', 'tail', 'VALUE_JOINED', 'TAIL_JOINED']],
		['Authorization: Basic VALUE_BASIC\nAuthorization: Digest username="VALUE_DIGEST", response="VALUE_RESPONSE"', ['VALUE_BASIC', 'VALUE_DIGEST', 'VALUE_RESPONSE']],
		['{"Authorization":"Bearer VALUE_AUTH_JSON"}\nAuthorization: Custom VALUE_CUSTOM\n  VALUE_FOLDED', ['VALUE_AUTH_JSON', 'VALUE_CUSTOM', 'VALUE_FOLDED']],
		['Bearer VALUE_BEARER\npassword="VALUE_UNFINISHED\nVALUE_LAST_LINE', ['VALUE_BEARER', 'VALUE_UNFINISHED', 'VALUE_LAST_LINE']]
	];
	for (const [input, hidden] of cases) {
		const output = redactSerialLogText(input);
		for (const value of hidden) check(!output.includes(value), `redaction must remove ${value}`);
	}
	const controlled = redactSerialLogText('known=RAW\u0001VALUE split=CLEAN\u001b[31mVALUE\u001b[0m\npa\u0000ssword=VALUE_CONTROL\n\u001b]0;VALUE_TITLE\u0007hostname=orangepiaipro\n\u001b]8;;VALUE_LINK\u001b\\ip=198.51.100.20\n\u009d0;VALUE_C1\u009cmac=c0:74:2b:fd:f6:96\n\u001bP VALUE_DCS\u001b\\\u009b31mplain\u009b0m\n\u001b]0;VALUE_UNFINISHED_OSC', ['RAW\u0001VALUE', 'CLEANVALUE']);
	for (const value of ['RAW', 'CLEAN', 'VALUE_CONTROL', 'VALUE_TITLE', 'VALUE_LINK', 'VALUE_C1', 'VALUE_DCS', 'VALUE_UNFINISHED_OSC']) check(!controlled.includes(value), `control cleanup must not leak ${value}`);
	check(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(controlled), 'terminal controls must be removed');
	for (const value of ['orangepiaipro', '198.51.100.20', 'c0:74:2b:fd:f6:96', 'plain']) check(controlled.includes(value), `control cleanup must preserve ${value}`);
	check(!redactSerialLogText('CLEANVALUE', ['CLEAN\u001b[31mVALUE']).includes('CLEANVALUE'), 'normalized known secrets must also be removed');
	check(redactSerialLogText('prefix\u009d0;unterminated') === 'prefix', 'unfinished C1 OSC must drop its payload');
	for (const label of ['PRIVATE KEY', 'RSA PRIVATE KEY', 'EC PRIVATE KEY', 'ENCRYPTED PRIVATE KEY', 'OPENSSH PRIVATE KEY']) {
		const output = redactSerialLogText(`before\n-----BEGIN ${label}-----\nVALUE_PEM_LINE_ONE\nVALUE_PEM_LINE_TWO\n-----END ${label}-----\nhostname=orangepiaipro`);
		check(!output.includes('VALUE_PEM_') && output.includes('orangepiaipro'), `${label} must be redacted without removing following diagnostics`);
	}
	check(!redactSerialLogText('-----BEGIN PRIVATE KEY-----\nVALUE_PARTIAL_PEM').includes('VALUE_PARTIAL_PEM'), 'unfinished private keys must be redacted to the end');
	check(redactSerialLogText('-----BEGIN CERTIFICATE-----\nPUBLIC_DATA\n-----END CERTIFICATE-----').includes('PUBLIC_DATA'), 'public certificate data must not be mistaken for a private key');
	check(redactSerialLogText('monkey=ok keyboard=ok tokenizer=ok hostname=orangepiaipro ip=198.51.100.20 mac=c0:74:2b:fd:f6:96').includes('monkey=ok keyboard=ok tokenizer=ok hostname=orangepiaipro ip=198.51.100.20 mac=c0:74:2b:fd:f6:96'), 'redaction must not match unrelated substrings or network identifiers');

	const known = persistSerialSessionLog(configFor('known'), { ...session(), transcript: 'VALUE_KNOWN_RAW VALUE_KNOWN_\u001b[31mSPLIT', details: ['VALUE_KNOWN_RAW'] }, { secrets: ['VALUE_KNOWN_RAW', 'VALUE_KNOWN_SPLIT'] });
	for (const file of [known.path, `${known.path}.bak`]) check(!readFileSync(file, 'utf8').includes('VALUE_KNOWN'), 'configured secrets must be removed from all stored strings and both copies');

	const log = session();
	log.hostname = 'orangepiaipro';
	log.ipAddress = '198.51.100.20';
	log.macAddress = 'c0:74:2b:fd:f6:96';
	log.details = { credentials: { value: 'VALUE_STRUCTURED' }, token_value: 'VALUE_NESTED', key: 'VALUE_KEY', keyboard: 'retained' };
	appendSerialSessionPhase(log, 'session', { status: 'ok', detail: 'token=VALUE_TIMELINE' });
	finishSerialSessionLog(log, { status: 'success', transcript: `${'x'.repeat(SERIAL_LOG_MAX_TRANSCRIPT_BYTES + 1000)}\npassword=VALUE_PASSWORD`, at: iso(now + 1000) });
	const persisted = persistSerialSessionLog(config, log);
	check(existsSync(persisted.path) && existsSync(`${persisted.path}.bak`), 'log and backup must be written');
	const loaded = readSerialSessionLog(config, persisted.id);
	check(loaded?.status === 'success' && loaded?.durationMs === 1000, 'saved log must retain status/duration');
	check(Buffer.byteLength(loaded?.transcript ?? '', 'utf8') === SERIAL_LOG_MAX_TRANSCRIPT_BYTES, 'ASCII truncation must use exactly the 64 KiB budget including the marker');
	for (const file of [persisted.path, `${persisted.path}.bak`]) check(!readFileSync(file, 'utf8').includes('VALUE_'), 'neither persisted copy may contain secret values');
	check(loaded?.details?.keyboard === 'retained', 'unrelated structured data must remain');
	const summary = listSerialSessionLogs(config)[0];
	check(summary?.hostname === 'orangepiaipro' && summary?.ipAddress === '198.51.100.20' && summary?.macAddress === 'c0:74:2b:fd:f6:96', 'summary must preserve hostname/IP/MAC');
	for (const transcript of ['x'.repeat(SERIAL_LOG_MAX_TRANSCRIPT_BYTES), `HEAD${'\u4e2d\u6587\ud83d\ude00'.repeat(10000)}TAIL`]) {
		log.transcript = transcript;
		const updated = persistSerialSessionLog(config, log).log.transcript;
		check(Buffer.byteLength(updated, 'utf8') <= SERIAL_LOG_MAX_TRANSCRIPT_BYTES, 'UTF-8 transcript must never exceed 64 KiB');
		check(!updated.includes('\ufffd'), 'UTF-8 truncation must not split code points');
		if (Buffer.byteLength(transcript) <= SERIAL_LOG_MAX_TRANSCRIPT_BYTES) check(updated === transcript, 'an exactly full transcript must not be truncated');
		else check(updated.startsWith('HEAD') && updated.endsWith('TAIL') && updated.includes('<truncated>'), 'truncation must retain both ends and its marker');
	}
	writeFileSync(persisted.path, '{broken', 'utf8');
	check(readSerialSessionLog(config, persisted.id)?.status === 'success', 'corrupt primary must recover from backup');
	writeFileSync(persisted.path, JSON.stringify(session(now + 1)), 'utf8');
	check(readSerialSessionLog(config, persisted.id)?.status === 'success', 'a primary with the wrong embedded id must recover from backup');
	rmSync(persisted.path);
	check(listSerialSessionLogs(config).some((item) => item.id === persisted.id), 'backup-only log must remain enumerable');
	writeFileSync(`${persisted.path}.bak`, JSON.stringify(session(now + 2)), 'utf8');
	check(readSerialSessionLog(config, persisted.id) === null, 'a mismatched backup id must never be returned');
	persistSerialSessionLog(config, log);
	check(readFileSync(persisted.path, 'utf8') === readFileSync(`${persisted.path}.bak`, 'utf8'), 'replacement must refresh both copies');

	const invalidConfig = configFor('invalid');
	for (const id of ['../escaped', '..\\escaped', join(root, 'absolute'), `${log.id}/child`, `${log.id}\\child`, `${log.id}\n`, `${log.id}\0`, log.id.toUpperCase(), log.id.replace(/-4([0-9a-f]{3})-/u, '-1$1-'), `2026-02-30T00-00-00.000Z-${log.id.slice(-36)}`, `.-${log.id.slice(-36)}`, null, 123]) {
		throws(() => persistSerialSessionLog(invalidConfig, { ...log, id }), `persist must reject malformed id ${JSON.stringify(id)}`);
		check(readSerialSessionLog(invalidConfig, id) === null, 'read must reject malformed ids');
	}
	check(!existsSync(invalidConfig.serialLogDir) && !existsSync(join(root, 'escaped.json')), 'id rejection must precede all filesystem writes');

	const ageConfig = configFor('age');
	const expired = session(now - SERIAL_LOG_MAX_AGE_MS - 1);
	expired.startedAt = iso(now);
	const expiredPath = fixture(ageConfig, expired);
	const expiredBackup = fixture(ageConfig, session(now - SERIAL_LOG_MAX_AGE_MS - 2), { primary: false });
	const boundary = fixture(ageConfig, session(now - SERIAL_LOG_MAX_AGE_MS));
	const young = fixture(ageConfig, session(now - SERIAL_LOG_MAX_AGE_MS + 1000));
	check(pruneSerialSessionLogs(ageConfig, now).removed === 2, '30-day age pruning must include backup-only entries and use generated timestamps');
	check(!existsSync(expiredPath) && !existsSync(`${expiredPath}.bak`) && !existsSync(`${expiredBackup}.bak`), 'expired records must lose both copies');
	check(existsSync(boundary) && existsSync(young), 'the exact 30-day boundary and younger records must be retained');
	throws(() => persistSerialSessionLog(invalidConfig, expired), 'expired sessions must be refused before writing');

	const countConfig = configFor('count');
	for (let index = 0; index < SERIAL_LOG_MAX_ENTRIES + 4; index += 1) {
		const newest = session(now - (SERIAL_LOG_MAX_ENTRIES + 4 - index) * 1000);
		persistSerialSessionLog(countConfig, newest);
	}
	check(listSerialSessionLogs(countConfig).length === SERIAL_LOG_MAX_ENTRIES, 'retention must keep exactly the newest 50 entries');
	const countAdded = session(now);
	observeWrites(countConfig, () => persistSerialSessionLog(countConfig, countAdded));
	observeWrites(countConfig, () => persistSerialSessionLog(countConfig, countAdded));
	check(diskUsage(countConfig).entries === SERIAL_LOG_MAX_ENTRIES, 'replacement must not consume a second entry slot');

	const quotaConfig = configFor('quota');
	const largeSize = 4 * 1024 * 1024;
	const oldest = fixture(quotaConfig, session(now - 3000), { size: largeSize });
	fixture(quotaConfig, session(now - 2000), { size: largeSize });
	fixture(quotaConfig, session(now - 1000), { size: largeSize });
	check(pruneSerialSessionLogs(quotaConfig, now).removed === 1, 'quota must count primary plus backup, not just primary');
	check(!existsSync(oldest) && !existsSync(`${oldest}.bak`) && diskUsage(quotaConfig).bytes === largeSize * 4, 'quota eviction must remove the oldest pair');
	const orphan = fixture(quotaConfig, session(now), { size: 5 * 1024 * 1024, primary: false });
	pruneSerialSessionLogs(quotaConfig, now);
	check(existsSync(`${orphan}.bak`) && diskUsage(quotaConfig).bytes <= SERIAL_LOG_MAX_TOTAL_BYTES, 'backup-only bytes must participate in quota eviction');

	const asymmetricConfig = configFor('asymmetric');
	for (let index = 0; index < 3; index += 1) fixture(asymmetricConfig, session(now - index * 1000), { size: 1024 * 1024, backupSize: 7 * 1024 * 1024 });
	check(pruneSerialSessionLogs(asymmetricConfig, now).removed === 1 && diskUsage(asymmetricConfig).bytes === 16 * 1024 * 1024, 'quota must measure unequal backup sizes, not assume two primary-sized copies');

	const reserveConfig = configFor('reserve');
	fixture(reserveConfig, session(now - 1000), { size: SERIAL_LOG_MAX_TOTAL_BYTES / 2 - 128 });
	const reserved = observeWrites(reserveConfig, () => persistSerialSessionLog(reserveConfig, session()));
	check(diskUsage(reserveConfig).entries === 1, 'incoming copies must reserve space and evict old records before writing');
	const updated = { ...reserved.log, details: 'x'.repeat(4 * 1024 * 1024) };
	fixture(reserveConfig, session(now - 500), { size: 6 * 1024 * 1024 });
	observeWrites(reserveConfig, () => persistSerialSessionLog(reserveConfig, updated));
	check(diskUsage(reserveConfig).bytes <= SERIAL_LOG_MAX_TOTAL_BYTES, 'replacement staging must remain within the byte quota');
	const original = readFileSync(reserved.path, 'utf8');
	throws(() => persistSerialSessionLog(reserveConfig, { ...updated, details: 'x'.repeat(SERIAL_LOG_MAX_TOTAL_BYTES / 2) }), 'oversized non-transcript data must be rejected before writing');
	check(readFileSync(reserved.path, 'utf8') === original && readFileSync(`${reserved.path}.bak`, 'utf8') === original, 'oversized replacement must leave both existing copies intact');
	check(!readdirSync(reserveConfig.serialLogDir).some((name) => name.endsWith('.tmp')), 'normal persistence must leave no staging files');

	const unrelatedConfig = configFor('unrelated');
	mkdirSync(unrelatedConfig.serialLogDir);
	const unrelated = ['settings.json', 'settings.json.bak', 'other.json', `${log.id}.json.extra`, `.-${log.id.slice(-36)}.json`];
	for (const name of unrelated) writeFileSync(join(unrelatedConfig.serialLogDir, name), 'unrelated', 'utf8');
	const directoryLog = session(now + 2);
	const directoryName = join(unrelatedConfig.serialLogDir, `${directoryLog.id}.json`);
	mkdirSync(directoryName);
	const directoryMarker = join(directoryName, 'keep.txt');
	writeFileSync(directoryMarker, 'keep', 'utf8');
	const managed = fixture(unrelatedConfig, session());
	pruneSerialSessionLogs(unrelatedConfig, now + SERIAL_LOG_MAX_AGE_MS + 1);
	check(!existsSync(managed) && !existsSync(`${managed}.bak`), 'pruning must still remove expired generated files');
	for (const name of unrelated) check(readFileSync(join(unrelatedConfig.serialLogDir, name), 'utf8') === 'unrelated', 'pruning must not touch unrelated JSON files');
	throws(() => persistSerialSessionLog(unrelatedConfig, directoryLog), 'persistence must not replace a non-file destination');
	throws(() => clearSerialSessionLogs(unrelatedConfig), 'clear must require confirm=true');
	check(clearSerialSessionLogs(unrelatedConfig, { confirm: true }).removed === 0, 'clear must ignore unrelated files and generated-name directories');
	check(existsSync(directoryMarker), 'generated-name directories and their contents must remain untouched');
	for (const name of unrelated) check(readFileSync(join(unrelatedConfig.serialLogDir, name), 'utf8') === 'unrelated', 'clear must not touch unrelated JSON files');

	const linksConfig = configFor('links');
	mkdirSync(linksConfig.serialLogDir);
	const linkedLog = session();
	const target = join(root, 'outside.json');
	const targetBody = JSON.stringify(linkedLog);
	writeFileSync(target, targetBody, 'utf8');
	const linkedPath = join(linksConfig.serialLogDir, `${linkedLog.id}.json`);
	if (link(target, linkedPath)) {
		check(readSerialSessionLog(linksConfig, linkedLog.id) === null && listSerialSessionLogs(linksConfig).length === 0, 'reads/listing must not follow a primary symlink');
		throws(() => persistSerialSessionLog(linksConfig, linkedLog), 'persistence must refuse a symlink primary');
		fixture(linksConfig, linkedLog, { primary: false });
		check(readSerialSessionLog(linksConfig, linkedLog.id)?.id === linkedLog.id, 'safe regular backups must remain recoverable beside a symlink primary');
		check(clearSerialSessionLogs(linksConfig, { confirm: true }).removed === 1 && lstatSync(linkedPath).isSymbolicLink(), 'clear must remove the regular backup but leave the symlink untouched');
	}
	const backupLog = session(now + 1);
	const backupPath = fixture(linksConfig, backupLog, { backup: false });
	if (link(target, `${backupPath}.bak`)) {
		throws(() => persistSerialSessionLog(linksConfig, backupLog), 'persistence must refuse a symlink backup');
		writeFileSync(backupPath, '{broken', 'utf8');
		check(readSerialSessionLog(linksConfig, backupLog.id) === null, 'recovery must not follow a backup symlink');
		clearSerialSessionLogs(linksConfig, { confirm: true });
		check(lstatSync(`${backupPath}.bak`).isSymbolicLink(), 'clear must leave backup symlinks untouched');
	}
	const linkedDirectory = configFor('linked-directory');
	if (link(linksConfig.serialLogDir, linkedDirectory.serialLogDir, process.platform === 'win32' ? 'junction' : 'dir')) {
		throws(() => persistSerialSessionLog(linkedDirectory, session()), 'persistence must refuse a symlink log directory');
		check(clearSerialSessionLogs(linkedDirectory, { confirm: true }).removed === 0, 'clear must not traverse a symlink log directory');
	}
	check(readFileSync(target, 'utf8') === targetBody, 'symlink targets must remain byte-for-byte unchanged');
	const cleared = clearSerialSessionLogs(config, { confirm: true });
	check(cleared.removed === 1 && listSerialSessionLogs(config).length === 0, 'confirmed clear must remove a managed pair');
} finally {
	if (!isAbsolute(root) || !root.startsWith(join(tmpdir(), 'ascend-serial-log-'))) throw new Error('Unexpected test cleanup path.');
	rmSync(root, { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error(`serial-log test FAILED (${failures.length}/${checks})`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`serial-log test passed (${checks} checks; ${skipped} skipped symlink groups)`);
