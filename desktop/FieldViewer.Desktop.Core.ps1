# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Field Viewer desktop adapter. No extraction logic lives here.
Set-StrictMode -Version Latest

function Get-FvEngineRoot([string]$PackageRoot) {
    foreach ($root in @((Join-Path $PackageRoot 'FieldViewer'), $PackageRoot)) {
        if (Test-Path -LiteralPath (Join-Path $root 'scripts/launch_standalone_petrel.ps1') -PathType Leaf) { return $root }
    }
    throw 'The FieldViewer engine launcher is missing from this package.'
}
function Get-FvMetadata([string]$PackageRoot) {
    $engine = Get-FvEngineRoot $PackageRoot
    $toolkit = Read-FvJson (Join-Path $engine 'toolkit.json') 65536
    if (-not $toolkit) { throw 'Engine toolkit.json is unavailable.' }
    $edition = Read-FvJson (Join-Path $PackageRoot 'EDITION.json') 65536
    $release=[string](Get-FvProp $edition 'release_version')
    if (-not $release) { $release=[string](Get-FvProp $toolkit 'release_version') }
    if (-not $release) { $release=[string](Get-FvProp $toolkit 'version') }
    $preparation=[string](Get-FvProp $edition 'preparation_revision')
    if (-not $preparation) { $preparation='development' }
    $source=[string](Get-FvProp $edition 'source_revision')
    if (-not $source) { $source='development' }
    [pscustomobject]@{ EngineRoot=$engine; PackageRoot=[IO.Path]::GetFullPath($PackageRoot);
        Version=[string](Get-FvProp $toolkit 'version'); Build=[string](Get-FvProp $toolkit 'build_revision');
        ReleaseVersion=$release; PreparationRevision=$preparation; SourceRevision=$source;
        RescueExportAvailable=((Get-FvProp $toolkit 'rescue_export_available') -ne $false);
        Edition=$(if ($edition) { [string](Get-FvProp $edition 'edition') } else { 'Desktop development' });
        GuiRevision=$(if ($edition) { [string](Get-FvProp $edition 'gui_revision') } else { 'source' });
        EngineTag=$(if ($edition) { [string](Get-FvProp $edition 'engine_tag') } else { 'unpackaged' });
        EngineZipSha256=$(if ($edition) { [string](Get-FvProp $edition 'engine_zip_sha256') } else { 'unpackaged' }) }
}
function Read-FvJson([string]$Path, [long]$MaxBytes=262144) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $info = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($info.Length -gt $MaxBytes) { throw "JSON file exceeds $MaxBytes bytes: $Path" }
    $stream = [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try {
        $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8, $true); $value=$reader.ReadToEnd()
        try { return ($value | ConvertFrom-Json -ErrorAction Stop) }
        catch {
            # Engine receipts can contain an empty metadata key, which Windows
            # PowerShell ConvertFrom-Json rejects. The framework parser accepts it.
            Add-Type -AssemblyName System.Web.Extensions
            $parser=New-Object System.Web.Script.Serialization.JavaScriptSerializer
            $parser.MaxJsonLength=[int]$MaxBytes; $parser.RecursionLimit=512
            return $parser.DeserializeObject($value)
        }
    }
    finally { $stream.Dispose() }
}
function Get-FvProp($Object,[string]$Name) {
    if ($null -eq $Object) { return $null }
    if ($Object -is [Collections.IDictionary]) { if (([Collections.IDictionary]$Object).Contains($Name)) { return $Object[$Name] }; return $null }
    $p=$Object.PSObject.Properties[$Name]
    if ($p) { return $p.Value }; return $null
}
function Write-FvJson([string]$Path, $Value) {
    $parent=[IO.Path]::GetDirectoryName($Path); [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temp,($Value | ConvertTo-Json -Depth 14), (New-Object Text.UTF8Encoding($false)))
    $backup=$Path+'.'+[guid]::NewGuid().ToString('N')+'.bak'
    try { if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temp,$Path,$backup) } else { [IO.File]::Move($temp,$Path) } }
    finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }; if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) } }
}
function ConvertTo-FvArg([string]$Value) {
    # CommandLineToArgvW-compatible quoting; do not invoke a shell expression.
    if ($Value -match '^[^\s"]+$') { return $Value }
    $out='"'; $slashes=0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '\') { $slashes++; continue }
        if ($ch -eq '"') { $out += ('\' * (2*$slashes+1)) + '"'; $slashes=0; continue }
        $out += ('\' * $slashes) + $ch; $slashes=0
    }
    $out += ('\' * (2*$slashes)) + '"'
    return $out
}
function Join-FvArgs([string[]]$Values) { return (($Values | ForEach-Object { ConvertTo-FvArg $_ }) -join ' ') }
function Test-FvWithin([string]$Candidate,[string]$Root,[bool]$AllowRoot=$true) {
    $candidateFull=[IO.Path]::GetFullPath($Candidate).TrimEnd('\','/')
    $rootFull=[IO.Path]::GetFullPath($Root).TrimEnd('\','/')
    if ($AllowRoot -and [string]::Equals($candidateFull,$rootFull,[StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $candidateFull.StartsWith($rootFull+'\',[StringComparison]::OrdinalIgnoreCase)
}
function Test-FvNoReparse([string]$Path) {
    $current=[IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    $parts=[IO.Path]::GetFullPath($Path).Substring($current.Length).Split([char[]]@('\','/'),[StringSplitOptions]::RemoveEmptyEntries)
    foreach ($part in $parts) {
        $current=Join-Path $current $part
        if (Test-Path -LiteralPath $current) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
        }
    }
    return $true
}
function Assert-FvInputs([string]$Project,[string]$Output) {
    $pet=[IO.Path]::GetFullPath($Project); $target=[IO.Path]::GetFullPath($Output)
    if ([IO.Path]::GetExtension($pet) -ine '.pet' -or -not (Test-Path -LiteralPath $pet -PathType Leaf)) { throw 'Choose an existing exact .pet file.' }
    $ptd=[IO.Path]::ChangeExtension($pet,'.ptd')
    if (-not (Test-Path -LiteralPath $ptd -PathType Container)) { throw "Matching .ptd folder is missing: $ptd" }
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw 'Choose an existing output folder.' }
    if (Test-FvWithin $target ([IO.Path]::GetDirectoryName($pet))) { throw 'Output must be outside the source project directory.' }
    if (-not (Test-FvNoReparse $target)) { throw 'Output folder cannot use a junction or symbolic link.' }
    $probe=Join-Path $target ('.fv_write_'+[guid]::NewGuid().ToString('N'))
    try { [IO.File]::WriteAllText($probe,''); [IO.File]::Delete($probe) } catch { throw "Output folder is not writable: $($_.Exception.Message)" }
    [pscustomobject]@{ Project=$pet; Ptd=$ptd; Output=$target }
}
function Get-FvStateHome([string]$PackageRoot) {
    $key=[Security.Cryptography.SHA256]::Create()
    try { $hash=([BitConverter]::ToString($key.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($PackageRoot).ToUpperInvariant())))).Replace('-','').Substring(0,16) }
    finally { $key.Dispose() }
    $base=[Environment]::GetFolderPath('LocalApplicationData')
    if (-not $base) { $base=[IO.Path]::GetTempPath() }
    return (Join-Path $base ('FieldViewerDesktop\'+$hash))
}
function Assert-FvReportPathBudget([string]$Output) {
    # RC1009's report writer stages assessments below the run folder. Its
    # native-data preflight does not reserve these flat/sharded report paths.
    # Reserve the maximum sanitized label (28 ASCII characters), full generation
    # identity and longest framed member without changing the engine or settings.
    $run=Join-Path ([IO.Path]::GetFullPath($Output)) ('FV_'+('f'*16))
    $stem=('p'*28)+'_20000101_000000_ffffff_data'
    $stage=Join-Path (Join-Path (Join-Path (Join-Path $run $stem) 'interoperability') ('f'*32)) 'validation_opendtect.json.ffffffff.assessment-stage'
    $names=@('validation_opendtect.json.ffffffffffff.partial','validation_opendtect.preparation_dependency_cycles.0024.jsonl')
    foreach($name in $names) {
        $path=Join-Path $stage $name
        if ($path.Length -gt 259 -or $stage.Length -gt 247) {
            throw 'The output folder is too long for complete Windows reports. Choose a shorter folder, such as C:\FV. Nothing has been started.'
        }
    }
}
function Get-FvRescueRuntime([string]$PackageRoot) {
    $engine=Get-FvEngineRoot $PackageRoot
    $launcher=Join-Path $engine 'scripts/launch_rescue_export.ps1'
    $localConfig=Join-Path $PackageRoot 'rescue_runtime.local.json'
    $config=if (Test-Path -LiteralPath $localConfig -PathType Leaf) { $localConfig } else { Join-Path $engine 'rescue_runtime.json' }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'RESCUE launcher is unavailable in this package.' }
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw "RESCUE runtime configuration is missing: $config" }
    if (-not (Test-FvNoReparse $launcher) -or -not (Test-FvNoReparse $config)) { throw 'RESCUE launcher or runtime configuration uses a junction or symbolic link.' }
    $runtime=Read-FvJson $config 65536
    $schema=[string](Get-FvProp $runtime 'schema')
    if ($schema -cnotin @('fieldviewer.rescue-runtime/1','fieldviewer.rescue-runtime/2')) { throw 'RESCUE runtime configuration schema is invalid.' }
    if ((Test-Path -LiteralPath $localConfig -PathType Leaf) -and $schema -cne 'fieldviewer.rescue-runtime/1') { throw 'Local RESCUE override must use runtime schema 1.' }
    foreach ($name in @('python','writer','validator','profile')) {
        $path=[string](Get-FvProp $runtime $name)
        if ($schema -ceq 'fieldviewer.rescue-runtime/2') { $path=Resolve-FvPortableRescueFile $config $path $name ($name -ceq 'python') }
        elseif (-not $path -or -not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf) -or -not (Test-FvNoReparse $path)) { throw "RESCUE runtime $name must be an existing absolute file without reparse points." }
    }
    if ($schema -ceq 'fieldviewer.rescue-runtime/2') {
        foreach ($name in @('enrichment_writer','stratigraphy_writer','interval_writer','receiver_interval_writer','receiver_global_writer','receiver_reader')) {
            $path=Get-FvProp $runtime $name
            $digestValue=Get-FvProp $runtime ($name+'_sha256')
            if (($null -eq $path) -ne ($null -eq $digestValue)) { throw "RESCUE runtime $name path and SHA-256 must be paired." }
            if ($null -ne $path) {
                Resolve-FvPortableRescueFile $config ([string]$path) $name | Out-Null
                if ([string]$digestValue -cnotmatch '^[0-9a-fA-F]{64}$') { throw "RESCUE runtime $name SHA-256 is invalid." }
            }
        }
    }
    foreach ($name in @('writer_sha256','validator_sha256','profile_sha256')) {
        if ([string](Get-FvProp $runtime $name) -cnotmatch '^[0-9a-fA-F]{64}$') { throw "RESCUE runtime $name must be a SHA-256 digest." }
    }
    return [pscustomobject]@{ Config=[IO.Path]::GetFullPath($config); Launcher=$launcher }
}
function Resolve-FvPortableRescueFile([string]$Config,[string]$Raw,[string]$Name,[bool]$AllowPendingManagedPython=$false) {
    if (-not $Raw -or $Raw -match '[:\x00]' -or $Raw -match '^[\\/]' ) { throw "Invalid package-relative RESCUE path: $Name" }
    $parts=$Raw -split '[\\/]'
    foreach ($part in $parts) {
        if (-not $part -or $part -eq '.' -or $part -eq '..' -or $part -match '[ .]$') { throw "Invalid package-relative RESCUE path: $Name" }
    }
    $root=[IO.Path]::GetFullPath((Split-Path -Parent $Config))
    $path=$root
    foreach ($part in $parts) { $path=Join-Path $path $part }
    $path=[IO.Path]::GetFullPath($path)
    if (-not (Test-FvWithin $path $root $false) -or -not (Test-FvNoReparse $path)) { throw "RESCUE runtime $Name must be a local package file without reparse points." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        # The managed runtime is expanded by the engine launcher on first use.
        # Only its exact canonical executable may be pending, and only when the
        # same engine carries the standalone marker and bundled offline cache.
        $marker=Join-Path $root 'STANDALONE.txt'
        $cache=Join-Path $root 'bootstrap/runtime.zip'
        if (-not ($AllowPendingManagedPython -and $Name -ceq 'python' -and $Raw -ceq 'runtime/python.exe' -and
            (Test-Path -LiteralPath $marker -PathType Leaf) -and (Test-FvNoReparse $marker) -and
            (Test-Path -LiteralPath $cache -PathType Leaf) -and (Test-FvNoReparse $cache))) {
            throw "RESCUE runtime $Name must be a local package file without reparse points."
        }
    }
    return $path
}
function New-FvSession([string]$PackageRoot,[string]$Project,[string]$Output,[bool]$ReportOnly,[bool]$Rescue=$false,[bool]$CrsOnly=$false) {
    if (($Rescue -and $CrsOnly) -or ($ReportOnly -and ($Rescue -or $CrsOnly))) { throw 'Choose one extraction mode.' }
    $paths=Assert-FvInputs $Project $Output
    if ($Rescue) { $rescueRuntime=Get-FvRescueRuntime $PackageRoot }
    elseif (-not $CrsOnly) { Assert-FvReportPathBudget $paths.Output }
    $engine=Get-FvEngineRoot $PackageRoot
    if ($CrsOnly) {
        $crsLauncher=Join-Path $engine 'scripts/launch_crs_extract.ps1'
        if (-not (Test-Path -LiteralPath $crsLauncher -PathType Leaf) -or -not (Test-FvNoReparse $crsLauncher)) { throw 'CRS-only launcher is unavailable in this package.' }
    }
    $id=[guid]::NewGuid().ToString('N'); $run=Join-Path $paths.Output ('FV_'+$id.Substring(0,16))
    # New-Item without Force rejects an existing run folder rather than reusing it.
    New-Item -ItemType Directory -Path $run -ErrorAction Stop | Out-Null
    $exe=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Windows PowerShell 5.1 executable is missing.' }
    if ($Rescue) {
        $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$rescueRuntime.Launcher,'-ProjectFile',$paths.Project,'-OutputRoot',$run,'-RuntimeConfig',$rescueRuntime.Config)
    } elseif ($CrsOnly) {
        $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$crsLauncher,'-ProjectFile',$paths.Project,'-OutputRoot',$run)
    } else {
        $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $engine 'scripts/launch_standalone_petrel.ps1'),'-ProjectFile',$paths.Project,'-OutputRoot',$run,'-CompanionMode','convert','-NoPause','-FullHash','-RunToCompletion')
        if ($ReportOnly) { $args+= '-ReportOnly' }
    }
    $session=[ordered]@{ schema='fieldviewer.desktop.session/1'; id=$id; package_root=[IO.Path]::GetFullPath($PackageRoot);
        project=$paths.Project; ptd=$paths.Ptd; selected_output=$paths.Output; output=$run; report_only=$ReportOnly;
        export_mode=$(if ($Rescue) { 'rescue' } elseif ($CrsOnly) { 'crs' } else { 'standard' }); rescue_runtime_config=$(if ($Rescue) { $rescueRuntime.Config } else { $null });
        executable=$exe; arguments=$args; argument_line=(Join-FvArgs $args); pid=$null; creation_utc=$null;
        command_line=$null; started_utc=[DateTime]::UtcNow.ToString('o'); ended_utc=$null; exit_code=$null;
        state='starting'; stdout=(Join-Path $run 'GUI_LAUNCHER_STDOUT.txt'); stderr=(Join-Path $run 'GUI_LAUNCHER_STDERR.txt');
        session_file=(Join-Path $run 'GUI_SESSION.json') }
    Write-FvJson $session.session_file $session
    $stateHome=Get-FvStateHome $PackageRoot
    Write-FvJson (Join-Path $stateHome 'current.json') ([ordered]@{session_file=$session.session_file; id=$id; package_root=$session.package_root})
    return $session
}
function Get-FvLatestSession([string]$PackageRoot) {
    $pointerPath=Join-Path (Get-FvStateHome $PackageRoot) 'current.json'
    if (-not (Test-Path -LiteralPath $pointerPath)) { return $null }
    $pointer=Read-FvJson $pointerPath 16384
    if (-not $pointer -or $pointer.package_root -ine [IO.Path]::GetFullPath($PackageRoot)) { throw 'The saved session pointer is invalid or belongs to another package. Inspect current.json before starting more work.' }
    $s=Read-FvJson ([string]$pointer.session_file) 65536
    if ($s -and $s.id -eq $pointer.id -and $s.session_file -eq $pointer.session_file -and (Test-FvWithin $s.session_file $s.output)) { return $s }
    throw 'The saved session is missing or does not match its pointer. Inspect the saved session before starting more work.'
}
function Test-FvProcessIdentity($Session) {
    if (-not $Session.pid -or -not $Session.creation_utc -or -not $Session.command_line) { return $false }
    try {
        $p=[Diagnostics.Process]::GetProcessById([int]$Session.pid)
        if ($p.StartTime.ToUniversalTime().Ticks -ne [DateTime]::Parse($Session.creation_utc).ToUniversalTime().Ticks) { return $false }
        $w=Get-WmiObject Win32_Process -Filter "ProcessId=$($Session.pid)" -ErrorAction Stop
        if (-not $w -or $w.ExecutablePath -ine $Session.executable -or $w.CommandLine -cne $Session.command_line) { return $false }
        return $true
    } catch { return $false }
}
function Get-FvSessionState($Session) {
    if ($Session.state -eq 'finished') { return 'finished' }
    if ($Session.state -eq 'launch_failed') { return 'launch_failed' }
    if ($Session.state -eq 'supervision_lost') { return 'stale_identity' }
    if (Test-FvProcessIdentity $Session) { return 'running' }
    if ($Session.state -eq 'starting' -and ([DateTime]::UtcNow - [DateTime]::Parse($Session.started_utc)).TotalSeconds -lt 15) { return 'starting' }
    return 'stale_identity'
}
function Get-FvOutcome($Session,$Receipt) {
    $state=Get-FvSessionState $Session
    if ($state -eq 'launch_failed') { return 'launcher_failed' }
    if ($state -ne 'finished') { return $state }
    $exit=[int](Get-FvProp $Session 'exit_code'); $status=Get-FvProp $Receipt 'status'
    if ((Get-FvProp $Session 'export_mode') -eq 'crs') {
        if ($exit -eq 130) { return 'cancelled' }
        if ($Receipt -and (Test-FvCrsReceipt $Session $Receipt)) {
            if ($exit -eq 0 -and $status -eq 'completed') { return 'completed' }
            if ($exit -eq 10 -and $status -eq 'completed_with_gaps') { return 'completed_with_gaps' }
        }
        return 'failed_or_missing_receipt'
    }
    if ((Get-FvProp $Session 'export_mode') -eq 'rescue') {
        if ($exit -eq 0 -and $status -eq 'experimental_export_verified' -and (Test-FvRescueReceipt $Session $Receipt)) { return 'experimental_export_verified' }
        if ($exit -eq 130) { return 'cancelled' }
        return 'failed_or_missing_receipt'
    }
    if ($exit -eq 0 -and $status -eq 'completed') { return 'completed' }
    if ($exit -eq 10 -and $status -eq 'completed_with_gaps') { return 'completed_with_gaps' }
    if ($exit -eq 130) { return 'cancelled' }
    return 'failed_or_missing_receipt'
}
function Get-FvReceipt($Session) {
    if (-not (Test-Path -LiteralPath $Session.output -PathType Container)) { return $null }
    if ((Get-FvProp $Session 'export_mode') -eq 'crs') {
        $path=Join-Path (Join-Path $Session.output 'CRS') 'CRS_EXPORT.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or -not (Test-FvNoReparse $path)) { return $null }
        try { $receipt=Read-FvJson $path 33554432; if (Test-FvCrsReceipt $Session $receipt) { return $receipt } } catch {}
        return $null
    }
    if ((Get-FvProp $Session 'export_mode') -eq 'rescue') {
        $root=Join-Path $Session.output 'RESCUE'
        $path=Join-Path $root 'RESCUE_EXPORT.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or -not (Test-FvNoReparse $path)) { return $null }
        try {
            $receipt=Read-FvJson $path 33554432
            if (Test-FvRescueReceipt $Session $receipt) { return $receipt }
        } catch {}
        return $null
    }
    $files=@(Get-ChildItem -LiteralPath $Session.output -Directory -Filter '*_data' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'RUN_RESULT.json' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($files.Count -ne 1) { return $null }
    if (-not (Test-FvWithin $files[0] $Session.output) -or -not (Test-FvNoReparse $files[0])) { return $null }
    $bound=Get-FvBoundRun $Session
    if (-not $bound -or [IO.Path]::GetDirectoryName($files[0]) -ine $bound.FullName) { return $null }
    try { return (Read-FvJson $files[0] 33554432) } catch { return $null }
}
function Get-FvReceiptProblem($Session) {
    if ((Get-FvProp $Session 'export_mode') -eq 'crs') { return 'CRS_EXPORT.json is missing, malformed, or does not bind to this run and its artifacts.' }
    if ((Get-FvProp $Session 'export_mode') -eq 'rescue') { return 'RESCUE_EXPORT.json is missing, malformed, or does not bind to this run and its artifacts.' }
    $files=@(Get-ChildItem -LiteralPath $Session.output -Directory -Filter '*_data' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'RUN_RESULT.json' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($files.Count -eq 0) { return 'No RUN_RESULT.json was written in this GUI run.' }
    if ($files.Count -ne 1) { return 'Multiple RUN_RESULT.json files exist in this GUI run; identity is ambiguous.' }
    if ((Get-Item -LiteralPath $files[0]).Length -gt 33554432) { return 'RUN_RESULT.json exceeds the GUI 32 MiB reader limit; open it from this run folder for manual review.' }
    return 'RUN_RESULT.json is malformed or its paths did not validate.'
}
function Test-FvCrsReceipt($Session,$Receipt) {
    if ((Get-FvProp $Receipt 'schema') -cne 'fieldviewer.crs-export/1') { return $false }
    $root=Join-Path $Session.output 'CRS'; $request=Get-FvProp $Receipt 'request'; $status=Get-FvProp $Receipt 'status'
    if ((Get-FvProp $request 'project_file') -cne $Session.project -or (Get-FvProp $request 'output_root') -cne $root) { return $false }
    if ($status -cnotin @('completed','completed_with_gaps','failed')) { return $false }
    foreach ($field in @('physical_crs_verified','physical_datum_verified','petrel_launched','data_store_read')) { if ((Get-FvProp $Receipt $field) -ne $false) { return $false } }
    if ($status -ne 'failed' -and (Get-FvProp $Receipt 'source_integrity_unchanged') -ne $true) { return $false }
    if ((Get-FvProp $Receipt 'export_directory') -cne $root -or -not (Test-FvNoReparse $root)) { return $false }
    foreach ($field in @('full_report','metadata_file','manifest_file')) {
        if (-not (Test-FvRescueArtifact (Get-FvProp $Receipt $field) $root 'file')) { return $false }
    }
    return $true
}
function Test-FvRescueReceipt($Session,$Receipt) {
    if ((Get-FvProp $Receipt 'schema') -cne 'fieldviewer.rescue-export/1') { return $false }
    $root=Join-Path $Session.output 'RESCUE'
    $request=Get-FvProp $Receipt 'request'
    if ((Get-FvProp $request 'project_file') -cne $Session.project -or (Get-FvProp $request 'output_root') -cne $root) { return $false }
    $status=Get-FvProp $Receipt 'status'
    if ($status -cnotin @('experimental_export_verified','failed','unsupported_profile')) { return $false }
    if ((Get-FvProp $Receipt 'complete_model_qualified') -ne $false -or (Get-FvProp $Receipt 'independent_receiver') -ne $false) { return $false }
    if (-not (Test-FvWithin $root $Session.output) -or -not (Test-FvNoReparse $root)) { return $false }
    foreach ($field in @('full_report','diagnostic_summary','process_log','structured_events','file_index')) {
        $path=Get-FvProp $Receipt $field
        if ($status -eq 'experimental_export_verified' -or $path) {
            if (-not (Test-FvRescueArtifact $path $root 'file')) { return $false }
        }
    }
    $qc=Get-FvProp $Receipt 'qc'; $qcPath=Get-FvProp $qc 'report_path'
    if ($status -eq 'experimental_export_verified' -or $qcPath) { if (-not (Test-FvRescueArtifact $qcPath $root 'file')) { return $false } }
    $export=Get-FvProp $Receipt 'export_directory'
    if ($status -eq 'experimental_export_verified' -or $export) {
        if (-not $export -or $export -cne (Join-Path $root 'model') -or -not (Test-FvRescueArtifact $export $root 'dir')) { return $false }
    }
    return $true
}
function Test-FvRescueArtifact($Path,[string]$Root,[string]$Kind) {
    if ($Path -isnot [string] -or -not [IO.Path]::IsPathRooted($Path)) { return $false }
    try {
        $full=[IO.Path]::GetFullPath($Path)
        if ($full -cne $Path -or -not (Test-FvWithin $full $Root $false) -or -not (Test-FvNoReparse $full)) { return $false }
        return (Test-Path -LiteralPath $full -PathType $(if ($Kind -eq 'dir') { 'Container' } else { 'Leaf' }))
    } catch { return $false }
}
function Get-FvBoundRun($Session) {
    $dirs=@(Get-ChildItem -LiteralPath $Session.output -Directory -Filter '*_data' -ErrorAction SilentlyContinue)
    $bound=@()
    foreach ($dir in $dirs) {
        if (-not (Test-FvNoReparse $dir.FullName)) { continue }
        try {
            $request=Read-FvJson (Join-Path $dir.FullName 'request.json') 131072
            if ($request -and (Get-FvProp $request 'project_file') -ieq $Session.project -and
                (Get-FvProp $request 'output_root') -ieq $Session.output -and
                [bool](Get-FvProp $request 'report_only') -eq [bool]$Session.report_only) { $bound+= $dir }
        } catch {}
    }
    if ($bound.Count -eq 1) { return $bound[0] }; return $null
}
function Resolve-FvArtifact($Session,[string]$Path,[string]$Kind='file') {
    if (-not $Path) { return $null }
    try {
        $full=[IO.Path]::GetFullPath($Path)
        if (-not (Test-FvWithin $full $Session.output) -or -not (Test-FvNoReparse $full)) { return $null }
        if ($Kind -eq 'dir' -and (Test-Path -LiteralPath $full -PathType Container)) { return $full }
        if ($Kind -eq 'file' -and (Test-Path -LiteralPath $full -PathType Leaf)) { return $full }
    } catch {}
    return $null
}
function Get-FvLinks($Session,$Receipt) {
    $links=[ordered]@{ reports=[ordered]@{}; folders=[ordered]@{} }
    $links.folders['Selected output']=$(if ((Test-Path -LiteralPath $Session.selected_output -PathType Container) -and (Test-FvNoReparse $Session.selected_output)) { [string]$Session.selected_output } else { $null })
    $links.folders['This run']=Resolve-FvArtifact $Session $Session.output 'dir'
    if ((Get-FvProp $Session 'export_mode') -eq 'crs') {
        if (-not $Receipt -or -not (Test-FvCrsReceipt $Session $Receipt)) { return $links }
        $links.folders['CRS and datum']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'export_directory') 'dir'
        $links.reports['CRS and datum report']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'full_report')
        $links.reports['CRS and datum (JSON)']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'metadata_file')
        $links.reports['CRS receipt (JSON)']=Resolve-FvArtifact $Session (Join-Path (Join-Path $Session.output 'CRS') 'CRS_EXPORT.json')
        $links.reports['CRS file manifest']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'manifest_file')
        return $links
    }
    if ((Get-FvProp $Session 'export_mode') -eq 'rescue') {
        if (-not $Receipt -or -not (Test-FvRescueReceipt $Session $Receipt)) { return $links }
        $links.folders['RESCUE model']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'export_directory') 'dir'
        $links.reports['RESCUE report']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'full_report')
        $links.reports['RESCUE QC (JSON)']=Resolve-FvArtifact $Session (Get-FvProp (Get-FvProp $Receipt 'qc') 'report_path')
        $links.reports['RESCUE receipt (JSON)']=Resolve-FvArtifact $Session (Join-Path (Join-Path $Session.output 'RESCUE') 'RESCUE_EXPORT.json')
        $links.reports['Diagnostics']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'diagnostic_summary')
        $links.reports['View log']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'process_log')
        $links.reports['File index (CSV)']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'file_index')
        return $links
    }
    if (-not $Receipt) {
        $bound=Get-FvBoundRun $Session
        if (-not $bound) { return $links }
        $prefix=$bound.Name.Substring(0,$bound.Name.Length-5)
        $links.folders['Evidence']=$bound.FullName
        $links.reports['Main report']=Resolve-FvArtifact $Session (Join-Path $Session.output ($prefix+'_REPORT.html'))
        $links.reports['View log']=Resolve-FvArtifact $Session (Join-Path $Session.output ($prefix+'_LOG.txt'))
        $links.reports['Diagnostics']=Resolve-FvArtifact $Session (Join-Path $Session.output ($prefix+'_DIAGNOSTICS.txt'))
        $exports=Resolve-FvArtifact $Session (Join-Path $Session.output ($prefix+'_EXPORTS')) 'dir'
        if ($exports) {
            $links.folders['All exports']=$exports
            $links.reports['File index (CSV)']=Resolve-FvArtifact $Session (Join-Path $exports 'FILE_INDEX.csv')
            foreach ($dir in Get-ChildItem -LiteralPath $exports -Directory -ErrorAction SilentlyContinue) { if (Test-FvNoReparse $dir.FullName) { $links.folders[$dir.Name]=$dir.FullName } }
        }
        return $links
    }
    $links.reports['Main report']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'full_report')
    $qc=Get-FvProp $Receipt 'qc'; if ($qc) { $links.reports['QC']=Resolve-FvArtifact $Session (Get-FvProp $qc 'report_path') }
    $links.reports['Diagnostics']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'diagnostic_summary')
    $links.reports['View log']=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'process_log')
    $access=Get-FvProp $Receipt 'result_access'; if ($access) { $links.reports['Results page']=Resolve-FvArtifact $Session (Get-FvProp $access 'entry_page') }
    $extraction=Get-FvProp $Receipt 'extraction'; $summary=Get-FvProp $extraction 'summary'
    $links.reports['Internal package report']=Resolve-FvArtifact $Session (Get-FvProp $summary 'dashboard')
    $interop=Get-FvProp $Receipt 'interoperability_contract'; $assess=Get-FvProp $interop 'assessments'
    foreach ($name in @('source','opendtect','paleoscan')) {
        $assessment=Get-FvProp $assess $name
        $links.reports[($name+' preparation assessment (JSON)')]=Resolve-FvArtifact $Session (Get-FvProp $assessment 'path')
    }
    $index=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'file_index')
    if ($index) { $links.reports['File index (CSV)']=$index }
    $receiptFiles=@(Get-ChildItem -LiteralPath $Session.output -Directory -Filter '*_data' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'RUN_RESULT.json' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($receiptFiles.Count -eq 1) { $links.folders['Evidence']=Resolve-FvArtifact $Session ([IO.Path]::GetDirectoryName($receiptFiles[0])) 'dir' }
    $links.folders['All exports']=$(if ($index) { Resolve-FvArtifact $Session ([IO.Path]::GetDirectoryName($index)) 'dir' } else { $null })
    if ($links.folders['All exports']) {
        foreach ($dir in Get-ChildItem -LiteralPath $links.folders['All exports'] -Directory -ErrorAction SilentlyContinue) {
            $name=$dir.Name
            if (Test-FvNoReparse $dir.FullName) { $links.folders[$name]=$dir.FullName }
        }
    }
    return $links
}
function Read-FvTail([string]$Path,[int]$Maximum=131072) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try { $count=[int][math]::Min($Maximum,$stream.Length); [void]$stream.Seek(-$count,[IO.SeekOrigin]::End); $buffer=New-Object byte[] $count; [void]$stream.Read($buffer,0,$count); return [Text.Encoding]::UTF8.GetString($buffer) }
    finally { $stream.Dispose() }
}
function Read-FvEventSlice([string]$Path,[long]$Offset,[int]$Maximum=131072) {
    $result=[ordered]@{ offset=$Offset; events=@(); skipped=$false }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $result }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try {
        if ($Offset -gt $stream.Length) { $Offset=0 }
        [void]$stream.Seek($Offset,[IO.SeekOrigin]::Begin)
        $count=[int][math]::Min($Maximum,$stream.Length-$Offset); $bytes=New-Object byte[] $count; $read=$stream.Read($bytes,0,$count)
        # Track byte positions, not decoded string lengths: a UTF-8 character
        # may straddle the bounded read. An oversized event must not stall polling.
        $last=-1
        for ($i=$read-1;$i -ge 0;$i--) { if ($bytes[$i] -eq 10) { $last=$i; break } }
        if ($last -lt 0) {
            if ($read -eq $Maximum) { $result.offset=$Offset+$read; $result.skipped=$true }
            else { $result.offset=$Offset }
            return $result
        }
        $result.offset=$Offset+$last+1
        $raw=[Text.Encoding]::UTF8.GetString($bytes,0,$last+1)
        foreach ($line in $raw.Split("`n")) { if ($line.Trim()) { try { $result.events+=($line | ConvertFrom-Json) } catch { $result.skipped=$true } } }
        return $result
    } finally { $stream.Dispose() }
}
function Get-FvProgress($Events,$Previous=$null) {
    $stage=$(if ($Previous) { [string]$Previous.Stage } else { 'Awaiting recorded stage' })
    $activity=$(if ($Previous) { [string]$Previous.Activity } else { 'No recorded activity yet' })
    $percent=$(if ($Previous) { $Previous.Percent } else { $null })
    $unit=$(if ($Previous) { [string]$Previous.Unit } else { '' })
    $time=$(if ($Previous) { [string]$Previous.LastActivityUtc } else { '' })
    foreach ($e in $Events) {
        $kind=Get-FvProp $e 'event'
        $meaningful=$kind -in @('stage','stage_started','hash_file_started','hash_file_progress','hash_file_completed','object_started','object_phase','native_object_outcome','operation_started','operation_finished')
        if ($kind -eq 'stage' -or $kind -eq 'stage_started') { $stage=[string]$(if (Get-FvProp $e 'label') { Get-FvProp $e 'label' } else { Get-FvProp $e 'stage' }); $percent=$null; $unit='' }
        if ($kind -match '^hash_file_(started|progress|completed)$') {
            $activity='Hashing '+[string](Get-FvProp $e 'source')
            if ($kind -eq 'hash_file_started') { $percent=$null; $unit='' }
            $total=Get-FvProp $e 'total'; $done=Get-FvProp $e 'done'
            if ($total -is [ValueType] -and $done -is [ValueType] -and $total -gt 0 -and $done -ge 0 -and $done -le $total) {
                $percent=[int][math]::Floor(100.0*$done/$total); $unit="Hash file bytes: $done / $total"
            }
        } elseif ($kind -in @('object_started','object_phase','native_object_outcome')) {
            $activity="Object $(Get-FvProp $e 'object_index') of $(Get-FvProp $e 'object_total'): $(Get-FvProp $e 'operation')"; $percent=$null; $unit='Objects are processing; recovery varies'
        } elseif ($kind -in @('operation_started','operation_finished')) {
            $activity="Operation: $(Get-FvProp $e 'operation') $(Get-FvProp $e 'status')"; $percent=$null; $unit=''
        } elseif ($kind -eq 'stage' -or $kind -eq 'stage_started') { $activity=$stage }
        if ($meaningful -and (Get-FvProp $e 'time_utc')) { $time=[string](Get-FvProp $e 'time_utc') }
    }
    [pscustomobject]@{ Stage=$stage; Activity=$activity; Percent=$percent; Unit=$unit; LastActivityUtc=$time }
}
function Format-FvDuration([double]$Seconds) {
    $s=[long][math]::Max(0,[math]::Floor($Seconds)); $days=[long][math]::Floor($s/86400); $hours=[long][math]::Floor(($s%86400)/3600)
    $body='{0:00}:{1:00}:{2:00}' -f $hours,([long][math]::Floor(($s%3600)/60)),($s%60)
    if ($days) { return "$days d $body" }; return $body
}
function Format-FvBytes([long]$Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N2} GiB' -f ($Bytes/1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N2} MiB' -f ($Bytes/1MB)) }
    return "$Bytes bytes"
}
function Start-FvSizeScan([string]$Root,[string]$ResultPath,[int]$MaxFiles=20000,[int]$MaxSeconds=8) {
    # Independent bounded scan; a new scan never waits on the UI thread.
    $script=Join-Path $PSScriptRoot 'FieldViewer.Desktop.SizeScan.ps1'
    $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$script,'-Root',$Root,'-ResultPath',$ResultPath,'-MaxFiles',"$MaxFiles",'-MaxSeconds',"$MaxSeconds")
    Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList (Join-FvArgs $args) -WindowStyle Hidden -PassThru
}
