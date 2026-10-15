# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Website: https://saherlabs.dev/
# Bootstrap diagnostics must also work when managed Python cannot start.
function Start-GeoViewerSupport {
    param([string]$ToolkitRoot, [hashtable]$Options)
    $session = Join-Path ([System.IO.Path]::GetTempPath()) ('GeoViewer_support_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $session -ErrorAction Stop | Out-Null
    $env:GEOVIEWER_SUPPORT_SESSION = $session
    $env:GEOVIEWER_BOOTSTRAP_LOG = Join-Path $session 'BOOTSTRAP_LOG.txt'
    $metadata = @{ schema='geoviewer.bootstrap/1'; started_utc=[DateTime]::UtcNow.ToString('o');
        powershell=$PSVersionTable.PSVersion.ToString(); os=[Environment]::OSVersion.VersionString;
        process_bits=([IntPtr]::Size*8); toolkit=$ToolkitRoot; options=$Options }
    $metadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $session 'session.json') -Encoding UTF8
    try { Start-Transcript -Path $env:GEOVIEWER_BOOTSTRAP_LOG -ErrorAction Stop | Out-Null }
    catch { $_.Exception.ToString() | Set-Content -LiteralPath $env:GEOVIEWER_BOOTSTRAP_LOG -Encoding UTF8 }
    return $session
}

function Stop-GeoViewerSupport {
    param([string]$ToolkitRoot, [string]$Session, [int]$ExitCode, $Failure)
    # Diagnostics may fail independently; never replace the extraction exit code.
    try {
        $result = @{ finished_utc=[DateTime]::UtcNow.ToString('o'); exit_code=$ExitCode }
        if ($null -ne $Failure) {
            $result.error = @{ type=$Failure.Exception.GetType().FullName; message=$Failure.Exception.Message;
                exception=$Failure.Exception.ToString(); stack=$Failure.ScriptStackTrace;
                position=$Failure.InvocationInfo.PositionMessage; error_id=$Failure.FullyQualifiedErrorId }
        }
        $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Session 'launcher_result.json') -Encoding UTF8
        try { Stop-Transcript -ErrorAction Stop | Out-Null } catch {}
        $python = Join-Path $ToolkitRoot 'runtime\python.exe'
        $builder = Join-Path $ToolkitRoot 'scripts\geoviewer_support.py'
        $collectorVerified = $false
        $collector = @{ status='verification_started'; phase='verify_collector'; verified=$false; error=$null; exit_code=$null }
        $collectorReceipt = Join-Path $Session 'support_collector.json'
        try {
            $manifest = Get-Content -LiteralPath (Join-Path $ToolkitRoot '00_manifest\toolkit_files.json') -Raw | ConvertFrom-Json
            $entry = @($manifest.files | Where-Object { $_.path -eq 'scripts/geoviewer_support.py' })
            if ($entry.Count -ne 1) { throw 'Support collector requires exactly one manifest entry.' }
            # Windows PowerShell launched by another runtime can inherit a
            # PSModulePath where Get-FileHash is unavailable. Use built-in .NET.
            $hasher = [System.Security.Cryptography.SHA256]::Create()
            $stream = $null
            try {
                $stream = [System.IO.File]::OpenRead($builder)
                $actualHash = ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '')
            } finally {
                if ($null -ne $stream) { $stream.Dispose() }
                $hasher.Dispose()
            }
            if ($actualHash -ine $entry[0].sha256) { throw 'Support collector hash does not match the toolkit manifest.' }
            $collectorVerified = $true
            $collector.verified = $true
            $collector.status = 'verified'
        } catch {
            $collector.status = 'verification_failed'
            $collector.error = @{ type=$_.Exception.GetType().FullName; message=$_.Exception.Message; stack=$_.ScriptStackTrace }
        }
        $collector | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $collectorReceipt -Encoding UTF8
        if ($collectorVerified -and (Test-Path -LiteralPath $python)) {
            try {
                $collector.phase = 'collect_support'
                $collector.status = 'started'
                $collector | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $collectorReceipt -Encoding UTF8
                $collectorOut = Join-Path $Session 'support_collector_stdout.txt'
                $collectorErr = Join-Path $Session 'support_collector_stderr.txt'
                $arguments = @('-B', ('"' + $builder + '"'), '--session', ('"' + $Session + '"'))
                $process = Start-Process -FilePath $python -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $collectorOut -RedirectStandardError $collectorErr
                $collector.exit_code = $process.ExitCode
                if (Test-Path -LiteralPath $collectorOut) { Get-Content -LiteralPath $collectorOut | Write-Output }
                if ($process.ExitCode -eq 0) {
                    $collector.status = 'completed'
                    $collector | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $collectorReceipt -Encoding UTF8
                    return
                }
                $collector.status = 'collector_failed'
                $collector.error = @{ message=('Support collector exited with code ' + $process.ExitCode); stderr=$collectorErr }
            } catch {
                $collector.status = 'collector_exception'
                $collector.error = @{ type=$_.Exception.GetType().FullName; message=$_.Exception.Message; stack=$_.ScriptStackTrace }
            }
        } elseif ($collectorVerified) {
            $collector.status = 'python_missing'
            $collector.error = @{ message='Managed Python executable is missing.' }
        }
        $collector | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $collectorReceipt -Encoding UTF8
        Write-Output ('Full support collection unavailable: ' + $collector.status + '. Details: ' + $collectorReceipt)
        # Last-resort startup ZIP requires only Windows/.NET. It is explicitly
        # labelled unredacted; normal Python bundles apply identifier replacement.
        $fallback = Join-Path $Session 'startup_support'
        New-Item -ItemType Directory -Path $fallback -Force | Out-Null
        foreach ($name in @('BOOTSTRAP_LOG.txt','session.json','launcher_result.json','support_collector.json','support_collector_stdout.txt','support_collector_stderr.txt')) {
            $source = Join-Path $Session $name
            if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $fallback }
        }
        'STARTUP-ONLY DIAGNOSTICS. Full support collection was unavailable or failed; see support_collector.json and retained collector streams for the cause. These logs are UNREDACTED; review paths/names before sharing. No source files or exports included. No upload performed.' | Set-Content -LiteralPath (Join-Path $fallback 'README.txt') -Encoding UTF8
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = Join-Path $Session 'STARTUP_SUPPORT.zip'
        [System.IO.Compression.ZipFile]::CreateFromDirectory($fallback,$zip)
        Write-Output ('SUPPORT ZIP (startup only, unredacted; review before sharing): ' + $zip)
    } catch { Write-Output ('Support ZIP could not be completed. Retained startup logs: ' + $Session + '. ' + $_.Exception.Message) }
    finally {
        Remove-Item Env:\GEOVIEWER_SUPPORT_SESSION -ErrorAction SilentlyContinue
        Remove-Item Env:\GEOVIEWER_BOOTSTRAP_LOG -ErrorAction SilentlyContinue
    }
}
