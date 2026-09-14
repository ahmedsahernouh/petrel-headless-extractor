# Website: https://saherlabs.dev/
# Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
param(
    [Parameter(Position=0)][string]$InputFile = '',
    [Parameter(Position=1)][string]$OutputRoot = '',
    [ValidateSet('time','depth')][string]$Domain,
    [ValidateSet('s','ms','us')][string]$VerticalUnit,
    [ValidateSet('m','ft')][string]$HorizontalUnit,
    [string]$Crs,
    [Alias('-inspect')][switch]$Inspect,
    [Alias('-capabilities')][switch]$Capabilities,
    [Alias('-help')][switch]$Help,
    [switch]$NoPause
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$toolkitRoot = Split-Path -Parent $PSScriptRoot
$exitCode = 1
$interactive = (-not $NoPause) -and [string]::IsNullOrWhiteSpace($OutputRoot)
try {
    if ($Help) {
        Write-Output 'convert_zgy_to_segy.bat "INPUT.zgy" [OUTPUT_ROOT] [-Domain time] [-VerticalUnit ms] [-HorizontalUnit m] [-Crs "identifier"] [-NoPause]'
        Write-Output 'Use -Inspect to read metadata or -Capabilities to list supported profiles. Double-click or drag a ZGY to enter missing metadata interactively.'
        exit 0
    }
    Write-Output 'Petrel binary seismic to SEG-Y 0.3.0 - beta, source read-only'
    . (Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1')
    $dependencyCheck = Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot
    $arguments = @('-B', (Join-Path $PSScriptRoot 'petrel_file_convert.py'))
    if ($Capabilities) { $arguments += '--capabilities' }
    if ($Inspect) { $arguments += '--inspect' }
    if ($InputFile) { $arguments += @('--input', $InputFile) }
    if ($OutputRoot) { $arguments += @('--output-root', $OutputRoot) }
    if ($Domain) { $arguments += @('--domain', $Domain) }
    if ($VerticalUnit) { $arguments += @('--vertical-unit', $VerticalUnit) }
    if ($HorizontalUnit) { $arguments += @('--horizontal-unit', $HorizontalUnit) }
    if ($Crs) { $arguments += @('--crs', $Crs) }
    if ($interactive) { $arguments += '--interactive' }
    & (Join-Path $toolkitRoot 'runtime\python.exe') @arguments
    $exitCode = $LASTEXITCODE
} catch {
    Write-Output ('ERROR: ' + $_.Exception.Message)
} finally {
    if ($interactive) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode
