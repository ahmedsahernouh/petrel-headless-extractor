# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectName,

    [string]$ProjectPath = "",

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$PetrelVersion = "unknown",

    [string]$InventoryPackage = "",

    [string]$Scope = "pilot_wells_logs_tables_surface",

    [string]$Operator = $env:USERNAME,

    [switch]$SparseDirectories
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-Slug {
    param([string]$Value)

    $slug = $Value.ToLowerInvariant() -replace "[^a-z0-9]+", "_"
    $slug = $slug.Trim("_")
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "petrel_project"
    }
    return $slug
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$exportId = "p_${timestamp}_$([guid]::NewGuid().ToString('N').Substring(0,6))"
$packageRoot = Join-Path $OutputRoot $exportId

$domainDirs = @(
    "00_manifest",
    "01_project_metadata",
    "02_wells/well_headers",
    "02_wells/well_trajectories",
    "02_wells/well_logs_las",
    "02_wells/well_tops",
    "02_wells/checkshots_vsp",
    "03_seismic/segy",
    "03_seismic/zgy",
    "03_seismic/navigation",
    "03_seismic/seismic_metadata",
    "04_surfaces_maps/zmap_dat",
    "04_surfaces_maps/xyz_ascii",
    "04_surfaces_maps/grids_metadata",
    "05_interpretation/horizons",
    "05_interpretation/faults",
    "05_interpretation/polygons",
    "05_interpretation/points",
    "06_models_properties/grids",
    "06_models_properties/properties",
    "06_models_properties/zones_layers",
    "06_models_properties/simulation_exports",
    "07_workflows_reports/screenshots",
    "07_workflows_reports/exported_reports",
    "07_workflows_reports/validation_reports",
    "99_unexported_or_manual"
)

$dirs = if ($SparseDirectories) {
    @(
        "00_manifest",
        "01_project_metadata",
        "07_workflows_reports/validation_reports"
    )
} else {
    $domainDirs
}

foreach ($dir in $dirs) {
    New-Item -ItemType Directory -Path (Join-Path $packageRoot $dir) -Force | Out-Null
}

$manifestHeader = "export_id,project_name,petrel_version,export_date_utc,source_object_path,source_object_type,source_object_uuid,export_format,export_file,coordinate_reference_system,xy_units,z_domain,z_units,null_value,template_or_property_unit,export_status,validation_status,sha256,notes"
Set-Content -LiteralPath (Join-Path $packageRoot "00_manifest/export_manifest.csv") -Value $manifestHeader -Encoding UTF8
Set-Content -LiteralPath (Join-Path $packageRoot "00_manifest/checksums_sha256.txt") -Value "" -Encoding UTF8

$exportDateUtc = (Get-Date).ToUniversalTime().ToString("o")
$manifest = [ordered]@{
    manifest_version = "0.1"
    project = [ordered]@{
        name = $ProjectName
        petrel_version = $PetrelVersion
        project_path = $ProjectPath
        coordinate_reference_system = "unknown"
        units = [ordered]@{
            xy = "unknown"
            depth = "unknown"
            time = "unknown"
            velocity = "unknown"
        }
    }
    export = [ordered]@{
        export_id = $exportId
        export_date_utc = $exportDateUtc
        output_root = $packageRoot
        scope = $Scope
        operator = $Operator
        tool_version = "new_export_package.ps1/0.1"
    }
    items = @()
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $packageRoot "00_manifest/export_manifest.json") -Encoding UTF8

$projectSummary = [ordered]@{
    project_name = $ProjectName
    project_path = $ProjectPath
    petrel_version = $PetrelVersion
    export_id = $exportId
    inventory_package = $InventoryPackage
    coordinate_reference_system = "unknown"
    xy_units = "unknown"
    depth_units = "unknown"
    time_units = "unknown"
    velocity_units = "unknown"
    notes = "Fill these values from Petrel project settings before final export validation."
}
$projectSummary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $packageRoot "01_project_metadata/project_summary.json") -Encoding UTF8

if (-not $SparseDirectories) {
    $unexportedHeader = "project_name,explorer_path,object_name,object_type,reason_not_exported,required_manual_action,status,notes"
    Set-Content -LiteralPath (Join-Path $packageRoot "99_unexported_or_manual/unexported_inventory.csv") -Value $unexportedHeader -Encoding UTF8
}

$pilotPlan = @(
    [pscustomobject]@{ test_id = "P01"; object_type = "well"; help_topic_signal = "Export well data; Export coordinates to MS Excel"; target_folder = "02_wells/well_headers"; target_format = "CSV or ASCII"; status = "not_started"; notes = "Capture well names, coordinates, datum, CRS, units." },
    [pscustomobject]@{ test_id = "P02"; object_type = "well_log"; help_topic_signal = "Export well log data as LAS"; target_folder = "02_wells/well_logs_las"; target_format = "LAS"; status = "not_started"; notes = "Validate LAS header, curves, depth unit, null value." },
    [pscustomobject]@{ test_id = "P03"; object_type = "data_table"; help_topic_signal = "Export data displayed in a data table"; target_folder = "07_workflows_reports/exported_reports"; target_format = "CSV or Excel"; status = "not_started"; notes = "Use for inventory/QA table export if available." },
    [pscustomobject]@{ test_id = "P04"; object_type = "surface_map"; help_topic_signal = "Export gridded surfaces; Export surfaces"; target_folder = "04_surfaces_maps/zmap_dat"; target_format = "ZMAP DAT or XYZ ASCII"; status = "not_started"; notes = "Capture domain, units, CRS, null value, grid increment." },
    [pscustomobject]@{ test_id = "P05"; object_type = "seismic_cube"; help_topic_signal = "Export seismic data in SEG-Y format; Export a seismic cube in ZGY format"; target_folder = "03_seismic/segy"; target_format = "SEG-Y or ZGY"; status = "defer_until_simple_exports_pass"; notes = "Capture sample rate, domain, byte mapping, trace count, CRS." }
)
$pilotPlan | Export-Csv -LiteralPath (Join-Path $packageRoot "00_manifest/export_pilot_plan.csv") -NoTypeInformation -Encoding UTF8

if (-not [string]::IsNullOrWhiteSpace($InventoryPackage) -and (Test-Path -LiteralPath $InventoryPackage)) {
    $inventoryManifest = Join-Path $InventoryPackage "00_manifest/object_inventory.csv"
    $capabilityMap = Join-Path $InventoryPackage "00_manifest/export_capability_map.csv"
    if (Test-Path -LiteralPath $inventoryManifest) {
        Copy-Item -LiteralPath $inventoryManifest -Destination (Join-Path $packageRoot "00_manifest/source_object_inventory.csv") -Force
    }
    if (Test-Path -LiteralPath $capabilityMap) {
        Copy-Item -LiteralPath $capabilityMap -Destination (Join-Path $packageRoot "00_manifest/source_export_capability_map.csv") -Force
    }
}

$log = @(
    "# Export Log",
    "",
    "Project: $ProjectName",
    "",
    "Petrel version: $PetrelVersion",
    "",
    "Project path: $ProjectPath",
    "",
    "Export package: $packageRoot",
    "",
    "Export ID: $exportId",
    "",
    "Created UTC: $exportDateUtc",
    "",
    "## First Pilot Steps",
    "",
    "1. Fill project CRS and unit metadata in ``01_project_metadata/project_summary.json``.",
    "2. Capture the selected objects in ``00_manifest/source_object_inventory.csv`` or the inventory package object inventory.",
    "3. Export well headers/coordinates to ``02_wells/well_headers``.",
    "4. Export one well log set to LAS in ``02_wells/well_logs_las``.",
    "5. Add one row per exported file to ``00_manifest/export_manifest.csv``.",
    "6. Validate files outside Petrel and update ``validation_status``.",
    "7. Add checksums after the pilot files are final.",
    "",
    "## Notes",
    "",
    "- Keep raw Petrel exports unchanged.",
    "- Put conversion or cleanup outputs in separate subfolders or name them clearly.",
    "- Do not mark batch/API export as supported until tested in Petrel."
)
$log | Set-Content -LiteralPath (Join-Path $packageRoot "00_manifest/export_log.md") -Encoding UTF8

Write-Output "Created export package: $packageRoot"
