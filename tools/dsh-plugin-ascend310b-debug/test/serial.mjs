/**
 * Offline serial parser and process-lifecycle tests. All sessions use injected
 * child processes; optional PowerShell checks parse or substitute a fake port.
 * These tests never enumerate, open, or write a real COM device.
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { SERIAL_LOG_MAX_TRANSCRIPT_BYTES } from '../lib/serial-log.js';

import {
	cleanSerialOutput,
	extractMarkedOutput,
	formatSerialError,
	parseSerialNetwork,
	parseSerialPortList,
	runSerialCommand,
	runSerialSession,
	serialSessionTimeoutMs,
	validateSerialSettings
} from '../lib/serial.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => check(actual === expected, `${message}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);

const transcript = [
	'orangepiaipro-20t login: ',
	'Password: ',
	'ASC_TOKEN_BEGIN_1',
	'orangepiaipro-20t',
	'ASC_TOKEN_END_1',
	'ASC_TOKEN_BEGIN_2',
	'10.0.0.101 172.17.0.1',
	'ASC_TOKEN_END_2',
	'ASC_TOKEN_BEGIN_3',
	'2: wlan0    inet 10.0.0.101/24 brd 10.0.0.255 scope global wlan0',
	'3: docker0  inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0',
	'ASC_TOKEN_END_3',
	'ASC_TOKEN_BEGIN_4',
	'default via 10.0.0.1 dev wlan0 proto dhcp metric 600',
	'10.0.0.0/24 dev wlan0 proto kernel scope link src 10.0.0.101',
	'ASC_TOKEN_END_4'
].join('\r\n');

const section = (n) => extractMarkedOutput(transcript, `ASC_TOKEN_BEGIN_${n}`);
const network = parseSerialNetwork({
	hostname: section(1),
	addresses: section(2),
	interfaces: section(3),
	routes: section(4),
	macs: 'eth0=c0:74:2b:fd:dd:7a\nwlan0=c0:74:2b:fd:dd:7b\n'
}, transcript);

equal(network.hostname, 'orangepiaipro-20t', 'hostname');
equal(network.ipAddress, '10.0.0.101', 'default route IP must win over docker address');
equal(network.interfaces.length, 2, 'interfaces parsed');
equal(network.macAddress, 'c0:74:2b:fd:dd:7a', 'stable eth0 MAC should match network probe identity');
equal(network.defaultRoute, 'default via 10.0.0.1 dev wlan0 proto dhcp metric 600', 'default route');
equal(cleanSerialOutput('\u001b[32mgreen\u001b[0m\r\n'), 'green', 'ANSI/CR cleaned');
equal(extractMarkedOutput(transcript, 'ASC_TOKEN_BEGIN_2'), '10.0.0.101 172.17.0.1', 'marked output');
const candidates = parseSerialPortList('__SERIAL_LIST__{"ports":[{"port":"COM14","label":"CH343"},{"port":"COM23","label":"CH343"}]}');
equal(candidates.length, 2, 'auto candidate list parsed');
equal(candidates[0].port, 'COM14', 'first candidate port');

check(validateSerialSettings({ port: 'auto', baudRate: 115200, user: 'HwHiAiUser', password: 'x', timeoutMs: 1000 }).ok, 'auto serial setting');
check(validateSerialSettings({ port: 'COM23', baudRate: 115200, user: 'HwHiAiUser', password: 'x', timeoutMs: 1000 }).ok, 'valid Windows serial setting');
check(validateSerialSettings({ port: '/dev/ttyUSB0', baudRate: 115200, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'valid POSIX serial setting');
check(!validateSerialSettings({ port: 'COM', baudRate: 115200, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'invalid port rejected');
check(!validateSerialSettings({ port: 'COM23', baudRate: 0, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'invalid baud rejected');
check(
	formatSerialError('COM7', '<S S="Error">UnauthorizedAccessException: access denied</S>').includes('占用'),
	'busy serial errors must be explained without CLIXML'
);

equal(serialSessionTimeoutMs({ timeoutMs: 60000 }, 5), 145000, 'serial session has a bounded host deadline');
equal(serialSessionTimeoutMs({ timeoutMs: 120000 }, 100), 300000, 'serial host deadline is capped');
const cancelled = new AbortController();
cancelled.abort();
try {
	await runSerialSession({
		settings: { port: 'COM23', baudRate: 115200, user: 'HwHiAiUser', password: 'x', timeoutMs: 1000 },
		commands: ['hostname'],
		signal: cancelled.signal
	});
	failures.push('an already-cancelled serial session must reject before spawning');
} catch (error) {
	equal(error?.code, 'ABORT_ERR', 'serial cancellation code');
}

const settings = { port: 'COM23', baudRate: 115200, user: 'HwHiAiUser', password: 'offline-private-password', timeoutMs: 1000 };
const at = '2026-11-01T00:00:00.000Z';
const event = (phase, status, extra = {}) => `__ASC_SERIAL_EVENT__${JSON.stringify({ at, phase, status, detail: `${phase} ${status}`, data: { port: settings.port }, ...extra })}\n`;
const progress = (text) => event('transcript', 'running', { text });
const resultLine = (status = 0, text = 'board\n', newline = '\n') => `__ASC_SERIAL_RESULT__${JSON.stringify({ identity: 'HwHiAiUser', sections: { command1: 'board', status1: String(status) }, transcript: text })}${newline}`;
let scriptCapture;
let behavioralTests = 0;
const test = async (name, run) => {
	try { await run(); behavioralTests += 1; }
	catch (error) { failures.push(`${name}: ${error.stack ?? error}`); }
};
function fakeChild() {
	const child = new EventEmitter();
	child.stdout = new PassThrough();
	child.stderr = new PassThrough();
	child.killCount = 0;
	child.kill = () => {
		child.killCount += 1;
		child.emit('close', null, 'SIGTERM');
		return true;
	};
	return child;
}
function session(options = {}) {
	const child = fakeChild();
	const events = [];
	const promise = runSerialSession({
		settings, commands: ['hostname'], onEvent: (value) => events.push(value),
		spawnImpl: (executable, args, spawnOptions) => {
			scriptCapture = { executable, args, options: spawnOptions, script: Buffer.from(args.at(-1), 'base64').toString('utf16le') };
			return child;
		},
		...options
	});
	return { child, events, promise };
}
const opened = () => event('port-open', 'running') + event('port-open', 'ok', { durationMs: 3 });
const commandEvents = (status = 0) => event('command', 'running', { data: { index: 1 } }) + event('command', status === 0 ? 'ok' : 'failed', { data: { index: 1, exitCode: status }, durationMs: 17 });

await test('port-open failure reports no unattempted phases', async () => {
	const run = session();
	assert.deepEqual(run.events, [], 'nothing happened before helper telemetry');
	run.child.stdout.write(event('port-open', 'running'));
	assert.deepEqual(run.events.map((entry) => entry.phase), ['port-open']);
	run.child.stdout.write(event('port-open', 'failed', { detail: 'AccessDenied', data: { code: 'SERIAL_HELPER_FAILED', retryable: true }, durationMs: 9 }));
	run.child.emit('close', 1);
	await assert.rejects(run.promise, (error) => error.serialPhase === 'port-open' && error.serialTranscript === '' && error.serialRetryable === true);
	assert.deepEqual(run.events.map((entry) => [entry.phase, entry.status]), [['port-open', 'running'], ['port-open', 'failed']]);
	assert.equal(run.events[1].durationMs, 9);
	assert.equal(run.events[1].at, at);
	assert.equal(run.events[1].data.port, 'COM23');
});

await test('failed prompt retains streamed partial output and actual phase', async () => {
	const run = session();
	run.child.stdout.write(opened() + event('prompt', 'running'));
	run.child.stdout.write(progress('\u001b[32mBooting board\u001b[0m\r\npartial login prompt'));
	assert.equal(run.events.at(-1).text, 'Booting board\npartial login prompt');
	run.child.stdout.write(event('prompt', 'failed', { detail: 'serial-timeout waiting for expected prompt', data: { code: 'SERIAL_PROMPT_TIMEOUT', retryable: true }, durationMs: 1001 }));
	run.child.stderr.write('AccessDenied: misleading stderr must not guess another phase');
	run.child.emit('close', 1);
	await assert.rejects(run.promise, (error) => {
		assert.equal(error.serialPhase, 'prompt');
		assert.equal(error.code, 'SERIAL_PROMPT_TIMEOUT');
		assert.equal(error.serialTranscript, 'Booting board\npartial login prompt');
		return true;
	});
	assert(!run.events.some((entry) => ['authenticate', 'identity', 'command'].includes(entry.phase)));
	assert.equal(run.events.filter((entry) => entry.phase === 'prompt' && entry.status === 'failed').length, 1);
});

await test('cancellation kills helper and preserves bounded Unicode transcript', async () => {
	const controller = new AbortController();
	const run = session({ signal: controller.signal });
	run.child.stdout.write(opened() + event('prompt', 'running'));
	run.child.stdout.write(progress(`${'板'.repeat(40000)}\npartial prompt`));
	controller.abort();
	await assert.rejects(run.promise, (error) => {
		assert.equal(error.code, 'ABORT_ERR');
		assert.equal(error.serialPhase, 'prompt');
		assert(error.serialTranscript.endsWith('partial prompt'));
		assert(Buffer.byteLength(error.serialTranscript, 'utf8') <= SERIAL_LOG_MAX_TRANSCRIPT_BYTES);
		assert(!error.serialTranscript.includes('\ufffd'));
		return true;
	});
	assert.equal(run.child.killCount, 1);
	assert.equal(run.events.at(-1).status, 'cancelled');
	const before = run.events.length;
	run.child.stdout.write(event('command', 'running', { data: { index: 1 } }));
	assert.equal(run.events.length, before, 'late child output must be ignored');
});

await test('host deadline records the active phase and clears injected timer', async () => {
	let timeout;
	let cleared = 0;
	const run = session({ timers: {
		setTimeout: (callback, delay) => { assert.equal(delay, serialSessionTimeoutMs(settings, 1)); timeout = callback; return 42; },
		clearTimeout: (handle) => { assert.equal(handle, 42); cleared += 1; }
	} });
	run.child.stdout.write(opened() + event('identity', 'running') + progress('identity response pending'));
	timeout();
	await assert.rejects(run.promise, (error) => error.code === 'SERIAL_TIMEOUT' && error.serialPhase === 'identity' && error.serialTranscript === 'identity response pending');
	assert.equal(run.child.killCount, 1);
	assert.equal(cleared, 1);
	assert.equal(run.events.at(-1).status, 'timeout');
});

await test('split and coalesced UTF-8 telemetry preserves order and redacts callbacks', async () => {
	const run = session();
	const output = opened() + event('prompt', 'running') + event('prompt', 'ok', { durationMs: 4 }) +
		event('identity', 'running') + event('identity', 'ok', { durationMs: 8 }) +
		progress(`板卡\u001b[31m ready\u001b[0m\n${settings.password}\nBearer unsafe-bearer-value`) +
		event('command', 'running', { detail: `\u001b[31m${settings.password}\u001b[0m token=unsafe-token-value`, data: { index: 1, password: settings.password, command: settings.password } }) +
		event('command', 'ok', { data: { index: 1, exitCode: 0 }, durationMs: 17 }) +
		`unstructured secret ${settings.password}\n` + resultLine(0, '板卡 ready\n', '');
	const bytes = Buffer.from(output.replaceAll('\n', '\r\n'));
	for (let offset = 0, width = 1; offset < bytes.length; width = width % 31 + 1) {
		run.child.stdout.write(bytes.subarray(offset, offset + width));
		offset += width;
	}
	run.child.emit('close', 0);
	const result = await run.promise;
	assert.equal(result.identity, settings.user);
	assert.equal(result.transcript, '板卡 ready\n');
	assert.equal(result.sections.status1, '0');
	assert.equal(run.events.filter((entry) => entry.phase === 'command').length, 2);
	assert.equal(run.events.at(-1).durationMs, 17);
	assert.equal(run.events.at(-1).data.index, 1);
	assert(!run.events.some((entry) => entry.phase === 'authenticate'), 'pre-authenticated shell must not claim a login');
	const captured = JSON.stringify(run.events);
	for (const secret of [settings.password, 'unsafe-bearer-value', 'unsafe-token-value']) assert(!captured.includes(secret));
	assert(!captured.includes('\\u001b'));
	assert(!captured.includes('"password":'));
	assert(!captured.includes('"command":'));
});

await test('snapshot redaction withholds a secret prefix split across reads', async () => {
	const run = session();
	run.child.stdout.write(event('prompt', 'running') + progress('output offline-private-'));
	assert(!run.events.at(-1).text.includes('offline-private-'));
	run.child.stdout.write(progress(`output ${settings.password}\n`));
	run.child.emit('close', 1);
	await assert.rejects(run.promise, (error) => !error.serialTranscript.includes(settings.password));
	assert(!JSON.stringify(run.events).includes(settings.password));
});

await test('nonzero command status is never reported ok', async () => {
	const child = fakeChild();
	const events = [];
	const promise = runSerialCommand(settings, 'hostname', { spawnImpl: () => child, onEvent: (entry) => events.push(entry) });
	child.stdout.write(opened() + commandEvents(17) + resultLine(17));
	child.emit('close', 0);
	const result = await promise;
	assert.equal(result.exitCode, 17);
	assert(events.some((entry) => entry.phase === 'command' && entry.status === 'failed' && entry.data.exitCode === 17));
	assert(!events.some((entry) => entry.phase === 'command' && entry.status === 'ok'));
	const malformed = session();
	malformed.child.stdout.write(event('command', 'ok', { data: { index: 1, exitCode: 2 } }) + resultLine(2));
	malformed.child.emit('close', 0);
	await malformed.promise;
	assert.equal(malformed.events[0].status, 'failed', 'even inconsistent helper telemetry must fail closed');
});

await test('nonzero helper exit cannot be masked by an earlier result', async () => {
	const run = session();
	run.child.stdout.write(opened() + event('identity', 'running') + resultLine());
	run.child.emit('close', 1);
	await assert.rejects(run.promise, (error) => error.serialPhase === 'identity');
	assert.equal(run.events.at(-1).status, 'failed');
});

await test('spawn failures do not fabricate a port-open attempt', async () => {
	const run = session({ spawnImpl: () => { throw new Error(`spawn failed ${settings.password}`); } });
	await assert.rejects(run.promise, (error) => error.code === 'SERIAL_SPAWN_FAILED' && error.serialPhase === 'session' && !error.message.includes(settings.password));
	assert.deepEqual(run.events.map((entry) => entry.phase), ['session']);
});

await test('telemetry callback exceptions cannot change serial outcome', async () => {
	const run = session({ onEvent: () => { throw new Error('logger failure'); } });
	run.child.stdout.write(opened() + commandEvents() + resultLine());
	run.child.emit('close', 0);
	assert.equal((await run.promise).sections.status1, '0');
});

await test('already-aborted sessions never spawn or emit phase evidence', async () => {
	const controller = new AbortController();
	controller.abort();
	const run = session({ signal: controller.signal, spawnImpl: () => { assert.fail('spawned an aborted request'); } });
	await assert.rejects(run.promise, (error) => error.code === 'ABORT_ERR' && error.serialTranscript === '');
	assert.deepEqual(run.events, []);
});

const autoSettings = { ...settings, port: 'auto', allowAutoLogin: true };
const listPortsImpl = async () => [{ port: 'COM23' }, { port: 'COM24' }];
await test('password prompt mismatch stops auto-port retries', async () => {
	let spawns = 0;
	const promise = runSerialCommand(autoSettings, 'hostname', { listPortsImpl, spawnImpl: () => {
		spawns += 1;
		const child = fakeChild();
		queueMicrotask(() => {
			child.stdout.write(opened() + event('prompt', 'running') + progress('Password: ') + event('prompt', 'failed', { data: { code: 'SERIAL_PASSWORD_PROMPT_MISMATCH', retryable: false } }));
			child.emit('close', 1);
		});
		return child;
	} });
	await assert.rejects(promise, (error) => error.code === 'SERIAL_PASSWORD_PROMPT_MISMATCH' && error.serialPort === 'COM23' && error.serialTranscript === 'Password: ');
	assert.equal(spawns, 1);
});

await test('auto-port cancellation propagates without retry or aggregation', async () => {
	let spawns = 0;
	const controller = new AbortController();
	let child;
	const promise = runSerialCommand(autoSettings, 'hostname', { listPortsImpl, signal: controller.signal, spawnImpl: () => {
		spawns += 1;
		child = fakeChild();
		queueMicrotask(() => { child.stdout.write(opened() + event('prompt', 'running') + progress('partial banner')); controller.abort(); });
		return child;
	} });
	await assert.rejects(promise, (error) => error.code === 'ABORT_ERR' && error.serialTranscript === 'partial banner');
	assert.equal(spawns, 1);
	assert.equal(child.killCount, 1);
});

await test('only proven pre-auth failures can advance auto-port attempts', async () => {
	const ports = [];
	const promise = runSerialCommand(autoSettings, 'hostname', { listPortsImpl, spawnImpl: (_exe, _args, options) => {
		ports.push(options.env.ASC_SERIAL_PORT);
		const child = fakeChild();
		const attempt = ports.length;
		queueMicrotask(() => {
			if (attempt === 1) {
				child.stdout.write(event('port-open', 'running') + event('port-open', 'failed', { data: { code: 'SERIAL_HELPER_FAILED', retryable: true } }));
				child.emit('close', 1);
			} else { child.stdout.write(opened() + commandEvents() + resultLine()); child.emit('close', 0); }
		});
		return child;
	} });
	assert.equal((await promise).port, 'COM24');
	assert.deepEqual(ports, ['COM23', 'COM24']);
});

if (process.platform === 'win32' && scriptCapture) {
	let powershellAvailable = false;
	await test('generated PowerShell parses without opening a device', async () => {
		const parser = String.raw`
$tokens = $null
$errors = $null
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:ASC_TEST_SCRIPT))
[void][System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; exit 1 }
`;
		const parsed = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', parser], {
			env: { ...process.env, ASC_TEST_SCRIPT: Buffer.from(scriptCapture.script).toString('base64') }, encoding: 'utf8', timeout: 15000
		});
		if (parsed.error?.code === 'ENOENT') { console.log('  PowerShell unavailable: parser check skipped'); return; }
		assert.ifError(parsed.error);
		powershellAvailable = true;
		assert.equal(parsed.status, 0, parsed.stderr || parsed.stdout);
		assert(!scriptCapture.args.join(' ').includes(settings.password));
		assert.equal(scriptCapture.options.env.ASC_SERIAL_PASSWORD, settings.password);
		assert(scriptCapture.args.join(' ').length < 32000, 'encoded helper must fit Windows command-line limit');
	});
	// Run the actual helper control flow with an in-memory port, never SerialPort.
	const fakePortHarness = String.raw`
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO.Ports;
public class OfflineSerialPort : IDisposable {
  public int ReadTimeout { get; set; }
  public int WriteTimeout { get; set; }
  public string NewLine { get; set; }
  public bool DtrEnable { get; set; }
  public bool RtsEnable { get; set; }
  public bool IsOpen { get; private set; }
  private readonly Queue<string> pending = new Queue<string>();
  private readonly string scenario = Environment.GetEnvironmentVariable("ASC_TEST_SCENARIO");
  private readonly string token = Environment.GetEnvironmentVariable("ASC_SERIAL_TOKEN");
  public OfflineSerialPort(string port, int baud, Parity parity, int bits, StopBits stopBits) {
    if (port != "COM23" || baud != 115200 || parity != Parity.None || bits != 8 || stopBits != StopBits.One) throw new Exception("serial parameters changed");
  }
  public int BytesToRead { get { return pending.Count == 0 ? 0 : pending.Peek().Length; } }
  public string ReadExisting() { return pending.Dequeue(); }
  public void Open() {
    if (ReadTimeout != 250 || WriteTimeout != 1500 || !DtrEnable || !RtsEnable) throw new Exception("serial settings changed");
    if (scenario == "open-failure") throw new Exception("offline-open-failure");
    IsOpen = true;
  }
  public void Close() { IsOpen = false; Console.Error.WriteLine("OFFLINE_CLOSED"); }
  public void Dispose() { Console.Error.WriteLine("OFFLINE_DISPOSED"); }
  public void Write(string value) {
    if (value == "\r") {
      if (scenario == "prompt-timeout") pending.Enqueue("Bootloader partial banner");
      else if (scenario == "stale-password") pending.Enqueue("Password: ");
      else if (scenario == "password-mismatch" || scenario == "authentication-echo") pending.Enqueue("board login: ");
      else pending.Enqueue("HwHiAiUser@board:~$ ");
    } else if (value.Trim() == "HwHiAiUser") {
      pending.Enqueue(scenario == "password-mismatch" ? "unexpected shell$ " : "Password: ");
    } else if (value.Trim() == Environment.GetEnvironmentVariable("ASC_SERIAL_PASSWORD")) {
      Console.Error.WriteLine("OFFLINE_PASSWORD_SENT");
      var password = Environment.GetEnvironmentVariable("ASC_SERIAL_PASSWORD");
      pending.Enqueue("ECHO-SECRET-PREFIX:" + password.Substring(0, 8));
      pending.Enqueue(password.Substring(8) + "\r\nHwHiAiUser@board:~$ ");
    } else if (value.Contains("_IDENTITY_BEGIN")) {
      pending.Enqueue(token + "_IDENTITY_BEGIN\r\nHwHiAiUser\r\n" + token + "_IDENTITY_END\r\nHwHiAiUser@board:~$ ");
    } else if (value.Contains("_BEGIN_1")) {
      var code = scenario == "command-failure" ? 7 : 0;
      pending.Enqueue(token + "_BEGIN_1\r\nboard\r\n" + token + "_STATUS_1=" + code + "\r\n" + token + "_END_1\r\nHwHiAiUser@board:~$ ");
    } else { throw new Exception("unexpected offline write"); }
  }
}
'@
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:ASC_TEST_SCRIPT))
$constructor = 'New-Object System.IO.Ports.SerialPort('
if ($source.IndexOf($constructor) -lt 0 -or $source.IndexOf($constructor) -ne $source.LastIndexOf($constructor)) { throw 'expected exactly one serial constructor' }
$source = $source.Replace($constructor, 'New-Object OfflineSerialPort(')
if ($source.Contains('New-Object System.IO.Ports.SerialPort')) { throw 'real serial constructor forbidden in offline tests' }
& ([scriptblock]::Create($source))
`;
	if (powershellAvailable) {
		for (const scenario of ['open-failure', 'prompt-timeout', 'stale-password', 'password-mismatch', 'authentication-echo', 'command-failure']) {
			await test(`PowerShell helper behavior: ${scenario}`, async () => {
				const execution = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', fakePortHarness], {
					env: { ...scriptCapture.options.env, ASC_SERIAL_TIMEOUT: '500', ASC_TEST_SCRIPT: Buffer.from(scriptCapture.script).toString('base64'), ASC_TEST_SCENARIO: scenario },
					encoding: 'utf8', timeout: 15000, maxBuffer: 2 * 1024 * 1024
				});
				assert.ifError(execution.error);
				const records = execution.stdout.split(/\r?\n/u).filter((line) => line.startsWith('__ASC_SERIAL_EVENT__')).map((line) => JSON.parse(line.slice('__ASC_SERIAL_EVENT__'.length)));
				assert(records.length > 0, execution.stderr || execution.stdout);
				assert(execution.stderr.includes('OFFLINE_DISPOSED'), execution.stderr);
				assert(!execution.stdout.includes(settings.password), 'helper stdout must never contain password');
				assert(!execution.stdout.includes('ECHO-SECRET-PREFIX'), 'all password echo must be suppressed, not just the known secret');
				const completed = records.filter((entry) => entry.phase !== 'transcript' && entry.status !== 'running');
				assert(completed.every((entry) => Number.isInteger(entry.durationMs) && entry.durationMs >= 0 && Number.isFinite(Date.parse(entry.at)) && entry.data.port === 'COM23'));
				if (scenario === 'open-failure') {
					assert.equal(execution.status, 1);
					assert.deepEqual(records.map((entry) => [entry.phase, entry.status]), [['port-open', 'running'], ['port-open', 'failed']]);
					assert(!execution.stderr.includes('OFFLINE_CLOSED'));
				} else {
					assert(execution.stderr.includes('OFFLINE_CLOSED'), execution.stderr);
					if (scenario === 'prompt-timeout') {
						assert.equal(execution.status, 1);
						assert(records.some((entry) => entry.phase === 'transcript' && entry.text.includes('Bootloader partial banner')));
						assert.equal(completed.at(-1).phase, 'prompt');
						assert.equal(completed.at(-1).data.code, 'SERIAL_PROMPT_TIMEOUT');
						assert(!records.some((entry) => entry.phase === 'authenticate' || entry.phase === 'command'));
					} else if (scenario === 'stale-password' || scenario === 'password-mismatch') {
						assert.equal(execution.status, 1);
						assert.equal(completed.at(-1).data.code, 'SERIAL_PASSWORD_PROMPT_MISMATCH');
						assert.equal(completed.at(-1).data.retryable, false);
						assert(!execution.stderr.includes('OFFLINE_PASSWORD_SENT'));
						assert(!records.some((entry) => entry.phase === 'command'));
					} else {
						assert.equal(execution.status, 0, execution.stderr || execution.stdout);
						const command = completed.find((entry) => entry.phase === 'command');
						assert.equal(command.status, scenario === 'command-failure' ? 'failed' : 'ok');
						assert.equal(command.data.exitCode, scenario === 'command-failure' ? 7 : 0);
						assert.equal(command.data.index, 1);
						assert(records.some((entry) => entry.phase === 'identity' && entry.status === 'ok'));
						assert.equal(records.some((entry) => entry.phase === 'authenticate'), scenario === 'authentication-echo');
					}
				}
			});
		}
	}
}

if (failures.length > 0) {
	console.error(`serial test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('serial test passed');
console.log(`  parsed ${network.interfaces.length} interfaces, ip=${network.ipAddress}`);
console.log(`  ${behavioralTests} offline behavior/syntax checks passed`);
