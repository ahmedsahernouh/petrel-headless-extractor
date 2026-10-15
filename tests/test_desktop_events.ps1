# Bounded display reader regression checks; no engine work.
$ErrorActionPreference='Stop'
$repo=[IO.Directory]::GetParent($PSScriptRoot).FullName
. (Join-Path $repo 'desktop/FieldViewer.Desktop.Core.ps1')
$root=Join-Path $repo ('build/desktop_events_'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$path=Join-Path $root 'events.jsonl'
$utf8=New-Object Text.UTF8Encoding($false)
$unicode=[char]0x00e9
$first='{"event":"stage","label":"'+$unicode+'","time_utc":"first"}'+"`n"
$second='{"event":"operation_started","operation":"next","time_utc":"second"}'+"`n"
[IO.File]::WriteAllText($path,$first+$second,$utf8)
$firstBytes=$utf8.GetByteCount($first)
$a=Read-FvEventSlice $path 0 ($firstBytes+7)
if ($a.offset -ne $firstBytes -or $a.events.Count -ne 1 -or $a.events[0].label -ne [string]$unicode) { throw 'UTF-8 byte offset or event value changed' }
$b=Read-FvEventSlice $path $a.offset 128
if ($b.events.Count -ne 1 -or $b.events[0].operation -ne 'next') { throw 'Event after split was lost' }
[IO.File]::WriteAllText($path,('x'*300)+"`n"+$second,$utf8)
$offset=0L; $found=$false; $skipped=$false
for ($i=0;$i -lt 8;$i++) {
    $slice=Read-FvEventSlice $path $offset 80
    if ($slice.offset -lt $offset) { throw 'Event cursor moved backwards' }
    $offset=$slice.offset; $skipped=$skipped -or $slice.skipped
    foreach ($e in $slice.events) { if ($e.operation -eq 'next') { $found=$true } }
}
if (-not $found -or -not $skipped) { throw 'Oversized line stalled bounded event polling' }
$p=Get-FvProgress @([pscustomobject]@{event='stage';label='Active';time_utc='real'})
$p=Get-FvProgress @([pscustomobject]@{event='heartbeat';time_utc='timer'}) $p
if ($p.LastActivityUtc -ne 'real' -or $p.Stage -ne 'Active') { throw 'Heartbeat replaced actual progress evidence' }
$stateHome=Get-FvStateHome $root
if ($null -ne (Get-FvLatestSession $root)) { throw 'Unexpected prior synthetic session' }
Write-FvJson (Join-Path $stateHome 'current.json') @{package_root=$root;session_file=(Join-Path $root 'missing.json');id='synthetic'}
$blocked=$false
try { Get-FvLatestSession $root | Out-Null } catch { $blocked=$true }
if (-not $blocked) { throw 'Missing saved session silently allowed a fresh run' }
$sessionFile=Join-Path $root 'session.json'
Write-FvJson $sessionFile @{id='synthetic';session_file=$sessionFile;output=$root;state='finished'}
Write-FvJson (Join-Path $stateHome 'current.json') @{package_root=$root;session_file=$sessionFile;id='synthetic'}
if ((Get-FvLatestSession $root).state -ne 'finished') { throw 'Valid saved session did not reconnect' }
[IO.File]::WriteAllText((Join-Path $stateHome 'current.json'),'{broken')
$blocked=$false
try { Get-FvLatestSession $root | Out-Null } catch { $blocked=$true }
if (-not $blocked) { throw 'Corrupt saved pointer silently allowed a fresh run' }
Write-FvJson (Join-Path $root 'ACCEPTANCE.json') @{unicode_cursor=$true;oversized_line_does_not_stall=$true;heartbeat_is_not_actual_activity=$true;missing_or_corrupt_session_blocks=$true;valid_session_reconnect=$true}
Write-Output ('Desktop events: PASS; evidence '+$root)
