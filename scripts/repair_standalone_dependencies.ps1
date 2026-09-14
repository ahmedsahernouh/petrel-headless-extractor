# Install or repair only the release's managed runtime, from its verified cache.
# Runs in Windows PowerShell without Python, pip, network access or admin rights.
Set-StrictMode -Version Latest

function Repair-PetrelStandaloneDependencies {
    param([Parameter(Mandatory = $true)][string]$ToolkitRoot)
    $root = [System.IO.Path]::GetFullPath($ToolkitRoot).TrimEnd('\')
    $rootPrefix = $root + '\'
    function Get-ManagedPath([string]$Relative) {
        if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative -match '(^[\\/]|^[A-Za-z]:|(^|[\\/])\.\.([\\/]|$))') {
            throw "Unsafe managed dependency path: $Relative"
        }
        $path = [System.IO.Path]::GetFullPath((Join-Path $root $Relative))
        if (-not $path.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Managed path escapes toolkit" }
        $ancestor = $path
        while ($ancestor -and $ancestor.Length -ge $root.Length) {
            if ([System.IO.File]::Exists($ancestor) -or [System.IO.Directory]::Exists($ancestor)) {
                if (([System.IO.File]::GetAttributes($ancestor) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Dependency repair refuses a linked file or directory: $ancestor"
                }
            }
            $ancestor = [System.IO.Path]::GetDirectoryName($ancestor)
        }
        return $path
    }
    function Test-ManagedFile($Row, [string]$Path) {
        if (-not [System.IO.File]::Exists($Path)) { return $false }
        if ((New-Object System.IO.FileInfo($Path)).Length -ne [int64]$Row.size_bytes) { return $false }
        # Use the built-in .NET implementation even when the calling terminal
        # has replaced PSModulePath and Get-FileHash cannot be auto-loaded.
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $stream = [System.IO.File]::OpenRead($Path)
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '') -ieq $Row.sha256 }
        finally { $stream.Dispose(); $sha.Dispose() }
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { $lockId = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-', '').Substring(0, 24) }
    finally { $hasher.Dispose() }
    $mutex = New-Object System.Threading.Mutex($false, ('Local\PetrelDependencies_' + $lockId))
    $ownsMutex = $false
    $report = [ordered]@{ status = 'checking'; checked_at_utc = (Get-Date).ToUniversalTime().ToString('o'); network_used = $false; system_python_modified = $false; installed_files = @() }
    $logPath = $null
    try {
        try { $ownsMutex = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw 'Another launcher is checking or installing dependencies in this folder. Retry after it finishes.' }
        Write-Host 'Checking managed Python and dependencies...'
        $manifestPath = Get-ManagedPath '00_manifest/toolkit_files.json'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $runtimeRows = @{}
        $missing = New-Object System.Collections.Generic.List[object]
        $known = @{}
        foreach ($row in $manifest.files) {
            $relative = ([string]$row.path).Replace('\', '/')
            if ($known.ContainsKey($relative)) { throw "Duplicate managed path: $relative" }
            $known[$relative] = $true
            if ($row.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or [int64]$row.size_bytes -lt 0) { throw "Invalid manifest entry: $relative" }
            $path = Get-ManagedPath $relative
            if ($relative.StartsWith('runtime/', [StringComparison]::Ordinal)) {
                $runtimeRows[$relative] = $row
                if (-not (Test-ManagedFile $row $path)) { $missing.Add($row) }
            } elseif (-not (Test-ManagedFile $row $path)) {
                throw "Application integrity failed: $relative. Re-extract the release; only runtime dependencies are auto-repaired."
            }
        }
        if ($runtimeRows.Count -eq 0) { throw 'The release has no managed runtime inventory.' }
        $logPath = Get-ManagedPath 'build/dependencies/last_check.json'
        [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($logPath)) | Out-Null
        $report['managed_runtime_files'] = $runtimeRows.Count
        $report['version'] = $manifest.version
        if ($missing.Count -gt 0) {
            $pythonPath = Get-ManagedPath 'runtime/python.exe'
            $runningHere = @(Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -ieq $pythonPath })
            if ($runningHere.Count -gt 0) { throw 'An extractor is using this runtime. Finish that run before repairing dependencies in the same folder.' }
            $cacheInfo = Get-Content -LiteralPath (Get-ManagedPath '00_manifest/dependency_repair.json') -Raw | ConvertFrom-Json
            $cachePath = Get-ManagedPath $cacheInfo.archive.path
            if (-not (Test-ManagedFile $cacheInfo.archive $cachePath)) { throw 'The offline dependency cache is missing or damaged. Re-extract the complete release ZIP; no unverified packages were installed.' }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipFile]::OpenRead($cachePath)
            try {
                $entries = @{}
                foreach ($entry in $archive.Entries) {
                    $key = $entry.FullName.Replace('\', '/')
                    if ($entries.ContainsKey($key) -or -not $runtimeRows.ContainsKey($key)) { throw "Unexpected runtime cache entry: $key" }
                    if ($entry.Length -ne [int64]$runtimeRows[$key].size_bytes) { throw "Runtime cache entry size mismatch: $key" }
                    $entries[$key] = $entry
                }
                if ($entries.Count -ne $runtimeRows.Count) { throw 'The runtime cache does not match the release inventory.' }
                $requiredBytes = ($missing | Measure-Object -Property size_bytes -Sum).Sum
                $drive = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($root))
                if ($drive.AvailableFreeSpace -lt ([int64]$requiredBytes + 16777216)) { throw 'Not enough space to install the bundled dependencies.' }
                Write-Host ("Installing or repairing {0} runtime files from the bundled cache..." -f $missing.Count)
                $installed = New-Object System.Collections.Generic.List[string]
                foreach ($row in $missing) {
                    $key = ([string]$row.path).Replace('\', '/')
                    $target = Get-ManagedPath $key
                    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
                    $temporaryRelative = 'runtime/.repair-' + [guid]::NewGuid().ToString('N') + '.tmp'
                    $temporary = Get-ManagedPath $temporaryRelative
                    try {
                        $inputStream = $entries[$key].Open()
                        try {
                            $outputStream = [System.IO.File]::Open($temporary, [System.IO.FileMode]::CreateNew)
                            try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
                        } finally { $inputStream.Dispose() }
                        if (-not (Test-ManagedFile $row $temporary)) { throw "Dependency hash verification failed: $key" }
                        Move-Item -LiteralPath $temporary -Destination $target -Force
                        $installed.Add($key)
                        if (($installed.Count % 500) -eq 0) { Write-Host ("Installed {0}/{1} files..." -f $installed.Count, $missing.Count) }
                    } finally {
                        # The temporary path was checked inside this exact toolkit.
                        if ([System.IO.File]::Exists($temporary)) { Remove-Item -LiteralPath $temporary }
                    }
                }
                $report['installed_files'] = $installed.ToArray()
                $report['status'] = 'repaired'
            } finally { $archive.Dispose() }
        } else { $report['status'] = 'ready' }
        foreach ($key in $runtimeRows.Keys) {
            if (-not (Test-ManagedFile $runtimeRows[$key] (Get-ManagedPath $key))) { throw "Dependency remains invalid after repair: $key" }
        }
        $report['installed_file_count'] = @($report['installed_files']).Count
        [System.IO.File]::WriteAllText($logPath, ($report | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host 'Managed runtime files ready. Checking Python imports next...'
        return [pscustomobject]$report
    } catch {
        $report['status'] = 'failed'
        $report['error'] = $_.Exception.Message
        if ($logPath) { [System.IO.File]::WriteAllText($logPath, ($report | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false))) }
        throw
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
