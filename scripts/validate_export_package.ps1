# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Mandatory = $true)]
    [string]$ExportPackage,

    [switch]$UpdateManifest,

    [switch]$WriteChecksums,

    [switch]$FailOnMissingExports
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePath,

        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }

    return (Join-Path $BasePath $PathValue)
}

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePath,

        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    $baseUri = [System.Uri]((Resolve-Path -LiteralPath $BasePath).ProviderPath.TrimEnd("\") + "\")
    $fileUri = [System.Uri]((Resolve-Path -LiteralPath $PathValue).ProviderPath)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fileUri).ToString()).Replace("/", "\")
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $sha = $null
    $stream = $null
    $bufferedStream = $null
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $bufferedStream = [System.IO.BufferedStream]::new($stream, 1MB)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bufferedStream))).Replace("-", "").ToLowerInvariant()
    } finally {
        if ($null -ne $bufferedStream) {
            $bufferedStream.Dispose()
            $stream = $null
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        if ($null -ne $sha) {
            $sha.Dispose()
        }
    }
}

function Test-LasFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $result = [ordered]@{
        is_las = $false
        has_version_section = $false
        has_well_section = $false
        has_curve_section = $false
        has_ascii_section = $false
        curve_count = 0
        issues = @()
    }

    $lines = Get-Content -LiteralPath $Path -TotalCount 400 -ErrorAction Stop
    $section = ""
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^~') {
            if ($trimmed -match '^~V') {
                $result.has_version_section = $true
                $section = "version"
            } elseif ($trimmed -match '^~W') {
                $result.has_well_section = $true
                $section = "well"
            } elseif ($trimmed -match '^~C') {
                $result.has_curve_section = $true
                $section = "curve"
            } elseif ($trimmed -match '^~A') {
                $result.has_ascii_section = $true
                $section = "ascii"
            } else {
                $section = "other"
            }
            continue
        }

        if ($section -eq "curve" -and $trimmed -and -not $trimmed.StartsWith("#")) {
            $result.curve_count += 1
        }
    }

    $result.is_las = $result.has_version_section -and $result.has_well_section -and $result.has_curve_section

    if (-not $result.has_version_section) { $result.issues += "missing_version_section" }
    if (-not $result.has_well_section) { $result.issues += "missing_well_section" }
    if (-not $result.has_curve_section) { $result.issues += "missing_curve_section" }
    if ($result.curve_count -eq 0) { $result.issues += "no_curves_detected" }

    return $result
}

$packageRoot = (Resolve-Path -LiteralPath $ExportPackage).ProviderPath
$manifestPath = Join-Path $packageRoot "00_manifest\export_manifest.csv"
$summaryPath = Join-Path $packageRoot "01_project_metadata\project_summary.json"
$validationRoot = Join-Path $packageRoot "07_workflows_reports\validation_reports"
$checksumPath = Join-Path $packageRoot "00_manifest\checksums_sha256.txt"

$requiredPaths = @(
    $manifestPath,
    $summaryPath,
    (Join-Path $packageRoot "00_manifest")
)

$missingRequired = @()
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
        $missingRequired += $path
    }
}

if ($missingRequired.Count -gt 0) {
    throw "Export package is missing required paths: $($missingRequired -join '; ')"
}

New-Item -ItemType Directory -Force -Path $validationRoot | Out-Null

$rows = @(Import-Csv -LiteralPath $manifestPath)
$results = @()
$checksumLines = @()
$missingExportCount = 0
$validatedCount = 0
$failedCount = 0
$skippedTransientCount = 0

foreach ($row in $rows) {
    $exportFile = [string]$row.export_file
    $rowStatus = "not_recorded"
    $issues = @()
    $sha256 = ""
    $lengthBytes = 0
    $las = $null
    $transientCompanion = $false

    if ([string]::IsNullOrWhiteSpace($exportFile)) {
        $issues += "missing_export_file_value"
    } else {
        $fullPath = Resolve-FullPath -BasePath $packageRoot -PathValue $exportFile
        $transientCompanion = ([string]$row.source_object_type -eq "companion_source_preserved" -and
            (Split-Path -Path $fullPath -Leaf) -like '~$*')
        try {
            if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
                $issues += "file_not_found"
            } else {
                # Preserved companions may be hidden (not absent), notably Office lock files.
                $fileItem = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
                $lengthBytes = $fileItem.Length
                $sourceObjectType = [string]$row.source_object_type
                if ($lengthBytes -le 0 -and $sourceObjectType -ne "petrel_native_store") {
                    $issues += "empty_file"
                }

                $sha256 = Get-Sha256Hex -Path $fullPath

                if ($fileItem.Extension -ieq ".las") {
                    $las = Test-LasFile -Path $fullPath
                    if (-not $las.is_las) {
                        $issues += $las.issues
                    }
                }
                $relative = Get-RelativePath -BasePath $packageRoot -PathValue $fullPath
                $checksumLines += "$sha256 *$relative"
            }
        } catch {
            if (-not $transientCompanion -or (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
                throw
            }
            $issues = @("file_not_found")
            $sha256 = ""
            $lengthBytes = 0
            $las = $null
        }
    }

    if ($transientCompanion -and $issues -contains "file_not_found") {
        $rowStatus = "skipped_transient_companion"
        $skippedTransientCount += 1
    } elseif ($issues.Count -eq 0) {
        $rowStatus = "validated"
        $validatedCount += 1
    } else {
        $rowStatus = "failed"
        $failedCount += 1
        if ($issues -contains "file_not_found" -or $issues -contains "missing_export_file_value") {
            $missingExportCount += 1
        }
    }

    if ($UpdateManifest) {
        if ($row.PSObject.Properties.Name -contains "validation_status") {
            $row.validation_status = $rowStatus
        }
        if ($row.PSObject.Properties.Name -contains "sha256") {
            $row.sha256 = $sha256
        }
    }

    $results += [pscustomobject]@{
        source_object_path = $row.source_object_path
        source_object_type = $row.source_object_type
        export_format = $row.export_format
        export_file = $exportFile
        status = $rowStatus
        length_bytes = $lengthBytes
        sha256 = $sha256
        issues = $issues
        las = $las
    }
}

if ($UpdateManifest -and $rows.Count -gt 0) {
    $rows | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8
}

if ($WriteChecksums) {
    $checksumLines | Sort-Object | Set-Content -LiteralPath $checksumPath -Encoding UTF8
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$summary = [ordered]@{
    export_package = $packageRoot
    manifest_path = $manifestPath
    validated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    row_count = $rows.Count
    validated_count = $validatedCount
    failed_count = $failedCount
    skipped_transient_count = $skippedTransientCount
    missing_export_count = $missingExportCount
    status = if ($rows.Count -eq 0) { "no_exports_recorded" } elseif ($failedCount -eq 0) { "passed" } else { "failed" }
    results = $results
}

$jsonPath = Join-Path $validationRoot "export_validation_$stamp.json"
$mdPath = Join-Path $validationRoot "export_validation_$stamp.md"
$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$md = @(
    "# Export Validation Report",
    "",
    "- Export package: $packageRoot",
    "- Validated UTC: $($summary.validated_at_utc)",
    "- Manifest rows: $($summary.row_count)",
    "- Validated rows: $validatedCount",
    "- Failed rows: $failedCount",
    "- Skipped transient companions: $skippedTransientCount",
    "- Status: $($summary.status)",
    "",
    "## Results",
    ""
)

if ($rows.Count -eq 0) {
    $md += "- No export rows are recorded yet."
} else {
    foreach ($item in $results) {
        $issueText = if ($item.issues.Count -gt 0) { $item.issues -join ", " } else { "none" }
        $md += "- $($item.status): $($item.export_file) [$($item.export_format)] issues=$issueText"
    }
}

$md | Set-Content -LiteralPath $mdPath -Encoding UTF8

Write-Output "Validation status: $($summary.status)"
Write-Output "Rows: $($summary.row_count), validated: $validatedCount, failed: $failedCount, skipped transient: $skippedTransientCount"
Write-Output "Report: $mdPath"

if ($FailOnMissingExports -and ($rows.Count -eq 0 -or $missingExportCount -gt 0 -or $failedCount -gt 0)) {
    exit 2
}
