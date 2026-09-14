param(
    [string]$ProjectName = "",

    [Parameter(Mandatory = $true)]
    [string]$ProjectFile,

    [string]$ProjectPath = "",

    [string]$PetrelVersion = "unknown",

    [string]$ExportRoot = (Join-Path $env:USERPROFILE "Petrel_Extracts"),

    [string]$ExportPackage = "",

    [string]$InventoryPackage = "",

    [int64]$MaxTextProbeBytes = 10485760,

    [int]$MaxCandidatesPerFile = 200,

    [switch]$CreateNewPackage,

    [switch]$SparsePackage,

    [switch]$NoValidate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-Slug {
    param([string]$Value)

    $slug = $Value.ToLowerInvariant() -replace "[^a-z0-9]+", "_"
    $slug = $slug.Trim("_")
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "petrel_native_store"
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

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePath,

        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    $baseUri = [System.Uri]((Resolve-Path -LiteralPath $BasePath).Path.TrimEnd("\") + "\")
    $fileUri = [System.Uri]((Resolve-Path -LiteralPath $PathValue).Path)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fileUri).ToString()).Replace("/", "\")
}

function Get-LatestPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,

        [Parameter(Mandatory = $true)]
        [string]$Pattern
    )

    if (-not (Test-Path -LiteralPath $Root)) {
        return ""
    }

    $item = Get-ChildItem -LiteralPath $Root -Directory -Filter $Pattern |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1

    if ($null -eq $item) {
        return ""
    }

    return $item.FullName
}

function New-ManifestRow {
    param([Parameter(Mandatory = $true)][string[]]$Headers)

    $row = [ordered]@{}
    foreach ($header in $Headers) {
        $row[$header] = ""
    }
    return $row
}

function Get-NativeKind {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceRelativePath,

        [Parameter(Mandatory = $true)]
        [System.IO.FileInfo]$FileItem
    )

    $rel = $SourceRelativePath.Replace("/", "\")
    $ext = $FileItem.Extension.ToLowerInvariant()

    if ($ext -eq ".pet") { return "petrel_project_descriptor" }
    if ($FileItem.Name -ieq "Data.ptd") { return "petrel_data_store" }
    if ($FileItem.Name -ieq "Model.ptd") { return "petrel_model_store" }
    if ($rel -match "\\Ocean\\") { return "ocean_extension_store" }
    if ($rel -match "\\SMD\\GMS\\") { return "structural_modeling_grid_store" }
    if ($rel -match "\\SMD\\") { return "structural_modeling_store" }
    if ($rel -match "\\OceanExtensionData\\") { return "ocean_extension_data_store" }
    if ($ext -eq ".zgy") { return "seismic_zgy_native" }
    if ($ext -eq ".zhz" -or $ext -eq ".zhz_msk") { return "petrel_compressed_grid_store" }
    if ($ext -eq ".db") { return "sqlite_store" }
    if ($ext -eq ".xml" -or $ext -eq ".bxml") { return "xml_or_bxml_metadata" }
    if ($ext -eq ".ptd") { return "petrel_object_store" }
    return "petrel_native_auxiliary"
}

function Get-FormatSignature {
    param([Parameter(Mandatory = $true)][string]$Path)

    $bufferSize = 8192
    $bytesRead = 0
    $buffer = New-Object byte[] $bufferSize
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $bytesRead = $stream.Read($buffer, 0, $buffer.Length)
    } finally {
        $stream.Dispose()
    }

    if ($bytesRead -le 0) {
        return "EMPTY"
    }

    $prefixBytes = $buffer[0..($bytesRead - 1)]
    $ascii = [System.Text.Encoding]::ASCII.GetString($prefixBytes)
    $signatures = New-Object System.Collections.Generic.List[string]

    if ($ascii.StartsWith("SQLite format 3")) { $signatures.Add("SQLITE") }
    if ($ascii -match "<\?xml|<[^>]+>") { $signatures.Add("XML") }
    if ($ascii -match "BXML") { $signatures.Add("BXML") }
    if ($ascii -match "LZ4") { $signatures.Add("LZ4") }
    if ($ascii -match "ZGY") { $signatures.Add("ZGY") }
    if ($ascii -match "Petrel|Slb\.|Schlumberger") { $signatures.Add("PETREL_TEXT") }
    if ($prefixBytes.Length -ge 8 -and $prefixBytes[0] -eq 137 -and $prefixBytes[1] -eq 80 -and $prefixBytes[2] -eq 78 -and $prefixBytes[3] -eq 71) {
        $signatures.Add("PNG")
    }

    if ($signatures.Count -eq 0) {
        return "BINARY"
    }

    return (@($signatures) | Select-Object -Unique) -join ";"
}

function Read-ProbeText {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileInfo]$FileItem,

        [int64]$MaxBytes
    )

    if ($FileItem.Length -le 0 -or $FileItem.Length -gt $MaxBytes) {
        return ""
    }

    $bytes = [System.IO.File]::ReadAllBytes($FileItem.FullName)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    if ([string]::IsNullOrWhiteSpace($text)) {
        $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    }

    return $text
}

function Get-TermFlags {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }

    $terms = @(
        "Well",
        "WellLog",
        "Log",
        "Surface",
        "Horizon",
        "Fault",
        "Seismic",
        "Grid",
        "Property",
        "Workflow",
        "SheetSaveCmd",
        "SystemCmd",
        "Export",
        "BXML",
        "LZ4"
    )

    return (@($terms | Where-Object { $Text -match [regex]::Escape($_) }) | Select-Object -Unique) -join ";"
}

function Infer-CandidateType {
    param(
        [string]$Name,
        [string]$SourceRelativePath,
        [string]$EvidenceKind
    )

    $combined = "$Name $SourceRelativePath $EvidenceKind"
    if ($combined -match "(?i)well.?log|las|log") { return "well_log_or_log_metadata" }
    if ($combined -match "(?i)well") { return "well_or_well_metadata" }
    if ($combined -match "(?i)fault") { return "fault_or_fault_metadata" }
    if ($combined -match "(?i)horizon") { return "horizon_or_horizon_metadata" }
    if ($combined -match "(?i)surface") { return "surface_or_surface_metadata" }
    if ($combined -match "(?i)seismic|zgy|sgy|segy") { return "seismic_or_seismic_metadata" }
    if ($combined -match "(?i)grid|pillar|property") { return "grid_or_property_metadata" }
    if ($combined -match "(?i)workflow|cmd") { return "workflow_command_or_metadata" }
    return "native_object_or_metadata"
}

function Get-NativeCandidates {
    param(
        [string]$Text,
        [string]$SourceRelativePath,
        [int]$Limit
    )

    $candidates = New-Object System.Collections.Generic.List[object]
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @()
    }

    $patterns = @(
        [pscustomobject]@{ Kind = "xml_name"; Regex = "<Name>([^<]{1,220})</Name>" },
        [pscustomobject]@{ Kind = "xml_volcan_name"; Regex = "<VolcanName>([^<]{1,220})</VolcanName>" },
        [pscustomobject]@{ Kind = "xml_display_name"; Regex = "<(?:DisplayName|ObjectName|TemplateName|WindowName)>([^<]{1,220})</(?:DisplayName|ObjectName|TemplateName|WindowName)>" },
        [pscustomobject]@{ Kind = "droid_type_id"; Regex = "Type=([^&<;\s]{1,120})(?:&amp;|&)Id=([^&<;\s]{1,120})" },
        [pscustomobject]@{ Kind = "workflow_command"; Regex = "(SheetSaveCmd|PillarGridCmd|SimpleCmd|SystemCmd|[A-Za-z]{3,40}Cmd)" }
    )

    $seen = @{}
    foreach ($pattern in $patterns) {
        foreach ($match in [regex]::Matches($Text, $pattern.Regex, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
            if ($candidates.Count -ge $Limit) {
                return $candidates.ToArray()
            }

            if ($pattern.Kind -eq "droid_type_id") {
                $name = "$($match.Groups[1].Value):$($match.Groups[2].Value)"
            } else {
                $name = $match.Groups[1].Value
            }

            $name = ($name -replace "\s+", " ").Trim()
            if ([string]::IsNullOrWhiteSpace($name)) {
                continue
            }

            $key = "$($pattern.Kind)|$name|$SourceRelativePath"
            if ($seen.ContainsKey($key)) {
                continue
            }
            $seen[$key] = $true

            $contextStart = [Math]::Max(0, $match.Index - 80)
            $contextLength = [Math]::Min(220, $Text.Length - $contextStart)
            $context = ($Text.Substring($contextStart, $contextLength) -replace "[\r\n\t]+", " " -replace "\s+", " ").Trim()

            $candidates.Add([pscustomobject]@{
                candidate_id = "native_candidate_$(ConvertTo-Slug $SourceRelativePath)_$(Get-ShortHash "$SourceRelativePath|$($pattern.Kind)|$name")"
                candidate_type = Infer-CandidateType -Name $name -SourceRelativePath $SourceRelativePath -EvidenceKind $pattern.Kind
                object_name = $name
                evidence_kind = $pattern.Kind
                source_relative_path = $SourceRelativePath
                context = $context
            })
        }
    }

    return $candidates.ToArray()
}

function Upsert-ManifestRows {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ManifestPath,

        [Parameter(Mandatory = $true)]
        [object[]]$Rows
    )

    $existingRows = @(Import-Csv -LiteralPath $ManifestPath)
    $rowsByFile = @{}
    foreach ($row in $Rows) {
        $rowsByFile[[string]$row.export_file] = $row
    }

    $usedFiles = @{}
    $newRows = @()
    $updated = 0
    foreach ($existing in $existingRows) {
        $file = [string]$existing.export_file
        if (-not [string]::IsNullOrWhiteSpace($file) -and $rowsByFile.ContainsKey($file)) {
            $newRows += $rowsByFile[$file]
            $usedFiles[$file] = $true
            $updated += 1
        } else {
            $newRows += $existing
        }
    }

    $appended = 0
    foreach ($row in $Rows) {
        $file = [string]$row.export_file
        if (-not $usedFiles.ContainsKey($file)) {
            $newRows += $row
            $usedFiles[$file] = $true
            $appended += 1
        }
    }

    if ($Rows.Count -gt 0) {
        $newRows | Export-Csv -LiteralPath $ManifestPath -NoTypeInformation -Encoding UTF8
    }

    return [pscustomobject]@{ updated = $updated; appended = $appended }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$ProjectFile = (Resolve-Path -LiteralPath $ProjectFile).Path
if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = Split-Path -Parent $ProjectFile
}
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$projectStem = [System.IO.Path]::GetFileNameWithoutExtension($ProjectFile)
if ([string]::IsNullOrWhiteSpace($ProjectName)) { $ProjectName = $projectStem }
$ptdDir = Join-Path $ProjectPath "$projectStem.ptd"
if (-not (Test-Path -LiteralPath $ptdDir -PathType Container)) {
    throw "Petrel .ptd directory not found: $ptdDir"
}

if ($CreateNewPackage -or [string]::IsNullOrWhiteSpace($ExportPackage)) {
    if (-not $CreateNewPackage) {
        $ExportPackage = Get-LatestPackage -Root $ExportRoot -Pattern "*_export_*"
    }
    if ($CreateNewPackage -or [string]::IsNullOrWhiteSpace($ExportPackage)) {
        $newExport = Join-Path $scriptDir "new_export_package.ps1"
        $newArgs = @{
            ProjectName = $ProjectName
            ProjectPath = $ProjectPath
            OutputRoot = $ExportRoot
            PetrelVersion = $PetrelVersion
            InventoryPackage = $InventoryPackage
            Scope = "zero_gui_native_project_export"
        }
        if ($SparsePackage) { $newArgs["SparseDirectories"] = $true }
        $newOutput = & $newExport @newArgs
        $ExportPackage = ($newOutput | Select-Object -Last 1) -replace '^Created export package:\s*', ''
    }
}

$ExportPackage = (Resolve-Path -LiteralPath $ExportPackage).Path
$manifestPath = Join-Path $ExportPackage "00_manifest\export_manifest.csv"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Export manifest not found: $manifestPath"
}

$nativeRoot = Join-Path $ExportPackage "08_native_project"
$nativeProjectRoot = Join-Path $nativeRoot "project_file"
$nativeStoreRoot = Join-Path $nativeRoot "ptd_store"
$nativeReportRoot = Join-Path $ExportPackage "07_workflows_reports\native_zero_gui_export"
New-Item -ItemType Directory -Force -Path $nativeProjectRoot, $nativeStoreRoot, $nativeReportRoot | Out-Null

$headers = (Get-Content -LiteralPath $manifestPath -TotalCount 1).Split(",") | ForEach-Object { $_.Trim('"') }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$exportDateUtc = (Get-Date).ToUniversalTime().ToString("o")
$sourceFiles = New-Object System.Collections.Generic.List[System.IO.FileInfo]
$sourceFiles.Add((Get-Item -LiteralPath $ProjectFile))
Get-ChildItem -LiteralPath $ptdDir -Recurse -File |
    Where-Object { $_.Extension -ine ".lock" -and $_.Name -notmatch '\.lock$' } |
    ForEach-Object { $sourceFiles.Add($_) }

$nativeInventory = New-Object System.Collections.Generic.List[object]
$candidateRows = New-Object System.Collections.Generic.List[object]
$manifestRows = New-Object System.Collections.Generic.List[object]
$copiedCount = 0
$textProbeCount = 0
$totalBytes = 0L

foreach ($source in $sourceFiles) {
    $isProjectFile = ($source.FullName -ieq $ProjectFile)
    if ($isProjectFile) {
        $sourceRelative = $source.Name
        $destination = Join-Path $nativeProjectRoot $source.Name
    } else {
        $sourceRelative = Get-RelativePath -BasePath $ptdDir -PathValue $source.FullName
        $destination = Join-Path $nativeStoreRoot $sourceRelative
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $source.FullName -Destination $destination -Force
    $copiedCount += 1
    $totalBytes += $source.Length

    $destinationItem = Get-Item -LiteralPath $destination
    $packageRelative = Get-RelativePath -BasePath $ExportPackage -PathValue $destinationItem.FullName
    $sha256 = Get-Sha256Hex -Path $destinationItem.FullName
    $nativeKind = Get-NativeKind -SourceRelativePath $sourceRelative -FileItem $source
    $signature = Get-FormatSignature -Path $destinationItem.FullName
    $probeText = Read-ProbeText -FileItem $destinationItem -MaxBytes $MaxTextProbeBytes
    $termFlags = Get-TermFlags -Text $probeText
    $fileCandidates = @(Get-NativeCandidates -Text $probeText -SourceRelativePath $sourceRelative -Limit $MaxCandidatesPerFile)
    foreach ($candidate in $fileCandidates) {
        $candidateRows.Add($candidate)
    }
    if (-not [string]::IsNullOrWhiteSpace($probeText)) {
        $textProbeCount += 1
    }

    $nativeInventory.Add([pscustomobject]@{
        native_id = "native_store_$(ConvertTo-Slug $sourceRelative)_$(Get-ShortHash $sourceRelative)"
        source_relative_path = $sourceRelative
        package_relative_path = $packageRelative
        native_kind = $nativeKind
        format_signature = $signature
        extension = $source.Extension
        size_bytes = $destinationItem.Length
        sha256 = $sha256
        text_probe_scanned = -not [string]::IsNullOrWhiteSpace($probeText)
        text_probe_terms = $termFlags
        candidate_count = $fileCandidates.Count
        last_write_time_utc = $source.LastWriteTimeUtc.ToString("o")
    })

    $manifestRow = New-ManifestRow -Headers $headers
    $manifestRow["export_id"] = "zero_gui_native_$(ConvertTo-Slug $sourceRelative)_$(Get-ShortHash $packageRelative)"
    $manifestRow["project_name"] = $ProjectName
    $manifestRow["petrel_version"] = $PetrelVersion
    $manifestRow["export_date_utc"] = $exportDateUtc
    $manifestRow["source_object_path"] = "Petrel native project store/$sourceRelative"
    $manifestRow["source_object_type"] = "petrel_native_store"
    $manifestRow["source_object_uuid"] = ""
    $manifestRow["export_format"] = $signature
    $manifestRow["export_file"] = $packageRelative
    $manifestRow["export_status"] = "exported_zero_gui"
    $manifestRow["validation_status"] = "unchecked"
    $manifestRow["sha256"] = $sha256
    $manifestRow["notes"] = "Zero-GUI native project store copy; proprietary Petrel native format, not universal conversion."
    $manifestRows.Add([pscustomobject]$manifestRow)
}

$nativeInventoryPath = Join-Path $ExportPackage "00_manifest\native_store_inventory.csv"
$candidatePath = Join-Path $ExportPackage "00_manifest\native_object_candidates.csv"
$summaryJsonPath = Join-Path $nativeReportRoot "zero_gui_native_export_$stamp.json"
$summaryMdPath = Join-Path $nativeReportRoot "zero_gui_native_export_$stamp.md"

$nativeInventory | Export-Csv -LiteralPath $nativeInventoryPath -NoTypeInformation -Encoding UTF8
$candidateRows | Export-Csv -LiteralPath $candidatePath -NoTypeInformation -Encoding UTF8

foreach ($reportPath in @($nativeInventoryPath, $candidatePath, $summaryJsonPath, $summaryMdPath)) {
    if ($reportPath -eq $summaryJsonPath -or $reportPath -eq $summaryMdPath) {
        continue
    }
    $reportItem = Get-Item -LiteralPath $reportPath
    $reportRel = Get-RelativePath -BasePath $ExportPackage -PathValue $reportItem.FullName
    $reportHash = Get-Sha256Hex -Path $reportItem.FullName
    $row = New-ManifestRow -Headers $headers
    $row["export_id"] = "zero_gui_report_$(ConvertTo-Slug $reportRel)_$(Get-ShortHash $reportRel)"
    $row["project_name"] = $ProjectName
    $row["petrel_version"] = $PetrelVersion
    $row["export_date_utc"] = $exportDateUtc
    $row["source_object_path"] = "Zero-GUI native export report/$reportRel"
    $row["source_object_type"] = "native_export_report"
    $row["export_format"] = "CSV"
    $row["export_file"] = $reportRel
    $row["export_status"] = "exported_zero_gui"
    $row["validation_status"] = "unchecked"
    $row["sha256"] = $reportHash
    $row["notes"] = "Zero-GUI native export metadata report."
    $manifestRows.Add([pscustomobject]$row)
}

$upsertResult = Upsert-ManifestRows -ManifestPath $manifestPath -Rows $manifestRows.ToArray()

$byKind = @{}
foreach ($row in $nativeInventory) {
    $kind = [string]$row.native_kind
    if (-not $byKind.ContainsKey($kind)) {
        $byKind[$kind] = 0
    }
    $byKind[$kind] += 1
}

$summary = [ordered]@{
    run_id = "zero_gui_native_export_$stamp"
    created_at_utc = $exportDateUtc
    project_name = $ProjectName
    project_file = $ProjectFile
    ptd_directory = $ptdDir
    export_package = $ExportPackage
    runtime_gui_used = $false
    petrel_process_launched = $false
    export_mode = "zero_gui_native_project_store_copy"
    universal_conversion_status = "not_attempted_proprietary_native_decode_required"
    copied_file_count = $copiedCount
    copied_total_bytes = $totalBytes
    text_probe_file_count = $textProbeCount
    candidate_count = $candidateRows.Count
    native_inventory_path = $nativeInventoryPath
    native_object_candidates_path = $candidatePath
    native_store_root = $nativeRoot
    manifest_path = $manifestPath
    manifest_updated = $upsertResult.updated
    manifest_appended = $upsertResult.appended
    native_kind_counts = $byKind
}

$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryJsonPath -Encoding UTF8

$summaryLines = @(
    "# Zero-GUI Native Project Export",
    "",
    "- Project: $ProjectName",
    "- Project file: $ProjectFile",
    "- PTD directory: $ptdDir",
    "- Export package: $ExportPackage",
    "- Runtime GUI used: false",
    "- Petrel launched: false",
    "- Copied files: $copiedCount",
    "- Copied bytes: $totalBytes",
    "- Text-probed files: $textProbeCount",
    "- Native object candidates: $($candidateRows.Count)",
    "- Manifest updated rows: $($upsertResult.updated)",
    "- Manifest appended rows: $($upsertResult.appended)",
    "",
    "## Boundary",
    "",
    "This is a complete zero-GUI native-store export of the Petrel project files into the universal package structure.",
    "It is not yet a universal-format geological conversion. LAS, SEG-Y, ZMAP, RESQML, and grid/property conversions still require Petrel export commands, Ocean, or decoded native object layouts.",
    "",
    "## Reports",
    "",
    "- Native store inventory: ``00_manifest\native_store_inventory.csv``",
    "- Native object candidates: ``00_manifest\native_object_candidates.csv``",
    "- Summary JSON: ``$((Get-RelativePath -BasePath $ExportPackage -PathValue $summaryJsonPath))``"
)
$summaryLines | Set-Content -LiteralPath $summaryMdPath -Encoding UTF8

$upsertResult = Upsert-ManifestRows -ManifestPath $manifestPath -Rows $manifestRows.ToArray()

$validationStatus = "skipped"
$validationReport = ""
if (-not $NoValidate) {
    $validator = Join-Path $scriptDir "validate_export_package.ps1"
    $validationOutput = & $validator -ExportPackage $ExportPackage -UpdateManifest -WriteChecksums
    $validationStatus = (($validationOutput | Select-Object -First 1) -replace '^Validation status:\s*', '')
    $validationReportLine = @($validationOutput | Where-Object { $_ -match '^Report:' } | Select-Object -First 1)
    if ($validationReportLine.Count -gt 0) {
        $validationReport = ($validationReportLine[0] -replace '^Report:\s*', '')
    }
}

$summary["validation_status"] = $validationStatus
$summary["validation_report"] = $validationReport
$summary["manifest_updated"] = $upsertResult.updated
$summary["manifest_appended"] = $upsertResult.appended
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryJsonPath -Encoding UTF8

Write-Output "Zero-GUI native export: completed"
Write-Output "Export package: $ExportPackage"
Write-Output "Copied files: $copiedCount"
Write-Output "Copied bytes: $totalBytes"
Write-Output "Native object candidates: $($candidateRows.Count)"
Write-Output "Native inventory: $nativeInventoryPath"
Write-Output "Candidate index: $candidatePath"
Write-Output "Summary: $summaryJsonPath"
Write-Output "Validation: $validationStatus"

if ($validationStatus -eq "failed") {
    exit 5
}
