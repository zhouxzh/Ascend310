param(
    [string]$SshTarget = "ascend8t",
    [string]$RemoteRoot = "/home/HwHiAiUser/Documents/case3",
    [bool]$IncludePianoReference = $true
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeFiles = @(
    "requirements.txt",
    "realtime_ddsp.py",
    "midi_ddsp_realtime.py",
    "pyacl_ddsp.py",
    "pyacl_midi_ddsp.py",
    "prepare_piano_ddsp_models.py"
)
$RuntimeAssetFiles = @(
    "models/om/midi_ddsp_reverb_ir.npz",
    "models/om/ddsp_vst_feature_mixed_float16.om",
    "models/manifests/SHA256SUMS.txt",
    "models/ddsp_vst/metadata.json"
)
$ScriptFiles = @("run_webui.py", "check_webui_env.py")
$ToolFiles = @(
    "assemble_piano_ddsp_om_bundle.py",
    "benchmark_ddsp_vst_effect.py",
    "download_model_release.py",
    "generate_piano_ddsp_reference.py",
    "compare_midi_ddsp_stateful_onnx.py",
    "convert_onnx_to_om.sh",
    "convert_midi_ddsp_stateful_bundle.sh",
    "midi_ddsp_conversion_provenance.py",
    "finalize_midi_ddsp_stateful_bundle.py",
    "validate_piano_ddsp_om.py",
    "create_test_midi.py",
    "create_audio_test_fixtures.py",
    "validate_webui_runtime.py",
    "smoke_test_ddsp_om.py",
    "unroll_piano_ddsp_gru.py"
)
$PianoReleaseId = "model-suite-v1.0.1"
$PianoVariantId = "model-suite-v1.0.1-gru-unrolled"
$PianoBundleId = "model-suite-v1.0.1-gru-unrolled-fp32-origin"
$PianoSourceCommit = "c41911aa7de454aeacf0b3edbb2d06a0801fb3ff"
$PianoReleaseManifestSha256 = "fa6c6f2e3e7f61ec2eb4cd11cf526fa857303cd1b5e29e0b8aa7969a43f9f713"
$PianoChecksumsSha256 = "1a4a2500ae357577a4a6f7378c28d54235f543663b9b69cc3cf5938929c458d7"
$PianoModelIds = @(
    "gru_ir_96_64",
    "film_fdn_128_96",
    "gru_ir_fullwet_96_64",
    "film_ir_fullwet_96_64"
)

if ($RemoteRoot -notmatch '^/home/HwHiAiUser/Documents/case3(?:/[A-Za-z0-9._-]+)*$') {
    throw "RemoteRoot must stay inside /home/HwHiAiUser/Documents/case3: $RemoteRoot"
}

function Invoke-Checked([string]$Description, [scriptblock]$Action) {
    $Result = & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
    return $Result
}

function Invoke-Transfer([string]$Description, [scriptblock]$Action) {
    for ($Attempt = 1; $Attempt -le 4; $Attempt++) {
        $Result = & $Action
        if ($LASTEXITCODE -eq 0) {
            return $Result
        }
        if ($Attempt -lt 4) {
            Write-Warning "$Description failed (attempt $Attempt/4); retrying in 3 seconds"
            Start-Sleep -Seconds 3
        }
    }
    throw "$Description failed after 4 attempts"
}

function Escape-BashSingleQuoted([string]$Command) {
    return $Command -replace "'", ([char]39 + [char]92 + [char]39 + [char]39)
}

function Invoke-Remote([string]$Command) {
    $EscapedCommand = Escape-BashSingleQuoted $Command
    Invoke-Checked "Remote command" { ssh $SshTarget "bash -lc '$EscapedCommand'" }
}

function Invoke-RemoteCapture([string]$Command) {
    $EscapedCommand = Escape-BashSingleQuoted $Command
    return (Invoke-Checked "Remote command" { ssh $SshTarget "bash -lc '$EscapedCommand'" })
}

function Copy-Tree([string]$RelativePath) {
    $Source = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Required source tree is missing: $RelativePath"
    }
    $RemoteTree = "$RemoteRoot/$($RelativePath.Replace('\', '/'))"
    Invoke-Remote "mkdir -p '$RemoteTree'"
    Get-ChildItem -LiteralPath $Source -File -Recurse -Force | Where-Object {
        $_.FullName -notmatch '[\\/](?:__pycache__|\.pytest_cache)[\\/]'
    } | ForEach-Object {
        $RelativeFile = $_.FullName.Substring($Source.Length).TrimStart('\').Replace('\', '/')
        $RemoteParent = (Split-Path -Parent "$RemoteTree/$RelativeFile").Replace('\', '/')
        Invoke-Remote "mkdir -p '$RemoteParent'"
        Invoke-Transfer "Copy $RelativePath/$RelativeFile" { scp $_.FullName "${SshTarget}:$RemoteParent/" }
    }
}

Push-Location (Join-Path $ProjectRoot "webui")
try {
    Invoke-Checked "npm ci" { npm ci }
    Invoke-Checked "npm run build" { npm run build }
} finally {
    Pop-Location
}

$ResolvedRemoteRoot = (Invoke-RemoteCapture "readlink -f '$RemoteRoot'").Trim()
if ($ResolvedRemoteRoot -ne $RemoteRoot -or $ResolvedRemoteRoot -eq "/") {
    throw "Refusing to deploy outside the expected remote path: $ResolvedRemoteRoot"
}

Invoke-Remote "mkdir -p '$RemoteRoot/webui/dist-releases' '$RemoteRoot/midi_ddsp_webui' '$RemoteRoot/piano_ddsp_runtime' '$RemoteRoot/scripts' '$RemoteRoot/tools' '$RemoteRoot/doc' '$RemoteRoot/midi' '$RemoteRoot/models/om' '$RemoteRoot/models/ddsp_vst' '$RemoteRoot/models/piano_ddsp/bundles'"

# Sync package directories recursively. This carries midi_ddsp_webui/vendor/partitura,
# its NOTICE, and any future package data without relying on a top-level *.py glob.
Copy-Tree "midi_ddsp_webui"
Copy-Tree "piano_ddsp_runtime"
$RuntimeFiles | ForEach-Object {
    Invoke-Transfer "Copy $_" { scp (Join-Path $ProjectRoot $_) "${SshTarget}:$RemoteRoot/" }
}
$RuntimeAssetFiles | ForEach-Object {
    $Source = Join-Path $ProjectRoot $_
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Required runtime asset is missing: $_"
    }
    $RemoteAssetFolder = (Split-Path -Parent $_).Replace("\", "/")
    Invoke-Remote "mkdir -p '$RemoteRoot/$RemoteAssetFolder'"
    Invoke-Transfer "Copy $_" { scp $Source "${SshTarget}:$RemoteRoot/$RemoteAssetFolder/" }
}
$ScriptFiles | ForEach-Object {
    Invoke-Transfer "Copy scripts/$_" { scp (Join-Path $ProjectRoot "scripts/$_") "${SshTarget}:$RemoteRoot/scripts/" }
}
$ToolFiles | ForEach-Object {
    Invoke-Transfer "Copy tools/$_" { scp (Join-Path $ProjectRoot "tools/$_") "${SshTarget}:$RemoteRoot/tools/" }
}

$PianoRelease = Join-Path $ProjectRoot "models/piano_ddsp/$PianoReleaseId"
$PianoReleaseManifestPath = Join-Path $PianoRelease "model-suite.json"
$PianoChecksumsPath = Join-Path $PianoRelease "SHA256SUMS"
if (-not (Test-Path $PianoReleaseManifestPath) -or -not (Test-Path $PianoChecksumsPath)) {
    throw "Piano-DDSP release is missing. Run tools/download_model_release.py first."
}
$ReleaseManifestHash = (Get-FileHash -Algorithm SHA256 $PianoReleaseManifestPath).Hash.ToLowerInvariant()
$ReleaseChecksumsHash = (Get-FileHash -Algorithm SHA256 $PianoChecksumsPath).Hash.ToLowerInvariant()
if ($ReleaseManifestHash -ne $PianoReleaseManifestSha256 -or $ReleaseChecksumsHash -ne $PianoChecksumsSha256) {
    throw "Piano-DDSP v1.0.1 release manifest or SHA256SUMS digest is invalid."
}
$PianoReleaseManifest = Get-Content -Raw -Encoding UTF8 $PianoReleaseManifestPath | ConvertFrom-Json
$ReleaseModelIds = @($PianoReleaseManifest.models.PSObject.Properties.Name | Sort-Object)
if (
    $PianoReleaseManifest.schema -ne "ddsp-piano-release/v1" -or
    $PianoReleaseManifest.release -ne $PianoReleaseId -or
    $PianoReleaseManifest.default_model_id -ne "gru_ir_96_64" -or
    $null -ne (Compare-Object ($PianoModelIds | Sort-Object) $ReleaseModelIds)
) {
    throw "Piano-DDSP v1.0.1 release contract is invalid."
}
Invoke-Transfer "Copy Piano-DDSP release" { scp -r $PianoRelease "${SshTarget}:$RemoteRoot/models/piano_ddsp/" }

$PianoGruUnrolled = Join-Path $ProjectRoot "models/piano_ddsp/$PianoVariantId"
$ValidationFiles = @(Get-ChildItem $PianoGruUnrolled -Filter "*.validation.json" -ErrorAction SilentlyContinue)
if ($ValidationFiles.Count -ne $PianoModelIds.Count) {
    throw "Piano-DDSP v1.0.1 gru-unrolled suite must contain four validation reports."
}
Invoke-Transfer "Copy Piano-DDSP unrolled suite" { scp -r $PianoGruUnrolled "${SshTarget}:$RemoteRoot/models/piano_ddsp/" }

if ($IncludePianoReference) {
    $PianoReference = Join-Path $ProjectRoot "models/piano_ddsp/references"
    foreach ($ModelId in $PianoModelIds) {
        $ReferenceRoot = Join-Path $PianoReference "$PianoReleaseId/$ModelId"
        $ReferenceReportPath = Join-Path $ReferenceRoot "report.json"
        if (-not (Test-Path $ReferenceReportPath)) {
            throw "Piano-DDSP reference report is missing for $ModelId."
        }
        $ReferenceReport = Get-Content -Raw -Encoding UTF8 $ReferenceReportPath | ConvertFrom-Json
        $ReferenceNpz = Join-Path $ReferenceRoot ([string]$ReferenceReport.npz)
        if (
            $ReferenceReport.schema -notin @("piano-ddsp-reference/v1", "piano-ddsp-onnx-reference/v2") -or
            $ReferenceReport.model_id -ne $ModelId -or
            [int]$ReferenceReport.frames -lt 10000 -or
            -not (Test-Path $ReferenceNpz)
        ) {
            throw "Piano-DDSP reference contract is invalid for $ModelId."
        }
        $ReferenceHash = (Get-FileHash -Algorithm SHA256 $ReferenceNpz).Hash.ToLowerInvariant()
        if ($ReferenceHash -ne ([string]$ReferenceReport.npz_sha256).ToLowerInvariant()) {
            throw "Piano-DDSP reference SHA256 mismatch for $ModelId."
        }
    }
    Invoke-Transfer "Copy Piano-DDSP references" { scp -r $PianoReference "${SshTarget}:$RemoteRoot/models/piano_ddsp/" }
}

$PianoBundleRoot = Join-Path $ProjectRoot "models/piano_ddsp/bundles/$PianoBundleId"
$PianoBundleManifestPath = Join-Path $PianoBundleRoot "manifest.json"
$PianoBundleChecksumsPath = Join-Path $PianoBundleRoot "SHA256SUMS.txt"
$PianoActivePointerPath = Join-Path $ProjectRoot "models/piano_ddsp/active-bundle.json"
foreach ($RequiredPath in @($PianoBundleManifestPath, $PianoBundleChecksumsPath, $PianoActivePointerPath)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required Piano-DDSP runtime artifact is missing: $RequiredPath"
    }
}
$PianoBundleManifest = Get-Content -Raw -Encoding UTF8 $PianoBundleManifestPath | ConvertFrom-Json
$BundleModelIds = @($PianoBundleManifest.models.PSObject.Properties.Name | Sort-Object)
if (
    $PianoBundleManifest.schema -ne "piano-ddsp-om-bundle/v1" -or
    $PianoBundleManifest.id -ne $PianoBundleId -or
    $PianoBundleManifest.release -ne $PianoReleaseId -or
    $PianoBundleManifest.source_commit -ne $PianoSourceCommit -or
    $PianoBundleManifest.complete -ne $true -or
    $null -ne (Compare-Object ($PianoModelIds | Sort-Object) $BundleModelIds)
) {
    throw "Piano-DDSP active bundle contract is invalid."
}
foreach ($ModelId in $PianoModelIds) {
    $Model = $PianoBundleManifest.models.$ModelId
    if ($Model.validation.passed -ne $true -or [int]$Model.validation.frames -lt 10000) {
        throw "Piano-DDSP bundle model is not qualified: $ModelId"
    }
    $Artifacts = @(
        [PSCustomObject]@{ Path = [string]$Model.om; Hash = [string]$Model.om_sha256 },
        [PSCustomObject]@{ Path = [string]$Model.metadata; Hash = [string]$Model.metadata_sha256 },
        [PSCustomObject]@{ Path = [string]$Model.validation.path; Hash = [string]$Model.validation.sha256 }
    )
    foreach ($Artifact in $Artifacts) {
        $ArtifactPath = Join-Path $PianoBundleRoot $Artifact.Path
        if (
            -not (Test-Path -LiteralPath $ArtifactPath) -or
            (Get-FileHash -Algorithm SHA256 $ArtifactPath).Hash.ToLowerInvariant() -ne $Artifact.Hash.ToLowerInvariant()
        ) {
            throw "Piano-DDSP bundle hash mismatch: $ModelId/$($Artifact.Path)"
        }
    }
}
$PianoActivePointer = Get-Content -Raw -Encoding UTF8 $PianoActivePointerPath | ConvertFrom-Json
if (
    $PianoActivePointer.schema -ne "piano-ddsp-active-bundle/v1" -or
    $PianoActivePointer.bundle_id -ne $PianoBundleId -or
    $PianoActivePointer.manifest -ne "bundles/$PianoBundleId/manifest.json"
) {
    throw "Piano-DDSP active-bundle.json does not select the verified v1.0.1 bundle."
}

$RemotePianoBundle = "$RemoteRoot/models/piano_ddsp/bundles/$PianoBundleId"
$LocalBundleManifestHash = (Get-FileHash -Algorithm SHA256 $PianoBundleManifestPath).Hash.ToLowerInvariant()
$RemoteBundleManifestHash = (Invoke-RemoteCapture "if [ -f '$RemotePianoBundle/manifest.json' ]; then sha256sum '$RemotePianoBundle/manifest.json' | cut -d' ' -f1; fi").Trim()
if ($RemoteBundleManifestHash -and $RemoteBundleManifestHash -ne $LocalBundleManifestHash) {
    throw "Refusing to overwrite an existing Piano-DDSP bundle with different content."
}
if (-not $RemoteBundleManifestHash) {
    $PianoStageId = [DateTime]::UtcNow.ToString("yyyyMMddHHmmss")
    $RemotePianoBundleStage = "$RemoteRoot/models/piano_ddsp/bundles/.stage-$PianoBundleId-$PianoStageId"
    Invoke-Transfer "Stage Piano-DDSP active bundle" { scp -r $PianoBundleRoot "${SshTarget}:$RemotePianoBundleStage" }
    Invoke-Remote "cd '$RemotePianoBundleStage' && sha256sum -c SHA256SUMS.txt && mv '$RemotePianoBundleStage' '$RemotePianoBundle'"
}
$RemoteActivePointerStage = "$RemoteRoot/models/piano_ddsp/.active-bundle-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss')).part"
Invoke-Transfer "Stage Piano-DDSP active pointer" { scp $PianoActivePointerPath "${SshTarget}:$RemoteActivePointerStage" }
Invoke-Remote "mv '$RemoteActivePointerStage' '$RemoteRoot/models/piano_ddsp/active-bundle.json'"

# MIDI inputs are copied additively. Absence of the local ignored library is not
# an error because tests generate their deterministic fixture in a temp directory.
$MidiRoot = Join-Path $ProjectRoot "midi"
if (Test-Path $MidiRoot) {
    Get-ChildItem $MidiRoot -File | Where-Object { $_.Extension -in ".mid", ".midi" } | ForEach-Object {
        Invoke-Transfer "Copy MIDI input $($_.Name)" { scp $_.FullName "${SshTarget}:$RemoteRoot/midi/" }
    }
}

@("README.md", "THIRD_PARTY_NOTICES.md") | ForEach-Object {
    Invoke-Transfer "Copy $_" { scp (Join-Path $ProjectRoot $_) "${SshTarget}:$RemoteRoot/" }
}
Get-ChildItem (Join-Path $ProjectRoot "doc") -Filter "*.md" | ForEach-Object {
    Invoke-Transfer "Copy documentation $($_.Name)" { scp $_.FullName "${SshTarget}:$RemoteRoot/doc/" }
}

# The production bundle is staged and verified before the dist symlink changes.
$DistRoot = Join-Path $ProjectRoot "webui/dist"
$DeployId = "dist-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss") + "-" + (git -C $ProjectRoot rev-parse --short HEAD).Trim()
$ManifestPath = Join-Path ([System.IO.Path]::GetTempPath()) "$DeployId.sha256s"
try {
    $ManifestLines = Get-ChildItem $DistRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        $Relative = $_.FullName.Substring($DistRoot.Length).TrimStart('\').Replace('\', '/')
        "$((Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant())  $Relative"
    }
    [System.IO.File]::WriteAllText(
        $ManifestPath,
        (($ManifestLines -join "`n") + "`n"),
        [System.Text.Encoding]::ASCII
    )

    $RemoteStage = "$RemoteRoot/webui/.stage-$DeployId"
    $RemoteRelease = "$RemoteRoot/webui/dist-releases/$DeployId"
    Invoke-Transfer "Stage frontend bundle" { scp -r $DistRoot "${SshTarget}:$RemoteStage" }
    Invoke-Transfer "Copy frontend hash manifest" { scp $ManifestPath "${SshTarget}:$RemoteStage/.case3-sha256s" }
    Invoke-Remote "cd '$RemoteStage' && sha256sum -c .case3-sha256s && mv '$RemoteStage' '$RemoteRelease'"

    $PreviousDist = (Invoke-RemoteCapture "if [ -L '$RemoteRoot/webui/dist' ]; then readlink '$RemoteRoot/webui/dist'; elif [ -d '$RemoteRoot/webui/dist' ]; then echo directory; fi").Trim()
    Invoke-Remote "if [ -d '$RemoteRoot/webui/dist' ] && [ ! -L '$RemoteRoot/webui/dist' ]; then mv '$RemoteRoot/webui/dist' '$RemoteRoot/webui/dist-releases/legacy-$DeployId'; fi; ln -s 'dist-releases/$DeployId' '$RemoteRoot/webui/.dist-next-$DeployId'; mv -Tf '$RemoteRoot/webui/.dist-next-$DeployId' '$RemoteRoot/webui/dist'"
    $RollbackDist = if ($PreviousDist -eq "directory") { "dist-releases/legacy-$DeployId" } else { $PreviousDist }

    $BoardBootstrap = "source /usr/local/Ascend/ascend-toolkit/set_env.sh >/dev/null 2>&1 || source ~/Ascend/latest/set_env.sh >/dev/null 2>&1 || true; source /usr/local/miniconda3/etc/profile.d/conda.sh; conda activate base; cd '$RemoteRoot'; python -m pip install --upgrade-strategy only-if-needed -r requirements.txt; python -c 'import pytest; print(pytest.__version__)'; python scripts/check_webui_env.py"
    try {
        Invoke-Remote $BoardBootstrap
    } catch {
        if ($RollbackDist) {
            Invoke-Remote "ln -s '$RollbackDist' '$RemoteRoot/webui/.dist-rollback-$DeployId'; mv -Tf '$RemoteRoot/webui/.dist-rollback-$DeployId' '$RemoteRoot/webui/dist'"
        }
        throw
    }
} finally {
    Remove-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Web UI synchronized to ${SshTarget}:$RemoteRoot; webui/dist now points to $DeployId"
