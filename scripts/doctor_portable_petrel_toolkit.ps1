# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [string]$PythonPath = "",
    [string]$JsonOutput = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolkitRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "petrel_mcp_dependencies.ps1")

$checks = New-Object System.Collections.Generic.List[object]
function Add-Check([string]$Name, [string]$Status, [string]$Detail) {
    $checks.Add([pscustomobject]@{ name = $Name; status = $Status; detail = $Detail }) | Out-Null
}

try {
    $pythonExe = Resolve-PetrelMcpPython -ExplicitPath $PythonPath -ProjectRoot $toolkitRoot
    Add-Check "python" "ready" $pythonExe
} catch {
    Add-Check "python" "blocked" $_.Exception.Message
    $pythonExe = ""
}

$required = @(
    "invoke_portable_petrel_extract.ps1",
    "export_petrel_native_project_zero_gui.ps1",
    "export_petrel_native_semantic_zero_gui.py",
    "export_petrel_native_spatial_zero_gui.py",
    "portable_petrel_companion_extract.py",
    "register_petrel_file_exports.ps1",
    "validate_export_package.ps1",
    "report_petrel_project_audit.py",
    "new_export_package.ps1",
    "petrel_mcp_dependencies.ps1"
)
foreach ($name in $required) {
    $path = Join-Path $scriptDir $name
    Add-Check "script:$name" $(if (Test-Path -LiteralPath $path -PathType Leaf) { "ready" } else { "blocked" }) $path
}

if (-not [string]::IsNullOrWhiteSpace($pythonExe)) {
    foreach ($module in @("lasio", "pandas", "openpyxl", "shapefile", "numpy", "zmapio", "pyzgy")) {
        $previousErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $pythonExe -c "import $module" 2>$null
        $moduleExitCode = Get-PetrelMcpLastExitCode
        $ErrorActionPreference = $previousErrorPreference
        $status = if ($moduleExitCode -eq 0) { "ready" } elseif ($module -in @("numpy", "zmapio", "pyzgy")) { "optional_unavailable" } else { "degraded" }
        Add-Check "python:$module" $status $module
    }
    foreach ($script in @("export_petrel_native_semantic_zero_gui.py", "export_petrel_native_spatial_zero_gui.py", "portable_petrel_companion_extract.py", "report_petrel_project_audit.py")) {
        & $pythonExe -m py_compile (Join-Path $scriptDir $script)
        Add-Check "compile:$script" $(if ($LASTEXITCODE -eq 0) { "ready" } else { "blocked" }) $script
    }
}

$blocked = @($checks | Where-Object { $_.status -eq "blocked" }).Count
$degraded = @($checks | Where-Object { $_.status -eq "degraded" }).Count
$overall = if ($blocked -gt 0) { "blocked" } elseif ($degraded -gt 0) { "degraded" } else { "ready" }
$report = [ordered]@{
    toolkit = "portable-petrel-project-extractor"
    version = if (Test-Path -LiteralPath (Join-Path $toolkitRoot "toolkit.json")) { (Get-Content -LiteralPath (Join-Path $toolkitRoot "toolkit.json") -Raw | ConvertFrom-Json).version } else { "0.2.4" }
    checked_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    status = $overall
    petrel_required = $false
    source_mutation = $false
    checks = $checks
}
$json = $report | ConvertTo-Json -Depth 8
if (-not [string]::IsNullOrWhiteSpace($JsonOutput)) {
    $parent = Split-Path -Parent $JsonOutput
    if (-not [string]::IsNullOrWhiteSpace($parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $json | Set-Content -LiteralPath $JsonOutput -Encoding UTF8
}
Write-Output "Portable Petrel toolkit doctor: $overall"
foreach ($check in $checks) { Write-Output "$($check.status): $($check.name) - $($check.detail)" }
if ($blocked -gt 0) { exit 2 }
