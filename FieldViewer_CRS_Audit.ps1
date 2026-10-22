# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param(
    [Parameter(Mandatory=$true)][ValidateSet('discover','run','status')][string]$Action,
    [string]$ScanRoot = '',
    [Parameter(Mandatory=$true)][string]$CampaignDirectory,
    [int]$MaxProjects = 0
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    $engine = Join-Path $PSScriptRoot 'FieldViewer'
    $python = Join-Path $engine 'runtime/python.exe'
    $batch = Join-Path $engine 'scripts/fieldviewer_crs_batch.py'
    if (-not (Test-Path -LiteralPath $batch -PathType Leaf)) { throw 'Extract the complete standalone package beside this launcher.' }
    if ($Action -ne 'status' -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $engine 'scripts/launch_standalone_petrel.ps1') -Check -NoPause
        if ($LASTEXITCODE -ne 0) { throw 'Bundled dependency check failed. Review its diagnostics before retrying.' }
    }
    if ($Action -eq 'discover') {
        if ([string]::IsNullOrWhiteSpace($ScanRoot)) { throw '-ScanRoot is required for discovery.' }
        $values = @('-B',$batch,'discover','--root',$ScanRoot,'--output',$CampaignDirectory)
    } else {
        $values = @('-B',$batch,$Action,'--campaign',$CampaignDirectory)
        if ($MaxProjects -lt 0) { throw 'MaxProjects must be positive when supplied.' }
        if ($Action -eq 'run' -and $MaxProjects -gt 0) { $values += @('--max-projects',[string]$MaxProjects) }
    }
    & $python @values
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
