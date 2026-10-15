# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Exercise the launcher's actual PowerShell budget assignments and lane CLI
# with a harmless child. No Petrel project or optional Python package is used.
param([string]$PythonPath = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Get-Content -Raw -LiteralPath (Join-Path $scriptDir 'invoke_portable_petrel_extract.ps1')
$match = [regex]::Match($launcher, '(?ms)^# BEGIN lane budget arguments.*?\r?\n(.*?)^# END lane budget arguments')
if (-not $match.Success) { throw 'Launcher lane budget argument block was not found.' }
$budgetAssignments = [scriptblock]::Create($match.Groups[1].Value)
$pythonExe = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).ProviderPath } else { (Get-Command python).Source }
$laneScript = Join-Path $scriptDir 'geoviewer_lanes.py'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('geoviewer_lane_ps_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    $child = Join-Path $testRoot 'fixture_child.py'
    @'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text('child ran', encoding='utf-8')
print('fixture child ran', flush=True)
'@ | Set-Content -LiteralPath $child -Encoding UTF8

    foreach ($mode in @('finite', 'completion')) {
        $RunToCompletion = $mode -eq 'completion'
        $NativeBudgetSeconds = 30
        $CompanionBudgetSeconds = 30
        . $budgetAssignments
        $package = Join-Path $testRoot $mode
        New-Item -ItemType Directory -Path (Join-Path $package '00_manifest') -Force | Out-Null
        'relative_path' | Set-Content -LiteralPath (Join-Path $package '00_manifest/export_manifest.csv')
        $state = Join-Path $package 'stage_budgets.json'
        $nativeLog = Join-Path $package 'native_recovery.log'
        $companionLog = Join-Path $package 'companion.log'
        $spatialLog = Join-Path $package 'native_spatial.log'
        $nativeMarker = Join-Path $package 'native_recovery.ran'
        $companionMarker = Join-Path $package 'companion.ran'
        $spatialMarker = Join-Path $package 'native_spatial.ran'

        & $pythonExe $laneScript --lane native @nativeLaneArgs --state $state --log $nativeLog -- $pythonExe $child $nativeMarker
        if ($LASTEXITCODE -ne 0) { throw "Native recovery lane failed in $mode mode: $LASTEXITCODE" }
        & $pythonExe $laneScript --lane companions @companionLaneArgs --state $state --log $companionLog --companions-package $package -- $pythonExe $child $companionMarker
        if ($LASTEXITCODE -ne 0) { throw "Companion lane failed in $mode mode: $LASTEXITCODE" }
        & $pythonExe $laneScript --lane native @nativeLaneArgs --state $state --log $spatialLog -- $pythonExe $child $spatialMarker
        if ($LASTEXITCODE -ne 0) { throw "Native spatial lane failed in $mode mode: $LASTEXITCODE" }

        $attempts = @((Get-Content -Raw -LiteralPath $state | ConvertFrom-Json).attempts)
        if ($attempts.Count -ne 3) { throw "Expected three recorded lane attempts in $mode mode." }
        foreach ($index in 0..2) {
            $marker = @($nativeMarker, $companionMarker, $spatialMarker)[$index]
            $log = @($nativeLog, $companionLog, $spatialLog)[$index]
            if ((Get-Content -Raw -LiteralPath $marker) -ne 'child ran' -or
                (Get-Content -Raw -LiteralPath $log) -notmatch 'fixture child ran' -or
                $attempts[$index].status -ne 'completed' -or
                $RunToCompletion -ne ($null -eq $attempts[$index].limit_seconds)) {
                throw "Lane child, state, log, or budget mode was incorrect for $mode attempt $index."
            }
        }
    }
    Write-Output 'PowerShell lane invocation integration test passed (finite and completion; three stages each).'
} finally {
    $resolved = [System.IO.Path]::GetFullPath($testRoot)
    $tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not ($resolved + '\').StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside temp root: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
