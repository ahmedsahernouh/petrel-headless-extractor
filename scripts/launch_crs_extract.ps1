# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param(
    [Parameter(Mandatory=$true)][string]$ProjectFile,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function ConvertTo-CrsArg([string]$Value) {
    if ($Value -match '^[^\s"]+$') { return $Value }
    $out = '"'; $slashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '\') { $slashes++; continue }
        if ($ch -eq '"') { $out += ('\' * (2 * $slashes + 1)) + '"'; $slashes = 0; continue }
        $out += ('\' * $slashes) + $ch; $slashes = 0
    }
    return $out + ('\' * (2 * $slashes)) + '"'
}
function Start-CrsChild([string]$Executable, [string[]]$Values, [string]$Stdout, [string]$Stderr) {
    $line = ($Values | ForEach-Object { ConvertTo-CrsArg $_ }) -join ' '
    Start-Process -FilePath $Executable -ArgumentList $line -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
}
try {
    if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) { throw 'An existing exclusive session output root is required.' }
    $output = Join-Path $OutputRoot 'CRS'
    if (Test-Path -LiteralPath $output) { throw 'CRS output already exists; choose a new run.' }
    if (-not [string]::IsNullOrWhiteSpace($PythonPath) -and -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Explicit Python runtime is unavailable: $PythonPath"
    }
    $toolkitRoot = Split-Path -Parent $PSScriptRoot
    $repairScript = Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1'
    if (-not (Test-Path -LiteralPath $repairScript -PathType Leaf)) {
        throw 'Bundled runtime repair script is unavailable; re-extract the complete release ZIP.'
    }
    . $repairScript
    Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot | Out-Null
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $PythonPath = Join-Path $toolkitRoot 'runtime\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Bundled Python runtime is unavailable after verified offline repair.' }
    $doctorArgs = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        (Join-Path $PSScriptRoot 'doctor_portable_petrel_toolkit.ps1'),'-PythonPath',$PythonPath,
        '-JsonOutput',(Join-Path $OutputRoot 'CRS_DOCTOR.json'))
    $doctor = Start-CrsChild (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') $doctorArgs `
        (Join-Path $OutputRoot 'CRS_DOCTOR_STDOUT.txt') (Join-Path $OutputRoot 'CRS_DOCTOR_STDERR.txt')
    if ($doctor.ExitCode -ne 0) { throw 'The toolkit doctor is blocked. Read CRS_DOCTOR.json before retrying.' }
    $values = @('-B',(Join-Path $PSScriptRoot 'fieldviewer_crs.py'),'--project',$ProjectFile,'--output',$output)
    $process = Start-CrsChild $PythonPath $values (Join-Path $OutputRoot 'CRS_CLI_STDOUT.json') `
        (Join-Path $OutputRoot 'CRS_CLI_STDERR.txt')
    Get-Content -LiteralPath (Join-Path $OutputRoot 'CRS_CLI_STDOUT.json') -Raw | Write-Output
    exit $process.ExitCode
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
