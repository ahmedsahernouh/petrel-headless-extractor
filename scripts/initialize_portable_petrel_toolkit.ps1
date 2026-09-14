# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [string]$PythonPath = "",
    [switch]$WithGeodata,
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolkitRoot = Split-Path -Parent $scriptDir
$venvRoot = Join-Path $toolkitRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $candidate = if (-not [string]::IsNullOrWhiteSpace($PythonPath)) { $PythonPath } else { "python" }
    & $candidate -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $SkipInstall) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $venvPython -m pip install -r (Join-Path $toolkitRoot "requirements-core.txt")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($WithGeodata) {
        & $venvPython -m pip install -r (Join-Path $toolkitRoot "requirements-geodata.txt")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

Write-Output "Portable Petrel toolkit initialized"
Write-Output "Python: $venvPython"
Write-Output "Geodata dependencies requested: $([bool]$WithGeodata)"
