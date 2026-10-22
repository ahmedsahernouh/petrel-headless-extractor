# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Focused launcher tests; all Python/doctor children are intercepted.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$source = Join-Path $PSScriptRoot 'launch_crs_extract.ps1'
$hostExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('FV_CRS_launcher_' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $scratch
try {
    foreach ($case in @('cold relocated package', 'warm relocated package', 'explicit path', 'repair failure', 'missing explicit path')) {
        $root = Join-Path $scratch $case
        $scripts = Join-Path $root 'scripts'
        $session = Join-Path $root 'session'
        $null = New-Item -ItemType Directory -Path $scripts, $session
        Copy-Item -LiteralPath $source -Destination (Join-Path $scripts 'launch_crs_extract.ps1')
        Set-Content -LiteralPath (Join-Path $scripts 'doctor_portable_petrel_toolkit.ps1') -Value '# intercepted'
        Set-Content -LiteralPath (Join-Path $scripts 'fieldviewer_crs.py') -Value '# intercepted'
        $runtime = Join-Path $root 'runtime\python.exe'
        if ($case -eq 'warm relocated package') {
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $runtime)
            Set-Content -LiteralPath $runtime -Value 'runtime marker'
        }
        $repair = @'
function Repair-PetrelStandaloneDependencies {
    param([string]$ToolkitRoot)
    Set-Content -LiteralPath (Join-Path $env:FV_TEST_SESSION 'repair.txt') -Value $ToolkitRoot
    if ($env:FV_TEST_FAIL_REPAIR -eq '1') { throw 'verified cache unavailable' }
    $python = Join-Path $ToolkitRoot 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $python) -Force
        Set-Content -LiteralPath $python -Value 'runtime marker'
    }
}
'@
        Set-Content -LiteralPath (Join-Path $scripts 'repair_standalone_dependencies.ps1') -Value $repair
        $wrapper = @'
param([string]$Launcher,[string]$Project,[string]$Output,[string]$Explicit)
function Start-Process {
    param([string]$FilePath,[string]$ArgumentList,[string]$WindowStyle,[switch]$Wait,[switch]$PassThru,
          [string]$RedirectStandardOutput,[string]$RedirectStandardError)
    Add-Content -LiteralPath (Join-Path $env:FV_TEST_SESSION 'children.txt') -Value ($FilePath + '|' + $ArgumentList)
    Set-Content -LiteralPath $RedirectStandardOutput -Value '{"status":"completed"}'
    Set-Content -LiteralPath $RedirectStandardError -Value ''
    return [pscustomobject]@{ ExitCode = 0 }
}
if ($Explicit) { & $Launcher -ProjectFile $Project -OutputRoot $Output -PythonPath $Explicit }
else { & $Launcher -ProjectFile $Project -OutputRoot $Output }
exit $LASTEXITCODE
'@
        $wrapperPath = Join-Path $root 'invoke.ps1'
        Set-Content -LiteralPath $wrapperPath -Value $wrapper
        $env:FV_TEST_SESSION = $session
        $env:FV_TEST_FAIL_REPAIR = if ($case -eq 'repair failure') { '1' } else { '0' }
        $explicit = if ($case -in @('explicit path', 'missing explicit path')) { Join-Path $root 'chosen-python.exe' } else { '' }
        if ($explicit -and $case -ne 'missing explicit path') { Set-Content -LiteralPath $explicit -Value 'explicit marker' }
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$wrapperPath,
            '-Launcher',(Join-Path $scripts 'launch_crs_extract.ps1'),'-Project',(Join-Path $root 'sample.pet'),
            '-Output',$session)
        if ($explicit) { $args += @('-Explicit',$explicit) }
        $ErrorActionPreference = 'Continue'
        $result = & $hostExe @args 2>&1
        $ErrorActionPreference = 'Stop'
        $code = $LASTEXITCODE
        if ($case -eq 'missing explicit path') {
            Assert ($code -eq 2) "Missing explicit runtime exit code changed ($code)"
            Assert (-not (Test-Path -LiteralPath (Join-Path $session 'repair.txt'))) 'Repair started with an invalid explicit path'
            Assert (-not (Test-Path -LiteralPath (Join-Path $session 'children.txt'))) 'Doctor or CLI started with an invalid explicit path'
            Assert (($result -join "`n") -match 'Explicit Python runtime is unavailable') 'Missing explicit path detail missing'
            continue
        }
        Assert ((Test-Path -LiteralPath (Join-Path $session 'repair.txt'))) "$case did not invoke verified repair (exit $code): $($result -join ' ')"
        if ($case -eq 'repair failure') {
            Assert ($code -eq 2) "Repair failure exit code changed ($code): $($result -join ' ')"
            Assert (-not (Test-Path -LiteralPath (Join-Path $session 'children.txt'))) 'Doctor or CLI started after failed repair'
            Assert (($result -join "`n") -match 'verified cache unavailable') 'Repair failure detail missing'
            continue
        }
        Assert ($code -eq 0) "$case failed: $($result -join ' ')"
        $children = @(Get-Content -LiteralPath (Join-Path $session 'children.txt'))
        Assert ($children.Count -eq 2) "$case did not run doctor and CLI"
        $selected = if ($explicit) { $explicit } else { $runtime }
        Assert ($children[0].Contains($selected) -and $children[1].StartsWith($selected + '|')) "$case selected wrong Python"
        Assert (-not $children[1].Contains('system32\python')) "$case used system Python"
    }
    Write-Output 'CRS launcher tests: PASS (cold, warm, explicit, repair failure, invalid explicit path)'
} finally {
    Remove-Item Env:FV_TEST_SESSION -ErrorAction SilentlyContinue
    Remove-Item Env:FV_TEST_FAIL_REPAIR -ErrorAction SilentlyContinue
    $resolved = [IO.Path]::GetFullPath($scratch)
    if ([IO.Path]::GetDirectoryName($resolved) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or
        [IO.Path]::GetFileName($resolved) -notmatch '^FV_CRS_launcher_[0-9a-f]{32}$') {
        throw 'Refusing scratch cleanup outside exact launcher test folder.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
