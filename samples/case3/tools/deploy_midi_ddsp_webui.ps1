param(
    [string]$SshTarget = "ascend8t",
    [string]$RemoteRoot = "/home/HwHiAiUser/Documents/case3"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeFiles = @(
    "requirements.txt",
    "realtime_ddsp.py",
    "midi_ddsp_realtime.py",
    "pyacl_ddsp.py",
    "pyacl_midi_ddsp.py"
)
$RuntimeAssetFiles = @(
    "models/om/midi_ddsp_reverb_ir.npz"
)
$ScriptFiles = @(
    "run_webui.py",
    "check_webui_env.py"
)
$ToolFiles = @(
    "run_webui_benchmark_smoke.sh",
    "validate_midi_ddsp_ascend_om.sh",
    "benchmark_midi_ddsp_ascend.sh",
    "compare_midi_ddsp_om.py",
    "summarize_midi_ddsp_benchmark.py"
)

Push-Location (Join-Path $ProjectRoot "webui")
try {
    npm ci
    npm run build
} finally {
    Pop-Location
}

$ResolvedRemoteRoot = (ssh $SshTarget "readlink -f '$RemoteRoot'").Trim()
if ($ResolvedRemoteRoot -ne $RemoteRoot -or $ResolvedRemoteRoot -eq "/") {
    throw "Refusing to clean an unexpected remote path: $ResolvedRemoteRoot"
}
ssh $SshTarget "rm -rf '$ResolvedRemoteRoot/webui/dist'"
ssh $SshTarget "mkdir -p '$RemoteRoot/webui' '$RemoteRoot/midi_ddsp_webui' '$RemoteRoot/scripts' '$RemoteRoot/tools' '$RemoteRoot/doc' '$RemoteRoot/midi' '$RemoteRoot/models/om'"
scp -r (Join-Path $ProjectRoot "webui/dist") "${SshTarget}:$RemoteRoot/webui/"
Get-ChildItem (Join-Path $ProjectRoot "midi_ddsp_webui") -Filter "*.py" | ForEach-Object {
    scp $_.FullName "${SshTarget}:$RemoteRoot/midi_ddsp_webui/"
}
$RuntimeFiles | ForEach-Object {
    scp (Join-Path $ProjectRoot $_) "${SshTarget}:$RemoteRoot/"
}
$RuntimeAssetFiles | ForEach-Object {
    if (-not (Test-Path (Join-Path $ProjectRoot $_))) {
        throw "Required runtime asset is missing: $_"
    }
    scp (Join-Path $ProjectRoot $_) "${SshTarget}:$RemoteRoot/models/om/"
}
ssh $SshTarget "rm -f '$RemoteRoot/requirements-onnx.txt' '$RemoteRoot/requirements-realtime.txt' '$RemoteRoot/requirements-webui.txt'"
$ScriptFiles | ForEach-Object {
    scp (Join-Path $ProjectRoot "scripts/$_") "${SshTarget}:$RemoteRoot/scripts/"
}
ssh $SshTarget "rm -f '$RemoteRoot/run_webui.py' '$RemoteRoot/check_webui_env.py'"
$ToolFiles | ForEach-Object {
    scp (Join-Path $ProjectRoot "tools/$_") "${SshTarget}:$RemoteRoot/tools/"
}

# The board only needs MIDI inputs. Mirror these files so local deletions also
# disappear remotely, while MuseScore project files remain local source assets.
ssh $SshTarget "find '$RemoteRoot/midi' -maxdepth 1 -type f \( -name '*.mid' -o -name '*.midi' \) -delete"
Get-ChildItem (Join-Path $ProjectRoot "midi") -File | Where-Object {
    $_.Extension -in ".mid", ".midi"
} | ForEach-Object {
    scp $_.FullName "${SshTarget}:$RemoteRoot/midi/"
}

scp (Join-Path $ProjectRoot "README.md") "${SshTarget}:$RemoteRoot/"
Get-ChildItem (Join-Path $ProjectRoot "doc") -Filter "*.md" | ForEach-Object {
    scp $_.FullName "${SshTarget}:$RemoteRoot/doc/"
}

Write-Host "Web UI synchronized to ${SshTarget}:$RemoteRoot"
