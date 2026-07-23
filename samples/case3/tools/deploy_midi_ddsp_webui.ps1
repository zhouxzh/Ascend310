param(
    [string]$SshTarget = "ascend8t",
    [string]$RemoteRoot = "/home/HwHiAiUser/Documents/case3"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $ProjectRoot "webui")
try {
    npm ci
    npm run build
} finally {
    Pop-Location
}

ssh $SshTarget "mkdir -p '$RemoteRoot/webui' '$RemoteRoot/midi_ddsp_webui' '$RemoteRoot/tools' '$RemoteRoot/doc'"
scp -r (Join-Path $ProjectRoot "webui/dist") "${SshTarget}:$RemoteRoot/webui/"
Get-ChildItem (Join-Path $ProjectRoot "midi_ddsp_webui") -Filter "*.py" | ForEach-Object {
    scp $_.FullName "${SshTarget}:$RemoteRoot/midi_ddsp_webui/"
}
scp (Join-Path $ProjectRoot "requirements-webui.txt") "${SshTarget}:$RemoteRoot/"
scp (Join-Path $ProjectRoot "realtime_ddsp.py") "${SshTarget}:$RemoteRoot/"
scp (Join-Path $ProjectRoot "midi_ddsp_realtime.py") "${SshTarget}:$RemoteRoot/"
scp (Join-Path $ProjectRoot "run_webui.py") "${SshTarget}:$RemoteRoot/"
scp (Join-Path $ProjectRoot "check_webui_env.py") "${SshTarget}:$RemoteRoot/"
scp (Join-Path $ProjectRoot "tools/run_webui_benchmark_smoke.sh") "${SshTarget}:$RemoteRoot/tools/"
scp (Join-Path $ProjectRoot "doc/webui.md") "${SshTarget}:$RemoteRoot/doc/"

Write-Host "Web UI synchronized to ${SshTarget}:$RemoteRoot"
