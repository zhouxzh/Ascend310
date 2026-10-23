# Throwaway patch-composition check.
#
# `dsh --dump-config` resolves a patch row against the profile's installed
# bundles before it can report whether the row reached an entry, and the desktop
# profile refuses to boot outside the Electron app. This script therefore builds
# a disposable profile that installs this plugin, copies the 310B rows out of a
# real profile patch byte-for-byte, and dumps the composed tree.
#
# Windows PowerShell 5.1 decodes string literals with the machine code page, so
# every file here is written from UTF-8 bytes and no generated text is built
# from a decoded template.
#
# Usage:
#   powershell -File scripts/check-patch-compose.ps1 `
#       -ProfilePatch "$env:USERPROFILE\.dsh\profiles\desktop\cordis.patch.yml"

[CmdletBinding()]
param(
    [string]$PluginRoot = '',
    [string]$ProfilePatch = ''
)

$ErrorActionPreference = 'Stop'
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
    $PluginRoot = (Split-Path -Parent $PSScriptRoot)
}
$PluginRoot = (Resolve-Path $PluginRoot).Path
if ([string]::IsNullOrWhiteSpace($ProfilePatch)) {
    $ProfilePatch = Join-Path $dshHome 'profiles\desktop\cordis.patch.yml'
}
$ProfilePatch = (Resolve-Path $ProfilePatch).Path

$rootName = 'patchcheck-' + [guid]::NewGuid().ToString('N')
$root = Join-Path $dshHome "profiles\$rootName"
$pluginName = ([System.IO.File]::ReadAllText(
    (Join-Path $PluginRoot 'package.json'),
    [System.Text.Encoding]::UTF8
) | ConvertFrom-Json).name
if (-not $pluginName) { throw "could not read the plugin name from $PluginRoot\package.json" }

# The rows under test, taken verbatim from the real profile patch so this check
# validates the deployed YAML rather than a copy that can drift.
$patchText = [System.IO.File]::ReadAllText($ProfilePatch, [System.Text.Encoding]::UTF8)
$lines = $patchText -split "`n"
$start = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Trim() -eq "- id: $pluginName") { $start = $i; break }
}
if ($start -lt 0) { throw "no '- id: $pluginName' row found in $ProfilePatch" }
$end = $lines.Count
for ($i = $start + 1; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^- id: ') { $end = $i; break }
}
$rows = ($lines[$start..($end - 1)] -join "`n").TrimEnd() + "`n"
# Never write profile passwords into a temp YAML or dump. The values are not
# needed to validate YAML structure or patch resolution.
$rows = [regex]::Replace(
    $rows,
    '(?m)^(\s*(?:password|sudoPassword|rootPassword)\s*:\s*).*$',
    '$1''<redacted>'''
)

Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $root 'node_modules') | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $root 'package.json'),
    "{`n  `"name`": `"dsh-profile-patchcheck`",`n  `"private`": true,`n  `"dsh`": { `"profile`": { `"bundles`": [" +
        "`"@deepseek-ai/dsh-base`", `"@deepseek-ai/dsh-web-app`", `"$pluginName`" ] } }`n}`n",
    $utf8
)
Copy-Item -Recurse -Force $PluginRoot (Join-Path $root "node_modules\$pluginName")

$patch = Join-Path $env:TEMP ("ascend310-patchcheck-" + [guid]::NewGuid().ToString('N') + '.yml')
[System.IO.File]::WriteAllText($patch, $rows, $utf8)

$dump = ''
$dumpFile = Join-Path $env:TEMP ("ascend310-patchcheck-dump-" + [guid]::NewGuid().ToString('N') + '.txt')
try {
    # `dsh` writes diagnostics on stderr; the file keeps them out of the console
    # so the composed tree can be inspected without mixed formatting.
    & cmd /c "dsh --profile $rootName --patch `"$patch`" --dump-config > `"$dumpFile`" 2>&1"
    if (Test-Path $dumpFile) {
        $dump = [System.IO.File]::ReadAllText($dumpFile, [System.Text.Encoding]::UTF8)
    }
} finally {
    Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
    Remove-Item $patch -Force -ErrorAction SilentlyContinue
    Remove-Item $dumpFile -Force -ErrorAction SilentlyContinue
}

$failures = @()
if ([string]::IsNullOrWhiteSpace($dump)) { $failures += 'the profile dump produced no output' }
if ($dump -match 'patch: entry') { $failures += 'the patch rows did not resolve to a profile entry' }
if ($dump -notmatch [regex]::Escape($pluginName)) { $failures += 'the plugin is missing from the composed tree' }
if ($dump -match 'YAMLException') { $failures += 'the profile patch is not valid YAML' }
if ($dump -match 'JsonSchemaError') { $failures += 'the composed config failed schema validation' }
if ($dump -notmatch 'ascend310b-debug') { $failures += 'the plugin config row is missing from the composed config' }

# The dump is checked against the rows actually found in the profile patch, so a
# target silently dropped by the patch layer is caught without this script
# knowing any board address itself.
$targetIds = @($rows -split "`n" | ForEach-Object {
    if ($_ -match '^\s{4,}- id:\s*(\S+)\s*$') { $Matches[1] }
})
foreach ($targetId in $targetIds) {
    if ($dump -notmatch [regex]::Escape($targetId)) { $failures += "target $targetId is missing from the composed config" }
}

if ($failures.Count -gt 0) {
    Write-Error ("patch composition FAILED:`n  - " + ($failures -join "`n  - "))
    exit 1
}
Write-Host "patch composition passed: $pluginName resolved, $($targetIds.Count) target rows carried from the real profile patch"
