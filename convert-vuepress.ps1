#!/usr/bin/env pwsh

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$LatexDir = Join-Path $RepoRoot 'latex'
$PandocFrom = 'markdown+yaml_metadata_block+tex_math_dollars+pipe_tables+header_attributes+link_attributes'

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

function Test-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    try {
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'

        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $oldNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
            $PSNativeCommandUseErrorActionPreference = $false
        }

        $null = & $Command @Arguments 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference

        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $PSNativeCommandUseErrorActionPreference = $oldNativeErrorPreference
        }
    }
}

function Get-PythonCommand {
    if ((Get-Command 'python3' -ErrorAction SilentlyContinue) -and (Test-NativeCommand 'python3' @('--version'))) {
        return @{
            Command = 'python3'
            Arguments = @()
        }
    }

    if ((Get-Command 'python' -ErrorAction SilentlyContinue) -and (Test-NativeCommand 'python' @('--version'))) {
        return @{
            Command = 'python'
            Arguments = @()
        }
    }

    if ((Get-Command 'py' -ErrorAction SilentlyContinue) -and (Test-NativeCommand 'py' @('-3', '--version'))) {
        return @{
            Command = 'py'
            Arguments = @('-3')
        }
    }

    throw 'No usable Python 3 command found. Tried python3, python, and py -3.'
}

function Check-BookHeadingLevels {
    $failed = $false
    $bookDir = Join-Path $RepoRoot 'src/book'
    $chapterFiles = @(Get-ChildItem -LiteralPath $bookDir -Filter 'chapter*.md' -File | Sort-Object Name)

    if ($chapterFiles.Count -eq 0) {
        throw "No chapter files found in $bookDir."
    }

    foreach ($file in $chapterFiles) {
        $inFence = $false
        $inYaml = $false
        $lineNumber = 0

        foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
            $lineNumber++

            if ($lineNumber -eq 1 -and $line -eq '---') {
                $inYaml = $true
                continue
            }

            if ($inYaml -and $line -eq '---') {
                $inYaml = $false
                continue
            }

            if ($inYaml) {
                continue
            }

            if ($line -match '^\s*(```|~~~)') {
                $inFence = -not $inFence
                continue
            }

            if (-not $inFence -and $line -match '^#\s+') {
                [Console]::Error.WriteLine('{0}:{1}:{2}', $file.FullName, $lineNumber, $line)
                $failed = $true
            }
        }
    }

    if ($failed) {
        [Console]::Error.WriteLine('error: book chapters are included under explicit \chapter{} entries in latex/book.tex.')
        [Console]::Error.WriteLine('       Use "##" as the top-level heading in src/book/chapter*.md, not "#".')
        exit 1
    }
}

function Convert-Markdown {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InputPath,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath,

        [Parameter(Mandatory = $true)]
        [string]$MediaDir,

        [Parameter(Mandatory = $true)]
        [string]$ResourcePath
    )

    Invoke-CheckedNativeCommand 'pandoc' @(
        '-f', $PandocFrom,
        $InputPath,
        '--top-level-division=chapter',
        '--lua-filter=local-md-links.lua',
        '--syntax-highlighting=idiomatic',
        '-t', 'latex',
        "--extract-media=$MediaDir",
        "--resource-path=$ResourcePath",
        '-o', $OutputPath
    )
}

function Remove-LongTableCapType {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    $texFiles = Get-ChildItem -Path 'chapters', 'cases' -Filter '*.tex' -File -Recurse

    foreach ($texFile in $texFiles) {
        $content = [System.IO.File]::ReadAllText($texFile.FullName)
        $updated = $content -replace '\\def\\LTcaptype\{none\}', ''

        if ($updated -ne $content) {
            [System.IO.File]::WriteAllText($texFile.FullName, $updated, $utf8NoBom)
        }
    }
}

Check-BookHeadingLevels

Push-Location -LiteralPath $LatexDir
try {
    New-Item -ItemType Directory -Force -Path 'chapters', 'cases' | Out-Null

    Invoke-CheckedNativeCommand 'pandoc' @(
        '-f', $PandocFrom,
        '../src/book/README.md',
        '--top-level-division=chapter',
        '--lua-filter=remove-numbering.lua',
        '--lua-filter=local-md-links.lua',
        '--syntax-highlighting=idiomatic',
        '-t', 'latex',
        '--extract-media=chapters/',
        '--resource-path=../src/book',
        '-o', 'chapters/preface.tex'
    )

    foreach ($chapter in 1..9) {
        Convert-Markdown `
            -InputPath "../src/book/chapter$chapter.md" `
            -OutputPath "chapters/chapter$chapter.tex" `
            -MediaDir 'chapters/' `
            -ResourcePath '../src/book'
    }

    Convert-Markdown `
        -InputPath '../src/book/appendix.md' `
        -OutputPath 'chapters/appendix.tex' `
        -MediaDir 'chapters/' `
        -ResourcePath '../src/book'

    foreach ($caseNumber in 0..9) {
        Convert-Markdown `
            -InputPath "../src/experiment/case$caseNumber.md" `
            -OutputPath "cases/case$caseNumber.tex" `
            -MediaDir 'cases/' `
            -ResourcePath '../src/experiment'
    }

    $python = Get-PythonCommand
    Invoke-CheckedNativeCommand `
        -Command $python['Command'] `
        -Arguments @($python['Arguments'] + @('replace_block.py', 'chapters/chapter3.tex'))

    Remove-LongTableCapType

    Invoke-CheckedNativeCommand 'latexmk' @(
        '-xelatex',
        '-interaction=nonstopmode',
        '-file-line-error',
        '-synctex=1',
        '-halt-on-error',
        'book.tex'
    )
}
finally {
    Pop-Location
}
