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

const DEFAULT_BAUD = 115200;
const DEFAULT_TIMEOUT = 60000;
const MAX_TIMEOUT = 120000;
const SERIAL_PORT = /^(?:COM\d+|\/dev\/tty[A-Za-z0-9._-]+)$/iu;
const SERIAL_USER = /^[A-Za-z0-9._-]+$/u;
const MARKER = 'ASC_SERIAL_RESULT_';

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
export function listSerialPorts() {
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
		child.stdout.setEncoding('utf8');
		child.stderr.setEncoding('utf8');
		child.stdout.on('data', (chunk) => {
			stdout += chunk;
		});
		child.stderr.on('data', (chunk) => {
			stderr += chunk;
		});
		child.on('error', (error) => reject(new Error(`无法启动串口枚举器：${error.message}`)));
		child.on('close', (code) => {
			const ports = parseSerialPortList(stdout);
			if (ports.length > 0 || code === 0) resolve(ports);
			else reject(new Error(`串口枚举失败（exit ${code ?? 'unknown'}）：${cleanSerialOutput(stderr).slice(0, 500)}`));
		});
	});
}

/** Try an operation on an explicitly configured port or each candidate port. */
async function withAutoPort(settings, operation) {
	if (settings.port !== 'auto') return operation(settings);
	if (typeof settings.password === 'string' && settings.password !== '' && settings.allowAutoLogin !== true) {
		throw new Error(
			'串口 auto 登录会向多个设备发送密码；请显式配置 port，或在 profile 设置 serial.allowAutoLogin: true 确认该风险。'
		);
	}
	const candidates = await listSerialPorts();
	if (candidates.length === 0) {
		throw new Error('未发现可用的 USB 串口（CH340/CH343/ttyUSB），请检查 USB 线和驱动。');
	}
	const attempts = [];
	for (const candidate of candidates.slice(0, 4)) {
		const concrete = {
			...settings,
			port: candidate.port,
			timeoutMs: Math.min(settings.timeoutMs, 15000)
		};
		try {
			return await operation(concrete);
		} catch (error) {
			const message = String(error?.message ?? error);
			attempts.push(`${candidate.port}: ${message.slice(0, 160)}`);
			// A rejected password will be rejected on every other adapter too;
			// stop before causing repeated failed logins and credential sprays.
			if (/password|authentication|permission denied|login incorrect|密码.*错误|认证失败/iu.test(message)) break;
		}
	}
	throw new Error(`已扫描 ${Math.min(candidates.length, 4)} 个 USB 串口，但没有识别到可登录的昇腾板卡。${attempts.join(' | ')}`);
}

/**
 * Build the PowerShell session. Commands are JSON/base64 encoded so a board
 * command cannot become PowerShell code.
 */
function serialPowerShellScript() {
	return String.raw`
$ErrorActionPreference = 'Stop'
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
# Some CH343/USB bridges do not deliver the current login prompt until a
# harmless newline wakes the console. This does not execute a command.
$port.Write([string][char]13)
Start-Sleep -Milliseconds 200

function Read-Until {
  param([string]$Pattern, [int]$TimeoutMs)
  $sb = New-Object System.Text.StringBuilder
  $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
  while ([DateTime]::UtcNow -lt $deadline) {
    if ($port.BytesToRead -gt 0) {
      [void]$sb.Append($port.ReadExisting())
    }
    $text = $sb.ToString()
    if ($text -match $Pattern) { return $text }
    Start-Sleep -Milliseconds 50
  }
  throw "serial-timeout waiting for $Pattern"
}

function Send-Line {
  param([string]$Value)
  $port.Write($Value + [System.Environment]::NewLine)
  Start-Sleep -Milliseconds 120
}

function Login-User {
  Send-Line $env:ASC_SERIAL_USER
  $passwordPrompt = Read-Until '(?im)password:\s*$' ([int]$env:ASC_SERIAL_TIMEOUT)
  [void]$transcript.Append($passwordPrompt)
  Send-Line $env:ASC_SERIAL_PASSWORD
  $shellPrompt = Read-Until '(?im)[\$#]\s*$' ([int]$env:ASC_SERIAL_TIMEOUT)
  [void]$transcript.Append($shellPrompt)
}

function Read-ShellIdentity {
  $identityBegin = $token + "_IDENTITY_BEGIN"
  $identityEnd = $token + "_IDENTITY_END"
  Send-Line "printf '$identityBegin\n'; id -un; printf '$identityEnd\n'"
  $chunk = Read-Until ("(?m)^" + [regex]::Escape($identityEnd) + "\s*$") ([int]$env:ASC_SERIAL_TIMEOUT)
  [void]$transcript.Append($chunk)
  $startAt = $chunk.LastIndexOf($identityBegin)
  $endAt = $chunk.LastIndexOf($identityEnd)
  if ($startAt -lt 0 -or $endAt -le $startAt) { throw "serial-identity-marker-missing" }
  return $chunk.Substring($startAt + $identityBegin.Length, $endAt - $startAt - $identityBegin.Length).Trim()
}

$commands = [System.Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:ASC_SERIAL_COMMANDS)
) | ConvertFrom-Json
$token = $env:ASC_SERIAL_TOKEN
$sections = [ordered]@{}
$i = 0
$transcript = New-Object System.Text.StringBuilder

$banner = Read-Until '(?im)(login:\s*$|[\$#]\s*$)' ([int]$env:ASC_SERIAL_TIMEOUT)
[void]$transcript.Append($banner)
if ($banner -match '(?im)login:\s*$') {
  Login-User
}

$identity = Read-ShellIdentity
if ($identity -ne $env:ASC_SERIAL_USER) {
  # A previous serial session may have left the console logged in as another
  # account. Log out and authenticate again; otherwise a "root" tool would
  # silently execute as HwHiAiUser.
  Send-Line "exit"
  $loginPrompt = Read-Until '(?im)login:\s*$' ([int]$env:ASC_SERIAL_TIMEOUT)
  [void]$transcript.Append($loginPrompt)
  Login-User
  $identity = Read-ShellIdentity
}
if ($identity -ne $env:ASC_SERIAL_USER) {
  throw "serial-identity-mismatch expected=$env:ASC_SERIAL_USER actual=$identity"
}

foreach ($command in $commands) {
  $i++
  $begin = $token + "_BEGIN_" + $i
  $end = $token + "_END_" + $i
  $statusMarker = $token + "_STATUS_" + $i
  $statusTemplate = @'
printf '__BEGIN__\n'; __COMMAND__; __asc_status=$?; printf '__STATUS__=%s\n' $__asc_status; printf '__END__\n'
'@
  Send-Line ($statusTemplate.Replace('__BEGIN__', $begin).Replace('__COMMAND__', $command).Replace('__STATUS__', $statusMarker).Replace('__END__', $end))
  $chunk = Read-Until ("(?m)^" + [regex]::Escape($end) + "\s*$") ([int]$env:ASC_SERIAL_TIMEOUT)
  [void]$transcript.Append($chunk)
  $startAt = $chunk.LastIndexOf($begin)
  $endAt = $chunk.LastIndexOf($end)
  $statusMatch = [regex]::Match($chunk, [regex]::Escape($statusMarker) + '=(\d+)')
  if ($statusMatch.Success) {
    $sections["status$i"] = $statusMatch.Groups[1].Value
  }
  if ($startAt -ge 0 -and $endAt -gt $startAt) {
    $section = $chunk.Substring($startAt + $begin.Length, $endAt - $startAt - $begin.Length).Trim()
    if ($statusMatch.Success) { $section = ($section -replace ([regex]::Escape($statusMarker) + '=\d+\r?\n?'), '').Trim() }
    $sections["command$i"] = $section
  } else {
    $sections["command$i"] = $chunk.Trim()
  }
}
$port.Close()
$result = [ordered]@{
  identity = $identity
  sections = $sections
  transcript = $transcript.ToString()
}
Write-Output ('__ASC_SERIAL_RESULT__' + ($result | ConvertTo-Json -Compress -Depth 8))
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
 * @returns {Promise<object>} parsed JSON emitted by the serial helper.
 */
export function runSerialSession({ settings, commands }) {
	const validation = validateSerialSettings(settings);
	if (!validation.ok) return Promise.reject(new Error(validation.reason));
	if (!Array.isArray(commands) || commands.length === 0) return Promise.reject(new Error('串口命令为空。'));
	const token = `ASC${Date.now().toString(36).toUpperCase()}${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
	const script = serialPowerShellScript();
	const encodedScript = Buffer.from(script, 'utf16le').toString('base64');
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
		const child = spawn(powershellExecutable(), ['-NoProfile', '-NonInteractive', '-EncodedCommand', encodedScript], {
			env,
			stdio: ['ignore', 'pipe', 'pipe'],
			windowsHide: true
		});
		let stdout = '';
		let stderr = '';
		const limit = 2 * 1024 * 1024;
		child.stdout.setEncoding('utf8');
		child.stderr.setEncoding('utf8');
		child.stdout.on('data', (chunk) => {
			stdout += chunk;
			if (stdout.length > limit) stdout = stdout.slice(-limit);
		});
		child.stderr.on('data', (chunk) => {
			stderr += chunk;
			if (stderr.length > limit) stderr = stderr.slice(-limit);
		});
		child.on('error', (error) => reject(new Error(`串口辅助进程无法启动：${error.message}`)));
		child.on('close', (code) => {
			const marker = '__ASC_SERIAL_RESULT__';
			const at = stdout.lastIndexOf(marker);
			if (at >= 0) {
				try {
					const result = JSON.parse(stdout.slice(at + marker.length).trim());
					resolve({ ...result, token });
					return;
				} catch (error) {
					reject(new Error(`串口返回不是有效 JSON：${error.message}`));
					return;
				}
			}
			const detail = formatSerialError(settings.port, stderr || stdout);
			reject(new Error(`串口会话失败（exit ${code ?? 'unknown'}）：${detail}`));
		});
	});
}

/** Run the fixed network probe and parse its output. */
export async function runSerialProbe(settings) {
	return withAutoPort(settings, async (concrete) => {
		const result = await runSerialSession({ settings: concrete, commands: SERIAL_PROBE_COMMANDS });
		const sections = result.sections ?? {};
		const network = parseSerialNetwork({
			hostname: sections.command1,
			addresses: sections.command2,
			interfaces: sections.command3,
			routes: sections.command4,
			macs: sections.command5
		}, result.transcript);
		return { ...network, port: concrete.port, user: concrete.user };
	});
}

/** Run exactly one validated read-only command over the serial console. */
export async function runSerialCommand(settings, command) {
	return withAutoPort(settings, async (concrete) => {
		const result = await runSerialSession({ settings: concrete, commands: [command] });
		const status = Number(result.sections?.status1);
		return {
			port: concrete.port,
			stdout: cleanSerialOutput(result.sections?.command1 ?? ''),
			exitCode: Number.isInteger(status) && status >= 0 ? status : 1,
			transcriptExcerpt: cleanSerialOutput(result.transcript).slice(0, 8000)
		};
	});
}
