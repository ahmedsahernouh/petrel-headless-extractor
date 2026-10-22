# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param([Parameter(Mandatory)][string]$SessionFile)
. (Join-Path $PSScriptRoot 'FieldViewer.Desktop.Core.ps1')
$s=Read-FvJson $SessionFile 65536
if (-not $s -or $s.schema -ne 'fieldviewer.desktop.session/1') { exit 1 }
$trace=Join-Path $s.output 'GUI_SUPERVISOR_TRACE.txt'
function Write-FvTrace([string]$Message) { try { [IO.File]::AppendAllText($trace,([DateTime]::UtcNow.ToString('o')+' '+$Message+"`r`n")) } catch {} }
$backendStarted=$false
try {
    Write-FvTrace 'starting backend'
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=[string]$s.executable; $info.Arguments=[string]$s.argument_line
    $info.WorkingDirectory=[string]$s.package_root; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process; $process.StartInfo=$info
    $stdout=[IO.File]::Open($s.stdout,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $stderr=[IO.File]::Open($s.stderr,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try {
        if (-not $process.Start()) { throw 'Backend process did not start.' }
        $backendStarted=$true
        Write-FvTrace 'backend created'
        $s.pid=$process.Id
        $s.creation_utc=$process.StartTime.ToUniversalTime().ToString('o')
        for ($i=0;$i -lt 3;$i++) {
            if ($process.HasExited) { break }
            $w=Get-WmiObject Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction SilentlyContinue
            if ($w -and $w.CommandLine) { $s.command_line=[string]$w.CommandLine; break }
            Start-Sleep -Milliseconds 100
        }
        $s.state='running'; Write-FvJson $SessionFile $s
        $outTask=$process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $errTask=$process.StandardError.BaseStream.CopyToAsync($stderr)
        Write-FvTrace 'waiting for backend exit'
        $process.WaitForExit()
        [Threading.Tasks.Task]::WaitAll(@($outTask,$errTask))
        Write-FvTrace 'backend exited'
        $s.exit_code=$process.ExitCode
        $s.ended_utc=[DateTime]::UtcNow.ToString('o')
        $s.state='finished'
        Write-FvJson $SessionFile $s
    } finally { $stdout.Dispose(); $stderr.Dispose(); $process.Dispose() }
} catch {
    Write-FvTrace ('supervisor failure: '+$_.Exception.Message)
    $s.state=$(if ($backendStarted) { 'supervision_lost' } else { 'launch_failed' })
    $s | Add-Member -NotePropertyName launch_error -NotePropertyValue $_.Exception.Message -Force
    if (-not $backendStarted) { $s.ended_utc=[DateTime]::UtcNow.ToString('o') }
    try { Write-FvJson $SessionFile $s } catch {}
    exit 1
}
