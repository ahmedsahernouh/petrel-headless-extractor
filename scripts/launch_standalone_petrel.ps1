# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

param(
    [Parameter(Position=0)][string]$ProjectFile = "",
    [Parameter(Position=1)][string]$OutputRoot = "",
    [Parameter(Position=2)][ValidateSet("inventory", "copy", "convert")][string]$CompanionMode = "convert",
    [Parameter(Position=3)][string]$ProjectName = "",
    [Parameter(Position=4)][string]$PetrelVersion = "unknown",
    [switch]$NoPause,
    [switch]$ReportOnly,
    [Alias('-check')][switch]$Check,
    [Alias('-help')][switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$toolkitRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $toolkitRoot "runtime\python.exe"
$pauseAtEnd = (-not $NoPause) -and [string]::IsNullOrWhiteSpace($OutputRoot)
$exitCode = 1
try {
    if ($Help -or $ProjectFile -in @("--help", "/?")) {
        Write-Output 'Usage: run_portable_petrel_extract.bat "PROJECT.pet" [OUTPUT_ROOT] [convert|copy|inventory] [LABEL] [PETREL_VERSION] [-NoPause]'
        Write-Output 'Full report/inventory always runs. Add -ReportOnly to disable dataset conversion (enabled by default).'
        Write-Output 'Or: run_portable_petrel_extract.bat --check -NoPause'
        exit 0
    }
    Write-Output "Petrel Headless Extractor 0.5.0 - standalone, read-only"
    . (Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1')
    $dependencyCheck = Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot
    if ($Check -or $ProjectFile -eq "--check") {
        & $pythonExe -B (Join-Path $PSScriptRoot "standalone_petrel_extract.py") --check
        $exitCode = $LASTEXITCODE
    } else {
        if ([string]::IsNullOrWhiteSpace($ProjectFile)) {
            $pairs = @(Get-ChildItem -LiteralPath $toolkitRoot -File -Filter '*.pet' | Where-Object {
                Test-Path -LiteralPath (Join-Path $_.DirectoryName ($_.BaseName + '.ptd')) -PathType Container
            } | Sort-Object Name)
            if ($pairs.Count -eq 1) { $ProjectFile = $pairs[0].FullName }
            elseif ($pairs.Count -gt 1) {
                for ($i = 0; $i -lt $pairs.Count; $i++) { Write-Output ("{0}. {1}" -f ($i + 1), $pairs[$i].Name) }
                $selection = Read-Host "Project number"
                $number = 0
                if (-not [int]::TryParse($selection, [ref]$number) -or $number -lt 1 -or $number -gt $pairs.Count) { throw "Invalid project number." }
                $ProjectFile = $pairs[$number - 1].FullName
            } else { $ProjectFile = (Read-Host "Full path to the .pet file").Trim().Trim('"') }
            if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
                $OutputRoot = (Read-Host 'Output root [Enter for your user folder\Petrel_Extracts]').Trim().Trim('"')
            }
        }
        if ($pauseAtEnd -and -not $ReportOnly -and -not $PSBoundParameters.ContainsKey('CompanionMode')) {
            Write-Output 'Full visual report and complete inventory: always included.'
            do { $convertAnswer = (Read-Host 'Convert supported data as well? [Y/n; Enter = Yes]').Trim().ToLowerInvariant() } while ($convertAnswer -notin @('', 'y', 'yes', 'n', 'no'))
            $ReportOnly = $convertAnswer -in @('n', 'no')
        }
        if ($ReportOnly) { $CompanionMode = 'inventory' }
        Write-Output $(if ($ReportOnly) { 'Selected: full report + inventory. Dataset conversion: OFF.' } else { 'Selected: full report + inventory. Dataset conversion: ON (supported profiles).' })
        if ([string]::IsNullOrWhiteSpace($OutputRoot)) { $OutputRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Petrel_Extracts' }
        $nativeArgs = @('--project-file', $ProjectFile, '--output-root', $OutputRoot, '--mode', $CompanionMode, '--petrel-version', $PetrelVersion)
        if ($ReportOnly) { $nativeArgs += '--report-only' }
        if (-not [string]::IsNullOrWhiteSpace($ProjectName)) { $nativeArgs += @('--label', $ProjectName) }
        & $pythonExe -B (Join-Path $PSScriptRoot "standalone_petrel_extract.py") @nativeArgs
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    $exitCode = 1
} finally {
    if ($pauseAtEnd) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode
