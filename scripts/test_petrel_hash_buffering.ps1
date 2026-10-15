# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Extract only the hash function AST; never execute either extraction script.
param([string]$Report = '')
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('FV_hash_test_' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
$results = @()
foreach ($scriptName in @('export_petrel_native_project_zero_gui.ps1','validate_export_package.ps1')) {
    $tokens = $null; $parseErrors = $null
    $tree = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $scriptName),[ref]$tokens,[ref]$parseErrors)
    if ($parseErrors.Count) { throw "Parse failed: $scriptName" }
    $node = $tree.Find({param($a) $a -is [Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq 'Get-Sha256Hex'},$true)
    if ($null -eq $node) { throw 'Hash function missing' }
    . ([scriptblock]::Create($node.Extent.Text))
    foreach ($size in @(0,5,3145745)) {
        $bytes = New-Object byte[] $size
        if ($size) { [Random]::new(1701).NextBytes($bytes) }
        $path = Join-Path $testRoot ([guid]::NewGuid().ToString('N') + '.bin')
        [IO.File]::WriteAllBytes($path,$bytes)
        $reference = [Security.Cryptography.SHA256]::Create()
        try { $expected = [BitConverter]::ToString($reference.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() }
        finally { $reference.Dispose() }
        $actual = Get-Sha256Hex -Path $path
        if ($actual -cne $expected) { throw "Digest mismatch: $scriptName/$size" }
        # Exclusive reopen verifies closure; input content is then checked again.
        $exclusive = [IO.File]::Open($path,'Open','ReadWrite','None'); $exclusive.Dispose()
        if ((Get-Item -LiteralPath $path).Length -ne $size) { throw 'Input size changed' }
        $results += [pscustomobject]@{script=$scriptName;bytes=$size;digest_equal=$true;handle_closed=$true}
        Remove-Item -LiteralPath $path
    }
    $threw = $false
    try { Get-Sha256Hex -Path (Join-Path $testRoot 'missing.bin') | Out-Null }
    catch { $threw = $true }
    if (-not $threw) { throw 'Missing input was silently accepted' }
}
Remove-Item -LiteralPath $testRoot
Add-Type -TypeDefinition @'
using System;
using System.IO;
public sealed class FVCountingReads : MemoryStream {
 public int Calls;
 public FVCountingReads(byte[] bytes) : base(bytes, false) { }
 public override int Read(byte[] buffer, int offset, int count) { Calls++; return base.Read(buffer, offset, count); }
}
'@
$inputBytes = New-Object byte[] 4194321
$readCases = @()
foreach ($buffered in @($false,$true)) {
    $underlying = [FVCountingReads]::new($inputBytes)
    $hashStream = $underlying
    if ($buffered) { $hashStream = [IO.BufferedStream]::new($underlying,1048576) }
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($hasher.ComputeHash($hashStream)) }
    finally { $hashStream.Dispose(); $hasher.Dispose() }
    $readCases += [pscustomobject]@{buffered=$buffered;underlying_reads=$underlying.Calls;digest=$digest}
}
if ($readCases[0].digest -ne $readCases[1].digest -or $readCases[1].underlying_reads -ge $readCases[0].underlying_reads) { throw 'Read aggregation invariant failed' }
$receipt = [ordered]@{status='passed';checked_at=[DateTimeOffset]::Now.ToString('o');file_cases=$results;missing_files_rejected=2;read_aggregation=$readCases;scope='Synthetic local files and counting stream; no source project reads'}
$json = $receipt | ConvertTo-Json -Depth 6
if ($Report) { $json | Set-Content -LiteralPath $Report -Encoding UTF8 }
$json
