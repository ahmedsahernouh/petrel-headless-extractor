# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param(
    [Parameter(Mandatory=$true)][ValidateSet('discover','run','status')][string]$Action,
    [string]$ScanRoot = '',
    [Parameter(Mandatory=$true)][string]$CampaignDirectory,
    [int]$MaxProjects = 0,
    [string]$PackageRoot = '',
    [switch]$LocateOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Test-Package([string]$Root) {
    return ((Test-Path -LiteralPath (Join-Path $Root 'FieldViewer_CRS_Audit.ps1') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Root 'FieldViewer/scripts/fieldviewer_crs_batch.py') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Root 'FieldViewer/toolkit.json') -PathType Leaf))
}
try {
    if (-not [string]::IsNullOrWhiteSpace($PackageRoot)) {
        if (-not [IO.Path]::IsPathRooted($PackageRoot)) { throw 'PackageRoot must be an absolute path.' }
        $candidate = [IO.Path]::GetFullPath($PackageRoot)
        if (-not (Test-Package $candidate)) { throw 'The explicit PackageRoot is not a complete Field Viewer CRS-audit package.' }
    } else {
        $candidate = ''
        $folder = [IO.DirectoryInfo]$PSScriptRoot
        while ($null -ne $folder) {
            if (Test-Package $folder.FullName) { $candidate = $folder.FullName; break }
            $folder = $folder.Parent
        }
        if (-not $candidate -and -not [string]::IsNullOrWhiteSpace($env:FIELDVIEWER_HOME)) {
            if (-not [IO.Path]::IsPathRooted($env:FIELDVIEWER_HOME)) { throw 'FIELDVIEWER_HOME must be an absolute path.' }
            $candidate = [IO.Path]::GetFullPath($env:FIELDVIEWER_HOME)
            if (-not (Test-Package $candidate)) { throw 'FIELDVIEWER_HOME does not identify a complete Field Viewer CRS-audit package.' }
        }
        if (-not $candidate) { throw 'Package not found beside the skill. Supply -PackageRoot or set FIELDVIEWER_HOME to the extracted package folder.' }
    }
    if ($LocateOnly) {
        [pscustomobject]@{package_root=$candidate;launcher=(Join-Path $candidate 'FieldViewer_CRS_Audit.ps1')} | ConvertTo-Json -Compress
        exit 0
    }
    $values = @{Action=$Action;CampaignDirectory=$CampaignDirectory;MaxProjects=$MaxProjects}
    if ($ScanRoot) { $values.ScanRoot = $ScanRoot }
    & (Join-Path $candidate 'FieldViewer_CRS_Audit.ps1') @values
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
