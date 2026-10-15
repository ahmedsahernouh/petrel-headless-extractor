# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'FieldViewer.Desktop.Core.ps1')
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'FieldViewer.Desktop.Mail.ps1')
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
$temp=Join-Path ([IO.Path]::GetTempPath()) ('FVDesktopTest_'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temp) | Out-Null
$passed=$false
try {
    $src=Join-Path $temp 'source & é'; $out=Join-Path $temp 'output'; [IO.Directory]::CreateDirectory($src) | Out-Null; [IO.Directory]::CreateDirectory($out) | Out-Null
    $pet=Join-Path $src 'Exact.pet'; $ptd=Join-Path $src 'Exact.ptd'; [IO.Directory]::CreateDirectory($ptd) | Out-Null
    Assert ($src.Contains('é')) 'Windows PowerShell did not load this UTF-8 test script as Unicode'
    [IO.File]::WriteAllText($pet,'pet'); [IO.File]::WriteAllText((Join-Path $ptd 'one.bin'),'abc')
    [IO.File]::WriteAllText((Join-Path $src 'Neighbor.pet'),'neighbor'); [IO.Directory]::CreateDirectory((Join-Path $src 'Neighbor.ptd')) | Out-Null
    $valid=Assert-FvInputs $pet $out; Assert ($valid.Ptd -eq $ptd) 'Exact pair validation failed'
    $blocked=$false; try { Assert-FvInputs $pet $src | Out-Null } catch { $blocked=$true }; Assert $blocked 'Source-contained output was accepted'
    $quoted=Join-FvArgs @('-ProjectFile',$pet,'-OutputRoot',(Join-Path $out 'space & ^ !'))
    Assert ($quoted -match '".*source & é.*"') 'Windows argument quoting lost Unicode or spaces'
    $scan=Join-Path $temp 'input_size.json'
    & (Join-Path (Split-Path -Parent $PSScriptRoot) 'FieldViewer.Desktop.SizeScan.ps1') -Root $ptd -ExactFile $pet -ResultPath $scan
    $size=Read-FvJson $scan 16384
    Assert ($size.logical_path_sum_bytes -eq 6 -and -not $size.partial -and $size.aliases_included) 'Input scan included neighbors or wrong scope'
    $run=Join-Path $out 'FV_run_test'; [IO.Directory]::CreateDirectory($run) | Out-Null
    $data=Join-Path $run 'Exact_20261012_abc_data'; [IO.Directory]::CreateDirectory($data) | Out-Null
    $exports=Join-Path $run 'Exact_20261012_abc_EXPORTS'; [IO.Directory]::CreateDirectory($exports) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $exports 'logs')) | Out-Null
    $request=@{project_file=$pet;output_root=$run;report_only=$false}; Write-FvJson (Join-Path $data 'request.json') $request
    $report=Join-Path $run 'Exact_20261012_abc_REPORT.html'; [IO.File]::WriteAllText($report,'report')
    $log=Join-Path $run 'Exact_20261012_abc_LOG.txt'; [IO.File]::WriteAllText($log,'private path')
    $index=Join-Path $exports 'FILE_INDEX.csv'; [IO.File]::WriteAllText($index,'name')
    $s=[pscustomobject]@{ schema='fieldviewer.desktop.session/1'; id='test'; project=$pet; selected_output=$out; output=$run; report_only=$false;
        state='finished'; exit_code=10; process_log=$log; started_utc=[DateTime]::UtcNow.ToString('o') }
    $early=Get-FvLinks $s $null
    Assert ($early.reports['Main report'] -eq $report -and $early.folders['All exports'] -eq $exports) 'Interim links did not bind request.json'
    $receiptPath=Join-Path $data 'RUN_RESULT.json'
    # Empty metadata keys occur in the real engine receipt and break ConvertFrom-Json.
    $raw='{"status":"completed_with_gaps","elapsed_seconds":2.0,"full_report":"'+($report.Replace('\','\\'))+'","file_index":"'+($index.Replace('\','\\'))+'","process_log":"'+($log.Replace('\','\\'))+'","qc":{"report_path":"'+((Join-Path $data 'qc.json').Replace('\','\\'))+'"},"empty":{"":1}}'
    [IO.File]::WriteAllText($receiptPath,$raw)
    [IO.File]::WriteAllText((Join-Path $data 'qc.json'),'{}')
    $receipt=Get-FvReceipt $s; Assert ((Get-FvProp $receipt 'status') -eq 'completed_with_gaps') 'Large/empty-key JSON fallback failed'
    $links=Get-FvLinks $s $receipt
    Assert ($links.folders['All exports'] -eq $exports -and $links.reports['QC'] -like '*.json') 'Receipt link mapping failed'
    Assert ((Get-FvOutcome $s $receipt) -eq 'completed_with_gaps') 'Exit 10 mapping failed'
    $s.exit_code=0; Assert ((Get-FvOutcome $s $null) -eq 'failed_or_missing_receipt') 'Exit 0 without receipt became success'
    $s.exit_code=130; Assert ((Get-FvOutcome $s $null) -eq 'cancelled') 'Exit 130 lost cancellation distinction'
    $s.exit_code=7; Assert ((Get-FvOutcome $s $null) -eq 'failed_or_missing_receipt') 'Bootstrap failure became success'
    $s.state='running'; $s | Add-Member -NotePropertyName pid -NotePropertyValue 999999 -Force
    $s | Add-Member -NotePropertyName creation_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    $s | Add-Member -NotePropertyName command_line -NotePropertyValue 'not this process' -Force
    Assert ((Get-FvSessionState $s) -eq 'stale_identity') 'Stale PID was accepted'
    $s.state='finished'
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipPath=Join-Path $run 'Exact_20261012_abc_SUPPORT.zip'
    $zip=[IO.Compression.ZipFile]::Open($zipPath,[IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry=$zip.CreateEntry('diagnostics/001.txt'); $writer=New-Object IO.StreamWriter($entry.Open()); $writer.Write('redacted body'); $writer.Dispose()
        $manifest=@{files=@(@{role='Exact_LOG.txt';member='diagnostics/001.txt';original_bytes=(Get-Item -LiteralPath $log).Length});unavailable=@()}
        $entry=$zip.CreateEntry('CONTENTS.json'); $writer=New-Object IO.StreamWriter($entry.Open()); $writer.Write(($manifest | ConvertTo-Json -Depth 6)); $writer.Dispose()
    } finally { $zip.Dispose() }
    $mailReceipt=[pscustomobject]@{process_log=$log}
    $copy=New-FvClosedLogCopy $s $mailReceipt $false
    Assert ($copy.Privacy -eq 'Best-effort redacted' -and [IO.File]::ReadAllText($copy.Path) -eq 'redacted body') 'Mapped redacted copy failed'
    [IO.File]::Delete($zipPath)
    $unreviewed=New-FvClosedLogCopy $s $mailReceipt $false
    Assert (-not $unreviewed.Path) 'Unredacted log silently selected'
    $reviewed=New-FvClosedLogCopy $s $mailReceipt $true
    Assert ($reviewed.Privacy -eq 'UNREDACTED' -and [IO.File]::ReadAllText($reviewed.Path) -eq 'private path') 'Explicit unredacted copy failed'
    $events=@([pscustomobject]@{event='stage';number=2;label='Hashing';time_utc='today'},[pscustomobject]@{event='hash_file_progress';done=4;total=10;source='a.zgy';time_utc='today'})
    $progress=Get-FvProgress $events; Assert ($progress.Percent -eq 40) 'Known hash percentage wrong'
    $events+=([pscustomobject]@{event='object_started';object_index=1;object_total=2})
    Assert ($null -eq (Get-FvProgress $events).Percent) 'Object processing inherited hash percentage'
    $carried=Get-FvProgress @([pscustomobject]@{event='object_phase';object_index=2;object_total=2;operation='write_csv'}) (Get-FvProgress $events)
    Assert ($carried.Stage -eq 'Hashing' -and $carried.Activity -match 'write_csv' -and $null -eq $carried.Percent) 'Long-stage progress lost actual stage or reused hash percentage'
    $mailSource=[IO.File]::ReadAllText((Join-Path (Split-Path -Parent $PSScriptRoot) 'FieldViewer.Desktop.Mail.ps1'))
    $sourceMatch=[regex]::Match($mailSource,"(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@")
    Assert $sourceMatch.Success 'Simple MAPI declaration missing'
    Add-Type -TypeDefinition $sourceMatch.Groups[1].Value
    Assert ([bool]('FvMapi' -as [type])) 'Simple MAPI interop failed to compile'
    $fake=Join-Path $temp 'fake backend.ps1'; [IO.File]::WriteAllText($fake,"Write-Output 'durable stdout'; [Console]::Error.WriteLine('durable stderr'); Start-Sleep -Seconds 1; exit 7")
    $supervised=Join-Path $out 'supervised'; [IO.Directory]::CreateDirectory($supervised) | Out-Null
    $exe=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $fakeArgs=@('-NoProfile','-NonInteractive','-File',$fake)
    $sessionFile=Join-Path $supervised 'GUI_SESSION.json'
    $fakeSession=[ordered]@{schema='fieldviewer.desktop.session/1';id='fake';package_root=$temp;project=$pet;ptd=$ptd;selected_output=$out;output=$supervised;report_only=$false;
        executable=$exe;arguments=$fakeArgs;argument_line=(Join-FvArgs $fakeArgs);pid=$null;creation_utc=$null;command_line=$null;
        started_utc=[DateTime]::UtcNow.ToString('o');ended_utc=$null;exit_code=$null;state='starting';
        stdout=(Join-Path $supervised 'stdout.txt');stderr=(Join-Path $supervised 'stderr.txt');session_file=$sessionFile}
    Write-FvJson $sessionFile $fakeSession
    $supervisorArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path (Split-Path -Parent $PSScriptRoot) 'FieldViewer.Desktop.Supervisor.ps1'),'-SessionFile',$sessionFile)
    $supervisor=Start-Process -FilePath $exe -ArgumentList (Join-FvArgs $supervisorArgs) -WindowStyle Hidden -PassThru
    Assert ($supervisor.WaitForExit(30000)) 'Supervisor did not terminate'
    $after=Read-FvJson $sessionFile 65536
    Assert ($after.state -eq 'finished' -and $after.exit_code -eq 7 -and $after.pid -gt 0) 'Supervisor lost backend exit or PID'
    Assert ((Get-FvOutcome $after $null) -eq 'failed_or_missing_receipt') 'Bootstrap failure became success'
    Assert ((Read-FvTail $after.stdout) -match 'durable stdout' -and (Read-FvTail $after.stderr) -match 'durable stderr') 'Supervisor lost durable child streams'
    Assert (-not (Test-FvProcessIdentity $after)) 'Exited backend identity was accepted as live'
    Write-Output 'Desktop core tests: PASS (input scope, links, receipts, outcomes, email copies, progress, supervisor)'
    $passed=$true
} finally {
    $resolved=[IO.Path]::GetFullPath($temp)
    if ($passed -and $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('FVDesktopTest_')) { try { Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop } catch { Write-Warning "Synthetic test scratch retained: $resolved" } }
    elseif (-not $passed) { Write-Warning "Synthetic test scratch retained: $resolved" }
}
