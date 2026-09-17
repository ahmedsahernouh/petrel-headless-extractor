# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Website: https://saherlabs.dev/
# Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
param(
    [Parameter(Position=0)][string]$InputFile = '',
    [Parameter(Position=1)][string]$OutputRoot = '',
    [ValidateSet('time','depth')][string]$Domain,
    [ValidateSet('s','ms','us')][string]$VerticalUnit,
    [ValidateSet('m','ft')][string]$HorizontalUnit,
    [string]$Crs,
    [Alias('-inspect')][switch]$Inspect,
    [Alias('-capabilities')][switch]$Capabilities,
    [Alias('-help')][switch]$Help,
    [switch]$NoPause,
    [switch]$FullHash,
    [switch]$NoFullHash,
    [switch]$ReportOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$toolkitRoot = Split-Path -Parent $PSScriptRoot
$exitCode = 1
$interactive = (-not $NoPause) -and [string]::IsNullOrWhiteSpace($OutputRoot)
$ownsSupport = -not $env:GEOVIEWER_SUPPORT_SESSION
$supportSession = $null; $launcherFailure = $null
if ($ownsSupport) {
    . (Join-Path $PSScriptRoot 'geoviewer_support.ps1')
    $supportSession = Start-GeoViewerSupport -ToolkitRoot $toolkitRoot -Options $PSBoundParameters
}
$hashSpecified = $PSBoundParameters.ContainsKey('FullHash') -or $PSBoundParameters.ContainsKey('NoFullHash')
if (-not $PSBoundParameters.ContainsKey('FullHash')) { $FullHash = -not $NoFullHash }
try {
    if ($FullHash -and $NoFullHash) { throw 'Choose either -FullHash or -NoFullHash, not both.' }
    if ($Help) {
        Write-Output 'GeoViewer_data_extractor.bat "INPUT.zgy" [OUTPUT_ROOT] [-Domain time] [-VerticalUnit ms] [-HorizontalUnit m] [-Crs "identifier"] [-NoFullHash] [-NoPause]'
        Write-Output 'Use -Inspect to read metadata or -Capabilities to list supported profiles. Double-click or drag a ZGY to enter missing metadata interactively.'
        $exitCode=0; exit 0
    }
    Write-Output 'GeoViewer_data_extractor 1.0.0 - seismic to SEG-Y - beta, source read-only'
    . (Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1')
    $dependencyCheck = Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot
    # Keep all interactive reads in ConsoleHost. Mixing Read-Host with a child
    # Python input() can lose buffered answers when launched from the main BAT.
    if ($interactive -and -not $Inspect -and -not $Capabilities) {
        if (-not $InputFile) { $InputFile=([string](Read-Host 'Full path to the .zgy file')).Trim().Trim('"') }
        $metadataCode = @'
import json,sys
from pathlib import Path
from standalone_petrel_extract import preflight
from petrel_file_convert import open_zgy,zgy_metadata
preflight()
with open_zgy(Path(sys.argv[1])) as reader:
    print(json.dumps(zgy_metadata(reader)))
'@
        $metadataText = & (Join-Path $toolkitRoot 'runtime\python.exe') -B -c $metadataCode $InputFile
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect ZGY metadata. Check the source path and error above.' }
        $metadata = ($metadataText -join "`n") | ConvertFrom-Json
        Write-Output ($metadata | ConvertTo-Json -Depth 5)
        if (-not $ReportOnly) {
            do { $answer=([string](Read-Host 'Convert supported data as well? [Y/n; Enter = Yes]')).Trim().ToLowerInvariant() } while ($answer -notin @('','y','yes','n','no'))
            $ReportOnly=$answer -in @('n','no')
        }
        if (-not $ReportOnly) {
            if ($metadata.zunit_dimension -eq 'unknown' -and -not $Domain) { $Domain=([string](Read-Host 'Verified domain (time/depth)')).Trim() }
            if (-not $metadata.zunit_name -and -not $VerticalUnit) { $VerticalUnit=([string](Read-Host 'Verified vertical unit (s/ms/us)')).Trim() }
            if (-not $metadata.horizontal_unit -and -not $HorizontalUnit) { $HorizontalUnit=([string](Read-Host 'Verified horizontal unit (m/ft)')).Trim() }
            if (-not $Crs) { $Crs=([string](Read-Host 'CRS identifier [Enter keeps unknown]')).Trim(); if (-not $Crs) { $Crs='unknown' } }
        }
        if (-not $OutputRoot) { $OutputRoot=([string](Read-Host 'Output root [Enter for your user folder\Petrel_Conversions]')).Trim().Trim('"') }
        if (-not $hashSpecified) {
            do { $answer=([string](Read-Host 'Calculate full seismic SHA-256? [Y/n; Enter = Yes]')).Trim().ToLowerInvariant() } while ($answer -notin @('','y','yes','n','no'))
            $FullHash=$answer -notin @('n','no')
        }
    }
    $arguments = @('-B', (Join-Path $PSScriptRoot 'petrel_file_convert.py'))
    if ($Capabilities) { $arguments += '--capabilities' }
    if ($Inspect) { $arguments += '--inspect' }
    if ($InputFile) { $arguments += @('--input', $InputFile) }
    if ($OutputRoot) { $arguments += @('--output-root', $OutputRoot) }
    if ($Domain) { $arguments += @('--domain', $Domain) }
    if ($VerticalUnit) { $arguments += @('--vertical-unit', $VerticalUnit) }
    if ($HorizontalUnit) { $arguments += @('--horizontal-unit', $HorizontalUnit) }
    if ($Crs) { $arguments += @('--crs', $Crs) }
    if ($FullHash) { $arguments += '--full-hash' } else { $arguments += '--no-full-hash' }
    if ($ReportOnly) { $arguments += '--report-only' }
    & (Join-Path $toolkitRoot 'runtime\python.exe') @arguments
    $exitCode = $LASTEXITCODE
} catch {
    $launcherFailure = $_
    Write-Output ('ERROR: ' + $_.Exception.Message)
    if (-not $ownsSupport) {
        Write-Output $_.Exception.ToString()
        Write-Output $_.ScriptStackTrace
        Write-Output $_.InvocationInfo.PositionMessage
    }
} finally {
    if ($ownsSupport) { Stop-GeoViewerSupport -ToolkitRoot $toolkitRoot -Session $supportSession -ExitCode $exitCode -Failure $launcherFailure }
    if ($interactive -and $ownsSupport) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode
