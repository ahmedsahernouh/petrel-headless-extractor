# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Synthetic package regression only; no Petrel source or trial output is read.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('petrel_validation_' + [guid]::NewGuid().ToString('N'))
$validator = Join-Path $PSScriptRoot 'validate_export_package.ps1'
$runner = Join-Path $testRoot 'run_validator.ps1'

try {
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    @'
param([string]$Validator, [string]$Package, [string]$Disappearing, [string]$Marker)
function Get-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$Force)
    if ($Disappearing -ne '__none__' -and $LiteralPath -eq $Disappearing) {
        'intercepted' | Set-Content -LiteralPath $Marker
        Remove-Item -LiteralPath $LiteralPath -Force
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force -ErrorAction Stop
}
& $Validator -ExportPackage $Package -UpdateManifest -WriteChecksums -FailOnMissingExports
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath $runner -Encoding UTF8

    foreach ($case in @('hidden_companion', 'disappearing_companion', 'missing_native', 'missing_companion')) {
        $package = Join-Path $testRoot $case
        $manifestDir = Join-Path $package '00_manifest'
        $metadataDir = Join-Path $package '01_project_metadata'
        New-Item -ItemType Directory -Path $manifestDir, $metadataDir -Force | Out-Null
        '{}' | Set-Content -LiteralPath (Join-Path $metadataDir 'project_summary.json')

        $row = [ordered]@{
            source_object_path = 'synthetic'
            source_object_type = if ($case -eq 'missing_native') { 'petrel_native_store' } else { 'companion_source_preserved' }
            export_format = 'binary'
            export_file = if ($case -eq 'missing_native') { '08_native_store\required.bin' } elseif ($case -eq 'missing_companion') { '09_source_companions\ordinary.xlsx' } else { '09_source_companions\~$Book.xlsx' }
            validation_status = ''
            sha256 = ''
        }
        [pscustomobject]$row | Export-Csv -LiteralPath (Join-Path $manifestDir 'export_manifest.csv') -NoTypeInformation -Encoding UTF8
        $disappearing = '__none__'
        if ($case -eq 'hidden_companion') {
            $hidden = Join-Path $package $row.export_file
            New-Item -ItemType Directory -Path (Split-Path -Parent $hidden) -Force | Out-Null
            'preserved hidden Office lock' | Set-Content -LiteralPath $hidden
            [IO.File]::SetAttributes($hidden, [IO.FileAttributes]::Hidden)
        }
        if ($case -eq 'disappearing_companion') {
            $disappearing = Join-Path $package $row.export_file
            New-Item -ItemType Directory -Path (Split-Path -Parent $disappearing) -Force | Out-Null
            'temporary Office lock' | Set-Content -LiteralPath $disappearing
        }
        $marker = Join-Path $package 'intercepted.txt'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Validator $validator -Package $package -Disappearing $disappearing -Marker $marker | Out-Null
        $code = $LASTEXITCODE
        $report = Get-ChildItem -LiteralPath (Join-Path $package '07_workflows_reports\validation_reports') -Filter '*.json' | Select-Object -First 1
        if ($null -eq $report) { throw "No validation report: $case (exit $code)" }
        $summary = Get-Content -Raw -LiteralPath $report.FullName | ConvertFrom-Json
        $updated = @(Import-Csv -LiteralPath (Join-Path $manifestDir 'export_manifest.csv'))[0]
        if ($case -eq 'hidden_companion') {
            if ($code -ne 0 -or $summary.validated_count -ne 1 -or $summary.skipped_transient_count -ne 0 -or
                $updated.validation_status -ne 'validated' -or $updated.sha256 -notmatch '^[0-9a-f]{64}$') {
                throw "Present hidden companion was not hashed and validated (exit $code)"
            }
        } elseif ($case -eq 'disappearing_companion') {
            if ($code -ne 0 -or -not (Test-Path -LiteralPath $marker) -or $summary.status -ne 'passed' -or
                $summary.skipped_transient_count -ne 1 -or $summary.results[0].status -ne 'skipped_transient_companion' -or
                $updated.validation_status -ne 'skipped_transient_companion' -or $updated.sha256 -or
                ((Test-Path -LiteralPath (Join-Path $manifestDir 'checksums_sha256.txt')) -and
                    (Get-Content -Raw -LiteralPath (Join-Path $manifestDir 'checksums_sha256.txt')).Trim())) {
                throw "Disappearing transient was not skipped cleanly (exit $code)"
            }
        } elseif ($code -ne 2 -or $summary.status -ne 'failed' -or $summary.missing_export_count -ne 1 -or
                  $summary.results[0].status -ne 'failed' -or $summary.results[0].issues -notcontains 'file_not_found') {
            throw "Missing required export was not rejected: $case (exit $code)"
        }
    }
    'Hidden companion, transient race and strict missing-export checks passed.'
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
        if (-not $resolvedTestRoot.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path -Leaf $resolvedTestRoot) -notlike 'petrel_validation_*') { throw 'Unsafe test cleanup path' }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
