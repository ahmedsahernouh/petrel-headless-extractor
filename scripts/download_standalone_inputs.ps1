# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param([string]$PythonPath = "python", [string]$OutputRoot = "")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputRoot)) { $OutputRoot = Join-Path $repoRoot "build\downloads" }
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$archive = Join-Path $OutputRoot "python-3.13.15-embed-amd64.zip"
$expected = "791ada5e20aba24524f8d939cdeb069976d632a699fe5cb65274b23f4545e68a"
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embeddable-amd64.zip" -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw "Python archive SHA-256 mismatch" }
$wheels = Join-Path $OutputRoot "wheels"
New-Item -ItemType Directory -Path $wheels -Force | Out-Null
& $PythonPath -m pip download --require-hashes --no-deps --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 --dest $wheels -r (Join-Path $repoRoot "portable_petrel_toolkit\requirements-standalone-lock.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Verified runtime: $archive"
Write-Output "Pinned wheels: $wheels"
