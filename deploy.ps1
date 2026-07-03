#!/usr/bin/env pwsh

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$DistDir = Join-Path $RepoRoot 'src/.vuepress/dist'
$RemoteUrl = 'https://github.com/zhouxzh/Ascend310.git'

function Invoke-CheckedNativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command $($Arguments -join ' ')"
    }
}

Push-Location -LiteralPath $RepoRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'node_modules') -PathType Container)) {
        throw 'node_modules was not found. Run "pnpm install --frozen-lockfile" before deploying.'
    }

    Invoke-CheckedNativeCommand 'pnpm' @('run', 'docs:build')

    if (-not (Test-Path -LiteralPath $DistDir -PathType Container)) {
        throw "VuePress dist directory was not found: $DistDir"
    }

    Push-Location -LiteralPath $DistDir
    try {
        Invoke-CheckedNativeCommand 'git' @('init', '-b', 'main')
        Invoke-CheckedNativeCommand 'git' @('config', 'core.autocrlf', 'false')
        Invoke-CheckedNativeCommand 'git' @('add', '-A')
        Invoke-CheckedNativeCommand 'git' @('commit', '-m', 'deploy')
        Invoke-CheckedNativeCommand 'git' @('push', '-f', $RemoteUrl, 'main:deploy')
    }
    finally {
        Pop-Location
    }

    Write-Host 'Deploy complete.'
}
finally {
    Pop-Location
}
