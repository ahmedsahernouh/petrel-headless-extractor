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
        try {
            $manifest = Get-Content -LiteralPath (Join-Path $ToolkitRoot '00_manifest\toolkit_files.json') -Raw | ConvertFrom-Json
            $entry = @($manifest.files | Where-Object { $_.path -eq 'scripts/geoviewer_support.py' })
            $collectorVerified = $entry.Count -eq 1 -and (Test-Path -LiteralPath $builder) -and ((Get-FileHash -LiteralPath $builder -Algorithm SHA256).Hash -eq $entry[0].sha256)
        } catch { $collectorVerified = $false }
        if ($collectorVerified -and (Test-Path -LiteralPath $python)) {
            & $python -B $builder --session $Session
            if ($LASTEXITCODE -eq 0) { return }
        }
        # Last-resort startup ZIP requires only Windows/.NET. It is explicitly
        # labelled unredacted; normal Python bundles apply identifier replacement.
        $fallback = Join-Path $Session 'startup_support'
        New-Item -ItemType Directory -Path $fallback -Force | Out-Null
        foreach ($name in @('BOOTSTRAP_LOG.txt','session.json','launcher_result.json')) {
            $source = Join-Path $Session $name
            if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $fallback }
        }
        'STARTUP-ONLY DIAGNOSTICS. Python was unavailable. These logs are UNREDACTED; review paths/names before sharing. No source files or exports included. No upload performed.' | Set-Content -LiteralPath (Join-Path $fallback 'README.txt') -Encoding UTF8
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
