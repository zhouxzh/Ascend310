/**
 * Serial-console transport for an Ascend 310B board.
 *
 * This is deliberately a host-side helper: the Windows host owns the CH343
 * COM port, while the board owns the Linux shell. It uses .NET's SerialPort
 * through PowerShell so the plugin does not need a native Node dependency.
 * Only read-only probe commands are sent; the password is passed by environment
 * variable and is never included in the command line or returned output.
 */

import { spawn } from 'node:child_process';
import { accessSync, constants as fsConstants, readdirSync } from 'node:fs';
import { StringDecoder } from 'node:string_decoder';
import { redactSerialLogText, SERIAL_LOG_MAX_TRANSCRIPT_BYTES } from './serial-log.js';

const DEFAULT_BAUD = 115200;
const DEFAULT_TIMEOUT = 60000;
const MAX_TIMEOUT = 120000;
const MAX_SESSION_TIMEOUT = 300000;
const SERIAL_ENUMERATION_TIMEOUT = 15000;
const SERIAL_PORT = /^(?:COM\d+|\/dev\/tty[A-Za-z0-9._-]+)$/iu;
const SERIAL_USER = /^[A-Za-z0-9._-]+$/u;
const EVENT_MARKER = '__ASC_SERIAL_EVENT__';
const RESULT_MARKER = '__ASC_SERIAL_RESULT__';
const MAX_PROTOCOL_LINE = 2 * 1024 * 1024;
const SESSION_PHASES = new Set(['port-open', 'prompt', 'authenticate', 'identity', 'command']);

function deliverSerialEvent(onEvent, event) {
	if (typeof onEvent !== 'function') return;
	try {
		onEvent(event);
	} catch {
		// Logging/telemetry callbacks must never alter the serial operation.
	}
}

function emitSerialEvent(onEvent, phase, status = 'ok', detail = '', data = null) {
	deliverSerialEvent(onEvent, { at: new Date().toISOString(), phase, status, detail, ...(data === null ? {} : { data }) });
}

function redactSerialText(value, password) {
	const secrets = [password, cleanSerialOutput(password)].filter(Boolean);
	let text = redactSerialLogText(value, secrets).replace(/\r/gu, '');
	// A read may end halfway through an echoed secret. Do not publish that prefix.
	for (const secret of secrets) {
		for (let size = Math.min(secret.length - 1, text.length); size > 0; size -= 1) {
			if (text.endsWith(secret.slice(0, size))) {
				text = `${text.slice(0, -size)}<redacted>`;
				break;
			}
		}
	}
	return text;
}

function boundedTranscript(value) {
	const bytes = Buffer.from(value, 'utf8');
	if (bytes.length <= SERIAL_LOG_MAX_TRANSCRIPT_BYTES) return value;
	let start = bytes.length - SERIAL_LOG_MAX_TRANSCRIPT_BYTES;
	while ((bytes[start] & 0xc0) === 0x80) start += 1;
	return bytes.subarray(start).toString('utf8');
}

function serialAbortError() {
	const error = new Error('串口会话已取消。');
	error.code = 'ABORT_ERR';
	error.serialTranscript = '';
	return error;
}

/** Commands used by `ascend_serial_probe`; all are read-only. */
export const SERIAL_PROBE_COMMANDS = Object.freeze([
	'hostname',
	'hostname -I',
	'ip -4 -o addr show scope global',
	'ip -4 route',
	'for p in /sys/class/net/*; do if [ -r "$p/address" ]; then printf "%s=" "$(basename "$p")"; cat "$p/address"; fi; done'
]);

/** Redact control/ANSI noise while retaining shell output. */
export function cleanSerialOutput(value) {
	return String(value ?? '')
		.replace(/\r/gu, '')
		.replace(/\u001b\[[0-?]*[ -/]*[@-~]/gu, '')
		.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu, '')
		.trim();
}

/** Format a serial helper failure without leaking raw CLIXML noise. */
export function formatSerialError(port, text) {
	const detail = cleanSerialOutput(text);
	if (/UnauthorizedAccessException|AccessDenied|access.*denied|拒绝访问|访问.*拒绝|被占用|being used/iu.test(detail)) {
		return `串口 ${port} 被其他程序占用或当前用户没有访问权限；请关闭串口终端、释放 COM 口后重试。`;
	}
	if (/does not exist|not found|不存在|没有枚举|IOException/iu.test(detail)) {
		return `串口 ${port} 不存在或没有枚举成功；请重新插拔 USB 串口线。`;
	}
	return detail.slice(0, 1000) || '串口辅助进程没有返回错误信息。';
}

/** Parse one command's output from a serial transcript. */
export function extractMarkedOutput(transcript, marker, endMarker = null) {
	const text = cleanSerialOutput(transcript);
	const begin = text.indexOf(marker);
	if (begin < 0) return '';
	const closing = endMarker ?? (marker.includes('_BEGIN_') ? marker.replace('_BEGIN_', '_END_') : marker);
	const end = text.indexOf(closing, begin + marker.length);
	if (end < 0) return text.slice(begin + marker.length);
	return text.slice(begin + marker.length, end).trim();
}

/**
 * Parse the network facts from the four default probe commands.
 *
 * The parser is intentionally conservative: if a line does not look like an
 * `ip -o` record or a route, it is retained in the raw text but not promoted
 * to a structured field.
 */
export function parseSerialNetwork(sections, rawText = '') {
	const hostname = cleanSerialOutput(sections?.hostname).split(/\s+/u)[0] || null;
	const addressText = cleanSerialOutput(sections?.addresses);
	const ipv4 = [...addressText.matchAll(/\b(?:\d{1,3}\.){3}\d{1,3}\b/gu)]
		.map((match) => match[0])
		.filter((address) => !address.startsWith('127.') && !address.startsWith('169.254.'));
	const interfaces = [];
	for (const line of cleanSerialOutput(sections?.interfaces).split('\n')) {
		const match = /^\s*\d+:\s+(\S+)\s+inet\s+([^/\s]+)\/\d+/u.exec(line);
		if (match !== null) interfaces.push({ name: match[1], ipv4: match[2] });
	}
	const routes = cleanSerialOutput(sections?.routes)
		.split('\n')
		.map((line) => line.trim())
		.filter(Boolean);
	const defaultRoute = routes.find((line) => line.startsWith('default ')) ?? null;
	let ipAddress = null;
	let defaultDevice = null;
	if (defaultRoute !== null) {
		defaultDevice = /\bdev\s+(\S+)/u.exec(defaultRoute)?.[1] ?? null;
		ipAddress = interfaces.find((entry) => entry.name === defaultDevice)?.ipv4 ?? null;
	}
	const interfaceMacs = {};
	for (const line of cleanSerialOutput(sections?.macs).split('\n')) {
		const match = /^([A-Za-z0-9_.:-]+)=([0-9a-f]{2}(?::[0-9a-f]{2}){5})$/iu.exec(line.trim());
		if (match !== null) interfaceMacs[match[1]] = match[2].toLowerCase();
	}
	const macAddress = interfaceMacs.eth0
		?? (defaultDevice === null ? null : interfaceMacs[defaultDevice])
		?? interfaceMacs.wlan0
		?? null;
	if (ipAddress === null) {
		// Avoid Docker bridges when no route is available, then fall back to the
		// first non-loopback address reported by `hostname -I`.
		ipAddress = ipv4.find((address) => /^192\.168\./u.test(address))
			?? ipv4.find((address) => /^10\./u.test(address))
			?? ipv4.find((address) => !/^172\.17\./u.test(address))
			?? ipv4[0]
			?? null;
	}
	return {
		hostname,
		ipAddress,
		hostnameIps: ipv4,
		interfaces,
		interfaceMacs,
		macAddress,
		defaultRoute,
		routes,
		rawText: cleanSerialOutput(rawText)
	};
}

/** Validate serial connection settings without exposing the password. */
export function validateSerialSettings(settings) {
	if (typeof settings?.port !== 'string' || (settings.port !== 'auto' && !SERIAL_PORT.test(settings.port))) {
		return { ok: false, reason: '串口必须是 auto、COM<N> 或 /dev/tty...，例如 COM23。' };
	}
	if (!Number.isInteger(settings.baudRate) || settings.baudRate < 1200 || settings.baudRate > 921600) {
		return { ok: false, reason: 'baudRate 必须在 1200 到 921600 之间。' };
	}
	if (typeof settings.user !== 'string' || !SERIAL_USER.test(settings.user)) {
		return { ok: false, reason: '串口用户名只能包含字母、数字、点、下划线或短横线。' };
	}
	if (typeof settings.password !== 'string' || settings.password === '') {
		return { ok: false, reason: '串口密码为空；请放在 profile 的 serial.password。' };
	}
	if (!Number.isInteger(settings.timeoutMs) || settings.timeoutMs < 1000 || settings.timeoutMs > MAX_TIMEOUT) {
		return { ok: false, reason: `timeoutMs 必须在 1000 到 ${MAX_TIMEOUT} 之间。` };
	}
	return { ok: true, reason: '串口设置有效。' };
}

/** Return a safe copy of settings for reports. */
export function publicSerialSettings(settings) {
	return {
		port: settings.port,
		baudRate: settings.baudRate,
		user: settings.user,
		timeoutMs: settings.timeoutMs
	};
}

/** Bound the complete host-side session independently of per-prompt waits. */
export function serialSessionTimeoutMs(settings, commandCount) {
	const promptTimeout = Number.isInteger(settings?.timeoutMs) ? settings.timeoutMs : DEFAULT_TIMEOUT;
	const commands = Number.isInteger(commandCount) && commandCount > 0 ? commandCount : 1;
	return Math.min(
		MAX_SESSION_TIMEOUT,
		Math.max(10000, promptTimeout + commands * Math.min(promptTimeout, 15000) + 10000)
	);
}

/** Parse the machine-readable candidate list emitted by the Windows helper. */
export function parseSerialPortList(text) {
	const marker = '__SERIAL_LIST__';
	const at = String(text ?? '').lastIndexOf(marker);
	if (at < 0) return [];
	try {
		const value = JSON.parse(String(text).slice(at + marker.length).trim());
		const ports = Array.isArray(value?.ports) ? value.ports : [];
		return ports.filter((item) => typeof item?.port === 'string' && SERIAL_PORT.test(item.port));
	} catch {
		return [];
	}
}

/**
 * List present USB serial candidates. Bluetooth ports and unrelated serial
 * devices are excluded; a missing adapter is an empty list, not a guessed COM
 * number.
 */
export function listSerialPorts({ signal, onEvent } = {}) {
	emitSerialEvent(onEvent, 'enumerate', 'running', 'enumerating serial ports');
	if (signal?.aborted === true) {
		const error = new Error('串口枚举已取消。');
		error.code = 'ABORT_ERR';
		return Promise.reject(error);
	}
	if (process.platform !== 'win32') {
		try {
			return Promise.resolve(
				readdirSync('/dev')
					.filter((name) => /^(?:ttyUSB|ttyACM)\d+$/u.test(name))
					.map((name) => ({ port: `/dev/${name}`, label: name, status: 'present' }))
					.filter((item) => {
						try {
							accessSync(item.port, fsConstants.R_OK | fsConstants.W_OK);
							return true;
						} catch {
							return false;
						}
					})
			);
		} catch (error) {
			return Promise.reject(new Error(`无法枚举串口：${error.message}`));
		}
	}
	const script = String.raw`
$items = @()
$seen = @{}
try {
  $devices = Get-PnpDevice -Class Ports -PresentOnly -ErrorAction SilentlyContinue
  foreach ($device in $devices) {
    $name = [string]$device.FriendlyName
    if ($name -notmatch '\(COM\d+\)') { continue }
    if ($name -notmatch 'CH34|USB.*SERIAL|USB-Enhanced-SERIAL|USB.*串行|串行设备|CP210|FTDI|Silicon Labs|USB.*UART') { continue }
    if ($name -match 'Bluetooth') { continue }
    $match = [regex]::Match($name, 'COM\d+')
    if ($match.Success -and -not $seen.ContainsKey($match.Value)) {
      $seen[$match.Value] = $true
      $items += [ordered]@{ port = $match.Value; label = $name; status = [string]$device.Status }
    }
  }
} catch {}
if ($items.Count -eq 0) {
  try {
    $ports = Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue
    foreach ($port in $ports) {
      $name = [string]$port.Name
      if ($name -match 'Bluetooth') { continue }
      if ($name -notmatch 'CH34|USB.*SERIAL|USB-Enhanced-SERIAL|USB.*串行|串行设备|CP210|FTDI|Silicon Labs|USB.*UART') { continue }
      if ([string]$port.DeviceID -match '^COM\d+$' -and -not $seen.ContainsKey([string]$port.DeviceID)) {
        $seen[[string]$port.DeviceID] = $true
        $items += [ordered]@{ port = [string]$port.DeviceID; label = $name; status = [string]$port.Status }
      }
    }
  } catch {}
}
Write-Output ('__SERIAL_LIST__' + ([ordered]@{ ports = $items } | ConvertTo-Json -Compress -Depth 4))
`;
	const encoded = Buffer.from(script, 'utf16le').toString('base64');
	return new Promise((resolve, reject) => {
		const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], {
			stdio: ['ignore', 'pipe', 'pipe'],
			windowsHide: true
		});
		let stdout = '';
		let stderr = '';
		let settled = false;
		let timedOut = false;
		const finish = (operation) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			signal?.removeEventListener?.('abort', abort);
			operation();
		};
		const abort = () => {
			child.kill();
			finish(() => {
				const error = new Error('串口枚举已取消，辅助进程已终止。');
				error.code = 'ABORT_ERR';
				reject(error);
			});
		};
		const timer = setTimeout(() => {
			timedOut = true;
			child.kill();
			finish(() => reject(new Error(`串口枚举超过 ${SERIAL_ENUMERATION_TIMEOUT} ms，已终止辅助进程。`)));
		}, SERIAL_ENUMERATION_TIMEOUT);
		signal?.addEventListener?.('abort', abort, { once: true });
		if (signal?.aborted === true) abort();
		child.stdout.setEncoding('utf8');
		child.stderr.setEncoding('utf8');
		child.stdout.on('data', (chunk) => {
			stdout += chunk;
		});
		child.stderr.on('data', (chunk) => {
			stderr += chunk;
		});
		child.on('error', (error) => finish(() => reject(new Error(`无法启动串口枚举器：${error.message}`))));
		child.on('close', (code) => finish(() => {
			if (timedOut) {
				reject(new Error(`串口枚举超过 ${SERIAL_ENUMERATION_TIMEOUT} ms，已终止辅助进程。`));
				return;
			}
			const ports = parseSerialPortList(stdout);
			if (ports.length > 0 || code === 0) resolve(ports);
			else reject(new Error(`串口枚举失败（exit ${code ?? 'unknown'}）：${cleanSerialOutput(stderr).slice(0, 500)}`));
		}));
	});
}

/** Try another adapter only when the helper proves failure preceded authentication. */
async function withAutoPort(settings, operation, { signal, onEvent, listPortsImpl = listSerialPorts } = {}) {
	if (signal?.aborted === true) throw serialAbortError();
	if (settings.port !== 'auto') {
		emitSerialEvent(onEvent, 'port-attempt', 'running', `using configured port ${settings.port}`, { port: settings.port });
		return operation(settings);
	}
	if (typeof settings.password === 'string' && settings.password !== '' && settings.allowAutoLogin !== true) {
		throw new Error(
			'串口 auto 登录会向多个设备发送密码；请显式配置 port，或在 profile 设置 serial.allowAutoLogin: true 确认该风险。'
		);
	}
	const candidates = await listPortsImpl({ signal, onEvent });
	if (signal?.aborted === true) throw serialAbortError();
	emitSerialEvent(onEvent, 'enumerate', 'ok', `found ${candidates.length} candidate port(s)`, { count: candidates.length });
	if (candidates.length === 0) {
		throw new Error('未发现可用的 USB 串口（CH340/CH343/ttyUSB），请检查 USB 线和驱动。');
	}
	const attempts = [];
	let lastError;
	for (const candidate of candidates.slice(0, 4)) {
		if (signal?.aborted === true) throw serialAbortError();
		const concrete = {
			...settings,
			port: candidate.port,
			timeoutMs: Math.min(settings.timeoutMs, 15000)
		};
		emitSerialEvent(onEvent, 'port-attempt', 'running', `trying ${candidate.port}`, { port: candidate.port });
		try {
			return await operation(concrete);
		} catch (error) {
			if (error?.code === 'ABORT_ERR' || signal?.aborted === true) throw error;
			const message = redactSerialText(error?.message ?? error, settings.password);
			emitSerialEvent(onEvent, 'port-attempt', 'failed', message, { port: candidate.port });
			attempts.push(`${candidate.port}: ${message.slice(0, 160)}`);
			lastError = error;
			// Prompt mismatches and every attempted login fail closed, even on auto.
			if (error?.serialRetryable !== true) throw error;
		}
	}
	const error = new Error(`已扫描 ${attempts.length} 个 USB 串口，但没有识别到可登录的昇腾板卡。${attempts.join(' | ')}`);
	Object.assign(error, {
		code: lastError?.code,
		serialPhase: lastError?.serialPhase,
		serialPort: lastError?.serialPort,
		serialTranscript: lastError?.serialTranscript ?? ''
	});
	throw error;
}

/**
 * Build the PowerShell session. Commands are JSON/base64 encoded so a board
 * command cannot become PowerShell code.
 */
function serialPowerShellScript() {
	return String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$port = $null
$script:activePhase = ''
$script:activeIndex = 0
$script:phaseClock = $null
$script:suppressTranscript = $false
$script:authenticationAttempted = $false
$script:failureCode = 'SERIAL_HELPER_FAILED'
$script:retryable = $false
$script:transcript = New-Object System.Text.StringBuilder

function Protect-Text {
  param([string]$Text)
  $text = $Text -replace '\x1b\[[0-?]*[ -/]*[@-~]', '' -replace '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ''
  if ($env:ASC_SERIAL_PASSWORD) { $text = $text.Replace($env:ASC_SERIAL_PASSWORD, '<redacted>') }
  return $text
}

function Write-Event {
  param([string]$Phase, [string]$Status, [string]$Detail, [hashtable]$Data = @{}, [long]$DurationMs = -1, [string]$Text = '')
  $data.port = $env:ASC_SERIAL_PORT
  if ($script:activeIndex -gt 0) { $data.index = $script:activeIndex }
  $event = [ordered]@{ at = [DateTime]::UtcNow.ToString('o'); phase = $Phase; status = $Status; detail = (Protect-Text $Detail); data = $data }
  if ($DurationMs -ge 0) { $event.durationMs = $DurationMs }
  if ($Phase -eq 'transcript') { $event.text = Protect-Text $Text }
  [Console]::Out.WriteLine('__ASC_SERIAL_EVENT__' + ($event | ConvertTo-Json -Compress -Depth 5))
  [Console]::Out.Flush()
}

function Start-Phase {
  param([string]$Phase, [string]$Detail, [int]$Index = 0)
  $script:activePhase = $Phase
  $script:activeIndex = $Index
  $script:phaseClock = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Event $Phase 'running' $Detail
}

function Finish-Phase {
  param([string]$Status, [string]$Detail, [hashtable]$Data = @{})
  Write-Event $script:activePhase $Status $Detail $Data $script:phaseClock.ElapsedMilliseconds
  $script:activePhase = ''
  $script:activeIndex = 0
}

function Fail-Serial {
  param([string]$Code, [string]$Detail, [bool]$Retryable = $false)
  $script:failureCode = $Code
  $script:retryable = $Retryable
  throw $Detail
}

function Add-Transcript {
  param([string]$Text)
  if ($script:suppressTranscript -or $Text.Length -eq 0) { return }
  [void]$script:transcript.Append($Text)
  if ($script:transcript.Length -gt 65536) {
    [void]$script:transcript.Remove(0, $script:transcript.Length - 65536)
  }
  Write-Event 'transcript' 'running' 'serial output' @{} -1 $script:transcript.ToString()
}

function Read-Until {
  param([string]$Pattern, [int]$TimeoutMs)
  $sb = New-Object System.Text.StringBuilder
  $clock = [System.Diagnostics.Stopwatch]::StartNew()
  while ($clock.ElapsedMilliseconds -lt $TimeoutMs) {
    if ($port.BytesToRead -gt 0) {
      $received = $port.ReadExisting()
      Add-Transcript $received
      [void]$sb.Append($received)
      if ($sb.Length -gt 1048576) { [void]$sb.Remove(0, $sb.Length - 1048576) }
    }
    $text = $sb.ToString()
    if ($text -match $Pattern) { return $text }
    Start-Sleep -Milliseconds 50
  }
  Fail-Serial 'SERIAL_PROMPT_TIMEOUT' 'serial-timeout waiting for expected prompt or command marker' ($script:activePhase -eq 'prompt' -and -not $script:authenticationAttempted)
}

function Send-Line {
  param([string]$Value)
  $port.Write($Value + [System.Environment]::NewLine)
  Start-Sleep -Milliseconds 120
}

function Login-User {
  Start-Phase 'authenticate' 'waiting for password prompt'
  $script:authenticationAttempted = $true
  Send-Line $env:ASC_SERIAL_USER
  $passwordPrompt = Read-Until '(?im)(password:\s*$|login:\s*$|[\$#]\s*$)' ([int]$env:ASC_SERIAL_TIMEOUT)
  if ($passwordPrompt -notmatch '(?im)password:\s*$') {
    Fail-Serial 'SERIAL_PASSWORD_PROMPT_MISMATCH' 'serial-password-prompt-mismatch; no password sent'
  }
  Add-Transcript ([string][char]10 + '[password input suppressed]' + [char]10)
  # Never record any input echo or response bytes while the password is in flight.
  $script:suppressTranscript = $true
  Send-Line $env:ASC_SERIAL_PASSWORD
  $shellPrompt = Read-Until '(?im)([\$#]\s*$|login:\s*$|password:\s*$)' ([int]$env:ASC_SERIAL_TIMEOUT)
  if ($shellPrompt -notmatch '(?im)[\$#]\s*$') {
    Fail-Serial 'SERIAL_AUTHENTICATION_FAILED' 'serial-authentication-failed'
  }
  # Keep capture suppressed until the identity marker also confirms the shell.
  Finish-Phase 'ok' 'shell prompt received after authentication'
}

function Read-ShellIdentity {
  Start-Phase 'identity' 'verifying shell identity'
  $identityBegin = $token + '_IDENTITY_BEGIN'
  $identityEnd = $token + '_IDENTITY_END'
  Send-Line "printf '$identityBegin\n'; id -un; printf '$identityEnd\n'"
  $chunk = Read-Until ("(?m)^" + [regex]::Escape($identityEnd) + "\s*$") ([int]$env:ASC_SERIAL_TIMEOUT)
  $startAt = $chunk.LastIndexOf($identityBegin)
  $endAt = $chunk.LastIndexOf($identityEnd)
  if ($startAt -lt 0 -or $endAt -le $startAt) { Fail-Serial 'SERIAL_IDENTITY_MARKER_MISSING' 'serial-identity-marker-missing' }
  return $chunk.Substring($startAt + $identityBegin.Length, $endAt - $startAt - $identityBegin.Length).Trim()
}

try {
  Start-Phase 'port-open' 'opening serial port'
  $port = New-Object System.IO.Ports.SerialPort(
    $env:ASC_SERIAL_PORT,
    [int]$env:ASC_SERIAL_BAUD,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
  )
  $port.ReadTimeout = 250
  $port.WriteTimeout = 1500
  $port.NewLine = [System.Environment]::NewLine
  $port.DtrEnable = $true
  $port.RtsEnable = $true
  $port.Open()
  Finish-Phase 'ok' 'serial port opened'

  $commands = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:ASC_SERIAL_COMMANDS)) | ConvertFrom-Json
  $token = $env:ASC_SERIAL_TOKEN
  $sections = [ordered]@{}
  $i = 0
  Start-Phase 'prompt' 'waiting for login or shell prompt'
  # A harmless newline wakes bridges that do not deliver an existing prompt.
  $port.Write([string][char]13)
  Start-Sleep -Milliseconds 200
  $banner = Read-Until '(?im)(login:\s*$|password:\s*$|[\$#]\s*$)' ([int]$env:ASC_SERIAL_TIMEOUT)
  if ($banner -match '(?im)password:\s*$') {
    Fail-Serial 'SERIAL_PASSWORD_PROMPT_MISMATCH' 'serial-password-prompt-mismatch; existing password prompt is not owned by this session'
  }
  Finish-Phase 'ok' 'login or shell prompt received'
  if ($banner -match '(?im)login:\s*$') { Login-User }

  $identity = Read-ShellIdentity
  if ($identity -ne $env:ASC_SERIAL_USER) {
    Finish-Phase 'failed' 'shell identity mismatch; logging out before re-authentication'
    Start-Phase 'prompt' 'waiting for login prompt after logout'
    Send-Line 'exit'
    $loginPrompt = Read-Until '(?im)(login:\s*$|password:\s*$)' ([int]$env:ASC_SERIAL_TIMEOUT)
    if ($loginPrompt -notmatch '(?im)login:\s*$') { Fail-Serial 'SERIAL_PASSWORD_PROMPT_MISMATCH' 'serial-password-prompt-mismatch after logout' }
    Finish-Phase 'ok' 'login prompt received after logout'
    Login-User
    $identity = Read-ShellIdentity
  }
  if ($identity -ne $env:ASC_SERIAL_USER) { Fail-Serial 'SERIAL_IDENTITY_MISMATCH' 'serial-identity-mismatch for configured user' }
  $script:suppressTranscript = $false
  Finish-Phase 'ok' 'configured shell identity verified'

  foreach ($command in $commands) {
    $i++
    Start-Phase 'command' "command $i" $i
    $begin = $token + '_BEGIN_' + $i
    $end = $token + '_END_' + $i
    $statusMarker = $token + '_STATUS_' + $i
    $statusTemplate = @'
printf '__BEGIN__\n'; __COMMAND__; __asc_status=$?; printf '__STATUS__=%s\n' $__asc_status; printf '__END__\n'
'@
    Send-Line ($statusTemplate.Replace('__BEGIN__', $begin).Replace('__COMMAND__', $command).Replace('__STATUS__', $statusMarker).Replace('__END__', $end))
    $chunk = Read-Until ("(?m)^" + [regex]::Escape($end) + "\s*$") ([int]$env:ASC_SERIAL_TIMEOUT)
    $startAt = $chunk.LastIndexOf($begin)
    $endAt = $chunk.LastIndexOf($end)
    $statusMatch = [regex]::Match($chunk, '(?m)^' + [regex]::Escape($statusMarker) + '=(\d+)\r?$')
    if (-not $statusMatch.Success) { Fail-Serial 'SERIAL_COMMAND_STATUS_MISSING' 'serial-command-status-missing' }
    $exitCode = [int]$statusMatch.Groups[1].Value
    $sections["status$i"] = [string]$exitCode
    if ($startAt -lt 0 -or $endAt -le $startAt) { Fail-Serial 'SERIAL_COMMAND_MARKER_MISSING' 'serial-command-marker-missing' }
    $section = $chunk.Substring($startAt + $begin.Length, $endAt - $startAt - $begin.Length).Trim()
    $sections["command$i"] = Protect-Text (($section -replace ([regex]::Escape($statusMarker) + '=\d+\r?\n?'), '').Trim())
    if ($exitCode -eq 0) { Finish-Phase 'ok' "command $i completed" @{ exitCode = $exitCode } }
    else { Finish-Phase 'failed' "command $i exited with status $exitCode" @{ exitCode = $exitCode } }
  }
  $result = [ordered]@{ identity = $identity; sections = $sections; transcript = (Protect-Text $script:transcript.ToString()) }
  [Console]::Out.WriteLine('__ASC_SERIAL_RESULT__' + ($result | ConvertTo-Json -Compress -Depth 8))
  [Console]::Out.Flush()
} catch {
  $detail = if ($script:suppressTranscript) { 'serial-authentication-failed while waiting for shell prompt' } else { $_.Exception.Message }
  if ($script:activePhase -eq 'port-open') { $script:retryable = $true }
  if ($script:activePhase -eq '') { Start-Phase 'session' 'serial helper failed' }
  Finish-Phase 'failed' $detail @{ code = $script:failureCode; retryable = $script:retryable }
  exit 1
} finally {
  if ($null -ne $port) {
    try { if ($port.IsOpen) { $port.Close() } }
    finally { $port.Dispose() }
  }
}
`;
}

function powershellExecutable() {
	if (process.platform === 'win32') return 'powershell.exe';
	return 'pwsh';
}

/**
 * Open the serial console, log in, run commands, and return raw sections.
 *
 * @param {object} options - session inputs.
 * @param {object} options.settings - validated serial settings.
 * @param {string[]} options.commands - read-only commands.
 * @param {AbortSignal} [options.signal] - aborts and terminates the host helper.
 * @param {Function} [options.onEvent] - cleaned phase records; transcript events
 *   carry a bounded cumulative `text` snapshot, to replace rather than append.
 * @param {Function} [options.spawnImpl] - process factory for offline tests.
 * @param {object} [options.timers] - setTimeout/clearTimeout for deadline tests.
 * @returns {Promise<object>} parsed JSON emitted by the serial helper.
 */
export function runSerialSession({ settings, commands, signal, onEvent, spawnImpl = spawn, timers = globalThis }) {
	const validation = validateSerialSettings(settings);
	if (!validation.ok) return Promise.reject(new Error(validation.reason));
	if (!Array.isArray(commands) || commands.length === 0) return Promise.reject(new Error('串口命令为空。'));
	if (signal?.aborted === true) return Promise.reject(serialAbortError());
	const token = `ASC${Date.now().toString(36).toUpperCase()}${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
	const encodedScript = Buffer.from(serialPowerShellScript(), 'utf16le').toString('base64');
	const env = {
		...process.env,
		ASC_SERIAL_PORT: settings.port,
		ASC_SERIAL_BAUD: String(settings.baudRate),
		ASC_SERIAL_USER: settings.user,
		ASC_SERIAL_PASSWORD: settings.password,
		ASC_SERIAL_TIMEOUT: String(settings.timeoutMs),
		ASC_SERIAL_TOKEN: token,
		ASC_SERIAL_COMMANDS: Buffer.from(JSON.stringify(commands), 'utf8').toString('base64')
	};
	return new Promise((resolve, reject) => {
		const decoder = new StringDecoder('utf8');
		const stderrDecoder = new StringDecoder('utf8');
		let pending = '';
		let stderr = '';
		let transcript = '';
		let result = null;
		let active = null;
		let helperFailure = null;
		let authenticationAttempted = false;
		let settled = false;
		let timer = null;
		let child;
		const redact = (value) => redactSerialText(value, settings.password);
		const startedAt = Date.now();
		const hostTimeoutMs = serialSessionTimeoutMs(settings, commands.length);
		const finish = (operation) => {
			if (settled) return;
			settled = true;
			if (timer !== null) timers.clearTimeout(timer);
			signal?.removeEventListener?.('abort', abort);
			operation();
		};
		const failure = (message, code, status = 'failed') => {
			const stage = active ?? helperFailure;
			const error = new Error(redact(message));
			error.code = code ?? helperFailure?.data?.code ?? 'SERIAL_HELPER_FAILED';
			error.serialPhase = stage?.phase ?? 'session';
			error.serialPort = settings.port;
			error.serialTranscript = transcript;
			error.serialRetryable = helperFailure?.data?.retryable === true && !authenticationAttempted &&
				['port-open', 'prompt'].includes(error.serialPhase) && error.code !== 'SERIAL_PASSWORD_PROMPT_MISMATCH';
			if (stage?.data?.index !== undefined) error.serialIndex = stage.data.index;
			if (active !== null || helperFailure === null || status !== 'failed') {
				deliverSerialEvent(onEvent, {
					at: new Date().toISOString(), phase: error.serialPhase, status, detail: error.message,
					data: { port: settings.port, ...(error.serialIndex === undefined ? {} : { index: error.serialIndex }) },
					durationMs: Math.max(0, Date.now() - (active === null ? startedAt : Date.parse(active.at)))
				});
			}
			return error;
		};
		const terminate = (message, code, status) => {
			if (settled) return;
			const error = failure(message, code, status);
			// Settle before kill: a mocked or failed child can emit close synchronously.
			finish(() => {
				try { child?.kill(); } catch { /* Preserve the original failure. */ }
				reject(error);
			});
		};
		const abort = () => terminate('串口会话已取消，已请求终止辅助进程。', 'ABORT_ERR', 'cancelled');
		const consumeEvent = (raw) => {
			if (raw === null || typeof raw !== 'object' || typeof raw.at !== 'string' || !Number.isFinite(Date.parse(raw.at))) return;
			const at = new Date(raw.at).toISOString();
			const data = { port: settings.port };
			if (Number.isInteger(raw.data?.index) && raw.data.index > 0 && raw.data.index <= commands.length) data.index = raw.data.index;
			if (raw.phase === 'transcript') {
				if (typeof raw.text !== 'string') return;
				transcript = boundedTranscript(redact(raw.text));
				deliverSerialEvent(onEvent, { at, phase: 'transcript', status: 'running', detail: 'serial output', data, text: transcript });
				return;
			}
			if ((!SESSION_PHASES.has(raw.phase) && raw.phase !== 'session') || !['running', 'ok', 'failed', 'timeout', 'cancelled'].includes(raw.status)) return;
			if (raw.phase === 'command' && data.index === undefined) return;
			if (Number.isInteger(raw.data?.exitCode) && raw.data.exitCode >= 0) data.exitCode = raw.data.exitCode;
			if (typeof raw.data?.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/u.test(raw.data.code)) data.code = redact(raw.data.code);
			if (typeof raw.data?.retryable === 'boolean') data.retryable = raw.data.retryable;
			const event = { at, phase: raw.phase, status: raw.status, detail: redact(String(raw.detail ?? '')).slice(0, 1000), data };
			if (Number.isFinite(raw.durationMs) && raw.durationMs >= 0) event.durationMs = raw.durationMs;
			if (event.phase === 'command' && event.status === 'ok' && data.exitCode !== 0) {
				event.status = 'failed';
				event.detail = `command ${data.index} did not report exit status 0`;
			}
			if (event.phase === 'authenticate') authenticationAttempted = true;
			if (event.status === 'running') {
				active = event;
				helperFailure = null;
			} else {
				if (event.status !== 'ok') helperFailure = event;
				if (active?.phase === event.phase && active?.data?.index === data.index) active = null;
			}
			deliverSerialEvent(onEvent, event);
		};
		const consumeLine = (line) => {
			if (settled || line.trim() === '') return;
			const marker = line.startsWith(EVENT_MARKER) ? EVENT_MARKER : line.startsWith(RESULT_MARKER) ? RESULT_MARKER : null;
			if (marker === null) return; // Never forward raw helper stdout to callbacks.
			let parsed;
			try { parsed = JSON.parse(line.slice(marker.length)); } catch {
				terminate('串口辅助进程返回了无效的结构化数据。', 'SERIAL_PROTOCOL_ERROR', 'failed');
				return;
			}
			if (marker === EVENT_MARKER) consumeEvent(parsed);
			else if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
				const sections = {};
				for (const [key, value] of Object.entries(parsed.sections ?? {})) {
					if (/^(?:command|status)\d+$/u.test(key)) sections[key] = redact(value);
				}
				if (typeof parsed.transcript === 'string') transcript = boundedTranscript(redact(parsed.transcript));
				result = { identity: redact(parsed.identity), sections, transcript, token };
			}
		};
		const consume = (text) => {
			pending += text;
			let newline;
			while (!settled && (newline = pending.indexOf('\n')) >= 0) {
				if (newline > MAX_PROTOCOL_LINE) break;
				const line = pending.slice(0, newline).replace(/\r$/u, '');
				pending = pending.slice(newline + 1);
				consumeLine(line);
			}
			if (!settled && pending.length > MAX_PROTOCOL_LINE) terminate('串口辅助进程数据超过单行上限。', 'SERIAL_PROTOCOL_ERROR', 'failed');
		};
		try {
			child = spawnImpl(powershellExecutable(), ['-NoProfile', '-NonInteractive', '-EncodedCommand', encodedScript], {
				env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
			});
		} catch (error) {
			finish(() => reject(failure(`串口辅助进程无法启动：${error.message}`, 'SERIAL_SPAWN_FAILED')));
			return;
		}
		child.stdout.on('data', (chunk) => {
			if (!settled) consume(decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
		});
		child.stderr.on('data', (chunk) => {
			if (!settled) stderr = (stderr + stderrDecoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))).slice(-MAX_PROTOCOL_LINE);
		});
		child.on('error', (error) => {
			if (!settled) finish(() => reject(failure(`串口辅助进程错误：${error.message}`, 'SERIAL_PROCESS_ERROR')));
		});
		child.on('close', (code) => {
			if (settled) return;
			consume(decoder.end());
			consumeLine(pending.replace(/\r$/u, ''));
			if (settled) return;
			stderr += stderrDecoder.end();
			if (code === 0 && result !== null) {
				finish(() => resolve(result));
				return;
			}
			const detail = formatSerialError(settings.port, helperFailure?.detail || redact(stderr));
			finish(() => reject(failure(`串口会话失败（exit ${code ?? 'unknown'}）：${detail}`)));
		});
		timer = timers.setTimeout(() => terminate(`串口会话超过宿主总超时 ${hostTimeoutMs} ms，已请求终止辅助进程。`, 'SERIAL_TIMEOUT', 'timeout'), hostTimeoutMs);
		signal?.addEventListener?.('abort', abort, { once: true });
		if (signal?.aborted === true) abort();
	});
}

/** Run the fixed network probe and parse its output. */
export async function runSerialProbe(settings, options = {}) {
	return withAutoPort(settings, async (concrete) => {
		const result = await runSerialSession({ ...options, settings: concrete, commands: SERIAL_PROBE_COMMANDS });
		const sections = result.sections ?? {};
		const network = parseSerialNetwork({
			hostname: sections.command1,
			addresses: sections.command2,
			interfaces: sections.command3,
			routes: sections.command4,
			macs: sections.command5
		}, result.transcript);
		return { ...network, port: concrete.port, user: concrete.user };
	}, options);
}

/** Run exactly one validated read-only command over the serial console. */
export async function runSerialCommand(settings, command, options = {}) {
	return withAutoPort(settings, async (concrete) => {
		const result = await runSerialSession({ ...options, settings: concrete, commands: [command] });
		const status = Number(result.sections?.status1);
		return {
			port: concrete.port,
			stdout: cleanSerialOutput(result.sections?.command1 ?? ''),
			exitCode: Number.isInteger(status) && status >= 0 ? status : 1,
			transcriptExcerpt: cleanSerialOutput(result.transcript).slice(0, 8000)
		};
	}, options);
}
