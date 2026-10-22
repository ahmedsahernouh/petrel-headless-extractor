# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param(
    [Parameter(Mandatory=$true)][string]$ProjectFile,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$RuntimeConfig
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function ConvertTo-RescueArg([string]$Value) {
    if ($Value -match '^[^\s"]+$') { return $Value }
    $out='"'; $slashes=0
    foreach($ch in $Value.ToCharArray()) {
        if($ch -eq '\') { $slashes++; continue }
        if($ch -eq '"') { $out+=('\'*(2*$slashes+1))+'"'; $slashes=0; continue }
        $out+=('\'*$slashes)+$ch; $slashes=0
    }
    return $out+('\'*(2*$slashes))+'"'
}
function Start-RescueChild([string]$Executable,[string[]]$Values,[string]$Stdout,[string]$Stderr) {
    $line=($Values | ForEach-Object { ConvertTo-RescueArg $_ }) -join ' '
    Start-Process -FilePath $Executable -ArgumentList $line -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
}
function Resolve-RescuePortableFile([string]$Config,[string]$Raw,[string]$Name) {
    if (-not $Raw -or $Raw -match '[:\x00]' -or $Raw -match '^[\\/]') { throw "Invalid package-relative RESCUE path: $Name" }
    $parts=$Raw -split '[\\/]'
    foreach ($part in $parts) {
        if (-not $part -or $part -eq '.' -or $part -eq '..' -or $part -match '[ .]$') { throw "Invalid package-relative RESCUE path: $Name" }
    }
    $root=[IO.Path]::GetFullPath((Split-Path -Parent $Config))
    $path=$root
    foreach ($part in $parts) { $path=Join-Path $path $part }
    $path=[IO.Path]::GetFullPath($path)
    if (-not $path.StartsWith($root.TrimEnd('\','/')+'\',[StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "RESCUE runtime $Name must be a local package file." }
    $current=[IO.Path]::GetPathRoot($path)
    foreach ($part in $path.Substring($current.Length).Split([char[]]@('\','/'),[StringSplitOptions]::RemoveEmptyEntries)) {
        $current=Join-Path $current $part
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "RESCUE runtime $Name uses a junction or symbolic link." }
    }
    return $path
}
function Assert-RescueNoReparse([string]$Path) {
    $full=[IO.Path]::GetFullPath($Path)
    $current=[IO.Path]::GetPathRoot($full)
    foreach ($part in $full.Substring($current.Length).Split([char[]]@('\','/'),[StringSplitOptions]::RemoveEmptyEntries)) {
        $current=Join-Path $current $part
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'RESCUE configuration or executable uses a junction or symbolic link.' }
    }
}
try {
    if (-not $RuntimeConfig) { $RuntimeConfig=Join-Path (Split-Path -Parent $PSScriptRoot) 'rescue_runtime.json' }
    if(-not (Test-Path -LiteralPath $RuntimeConfig -PathType Leaf)) { throw 'The experimental RESCUE runtime is not configured in this build.' }
    Assert-RescueNoReparse $RuntimeConfig
    if((Get-Item -LiteralPath $RuntimeConfig).Length -gt 65536) { throw 'RESCUE runtime configuration exceeds64KiB.' }
    $runtime=Get-Content -LiteralPath $RuntimeConfig -Raw | ConvertFrom-Json
    if(-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) { throw 'An existing exclusive session output root is required.' }
    $output=Join-Path $OutputRoot 'RESCUE'
    if(Test-Path -LiteralPath $output) { throw 'RESCUE output already exists; choose a new run.' }
    $toolkitRoot=Split-Path -Parent $PSScriptRoot
    if(Test-Path -LiteralPath (Join-Path $toolkitRoot 'STANDALONE.txt') -PathType Leaf) {
        $repairScript=Join-Path $PSScriptRoot 'repair_standalone_dependencies.ps1'
        if(-not (Test-Path -LiteralPath $repairScript -PathType Leaf)) { throw 'Bundled runtime repair script is unavailable; re-extract the complete release ZIP.' }
        . $repairScript
        Repair-PetrelStandaloneDependencies -ToolkitRoot $toolkitRoot | Out-Null
    }
    if($runtime.schema -eq 'fieldviewer.rescue-runtime/2') {
        $runtime.python=Resolve-RescuePortableFile $RuntimeConfig ([string]$runtime.python) 'python'
    } elseif($runtime.schema -ne 'fieldviewer.rescue-runtime/1' -or -not [IO.Path]::IsPathRooted([string]$runtime.python) -or -not (Test-Path -LiteralPath $runtime.python -PathType Leaf)) { throw 'RESCUE runtime configuration is invalid.' }
    Assert-RescueNoReparse ([string]$runtime.python)
    $doctorArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'doctor_portable_petrel_toolkit.ps1'),'-PythonPath',[string]$runtime.python,'-JsonOutput',(Join-Path $OutputRoot 'RESCUE_DOCTOR.json'))
    $doctor=Start-RescueChild (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') $doctorArgs (Join-Path $OutputRoot 'RESCUE_DOCTOR_STDOUT.txt') (Join-Path $OutputRoot 'RESCUE_DOCTOR_STDERR.txt')
    if($doctor.ExitCode -ne 0) { throw 'The toolkit doctor is blocked. Read RESCUE_DOCTOR.json before retrying.' }
    $values=@('-B',(Join-Path $PSScriptRoot 'fieldviewer_rescue.py'),'--project',$ProjectFile,'--output',$output,'--runtime-config',$RuntimeConfig)
    $process=Start-RescueChild ([string]$runtime.python) $values (Join-Path $OutputRoot 'RESCUE_CLI_STDOUT.json') (Join-Path $OutputRoot 'RESCUE_CLI_STDERR.txt')
    Get-Content -LiteralPath (Join-Path $OutputRoot 'RESCUE_CLI_STDOUT.json') -Raw | Write-Output
    exit $process.ExitCode
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
