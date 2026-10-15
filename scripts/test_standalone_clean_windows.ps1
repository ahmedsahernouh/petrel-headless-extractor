# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Offline standalone acceptance helper; run on a separate clean Windows x64 host.
param(
    [Parameter(Mandatory=$true)][string]$PackageRoot,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$ProjectFile = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$package = [IO.Path]::GetFullPath($PackageRoot)
$output = [IO.Path]::GetFullPath($OutputRoot)
if ($output.TrimEnd('\').Equals($package.TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase) -or
    $output.StartsWith($package.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Test output must be outside the package.'
}
$bat = Join-Path $package 'FieldViewer_data_extractor.bat'
if (-not (Test-Path -LiteralPath $bat -PathType Leaf)) { throw 'Provide the folder containing the standalone BAT and its FieldViewer support folder.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
$run = Join-Path $output ('CleanWindows_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,6))
[IO.Directory]::CreateDirectory($run) | Out-Null
$tools = @('python','python3','py','pip','petrel') | ForEach-Object {
    $name = $_
    @(Get-Command $name -ErrorAction SilentlyContinue) | ForEach-Object {
        [ordered]@{name=$name; path=$_.Source; command_type=[string]$_.CommandType}
    }
}
$receipt = [ordered]@{
    schema='fieldviewer.clean-windows-acceptance/1'; started_utc=[DateTimeOffset]::UtcNow.ToString('o')
    package=$package; output=$run; windows=[Environment]::OSVersion.VersionString
    powershell=[string]$PSVersionTable.PSVersion; known_commands=@($tools)
    clean_host_status='not_proven; host history must be independently confirmed'
    python_scope='Launcher must use the package runtime; this helper uses no system Python'
    network_scope='Helper performs no download; test with networking disabled on the separate host'
    doctor_exit=$null; extraction_exit=$null; status='running'; error=$null
}
try {
    & $bat -Check -NoPause 2>&1 | Out-File -LiteralPath (Join-Path $run 'dependency_check.log') -Encoding utf8
    $receipt.doctor_exit=$LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw ('Standalone dependency check failed: '+$LASTEXITCODE) }
    if ($ProjectFile) {
        # Exact test input is supplied by the tester. Never select a source drive
        # or original project automatically. Full hashes retain the app default.
        & $bat $ProjectFile (Join-Path $run 'extraction') -NoPause 2>&1 |
            Out-File -LiteralPath (Join-Path $run 'extraction_console.log') -Encoding utf8
        $receipt.extraction_exit=$LASTEXITCODE
        if ($LASTEXITCODE -notin @(0,10)) { throw ('Test extraction failed: '+$LASTEXITCODE) }
    }
    $receipt.status='runtime_check_passed; clean_host_and_dataset_acceptance_require_review'
} catch {
    $receipt.status='failed';$receipt.error=$_.Exception.Message
} finally {
    $receipt['finished_utc']=[DateTimeOffset]::UtcNow.ToString('o')
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $run 'CLEAN_WINDOWS_RESULT.json') -Encoding utf8
    Write-Output ('Acceptance receipt: '+(Join-Path $run 'CLEAN_WINDOWS_RESULT.json'))
}
if ($receipt.status -eq 'failed') { exit 1 }
