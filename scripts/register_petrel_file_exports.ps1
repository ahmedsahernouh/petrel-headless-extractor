# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Mandatory = $true)]
    [string]$ExportPackage,

    [string]$ProjectName = "Petrel2010 demo project",

    [string]$PetrelVersion = "2018.2.0.5333",

    [string]$InventoryPackage = "",

    [switch]$RegisterUnknown,

    [switch]$FailOnUnmapped
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function ConvertTo-Slug {
    param([string]$Value)

    $slug = $Value.ToLowerInvariant() -replace "[^a-z0-9]+", "_"
    $slug = $slug.Trim("_")
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "exported_file"
    }
    return $slug
}

function Get-ShortHash {
    param([string]$Value)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value.ToLowerInvariant())
        $hashBytes = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()).Substring(0, 10)
    } finally {
        $sha.Dispose()
    }
}

function New-ManifestRow {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Headers
    )

    $row = [ordered]@{}
    foreach ($header in $Headers) {
        $row[$header] = ""
    }
    return $row
}

function Get-ObjectValue {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return ""
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) {
        return ""
    }

    return [string]$property.Value
}

function Normalize-ObjectName {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    return ($Value.ToLowerInvariant() -replace "[^a-z0-9]+", "")
}

function Find-InventoryMatch {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileInfo]$FileItem,

        [object[]]$InventoryRows
    )

    $fileKey = Normalize-ObjectName $FileItem.BaseName
    if ([string]::IsNullOrWhiteSpace($fileKey)) {
        return $null
    }

    foreach ($inventoryRow in $InventoryRows) {
        $objectName = Get-ObjectValue -Object $inventoryRow -Name "object_name"
        $objectKey = Normalize-ObjectName $objectName
        if ([string]::IsNullOrWhiteSpace($objectKey) -or $objectKey.Length -lt 3) {
            continue
        }

        if ($fileKey -eq $objectKey -or $fileKey.Contains($objectKey) -or $objectKey.Contains($fileKey)) {
            return $inventoryRow
        }
    }

    return $null
}

function Get-FormatFromExtension {
    param(
        [hashtable]$FormatMap,
        [string]$Extension,
        [string]$Fallback
    )

    $normalized = $Extension.ToLowerInvariant()
    if ($FormatMap.ContainsKey($normalized)) {
        return $FormatMap[$normalized]
    }

    if (-not [string]::IsNullOrWhiteSpace($Fallback)) {
        return $Fallback
    }

    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return "UNKNOWN"
    }

    return $normalized.TrimStart(".").ToUpperInvariant()
}

function Get-SourceTypeOverride {
    param(
        [string]$RelativePath,
        [string]$SourceObjectType
    )

    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_zero_gui_sources.csv") {
        return "well_top_reference"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_native_binary_probe.csv") {
        return "well_top_native_probe"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_source_ascii.csv") {
        return "well_top_source_ascii"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_native_decode_attempt.csv") {
        return "well_top_native_decode_attempt"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_petrel_gui_paste.csv") {
        return "well_top_gui_ground_truth"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_gui_vs_source_ascii_compare.csv") {
        return "well_top_comparison_report"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_manual01.txt") {
        return "well_top_petrel_ascii_export"
    }
    if ($RelativePath -ilike "02_wells\well_tops\well_tops_exportpilot_*.txt") {
        return "well_top_petrel_ascii_export"
    }
    if ($RelativePath -ilike "02_wells\well_tops\*.crsmeta.xml") {
        return "well_top_crs_metadata"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_petrel_ascii_export.csv") {
        return "well_top_petrel_ascii_export_parsed"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_export_vs_gui_compare.csv") {
        return "well_top_petrel_ascii_export_comparison"
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_export_vs_source_ascii_compare.csv") {
        return "well_top_petrel_ascii_export_comparison"
    }

    return $SourceObjectType
}

function Get-RegistrationNotes {
    param(
        [string]$RelativePath,
        [bool]$IsUnmapped
    )

    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_zero_gui_sources.csv") {
        return "Zero-GUI well-top reference inventory only; actual Petrel marker pick depths were not decoded/exported."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_native_binary_probe.csv") {
        return "Zero-GUI native binary well-top evidence/candidate probe; canonical top/history strings and LAS zone-log candidates only, not confirmed marker pick-depth export."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_source_ascii.csv") {
        return "Parsed local Petrel Well Tops ASCII source table with real pick rows; useful zero-GUI fallback, but not decoded from the native binary marker-pick payload."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_native_decode_attempt.csv") {
        return "Native decoding attempt output; currently populated from the local Petrel Well Tops ASCII source while native binary marker-pick payload decoding remains unconfirmed."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_petrel_gui_paste.csv") {
        return "Manual Petrel GUI Well Tops table paste parsed into structured rows; used as ground-truth validation for source/native well-top decoding, not native-binary-confirmed."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_gui_vs_source_ascii_compare.csv") {
        return "Comparison between the Petrel GUI Well Tops paste and the current source-ASCII fallback; highlights missing rows and numeric deltas for native decoder targeting."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_manual01.txt") {
        return "Petrel-authored Well Tops ASCII export saved manually from the Petrel UI; contains actual marker-pick rows and depths, but was not created by zero-GUI workflow insertion."
    }
    if ($RelativePath -ilike "02_wells\well_tops\well_tops_exportpilot_*.txt") {
        return "Petrel-authored Well Tops ASCII export written by the ExportPiloX donor capture path; contains actual marker-pick rows and depths if Petrel export validation succeeds."
    }
    if ($RelativePath -ilike "02_wells\well_tops\*.crsmeta.xml") {
        return "Petrel CRS metadata sidecar for the manually saved Well Tops ASCII export."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_from_petrel_ascii_export.csv") {
        return "Parsed Petrel-authored Well Tops ASCII export with actual marker-pick rows and depths; Petrel export confirmed, native binary decode not confirmed."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_export_vs_gui_compare.csv") {
        return "Comparison between the Petrel-authored Well Tops ASCII export and the manual GUI table paste."
    }
    if ($RelativePath -ieq "02_wells\well_tops\well_tops_petrel_ascii_export_vs_source_ascii_compare.csv") {
        return "Comparison between the Petrel-authored Well Tops ASCII export and the 98-row source-ASCII fallback."
    }

    if ($IsUnmapped) {
        return "Auto-registered from export package folder scan; no matching inventory row was found."
    }

    return "Auto-registered from export package folder scan."
}

$packageRoot = (Resolve-Path -LiteralPath $ExportPackage).ProviderPath
$manifestPath = Join-Path $packageRoot "00_manifest\export_manifest.csv"
$sourceInventoryPath = Join-Path $packageRoot "00_manifest\source_object_inventory.csv"
$runDir = Join-Path $packageRoot "07_workflows_reports\automation_runs"
$summaryPath = Join-Path $packageRoot "01_project_metadata\project_summary.json"

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Export manifest not found: $manifestPath"
}

if (-not (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf) -and -not [string]::IsNullOrWhiteSpace($InventoryPackage)) {
    $inventoryCandidate = Join-Path $InventoryPackage "00_manifest\object_inventory.csv"
    if (Test-Path -LiteralPath $inventoryCandidate -PathType Leaf) {
        $sourceInventoryPath = $inventoryCandidate
    }
}

New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$headerLine = Get-Content -LiteralPath $manifestPath -TotalCount 1
if ([string]::IsNullOrWhiteSpace($headerLine)) {
    throw "Export manifest is empty: $manifestPath"
}

$headers = $headerLine.Split(",") | ForEach-Object { $_.Trim('"') }
$requiredHeaders = @(
    "export_id",
    "project_name",
    "petrel_version",
    "export_date_utc",
    "source_object_path",
    "source_object_type",
    "export_format",
    "export_file",
    "export_status",
    "validation_status",
    "notes"
)

$missingHeaders = @($requiredHeaders | Where-Object { $headers -notcontains $_ })
if ($missingHeaders.Count -gt 0) {
    throw "Export manifest is missing required columns: $($missingHeaders -join ', ')"
}

$projectSummary = $null
if (Test-Path -LiteralPath $summaryPath -PathType Leaf) {
    $projectSummary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
}

$inventoryRows = @()
if (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf) {
    $inventoryRows = @(Import-Csv -LiteralPath $sourceInventoryPath)
}

$defaultCrs = Get-ObjectValue -Object $projectSummary -Name "coordinate_reference_system"
$defaultXyUnits = Get-ObjectValue -Object $projectSummary -Name "xy_units"
$defaultZUnits = Get-ObjectValue -Object $projectSummary -Name "depth_units"

$exportTargets = @(
    [pscustomobject]@{ Folder = "02_wells\well_headers"; SourceType = "well"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "02_wells\well_trajectories"; SourceType = "well_trajectory"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "02_wells\well_logs_las"; SourceType = "well_log"; DefaultFormat = "LAS"; Formats = @{ ".las" = "LAS" } },
    [pscustomobject]@{ Folder = "02_wells\well_tops"; SourceType = "well_top"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xlsx" = "XLSX"; ".xml" = "XML" } },
    [pscustomobject]@{ Folder = "02_wells\checkshots_vsp"; SourceType = "checkshot_vsp"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "03_seismic\segy"; SourceType = "seismic_cube"; DefaultFormat = "SEG-Y"; Formats = @{ ".sgy" = "SEG-Y"; ".segy" = "SEG-Y" } },
    [pscustomobject]@{ Folder = "03_seismic\zgy"; SourceType = "seismic_cube"; DefaultFormat = "ZGY"; Formats = @{ ".zgy" = "ZGY" } },
    [pscustomobject]@{ Folder = "03_seismic\zgy_arrays"; SourceType = "seismic_cube_array"; DefaultFormat = "NPY"; Formats = @{ ".npy" = "NPY"; ".json" = "JSON" } },
    [pscustomobject]@{ Folder = "03_seismic\navigation"; SourceType = "seismic_navigation"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "03_seismic\seismic_metadata"; SourceType = "seismic_metadata"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".json" = "JSON"; ".txt" = "TEXT"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "04_surfaces_maps\zmap_dat"; SourceType = "surface"; DefaultFormat = "ZMAP DAT"; Formats = @{ ".dat" = "ZMAP DAT"; ".zmap" = "ZMAP DAT"; ".grd" = "GRID ASCII" } },
    [pscustomobject]@{ Folder = "04_surfaces_maps\xyz_ascii"; SourceType = "surface"; DefaultFormat = "XYZ ASCII"; Formats = @{ ".xyz" = "XYZ ASCII"; ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII" } },
    [pscustomobject]@{ Folder = "04_surfaces_maps\grids_metadata"; SourceType = "surface_metadata"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".json" = "JSON"; ".txt" = "TEXT"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "05_interpretation\horizons"; SourceType = "horizon"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xyz" = "XYZ ASCII" } },
    [pscustomobject]@{ Folder = "05_interpretation\faults"; SourceType = "fault"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xyz" = "XYZ ASCII" } },
    [pscustomobject]@{ Folder = "05_interpretation\polygons"; SourceType = "polygon"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xyz" = "XYZ ASCII" } },
    [pscustomobject]@{ Folder = "05_interpretation\points"; SourceType = "point_set"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xyz" = "XYZ ASCII" } },
    [pscustomobject]@{ Folder = "06_models_properties\grids"; SourceType = "model_grid"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".xyz" = "XYZ ASCII"; ".grdecl" = "GRDECL" } },
    [pscustomobject]@{ Folder = "06_models_properties\properties"; SourceType = "model_property"; DefaultFormat = "ASCII"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".dat" = "ASCII"; ".grdecl" = "GRDECL" } },
    [pscustomobject]@{ Folder = "06_models_properties\zones_layers"; SourceType = "zones_layers"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".txt" = "ASCII"; ".asc" = "ASCII"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "06_models_properties\simulation_exports"; SourceType = "simulation_export"; DefaultFormat = "ECLIPSE"; Formats = @{ ".data" = "ECLIPSE DATA"; ".grdecl" = "GRDECL"; ".inc" = "ECLIPSE INCLUDE"; ".txt" = "TEXT" } },
    [pscustomobject]@{ Folder = "07_workflows_reports\exported_reports"; SourceType = "data_table"; DefaultFormat = "CSV"; Formats = @{ ".csv" = "CSV"; ".tsv" = "TSV"; ".txt" = "TEXT"; ".xlsx" = "XLSX" } },
    [pscustomobject]@{ Folder = "07_workflows_reports\project_audit"; SourceType = "project_audit_report"; DefaultFormat = "HTML"; Formats = @{ ".html" = "HTML"; ".json" = "JSON"; ".md" = "MD" } }
)

$rowsToRegister = @()
$skipped = @()
$unmapped = @()

foreach ($target in $exportTargets) {
    $folderPath = Join-Path $packageRoot $target.Folder
    if (-not (Test-Path -LiteralPath $folderPath -PathType Container)) {
        continue
    }

    $files = @(Get-ChildItem -LiteralPath $folderPath -File -Recurse)
    foreach ($fileItem in $files) {
        $ownedRelative = Get-RelativePath -BasePath $packageRoot -PathValue $fileItem.FullName
        if ($ownedRelative -match '(^|[\\/])native_data[\\/].*\.partial[\\/]') {
            $skipped += [pscustomobject]@{ file = $ownedRelative; reason = 'uncommitted_native_staging' }
            continue
        }
        if ($fileItem.Name -ieq "cli_variable_probe.csv") {
            $skipped += [pscustomobject]@{ file = Get-RelativePath -BasePath $packageRoot -PathValue $fileItem.FullName; reason = "owned_by_workflow_artifact_registrar" }
            continue
        }

        $extension = $fileItem.Extension.ToLowerInvariant()
        if (-not $target.Formats.ContainsKey($extension) -and -not $RegisterUnknown) {
            $skipped += [pscustomobject]@{ file = Get-RelativePath -BasePath $packageRoot -PathValue $fileItem.FullName; reason = "unknown_extension" }
            continue
        }

        $relativePath = Get-RelativePath -BasePath $packageRoot -PathValue $fileItem.FullName
        $inventoryMatch = Find-InventoryMatch -FileItem $fileItem -InventoryRows $inventoryRows
        $sourceObjectPath = Get-ObjectValue -Object $inventoryMatch -Name "explorer_path"
        $sourceObjectType = Get-ObjectValue -Object $inventoryMatch -Name "object_type"

        if ([string]::IsNullOrWhiteSpace($sourceObjectPath)) {
            $sourceObjectPath = "File export/$relativePath"
            $unmapped += $relativePath
        }
        if ([string]::IsNullOrWhiteSpace($sourceObjectType)) {
            $sourceObjectType = $target.SourceType
        }
        $sourceObjectType = Get-SourceTypeOverride -RelativePath $relativePath -SourceObjectType $sourceObjectType

        $row = New-ManifestRow -Headers $headers
        $row["export_id"] = "file_export_$(ConvertTo-Slug $relativePath)_$(Get-ShortHash $relativePath)"
        $row["project_name"] = $ProjectName
        $row["petrel_version"] = $PetrelVersion
        $row["export_date_utc"] = $fileItem.LastWriteTimeUtc.ToString("o")
        $row["source_object_path"] = $sourceObjectPath
        $row["source_object_type"] = $sourceObjectType
        $row["export_format"] = Get-FormatFromExtension -FormatMap $target.Formats -Extension $extension -Fallback $target.DefaultFormat
        $row["export_file"] = $relativePath
        $row["coordinate_reference_system"] = Get-ObjectValue -Object $inventoryMatch -Name "coordinate_reference_system"
        $row["xy_units"] = Get-ObjectValue -Object $inventoryMatch -Name "xy_units"
        $row["z_domain"] = Get-ObjectValue -Object $inventoryMatch -Name "domain"
        $row["z_units"] = Get-ObjectValue -Object $inventoryMatch -Name "z_units"
        $row["null_value"] = Get-ObjectValue -Object $inventoryMatch -Name "null_value"
        $row["template_or_property_unit"] = Get-ObjectValue -Object $inventoryMatch -Name "template_or_property_unit"
        $row["export_status"] = "exported"
        $row["validation_status"] = "unchecked"
        $isUnmapped = $unmapped -contains $relativePath
        $row["notes"] = Get-RegistrationNotes -RelativePath $relativePath -IsUnmapped $isUnmapped

        if ([string]::IsNullOrWhiteSpace($row["coordinate_reference_system"])) {
            $row["coordinate_reference_system"] = $defaultCrs
        }
        if ([string]::IsNullOrWhiteSpace($row["xy_units"])) {
            $row["xy_units"] = $defaultXyUnits
        }
        if ([string]::IsNullOrWhiteSpace($row["z_units"])) {
            $row["z_units"] = $defaultZUnits
        }
        $rowsToRegister += [pscustomobject]$row
    }
}

$existingRows = @(Import-Csv -LiteralPath $manifestPath)
$rowsByFile = @{}
$rowsById = @{}
foreach ($candidate in $rowsToRegister) {
    $rowsByFile[[string]$candidate.export_file] = $candidate
    $rowsById[[string]$candidate.export_id] = $candidate
}

$usedIds = @{}
$newRows = @()
$updatedCount = 0
foreach ($existingRow in $existingRows) {
    $replacement = $null
    $existingFile = [string]$existingRow.export_file
    $existingId = [string]$existingRow.export_id

    if (-not [string]::IsNullOrWhiteSpace($existingFile) -and $rowsByFile.ContainsKey($existingFile)) {
        $replacement = $rowsByFile[$existingFile]
    } elseif (-not [string]::IsNullOrWhiteSpace($existingId) -and $rowsById.ContainsKey($existingId)) {
        $replacement = $rowsById[$existingId]
    }

    if ($null -ne $replacement) {
        $newRows += $replacement
        $usedIds[[string]$replacement.export_id] = $true
        $updatedCount += 1
    } else {
        $newRows += $existingRow
    }
}

$appendedCount = 0
foreach ($candidate in $rowsToRegister) {
    if (-not $usedIds.ContainsKey([string]$candidate.export_id)) {
        $newRows += $candidate
        $usedIds[[string]$candidate.export_id] = $true
        $appendedCount += 1
    }
}

if ($updatedCount -gt 0 -or $appendedCount -gt 0) {
    $newRows | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $runDir "petrel_file_export_registration_$stamp.json"
$status = "no_file_exports_found"
if ($rowsToRegister.Count -gt 0) {
    $status = "registered"
}
if ($FailOnUnmapped -and $unmapped.Count -gt 0) {
    $status = "unmapped_exports_found"
}

$summary = [ordered]@{
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    export_package = $packageRoot
    manifest_path = $manifestPath
    inventory_path = if (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf) { $sourceInventoryPath } else { "" }
    scanned_targets = @($exportTargets | ForEach-Object { $_.Folder })
    registered_count = $rowsToRegister.Count
    updated_count = $updatedCount
    appended_count = $appendedCount
    skipped_count = $skipped.Count
    unmapped_count = $unmapped.Count
    skipped_files = $skipped
    unmapped_files = $unmapped
    status = $status
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Output "File export registration: $status"
Write-Output "Registered: $($rowsToRegister.Count)"
Write-Output "Updated: $updatedCount"
Write-Output "Appended: $appendedCount"
Write-Output "Unmapped: $($unmapped.Count)"
Write-Output "Report: $reportPath"

if ($FailOnUnmapped -and $unmapped.Count -gt 0) {
    exit 2
}
