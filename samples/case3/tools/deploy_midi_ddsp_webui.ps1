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
    "models/om/midi_ddsp_reverb_ir.npz",
    "models/ddsp_vst/metadata.json"
)
$ScriptFiles = @(
    "run_webui.py",
    "check_webui_env.py"
)
$ToolFiles = @(
    "compare_midi_ddsp_stateful_onnx.py",
    "convert_onnx_to_om.sh",
    "convert_midi_ddsp_stateful_bundle.sh",
    "finalize_midi_ddsp_stateful_bundle.py"
)

Push-Location (Join-Path $ProjectRoot "webui")
try {
    npm ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit code $LASTEXITCODE"
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$ResolvedRemoteRoot = (ssh $SshTarget "readlink -f '$RemoteRoot'").Trim()
if ($ResolvedRemoteRoot -ne $RemoteRoot -or $ResolvedRemoteRoot -eq "/") {
    throw "Refusing to clean an unexpected remote path: $ResolvedRemoteRoot"
}
ssh $SshTarget "rm -rf '$ResolvedRemoteRoot/webui/dist'"
ssh $SshTarget "mkdir -p '$RemoteRoot/webui' '$RemoteRoot/midi_ddsp_webui' '$RemoteRoot/scripts' '$RemoteRoot/tools' '$RemoteRoot/doc' '$RemoteRoot/midi' '$RemoteRoot/models/om' '$RemoteRoot/models/ddsp_vst' '$RemoteRoot/models/midi_ddsp/stateful_v2' '$RemoteRoot/models/midi_ddsp/bundles'"
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
    $RemoteAssetFolder = (Split-Path -Parent $_).Replace("\", "/")
    scp (Join-Path $ProjectRoot $_) "${SshTarget}:$RemoteRoot/$RemoteAssetFolder/"
}
ssh $SshTarget "rm -f '$RemoteRoot/requirements-onnx.txt' '$RemoteRoot/requirements-realtime.txt' '$RemoteRoot/requirements-webui.txt'"
$ScriptFiles | ForEach-Object {
    scp (Join-Path $ProjectRoot "scripts/$_") "${SshTarget}:$RemoteRoot/scripts/"
}
ssh $SshTarget "rm -f '$RemoteRoot/run_webui.py' '$RemoteRoot/check_webui_env.py'"
$ToolFiles | ForEach-Object {
    scp (Join-Path $ProjectRoot "tools/$_") "${SshTarget}:$RemoteRoot/tools/"
}

function Sync-StatefulOnnxExport([string]$ExportName) {
    $StatefulOnnx = Join-Path $ProjectRoot "models/midi_ddsp/$ExportName/onnx"
    $StatefulManifest = Join-Path $StatefulOnnx "export_manifest.json"
    if (-not (Test-Path $StatefulManifest)) {
        return
    }

    $ManifestData = Get-Content -Raw -Encoding UTF8 $StatefulManifest | ConvertFrom-Json
    $ResolvedOnnxRoot = [IO.Path]::GetFullPath($StatefulOnnx)
    $RemoteOnnx = "$RemoteRoot/models/midi_ddsp/$ExportName/onnx"
    ssh $SshTarget "rm -rf '$RemoteOnnx'"
    ssh $SshTarget "mkdir -p '$RemoteOnnx'"
    scp $StatefulManifest "${SshTarget}:$RemoteOnnx/"
    $ManifestData.components.PSObject.Properties | ForEach-Object {
        $ModelPath = [IO.Path]::GetFullPath((Join-Path $StatefulOnnx $_.Value.file))
        if ([IO.Path]::GetDirectoryName($ModelPath) -ne $ResolvedOnnxRoot) {
            throw "Stateful ONNX escapes the export directory: $ModelPath"
        }
        scp $ModelPath "${SshTarget}:$RemoteOnnx/"
    }
}

Sync-StatefulOnnxExport "stateful_v2"
Sync-StatefulOnnxExport "stateful_v2_batched"

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
