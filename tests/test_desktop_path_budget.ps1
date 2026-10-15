# GUI preflight regression; no extractor, Petrel, email or source mutation.
$ErrorActionPreference='Stop'
$repo=[IO.Directory]::GetParent($PSScriptRoot).FullName
. (Join-Path $repo 'desktop/FieldViewer.Desktop.Core.ps1')
$root=Join-Path ([IO.Path]::GetPathRoot($repo)) ('F'+[guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null
$source=Join-Path $root 'src';$output=Join-Path $root 'out';$deep=Join-Path $root 'output folder with excessive report nesting'
$app=Join-Path $root 'app';$scripts=Join-Path $app 'scripts'
foreach($p in @($source,$output,$deep,$scripts,(Join-Path $source 'test.ptd'))){[IO.Directory]::CreateDirectory($p)|Out-Null}
$pet=Join-Path $source 'test.pet';[IO.File]::WriteAllText($pet,'synthetic source')
[IO.File]::WriteAllText((Join-Path $scripts 'launch_standalone_petrel.ps1'),'throw "Synthetic launcher must not execute"')
$before=(Get-FileHash -LiteralPath $pet).Hash
$blocked=$false
try { New-FvSession $app $pet $deep $false | Out-Null } catch { $blocked=$_.Exception.Message -match 'too long for complete Windows reports' }
if(-not $blocked -or @(Get-ChildItem -LiteralPath $deep -Force).Count -ne 0){throw 'Long output did not fail before run creation'}
if(Test-Path -LiteralPath (Join-Path (Get-FvStateHome $app) 'current.json')){throw 'Rejected output wrote session pointer'}
$session=New-FvSession $app $pet $output $false
if([IO.Path]::GetFileName($session.output) -notmatch '^FV_[a-f0-9]{16}$' -or $session.id.Length -ne 32){throw 'Compact folder lost full session identity'}
if((Get-FvLatestSession $app).id -ne $session.id){throw 'Compact session failed reconnect binding'}
$collisionRejected=$false
try { New-Item -ItemType Directory -Path $session.output -ErrorAction Stop | Out-Null } catch { $collisionRejected=$true }
if(-not $collisionRejected){throw 'Existing run directory was reused'}
if((Get-FileHash -LiteralPath $pet).Hash -ne $before){throw 'Source changed'}
[ordered]@{status='passed';root=$root;checks=@('long_path_before_run','no_pointer_on_rejection','short_path_session','full_identity_preserved','reconnect_binding','existing_directory_refused','source_unchanged');session_file=$session.session_file}|ConvertTo-Json -Depth 4
