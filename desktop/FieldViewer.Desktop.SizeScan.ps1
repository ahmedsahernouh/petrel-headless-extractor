# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param([Parameter(Mandatory)][string]$Root,[Parameter(Mandatory)][string]$ResultPath,
      [string]$ExactFile='', [int]$MaxFiles=20000,[int]$MaxSeconds=8)
. (Join-Path $PSScriptRoot 'FieldViewer.Desktop.Core.ps1')
$timer=[Diagnostics.Stopwatch]::StartNew(); $bytes=[long]0; $count=0; $partial=$false; $errors=0
$queue=New-Object 'System.Collections.Generic.Queue[string]'
try {
    if ($ExactFile) { $bytes += (Get-Item -LiteralPath $ExactFile -ErrorAction Stop).Length; $count++ }
    $queue.Enqueue([IO.Path]::GetFullPath($Root))
    while ($queue.Count -gt 0) {
        if ($timer.Elapsed.TotalSeconds -ge $MaxSeconds -or $count -ge $MaxFiles) { $partial=$true; break }
        $dir=$queue.Dequeue()
        try {
            foreach ($item in Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop) {
                if ($timer.Elapsed.TotalSeconds -ge $MaxSeconds -or $count -ge $MaxFiles) { $partial=$true; break }
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $partial=$true; continue }
                if ($item.PSIsContainer) { $queue.Enqueue($item.FullName) }
                else { $bytes += [long]$item.Length; $count++ }
            }
        } catch { $errors++; $partial=$true }
    }
} catch { $errors++; $partial=$true }
Write-FvJson $ResultPath ([ordered]@{schema='fieldviewer.size/1'; root=$Root; exact_file=$ExactFile; logical_path_sum_bytes=$bytes;
    files=$count; partial=$partial; errors=$errors; aliases_included=$true; measured_utc=[DateTime]::UtcNow.ToString('o')})
