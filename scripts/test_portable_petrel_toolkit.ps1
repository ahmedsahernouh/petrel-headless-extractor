# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param([string]$PythonPath = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolkitRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "petrel_mcp_dependencies.ps1")
$pythonExe = Resolve-PetrelMcpPython -ExplicitPath $PythonPath -ProjectRoot $toolkitRoot

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("petrel_portable_smoke_" + [guid]::NewGuid().ToString("N"))
$sourceRoot = Join-Path $testRoot "source"
$outputRoot = Join-Path $testRoot "output"
$projectFile = Join-Path $sourceRoot "Synthetic.pet"
$ptdRoot = Join-Path $sourceRoot "Synthetic.ptd"
$neighborProjectFile = Join-Path $sourceRoot "Neighbor.pet"
$neighborPtdRoot = Join-Path $sourceRoot "Neighbor.ptd"
$coLocatedScripts = Join-Path $sourceRoot "scripts"
$coLocatedVenv = Join-Path $sourceRoot ".venv"
$coLocatedRuntime = Join-Path $sourceRoot "runtime"
$coLocatedBootstrap = Join-Path $sourceRoot "bootstrap"
$coLocatedBuild = Join-Path $sourceRoot "build"
New-Item -ItemType Directory -Path $ptdRoot, $neighborPtdRoot, $outputRoot, $coLocatedScripts, $coLocatedVenv, $coLocatedRuntime, $coLocatedBootstrap, $coLocatedBuild -Force | Out-Null

try {
    [System.IO.File]::WriteAllText($projectFile, "Synthetic Petrel project smoke fixture")
    [System.IO.File]::WriteAllText((Join-Path $ptdRoot "Data.ptd"), (@(
        "%3://Petrel/00000000-0000-0000-0000-000000000001FloatWellLog2026-01-01 00:00:00",
        "%3://Petrel/00000000-0000-0000-0000-000000000002Polygons32026-01-01 00:00:00",
        "%3://Petrel/00000000-0000-0000-0000-000000000003Points32026-01-01 00:00:00",
        "%3://Petrel/00000000-0000-0000-0000-000000000004MdInclAzimTrajectoryProviderData2026-01-01 00:00:00"
    ) -join "`n"))
    [System.IO.File]::WriteAllText((Join-Path $ptdRoot "Model.ptd"), "Petrel 2019.2 IEarlyBoundCoordinateReferenceSystem PowerPlan:BAHRAIN ILateBoundCoordinateReferenceSystem WGS_1984_UTM_Zone_39N Authority EPSG,32639")
    [System.IO.File]::WriteAllText($neighborProjectFile, "Neighbor Petrel project exclusion fixture")
    [System.IO.File]::WriteAllText((Join-Path $neighborPtdRoot "Data.ptd"), "NEIGHBOR_NATIVE_STORE_MUST_NOT_BE_INGESTED")
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "checkshots.txt"), "Well`tMD`tTWT`nTEST`t100`t25`n")
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "crs.prj"), "LOCAL_CS[`"Synthetic`"]")
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "well_tops.txt"), (@(
        "# Petrel well tops",
        "VERSION 2",
        "BEGIN HEADER",
        "Well",
        "Surface",
        "X",
        "Y",
        "Z",
        "MD",
        "END HEADER",
        '"TEST-1" "Top A" 500000 2900000 -1000 1100'
    ) -join "`n"))
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "run_portable_petrel_extract.bat"), "synthetic toolkit marker")
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "toolkit.json"), "{}`n")
    [System.IO.File]::WriteAllText((Join-Path $coLocatedScripts "invoke_portable_petrel_extract.ps1"), "synthetic toolkit marker")
    [System.IO.File]::WriteAllText((Join-Path $coLocatedVenv "runtime.bin"), "synthetic runtime file")
    [System.IO.File]::WriteAllText((Join-Path $coLocatedRuntime "runtime.bin"), "standalone runtime must not become companion data")
    [System.IO.File]::WriteAllText((Join-Path $coLocatedBootstrap "runtime.zip"), "dependency cache is not companion data")
    [System.IO.File]::WriteAllText((Join-Path $coLocatedBuild "last_check.json"), "dependency logs are not companion data")
    [System.IO.File]::WriteAllText((Join-Path $sourceRoot "STANDALONE.txt"), "standalone marker")

    $shapeFixture = @'
import os
import sys
import shapefile

base = os.path.join(sys.argv[1], "wells")
writer = shapefile.Writer(base, shapeType=shapefile.POINT)
writer.field("WELL", "C")
writer.point(500000.0, 2900000.0)
writer.record("TEST-1")
writer.close()
with open(base + ".prj", "w", encoding="utf-8") as handle:
    handle.write('PROJCS["WGS 84 / UTM zone 39N",AUTHORITY["EPSG","32639"]]')
'@
    $shapeFixture | & $pythonExe - $sourceRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create the synthetic shapefile fixture." }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptDir "invoke_portable_petrel_extract.ps1") `
        -ProjectFile $projectFile `
        -OutputRoot $outputRoot `
        -ProjectName "Synthetic" `
        -PetrelVersion "unknown" `
        -PythonPath $pythonExe `
        -CompanionMode convert `
        -SkipNativeSpatialExtraction
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $package = Get-ChildItem -LiteralPath $outputRoot -Directory | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($null -eq $package) { throw "Smoke test did not create an export package." }
    $manifest = Join-Path $package.FullName "00_manifest\export_manifest.csv"
    $companionInventory = Join-Path $package.FullName "00_manifest\companion_source_inventory.csv"
    $capability = Join-Path $package.FullName "07_workflows_reports\portable_extractor\companion_capability_report.json"
    $projectSummaryPath = Join-Path $package.FullName "01_project_metadata\project_summary.json"
    $spatialCapabilityPath = Join-Path $package.FullName "01_project_metadata\native_spatial_capability.csv"
    $convertedShapeCsv = Join-Path $package.FullName "10_converted_ascii\gis\wells.csv"
    $convertedShapeGeoJson = Join-Path $package.FullName "10_converted_ascii\gis\wells.geojson"
    $convertedWellTops = Join-Path $package.FullName "02_wells\well_tops\well_tops.csv"
    $validation = Get-ChildItem -LiteralPath (Join-Path $package.FullName "07_workflows_reports\validation_reports") -Filter "export_validation_*.md" | Select-Object -Last 1
    $audit = Get-ChildItem -LiteralPath (Join-Path $package.FullName "07_workflows_reports\project_audit") -Filter "petrel_project_audit_*.json" | Sort-Object LastWriteTimeUtc | Select-Object -Last 1
    $dashboard = Join-Path $package.FullName "PROJECT_REPORT.html"
    if (-not (Test-Path -LiteralPath $manifest) -or -not (Test-Path -LiteralPath $capability) -or -not (Test-Path -LiteralPath $dashboard) -or $null -eq $validation -or $null -eq $audit) {
        throw "Smoke test outputs are incomplete."
    }
    if (-not (Test-Path -LiteralPath $projectSummaryPath) -or -not (Test-Path -LiteralPath $spatialCapabilityPath) -or -not (Test-Path -LiteralPath $convertedShapeCsv) -or -not (Test-Path -LiteralPath $convertedShapeGeoJson) -or -not (Test-Path -LiteralPath $convertedWellTops)) {
        throw "CRS, native spatial capability, shapefile, or Well Tops ASCII conversion outputs are missing."
    }
    $rows = @(Import-Csv -LiteralPath $manifest)
    if (@($rows | Where-Object { $_.validation_status -ne "validated" }).Count -ne 0) { throw "Manifest contains unvalidated rows." }
    $companionRows = @(Import-Csv -LiteralPath $companionInventory)
    if (@($companionRows | Where-Object { $_.source_relative_path -eq "Neighbor.pet" -or $_.source_relative_path -like "Neighbor.ptd\*" }).Count -ne 0) {
        throw "Neighbor Petrel project was incorrectly ingested as companion data."
    }
    if (@($companionRows | Where-Object { $_.source_relative_path -eq "run_portable_petrel_extract.bat" -or $_.source_relative_path -eq "toolkit.json" -or $_.source_relative_path -eq "STANDALONE.txt" -or $_.source_relative_path -like "scripts\*" -or $_.source_relative_path -like ".venv\*" -or $_.source_relative_path -like "runtime\*" -or $_.source_relative_path -like "bootstrap\*" -or $_.source_relative_path -like "build\*" }).Count -ne 0) {
        throw "Co-located portable toolkit files were incorrectly ingested as companion data."
    }
    $capabilityReport = Get-Content -Raw -LiteralPath $capability | ConvertFrom-Json
    if ($capabilityReport.counts.neighbor_petrel_project_files_excluded -ne 1 -or $capabilityReport.counts.neighbor_petrel_store_directories_excluded -ne 1) {
        throw "Neighbor Petrel project exclusion was not reported correctly."
    }
    if (-not $capabilityReport.co_located_toolkit_exclusion.detected -or $capabilityReport.counts.co_located_toolkit_files_excluded -lt 4) {
        throw "Co-located portable toolkit exclusion was not reported correctly."
    }
    $projectSummary = Get-Content -Raw -LiteralPath $projectSummaryPath | ConvertFrom-Json
    if ($projectSummary.coordinate_reference_system -ne "WGS 84 / UTM zone 39N (EPSG:32639)") {
        throw "Conservative CRS evidence was not applied to the project summary."
    }
    $spatialRows = @(Import-Csv -LiteralPath $spatialCapabilityPath)
    if (@($spatialRows | Where-Object { $_.spatial_product -eq "polygons" }).Count -ne 1 -or @($spatialRows | Where-Object { $_.spatial_product -eq "points" }).Count -ne 1) {
        throw "Native polygon and point capability evidence was not reported."
    }
    $shapeRows = @(Import-Csv -LiteralPath $convertedShapeCsv)
    if ($shapeRows.Count -ne 1 -or $shapeRows[0].geometry_x -ne "500000.0" -or [string]::IsNullOrWhiteSpace($shapeRows[0].geometry_geojson)) {
        throw "Shapefile CSV did not preserve explicit XY and GeoJSON geometry."
    }
    $wellTopRows = @(Import-Csv -LiteralPath $convertedWellTops)
    if ($wellTopRows.Count -ne 1 -or $wellTopRows[0].well_name -ne "TEST-1" -or $wellTopRows[0].surface -ne "Top A" -or $wellTopRows[0].is_actual_pick_record -ne "yes") {
        throw "Petrel Well Tops ASCII companion was not parsed correctly."
    }
    foreach ($absentDomain in @("03_seismic", "04_surfaces_maps", "05_interpretation", "06_models_properties", "99_unexported_or_manual")) {
        if (Test-Path -LiteralPath (Join-Path $package.FullName $absentDomain)) {
            throw "Sparse package created an irrelevant domain folder: $absentDomain"
        }
    }
    foreach ($absentWellFolder in @("well_headers", "well_trajectories", "well_logs_las", "checkshots_vsp")) {
        if (Test-Path -LiteralPath (Join-Path $package.FullName "02_wells\$absentWellFolder")) {
            throw "Sparse package created an irrelevant well folder: $absentWellFolder"
        }
    }
    $emptyDirectories = @(Get-ChildItem -LiteralPath $package.FullName -Directory -Recurse | Where-Object { @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0 })
    if ($emptyDirectories.Count -ne 0) {
        throw "Sparse package retained empty directories: $($emptyDirectories.FullName -join '; ')"
    }
    $auditReport = Get-Content -Raw -LiteralPath $audit.FullName | ConvertFrom-Json
    if (@($auditReport.qc_flags | Where-Object { $_.message -match "Manifest rows not in 'validated' state" }).Count -ne 0) {
        throw "Project audit was generated before manifest validation."
    }
    $dashboardHtml = Get-Content -Raw -LiteralPath $dashboard
    foreach ($requiredDashboardText in @("Coordinate reference system and units", "Extraction coverage and remaining boundaries", "Extracted data structure", 'id="file-tree"')) {
        if ($dashboardHtml -notlike "*$requiredDashboardText*") {
            throw "Project dashboard is missing required content: $requiredDashboardText"
        }
    }
    Write-Output "Portable Petrel toolkit smoke test passed"
    Write-Output "Manifest rows: $($rows.Count)"
    Write-Output "Validation report: $($validation.FullName)"
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
        $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not (($resolvedTestRoot.TrimEnd('\') + '\').StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "Refusing to remove smoke-test directory outside the system temp root: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
