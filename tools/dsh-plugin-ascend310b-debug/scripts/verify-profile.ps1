<#
.SYNOPSIS
    Boot this plugin in a throwaway CLI profile and verify it end to end.

.DESCRIPTION
    The Electron desktop profile is application-owned, so `dsh --profile desktop`
    refuses to run from a shell. This script builds a disposable profile from the
    web template plus this plugin, boots it on a spare port, then checks that the
    report route answers, that the probe route parses a saved board transcript,
    and that the browser bundle is present in the boot graph.

    It is also the only check that proves the profile patch layer reaches the
    plugin: the target list written here must come back in the route response.

    Run it after every plugin change and before restarting the desktop app.

.PARAMETER PluginRoot
    The plugin package directory. Defaults to the package containing this script.

.PARAMETER Port
    Port for the throwaway instance. Defaults to 19399.

.PARAMETER RepoRoot
    Textbook checkout written into the throwaway profile's patch layer. Defaults
    to `DSH_ASCEND310B_ROOT` and then to the current directory.

.EXAMPLE
    powershell -File scripts/verify-profile.ps1 -RepoRoot <textbook checkout>
#>
[CmdletBinding()]
param(
    [string]$PluginRoot = '',
    [int]$Port = 19399,
    [string]$RepoRoot = ''
)

$ErrorActionPreference = 'Stop'
# $PSScriptRoot is not available while parameter defaults are evaluated.
if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
    $PluginRoot = (Split-Path -Parent $PSScriptRoot)
}
$PluginRoot = (Resolve-Path $PluginRoot).Path
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$profileRoot = Join-Path $dshHome 'profiles\ascendcheck'
# Windows PowerShell 5.1 decodes Get-Content without -Encoding as ANSI, which
# mangles the UTF-8 description, so the manifest is read and parsed as UTF-8.
$packageManifest = [System.IO.File]::ReadAllText(
    (Join-Path $PluginRoot 'package.json'),
    [System.Text.Encoding]::UTF8
) | ConvertFrom-Json
$packageName = $packageManifest.name
$utf8 = New-Object System.Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = if ($env:DSH_ASCEND310B_ROOT) { $env:DSH_ASCEND310B_ROOT } else { (Get-Location).Path }
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "port $Port is already in use; pick another with -Port"
}

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $utf8)
}

# YAML accepts JSON as a flow value, so the target list can be written without a
# YAML library. Reserved test addresses are used on purpose: this profile must
# never point the plugin at a real board.
$targetsJson = '[{"id":"smoke-8t","label":"8T smoke","transport":"ssh","host":"198.51.100.10","user":"HwHiAiUser"},' +
    '{"id":"smoke-20t","label":"20T smoke","transport":"ssh","host":"198.51.100.11","user":"HwHiAiUser","note":"second board"}]'

# A saved transcript with the same section markers a live probe emits. It is a
# fixture, not a claim about any real board.
$transcript = @'
===ABS_BEGIN:uname===
Linux smoke-host 5.10.0+ #1 SMP PREEMPT aarch64 GNU/Linux
===ABS_END:uname===
===ABS_BEGIN:hostname===
smoke-host
===ABS_END:hostname===
===ABS_BEGIN:kernel===
5.10.0+
===ABS_END:kernel===
===ABS_BEGIN:npu_smi_version===
npu-smi 25.2.0
===ABS_END:npu_smi_version===
===ABS_BEGIN:npu_smi_info===
| npu-smi 25.2.0                       Version: 25.2.0                                        |
| 0       310B4             | OK            | 12.8         45                0    / 0              |
| 0       0                 | 0000:00:00.0  | 0            2433 / 7564                            |
===ABS_END:npu_smi_info===
===ABS_BEGIN:cann_version_cfg===
version=[7.6.0.1.220]
runtime_running_version=[7.6.0.1.220:8.0.0]
===ABS_END:cann_version_cfg===
===ABS_BEGIN:driver_version_info===
Version=25.2.0
===ABS_END:driver_version_info===
===ABS_BEGIN:acl_import===
PyACL import: ok
===ABS_END:acl_import===
===ABS_BEGIN:devices===
crw-rw-rw- 1 root root 240, 0 Jan  1 00:00 /dev/davinci0
===ABS_END:devices===
'@

Write-Host "building throwaway profile at $profileRoot"
Remove-Item -Recurse -Force $profileRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $profileRoot 'node_modules') | Out-Null

Write-Utf8 (Join-Path $profileRoot 'package.json') @"
{
  "name": "dsh-profile-ascendcheck",
  "private": true,
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        "$packageName"
      ]
    }
  }
}
"@
Write-Utf8 (Join-Path $profileRoot 'cordis.patch.yml') @"
# Throwaway verification profile. The plugin keeps no built-in board address or
# checkout path, so this layer supplies them exactly as a real profile would.
- id: $packageName
  name: $packageName
  config:
    repoRoot: $RepoRoot
    inventoryPath: '$profileRoot\inventory.json'
    defaultTarget: smoke-8t
    targets: $targetsJson
"@
Write-Utf8 (Join-Path $profileRoot 'pnpm-workspace.yaml') "packages:`n  - .`n`nnodeLinker: hoisted`nautoInstallPeers: false`n"
Copy-Item -Recurse -Force $PluginRoot (Join-Path $profileRoot "node_modules\$packageName")

$log = Join-Path $env:TEMP "dsh-ascend-verify-$Port.log"
Remove-Item $log, "$log.err" -Force -ErrorAction SilentlyContinue
$process = Start-Process -FilePath 'dsh' -ArgumentList @('--profile', 'ascendcheck', '--no-open', '--port', "$Port") `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -WindowStyle Hidden

$failures = @()
try {
    Write-Host "waiting for http://127.0.0.1:$Port ..."
    $deadline = (Get-Date).AddSeconds(90)
    $ready = $false
    while (-not $ready -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/ascend310b-debug/report.json" -UseBasicParsing -TimeoutSec 5
            $ready = $true
        } catch {
            if ($_.Exception.Response.StatusCode.value__ -eq 404) { $ready = $true }
        }
    }

    try {
        $report = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/ascend310b-debug/report.json" -UseBasicParsing -TimeoutSec 30
        $json = $report.Content | ConvertFrom-Json
        Write-Host ("  route OK: repoRoot={0} targets={1} samples={2} knowledge={3}" -f `
            $json.repoRoot, $json.targets.Count, $json.samples.Count, $json.knowledge.Count)
        if ($json.ok -ne $true) { $failures += 'report route did not return ok=true' }
        if ($json.targets.Count -ne 2) { $failures += "expected 2 configured targets, got $($json.targets.Count)" }
        if ($null -ne $json.board) { $failures += 'the panel must report no board before the first probe' }
        if ($json.scope.applicable -ne 'unknown') {
            $failures += "an unprobed panel must report scope=unknown, got '$($json.scope.applicable)'"
        }
        if ($json.pairing.verdict -ne 'incomplete') {
            $failures += "an unprobed panel must report 'incomplete', got '$($json.pairing.verdict)'"
        }
        $expected = $RepoRoot -replace '\\', '/'
        $actual = "$($json.repoRoot)" -replace '\\', '/'
        if ($actual -ne $expected) {
            $failures += "config.repoRoot did not reach the plugin: expected '$expected', route reported '$actual'"
        } else {
            Write-Host '  config.repoRoot and target list reached the plugin'
        }
    } catch {
        $failures += "report route failed: $($_.Exception.Message)"
    }

    try {
        $body = @{ target = 'smoke-8t'; rawText = $transcript } | ConvertTo-Json -Compress
        $probe = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/ascend310b-debug/probe" -Method POST `
            -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 60
        $probeJson = $probe.Content | ConvertFrom-Json
        if ($probeJson.ok -ne $true) { $failures += "probe route failed: $($probeJson.error)" }
        else {
            Write-Host ("  probe OK: chip={0} soc={1} tier={2} cann={3} driver={4} pairing={5}/{6}" -f `
                $probeJson.board.chip, $probeJson.board.soc, $probeJson.board.tier, `
                $probeJson.facts.cannVersion.value, $probeJson.facts.driverVersion.value, `
                $probeJson.pairing.verdict, $probeJson.pairing.evidence)
            if ($probeJson.board.soc -ne 'Ascend310B4') { $failures += "expected Ascend310B4, got $($probeJson.board.soc)" }
            if ($probeJson.board.tier -ne '8T') { $failures += "expected the 8T tier, got $($probeJson.board.tier)" }
            if ($probeJson.board.memoryUsedMb -ne 2433) { $failures += 'the memory continuation row was not parsed' }
            if ($probeJson.pairing.verdict -ne 'observed-match') {
                $failures += "expected an observed-match pairing, got '$($probeJson.pairing.verdict)'"
            }
            if ($probeJson.scope.applicable -ne $true) {
                $failures += "expected a 310B board to be in scope, got '$($probeJson.scope.applicable)'"
            }
            if ($probeJson.knowledge.Count -lt 10) { $failures += 'the knowledge router looks empty' }
            if ($probeJson.samples.Count -lt 1) { $failures += 'the sample index looks empty' }
        }

        # A different accelerator must be reported out of scope, not given a
        # 310B verdict. This is a synthetic row, not a board observation.
        $foreign = @'
===ABS_BEGIN:npu_smi_info===
| 0       310P3             | OK            | 12.8         45                0    / 0              |
===ABS_END:npu_smi_info===
'@
        $foreignBody = @{ rawText = $foreign } | ConvertTo-Json -Compress
        $foreignProbe = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/ascend310b-debug/probe" -Method POST `
            -ContentType 'application/json' -Body $foreignBody -UseBasicParsing -TimeoutSec 60
        $foreignJson = $foreignProbe.Content | ConvertFrom-Json
        if ($foreignJson.scope.applicable -ne $false) {
            $failures += "a non-310B chip must be out of scope, got '$($foreignJson.scope.applicable)'"
        } elseif ($foreignJson.scope.soc -ne 'Ascend310P3') {
            $failures += "the out-of-scope chip was not named, got '$($foreignJson.scope.soc)'"
        } else {
            Write-Host ("  out-of-scope probe refused: soc={0}" -f $foreignJson.scope.soc)
        }
    } catch {
        $failures += "probe route failed: $($_.Exception.Message)"
    }

    try {
        $token = $null
        # The token line and the listener can appear in either order, so poll the
        # log instead of assuming it has already been flushed.
        $tokenDeadline = (Get-Date).AddSeconds(30)
        while (-not $token -and (Get-Date) -lt $tokenDeadline) {
            if (Test-Path $log) {
                $text = Get-Content $log -Raw
                if ($text) {
                    $match = [regex]::Match($text, 'token=([A-Za-z0-9_-]+)')
                    if ($match.Success) { $token = $match.Groups[1].Value }
                }
            }
            if (-not $token) { Start-Sleep -Milliseconds 500 }
        }
        if (-not $token) { $failures += 'the web server did not print its access token' }
        $indexUrl = if ($token) { "http://127.0.0.1:$Port/?token=$token" } else { "http://127.0.0.1:$Port/" }
        $index = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing -TimeoutSec 20
        if ($index.Content -notmatch [regex]::Escape($packageName)) { $failures += 'client bundle missing from the boot graph' }
        else { Write-Host '  boot graph lists the browser bundle' }

        # The visible label lives in the combined browser bundle declared by the
        # boot graph, so check the URL the browser will actually load instead of
        # the uncombined package entry (which only exists as a source chunk).
        $label = [char]0x6607 + [char]0x817E + '310B' + [char]0x8C03 + [char]0x8BD5
        $comboUrls = [regex]::Matches($index.Content, 'plugins/\?\?[^"'']+') |
            ForEach-Object { $_.Value -replace '\\u0026', '&' -replace '&amp;', '&' } |
            Select-Object -Unique
        $combined = ''
        foreach ($combo in $comboUrls) {
            $comboUrl = "http://127.0.0.1:$Port/$combo"
            $combined += (Invoke-WebRequest -Uri $comboUrl -UseBasicParsing -TimeoutSec 20).Content
        }
        if ($combined -notmatch [regex]::Escape($label)) {
            $failures += "the served browser bundle does not carry the $label label (checked $($comboUrls.Count) combo URLs)"
        } else {
            Write-Host "  browser bundle carries the $label label"
        }
    } catch {
        $failures += "index fetch failed: $($_.Exception.Message)"
    }
} finally {
    # `dsh` is a launcher script, so the listening process is a grandchild of the
    # one Start-Process returned: kill the whole tree by the port owner.
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($owner in ($listeners.OwningProcess | Select-Object -Unique)) {
        cmd /c "taskkill /F /T /PID $owner" | Out-Null
    }
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Remove-Item -Recurse -Force $profileRoot -ErrorAction SilentlyContinue
}

if (Test-Path "$log.err") { Get-Content "$log.err" | Where-Object { $_ -match 'did not activate|Error|JsonSchemaError' } }
if (Test-Path $log) { Get-Content $log | Where-Object { $_ -match 'did not activate|Error' } }

if ($failures.Count -gt 0) {
    Write-Error ("verification FAILED:`n  - " + ($failures -join "`n  - "))
    exit 1
}
Write-Host 'verification passed'
