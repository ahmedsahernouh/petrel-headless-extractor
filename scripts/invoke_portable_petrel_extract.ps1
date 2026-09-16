# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Mandatory = $true)][string]$ProjectFile,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [string]$ProjectName = "",
    [string]$PetrelVersion = "unknown",
    [string]$PythonPath = "",
    [ValidateSet("inventory", "copy", "convert")][string]$CompanionMode = "convert",
    [int64]$MaxCompanionFileBytes = 2000000000,
    [int64]$MaxTextProbeBytes = 10485760,
    [int]$MaxCandidatesPerFile = 200,
    [switch]$SkipSemanticExtraction,
    [switch]$SkipCompanionExtraction,
    [switch]$SkipNativeSpatialExtraction,
    [switch]$SkipNativeBinaryRecovery,
    [switch]$SkipAudit,
    [switch]$ReportOnly,
    [switch]$ReferenceSeismic,
    [switch]$NoValidate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if ($ReportOnly) { $CompanionMode = 'inventory'; $SkipAudit = $false }

function Test-IsWithinPath {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Parent)
    $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
    $container = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    return $candidate.StartsWith($container, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-RequiredFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label not found: $Path" }
    return (Resolve-Path -LiteralPath $Path).ProviderPath
}

function Remove-EmptyPackageDirectories {
    param([Parameter(Mandatory = $true)][string]$PackageRoot)

    $resolvedRoot = (Resolve-Path -LiteralPath $PackageRoot).ProviderPath.TrimEnd('\')
    $rootPrefix = $resolvedRoot + '\'
    $removed = 0
    do {
        $changed = $false
        $directories = @(Get-ChildItem -LiteralPath $resolvedRoot -Directory -Recurse | Sort-Object { $_.FullName.Length } -Descending)
        foreach ($directory in $directories) {
            $resolvedDirectory = [System.IO.Path]::GetFullPath($directory.FullName).TrimEnd('\')
            if (-not (($resolvedDirectory + '\').StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase))) {
                throw "Refusing to prune a directory outside the export package: $resolvedDirectory"
            }
            if (@(Get-ChildItem -LiteralPath $resolvedDirectory -Force).Count -eq 0) {
                Remove-Item -LiteralPath $resolvedDirectory -Force
                $removed++
                $changed = $true
            }
        }
    } while ($changed)
    return $removed
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolkitRoot = Split-Path -Parent $scriptDir
. (Get-RequiredFile -Path (Join-Path $scriptDir "petrel_mcp_dependencies.ps1") -Label "Dependency resolver")

$projectFileResolved = (Resolve-Path -LiteralPath $ProjectFile).ProviderPath
if ([System.IO.Path]::GetExtension($projectFileResolved) -ine ".pet") { throw "ProjectFile must be a .pet file: $projectFileResolved" }
$projectRoot = Split-Path -Parent $projectFileResolved
$projectStem = [System.IO.Path]::GetFileNameWithoutExtension($projectFileResolved)
$ptdRoot = Join-Path $projectRoot "$projectStem.ptd"
if (-not (Test-Path -LiteralPath $ptdRoot -PathType Container)) { throw "Matching Petrel store directory not found: $ptdRoot" }

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$outputRootResolved = (Resolve-Path -LiteralPath $OutputRoot).ProviderPath
if (Test-IsWithinPath -Path $outputRootResolved -Parent $projectRoot) {
    throw "OutputRoot must be outside the source project directory to prevent recursive self-ingestion: $outputRootResolved"
}
if ([string]::IsNullOrWhiteSpace($ProjectName)) { $ProjectName = $projectStem }

$pythonExe = Resolve-PetrelMcpPython -ExplicitPath $PythonPath -ProjectRoot $toolkitRoot
$nativeExporter = Get-RequiredFile -Path (Join-Path $scriptDir "export_petrel_native_project_zero_gui.ps1") -Label "Native exporter"
$semanticExporter = Get-RequiredFile -Path (Join-Path $scriptDir "export_petrel_native_semantic_zero_gui.py") -Label "Semantic exporter"
$nativeSpatialExporter = Get-RequiredFile -Path (Join-Path $scriptDir "export_petrel_native_spatial_zero_gui.py") -Label "Native spatial exporter"
$nativeBinaryExporter = Get-RequiredFile -Path (Join-Path $scriptDir "petrel_native_recovery.py") -Label "Native log and surface exporter"
$companionExtractor = Get-RequiredFile -Path (Join-Path $scriptDir "portable_petrel_companion_extract.py") -Label "Companion extractor"
$registrar = Get-RequiredFile -Path (Join-Path $scriptDir "register_petrel_file_exports.ps1") -Label "File registrar"
$validator = Get-RequiredFile -Path (Join-Path $scriptDir "validate_export_package.ps1") -Label "Package validator"
$auditReporter = Get-RequiredFile -Path (Join-Path $scriptDir "report_petrel_project_audit.py") -Label "Audit reporter"

$startedUtc = (Get-Date).ToUniversalTime().ToString("o")
Write-Output "Stage 1/6: hash-verified native project copy and inventory"
$nativeOutput = & $nativeExporter `
    -ProjectName $ProjectName `
    -ProjectFile $projectFileResolved `
    -ProjectPath $projectRoot `
    -PetrelVersion $PetrelVersion `
    -ExportRoot $outputRootResolved `
    -CreateNewPackage `
    -SparsePackage `
    -ReferenceSeismic:$ReferenceSeismic `
    -MaxTextProbeBytes $MaxTextProbeBytes `
    -MaxCandidatesPerFile $MaxCandidatesPerFile `
    -NoValidate
$nativeCode = Get-PetrelMcpLastExitCode
if ($nativeCode -ne 0) { exit $nativeCode }
$packageLine = @($nativeOutput | Where-Object { $_ -match '^Export package:' } | Select-Object -Last 1)
if ($packageLine.Count -eq 0) { throw "Native exporter did not report the export package path." }
$exportPackage = ($packageLine[0] -replace '^Export package:\s*', '').Trim()
$exportPackage = (Resolve-Path -LiteralPath $exportPackage).ProviderPath
$selection = @{ full_report = $true; full_inventory = $true; dataset_conversion_enabled = (-not $ReportOnly -and $CompanionMode -eq 'convert'); report_only = [bool]$ReportOnly }
$selection | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $exportPackage '01_project_metadata\extraction_options.json') -Encoding UTF8
$contextPath = Join-Path $exportPackage '01_project_metadata\project_context.json'
$workflowArgs = @()
if ($CompanionMode -eq 'convert') { $workflowArgs = @('--workflow-output', (Join-Path $exportPackage 'native_workflows')) }
& $pythonExe (Join-Path $scriptDir 'geoviewer_metadata.py') --project $projectFileResolved --output $contextPath @workflowArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$nativeContext = Get-Content -Raw -LiteralPath (Join-Path $exportPackage '01_project_metadata\native_compatibility.json') | ConvertFrom-Json
if (-not $nativeContext.native_decoders_applicable) {
    Write-Output "Native numeric decoding unavailable for storage layout: $($nativeContext.layout). Inventory and report will continue."
    $SkipNativeSpatialExtraction = $true
    $SkipNativeBinaryRecovery = $true
    if ($CompanionMode -eq 'convert') {
        & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category native_compatibility --code 10 --report (Join-Path $exportPackage '01_project_metadata\native_compatibility.json')
        if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
    }
}
if ($ReferenceSeismic) {
    & $pythonExe (Join-Path $scriptDir 'petrel_project_seismic.py') --project-file $projectFileResolved --output (Join-Path $exportPackage '01_project_metadata\project_seismic_inventory.json')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $SkipSemanticExtraction) {
    Write-Output "Stage 2/6: safe native semantic metadata extraction"
    & $pythonExe $semanticExporter `
        --project-name $ProjectName `
        --project-file $projectFileResolved `
        --petrel-version $PetrelVersion `
        --export-package $exportPackage `
        --no-validate
    $semanticCode = Get-PetrelMcpLastExitCode
    & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category semantic_metadata --code $semanticCode
    if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
} else {
    Write-Output "Stage 2/6: skipped by request"
}

if (-not $SkipCompanionExtraction) {
    Write-Output "Stage 3/6: companion source inventory, preservation, and supported conversion"
    $seismicArgs = @()
    if ($ReferenceSeismic) { $seismicArgs += '--reference-seismic' }
    & $pythonExe $companionExtractor `
        --project-file $projectFileResolved `
        --export-package $exportPackage `
        --project-name $ProjectName `
        --petrel-version $PetrelVersion `
        --mode $CompanionMode `
        --max-file-bytes $MaxCompanionFileBytes @seismicArgs
    $companionCode = Get-PetrelMcpLastExitCode
    & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category companions --code $companionCode
    if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
} else {
    Write-Output "Stage 3/6: skipped by request"
}

if ((-not $SkipNativeSpatialExtraction) -and $CompanionMode -eq "convert") {
    Write-Output "Stage 4/6: evidence-gated native well-head, polygon, point, trajectory, and calibrated well-top decode"
    & $pythonExe $nativeSpatialExporter --export-package $exportPackage
    $nativeSpatialCode = Get-PetrelMcpLastExitCode
    & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category spatial --code $nativeSpatialCode --report (Join-Path $exportPackage '07_workflows_reports\native_spatial_zero_gui\native_spatial_decode_report.json')
    if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
} elseif ($SkipNativeSpatialExtraction) {
    Write-Output "Stage 4/6: skipped by request"
} else {
    Write-Output "Stage 4/6: skipped because companion mode is $CompanionMode"
}

if ((-not $SkipNativeBinaryRecovery) -and $CompanionMode -eq "convert") {
    Write-Output "Stage 4/6: native well-log and surface recovery into LAS/CSV/XYZ"
    & $pythonExe $nativeBinaryExporter --export-package $exportPackage
    $nativeBinaryCode = Get-PetrelMcpLastExitCode
    & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category logs_surfaces --code $nativeBinaryCode --report (Join-Path $exportPackage '07_workflows_reports\native_recovery\native_recovery_report.json')
    if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
}

$prunedBeforeAudit = Remove-EmptyPackageDirectories -PackageRoot $exportPackage
if ($prunedBeforeAudit -gt 0) { Write-Output "Sparse package cleanup: removed $prunedBeforeAudit empty directories before audit" }

Write-Output "Stage 5/6: pre-audit registration, checksums, and validation"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $registrar `
    -ExportPackage $exportPackage `
    -ProjectName $ProjectName `
    -PetrelVersion $PetrelVersion `
    -RegisterUnknown
$registerCode = Get-PetrelMcpLastExitCode
if ($registerCode -ne 0) { exit $registerCode }

$validationStatus = "skipped"
$validationReport = ""
if (-not $NoValidate) {
    $validationOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $validator `
        -ExportPackage $exportPackage `
        -UpdateManifest `
        -WriteChecksums
    $validationCode = Get-PetrelMcpLastExitCode
    if ($validationCode -ne 0) { exit $validationCode }
    $validationStatus = (($validationOutput | Select-Object -First 1) -replace '^Validation status:\s*', '').Trim()
    $reportLine = @($validationOutput | Where-Object { $_ -match '^Report:' } | Select-Object -First 1)
    if ($reportLine.Count -gt 0) { $validationReport = ($reportLine[0] -replace '^Report:\s*', '').Trim() }
}

$htmlReport = ""
if (-not $SkipAudit) {
    Write-Output "Stage 6/6: self-contained audit of the validated package"
    $auditOutput = @(& $pythonExe $auditReporter --export-package $exportPackage --title "$ProjectName portable read-only extraction audit")
    $auditCode = Get-PetrelMcpLastExitCode
    $auditOutput | ForEach-Object { Write-Output $_ }
    & $pythonExe (Join-Path $PSScriptRoot 'geoviewer_stage.py') --package $exportPackage --category visual_report --code $auditCode
    if ((Get-PetrelMcpLastExitCode) -ne 0) { exit 1 }
    $dashboardLine = @($auditOutput | Where-Object { $_ -match '^Dashboard:' } | Select-Object -First 1)
    if ($dashboardLine.Count -gt 0) { $htmlReport = ($dashboardLine[0] -replace '^Dashboard:\s*', '').Trim() }
} else {
    Write-Output "Stage 6/6: audit skipped by request"
}

$manifestPath = Join-Path $exportPackage "00_manifest\export_manifest.csv"
$manifestRows = @(Import-Csv -LiteralPath $manifestPath)
$summaryRoot = Join-Path $exportPackage "07_workflows_reports\portable_extractor"
New-Item -ItemType Directory -Path $summaryRoot -Force | Out-Null
$summaryPath = Join-Path $summaryRoot "portable_extraction_run_summary.json"
$toolkitVersion = "0.2.5"
$toolkitMetadataPath = Join-Path $toolkitRoot "toolkit.json"
if (Test-Path -LiteralPath $toolkitMetadataPath -PathType Leaf) {
    $toolkitVersion = (Get-Content -Raw -LiteralPath $toolkitMetadataPath | ConvertFrom-Json).version
}
$summary = [ordered]@{
    toolkit_version = $toolkitVersion
    operation = "portable_petrel_read_only_extract"
    started_at_utc = $startedUtc
    completed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    project_file = $projectFileResolved
    project_name = $ProjectName
    petrel_version_reported = $PetrelVersion
    export_package = $exportPackage
    runtime_gui_used = $false
    petrel_process_launched = $false
    source_mutated = $false
    companion_mode = $CompanionMode
    report_only = [bool]$ReportOnly
    dataset_conversion_enabled = (-not $ReportOnly -and $CompanionMode -eq 'convert')
    native_spatial_extraction = if ((-not $SkipNativeSpatialExtraction) -and $CompanionMode -eq "convert") { "attempted_evidence_gated" } else { "skipped" }
    native_log_surface_recovery = if ((-not $SkipNativeBinaryRecovery) -and $CompanionMode -eq "convert") { "attempted_validated_profiles" } else { "skipped" }
    manifest_rows = $manifestRows.Count
    manifest_rows_at_summary_creation = $manifestRows.Count
    validation_status = $validationStatus
    validation_report = $validationReport
    html_report = $htmlReport
    boundaries = @(
        "Only validated native spatial, well-log and surface profiles are decoded; unsupported layouts and unresolved metadata are reported per object.",
        "Well-head X/Y values are native Model.ptd coordinates; CRS and horizontal units remain unresolved until independently confirmed.",
        "Trajectory-start Z is attached to a well head only after native X/Y cross-check and is not asserted to be datum elevation.",
        "Native well-top labels are emitted only after independent Petrel-authored ASCII XYZ calibration.",
        "Cross-version metadata extraction must be revalidated against Petrel-authored exports.",
        "Preserved or structurally converted data is not automatically scientifically validated or official."
    )
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $registrar `
    -ExportPackage $exportPackage `
    -ProjectName $ProjectName `
    -PetrelVersion $PetrelVersion `
    -RegisterUnknown
$registerSummaryCode = Get-PetrelMcpLastExitCode
if ($registerSummaryCode -ne 0) { exit $registerSummaryCode }

if (-not $NoValidate) {
    $validationOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $validator -ExportPackage $exportPackage -UpdateManifest -WriteChecksums
    $validationCode = Get-PetrelMcpLastExitCode
    if ($validationCode -ne 0) { exit $validationCode }
    $validationStatus = (($validationOutput | Select-Object -First 1) -replace '^Validation status:\s*', '').Trim()
    $reportLine = @($validationOutput | Where-Object { $_ -match '^Report:' } | Select-Object -First 1)
    if ($reportLine.Count -gt 0) { $validationReport = ($reportLine[0] -replace '^Report:\s*', '').Trim() }
}

$prunedAtEnd = Remove-EmptyPackageDirectories -PackageRoot $exportPackage
if ($prunedAtEnd -gt 0) { Write-Output "Sparse package cleanup: removed $prunedAtEnd empty directories after validation" }

Write-Output "Portable Petrel extraction: completed"
Write-Output "Export package: $exportPackage"
Write-Output "Run summary: $summaryPath"
if (-not [string]::IsNullOrWhiteSpace($htmlReport)) { Write-Output "HTML report: $htmlReport" }
Write-Output "Validation: $validationStatus"
if (-not [string]::IsNullOrWhiteSpace($validationReport)) { Write-Output "Validation report: $validationReport" }

if ($validationStatus -eq "failed") { exit 5 }
