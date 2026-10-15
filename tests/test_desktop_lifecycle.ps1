# Synthetic process lifetime checks; no Petrel, extraction or email.
$ErrorActionPreference='Stop'
$repo=[IO.Directory]::GetParent($PSScriptRoot).FullName
. (Join-Path $repo 'desktop/FieldViewer.Desktop.Core.ps1')
$root=Join-Path $repo ('build/desktop_lifecycle_'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$exe=Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
$fake=Join-Path $root 'fake child.ps1'
[IO.File]::WriteAllText($fake,"Write-Output 'started'; Start-Sleep -Seconds 8; Write-Output 'finished'; exit 0")
$sessionFile=Join-Path $root 'GUI_SESSION.json'
$childArgs=@('-NoProfile','-NonInteractive','-File',$fake)
$s=[ordered]@{schema='fieldviewer.desktop.session/1';id='lifecycle';package_root=$root;project='synthetic.pet';ptd='synthetic.ptd';selected_output=$root;output=$root;report_only=$false;
    executable=$exe;arguments=$childArgs;argument_line=(Join-FvArgs $childArgs);pid=$null;creation_utc=$null;command_line=$null;
    started_utc=[DateTime]::UtcNow.ToString('o');ended_utc=$null;exit_code=$null;state='starting';stdout=(Join-Path $root 'stdout.txt');stderr=(Join-Path $root 'stderr.txt');session_file=$sessionFile}
Write-FvJson $sessionFile $s
$driver=Join-Path $root 'short_lived_parent.ps1'
$driverText=@'
param($Repo,$SessionFile,$Receipt)
. (Join-Path $Repo 'desktop/FieldViewer.Desktop.Core.ps1')
$args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $Repo 'desktop/FieldViewer.Desktop.Supervisor.ps1'),'-SessionFile',$SessionFile)
$p=Start-Process -FilePath (Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe') -ArgumentList (Join-FvArgs $args) -WindowStyle Hidden -PassThru
Write-FvJson $Receipt @{supervisor_pid=$p.Id;created=$p.StartTime.ToUniversalTime().ToString('o')}
'@
[IO.File]::WriteAllText($driver,$driverText)
$receipt=Join-Path $root 'supervisor.json'
$ownerArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$driver,'-Repo',$repo,'-SessionFile',$sessionFile,'-Receipt',$receipt)
$owner=Start-Process -FilePath $exe -ArgumentList (Join-FvArgs $ownerArgs) -WindowStyle Hidden -PassThru
if (-not $owner.WaitForExit(15000) -or $owner.ExitCode -ne 0) { throw 'Synthetic launch parent failed' }
$deadline=[DateTime]::UtcNow.AddSeconds(15)
do { Start-Sleep -Milliseconds 200; $current=Read-FvJson $sessionFile 65536 } while ($current.state -eq 'starting' -and [DateTime]::UtcNow -lt $deadline)
if (-not (Test-FvProcessIdentity $current)) { throw 'Child was not live and identity-bound after launching parent exited' }
$wrongTime=($current | ConvertTo-Json -Depth 12 | ConvertFrom-Json)
$wrongTime.creation_utc=[DateTime]::Parse($current.creation_utc).AddTicks(1).ToString('o')
if (Test-FvProcessIdentity $wrongTime) { throw 'One-tick mismatch was accepted' }
$wrongArg=($current | ConvertTo-Json -Depth 12 | ConvertFrom-Json)
$wrongArg.command_line+=' unexpected'
if (Test-FvProcessIdentity $wrongArg) { throw 'Wrong command line was accepted' }
do { Start-Sleep -Milliseconds 250; $current=Read-FvJson $sessionFile 65536 } while ($current.state -ne 'finished' -and [DateTime]::UtcNow -lt $deadline)
if ($current.state -ne 'finished' -or $current.exit_code -ne 0 -or (Read-FvTail $current.stdout) -notmatch 'finished') { throw 'Independent backend did not complete with durable output' }
if ((Get-FvOutcome $current $null) -ne 'failed_or_missing_receipt') { throw 'Exit 0 without engine receipt became extraction success' }
Write-FvJson (Join-Path $root 'ACCEPTANCE.json') @{launch_parent_exited=$true;child_identity_after_parent_exit=$true;one_tick_mismatch_rejected=$true;argv_mismatch_rejected=$true;durable_completion=$true;no_extraction_performed=$true}
Write-Output ('Desktop lifecycle: PASS; evidence '+$root)
