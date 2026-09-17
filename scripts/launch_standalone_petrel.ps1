# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Position=0)][string]$ProjectFile = "",
    [Parameter(Position=1)][string]$OutputRoot = "",
    [Parameter(Position=2)][ValidateSet("inventory", "copy", "convert")][string]$CompanionMode = "convert",
    [Parameter(Position=3)][string]$ProjectName = "",
    [Parameter(Position=4)][string]$PetrelVersion = "unknown",
    [switch]$NoPause,
    [switch]$ReportOnly,
    [switch]$FullHash,
    [ValidateSet('time','depth')][string]$Domain,
    [ValidateSet('s','ms','us')][string]$VerticalUnit,
    [ValidateSet('m','ft')][string]$HorizontalUnit,
    [string]$Crs,
    [Alias('-inspect')][switch]$Inspect,
    [Alias('-capabilities')][switch]$Capabilities,
    [Alias('-check')][switch]$Check,
    [Alias('-help')][switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$toolkitRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $toolkitRoot "runtime\python.exe"
$pauseAtEnd = (-not $NoPause) -and [string]::IsNullOrWhiteSpace($OutputRoot)
$exitCode = 1
$transcriptStarted = $false
$diagnosticsRoot = Join-Path $toolkitRoot 'build\diagnostics'
New-Item -ItemType Directory -Path $diagnosticsRoot -Force | Out-Null
$env:GEOVIEWER_BOOTSTRAP_LOG = Join-Path $diagnosticsRoot ('bootstrap_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0,6) + '.txt')
try { Start-Transcript -Path $env:GEOVIEWER_BOOTSTRAP_LOG -ErrorAction Stop | Out-Null; $transcriptStarted = $true } catch { Write-Output 'Bootstrap transcript unavailable; extraction diagnostics will still be written.' }
function Invoke-ZgyInput {
    $zgyArgs = @{ InputFile=$ProjectFile; OutputRoot=$OutputRoot; NoPause=$NoPause; FullHash=$FullHash; ReportOnly=$ReportOnly; Inspect=$Inspect; Capabilities=$Capabilities }
    foreach ($field in @('Domain','VerticalUnit','HorizontalUnit','Crs')) {
        $value=Get-Variable -Name $field -ValueOnly
        if ($value) { $zgyArgs[$field]=$value }
    }
    & (Join-Path $PSScriptRoot 'launch_zgy_conversion.ps1') @zgyArgs
}
try {
    if ($Help -or $ProjectFile -in @("--help", "/?")) {
        Write-Output 'Usage: GeoViewer_data_extractor.bat "PROJECT.pet" [OUTPUT_ROOT] [convert|copy|inventory] [LABEL] [PETREL_VERSION] [-NoPause]'
        Write-Output 'Full report/inventory always runs. Add -ReportOnly to disable dataset conversion (enabled by default).'
        Write-Output 'The same BAT accepts INPUT.zgy, -Inspect, -Capabilities and optional -FullHash (off by default).'
        Write-Output 'Or: GeoViewer_data_extractor.bat --check -NoPause'
        exit 0
    }
    Write-Output "GeoViewer_data_extractor 1.0.0 - standalone, read-only"
    if ([System.IO.Path]::GetExtension($ProjectFile) -ieq '.zgy' -or $Inspect -or $Capabilities) {
        $pauseAtEnd=$false
        Invoke-ZgyInput
        exit $LASTEXITCODE
    }
    . (Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1')
    $dependencyCheck = Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot
    if ($Check -or $ProjectFile -eq "--check") {
        & $pythonExe -B (Join-Path $PSScriptRoot "standalone_petrel_extract.py") --check
        $exitCode = $LASTEXITCODE
    } else {
        if ([string]::IsNullOrWhiteSpace($ProjectFile)) {
            $launcherRoot = if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $toolkitRoot) 'GeoViewer_data_extractor.bat')) { Split-Path -Parent $toolkitRoot } else { $toolkitRoot }
            $pairs = @(Get-ChildItem -LiteralPath $launcherRoot -File -Filter '*.pet' | Where-Object {
                Test-Path -LiteralPath (Join-Path $_.DirectoryName ($_.BaseName + '.ptd')) -PathType Container
            } | Sort-Object Name)
            if ($pairs.Count -eq 1) { $ProjectFile = $pairs[0].FullName }
            elseif ($pairs.Count -gt 1) {
                for ($i = 0; $i -lt $pairs.Count; $i++) { Write-Output ("{0}. {1}" -f ($i + 1), $pairs[$i].Name) }
                $selection = Read-Host "Project number"
                $number = 0
                if (-not [int]::TryParse($selection, [ref]$number) -or $number -lt 1 -or $number -gt $pairs.Count) { throw "Invalid project number." }
                $ProjectFile = $pairs[$number - 1].FullName
            } else { $ProjectFile = ([string](Read-Host "Full path to the .pet project or .zgy file")).Trim().Trim('"') }
            if ([System.IO.Path]::GetExtension($ProjectFile) -ieq '.zgy') {
                $pauseAtEnd=$false
                Invoke-ZgyInput
                exit $LASTEXITCODE
            }
            if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
                $OutputRoot = ([string](Read-Host 'Output root [Enter for your user folder\Petrel_Extracts]')).Trim().Trim('"')
            }
        }
        if ($pauseAtEnd -and -not $ReportOnly -and -not $PSBoundParameters.ContainsKey('CompanionMode')) {
            Write-Output 'Full visual report and complete inventory: always included.'
            do { $convertAnswer = ([string](Read-Host 'Convert supported data as well? [Y/n; Enter = Yes]')).Trim().ToLowerInvariant() } while ($convertAnswer -notin @('', 'y', 'yes', 'n', 'no'))
            $ReportOnly = $convertAnswer -in @('n', 'no')
        }
        if ($ReportOnly) { $CompanionMode = 'inventory' }
        if ($pauseAtEnd -and -not $PSBoundParameters.ContainsKey('FullHash')) {
            do { $hashAnswer = ([string](Read-Host 'Calculate full seismic SHA-256? [y/N; Enter = No]')).Trim().ToLowerInvariant() } while ($hashAnswer -notin @('', 'y', 'yes', 'n', 'no'))
            $FullHash = $hashAnswer -in @('y','yes')
        }
        Write-Output $(if ($FullHash) { 'Full seismic hashing: ON (reads entire source files).' } else { 'Full seismic hashing: OFF. Metadata, previews and numerical conversion QC remain available.' })
        Write-Output $(if ($ReportOnly) { 'Selected: full report + inventory. Dataset conversion: OFF.' } else { 'Selected: full report + inventory. Dataset conversion: ON (supported profiles).' })
        if ([string]::IsNullOrWhiteSpace($OutputRoot)) { $OutputRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Petrel_Extracts' }
        $nativeArgs = @('--project-file', $ProjectFile, '--output-root', $OutputRoot, '--mode', $CompanionMode, '--petrel-version', $PetrelVersion)
        if ($ReportOnly) { $nativeArgs += '--report-only' }
        if ($FullHash) { $nativeArgs += '--full-hash' }
        if (-not [string]::IsNullOrWhiteSpace($ProjectName)) { $nativeArgs += @('--label', $ProjectName) }
        & $pythonExe -B (Join-Path $PSScriptRoot "standalone_petrel_extract.py") @nativeArgs
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    $exitCode = 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    if ($pauseAtEnd) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode
