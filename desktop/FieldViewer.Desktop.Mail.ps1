# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
. (Join-Path $PSScriptRoot 'FieldViewer.Desktop.Core.ps1')
function Get-FvRedactedLogMember($Session,[string]$LogPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zips=@(Get-ChildItem -LiteralPath $Session.output -File -Filter '*_SUPPORT.zip' -ErrorAction SilentlyContinue | Where-Object { Test-FvNoReparse $_.FullName })
    if ($zips.Count -ne 1) { return $null }
    $zip=[IO.Compression.ZipFile]::OpenRead($zips[0].FullName)
    try {
        $contents=$zip.GetEntry('CONTENTS.json'); if (-not $contents -or $contents.Length -gt 4194304) { return $null }
        $r=New-Object IO.StreamReader($contents.Open(),[Text.Encoding]::UTF8)
        try { $manifest=$r.ReadToEnd() | ConvertFrom-Json } finally { $r.Dispose() }
        $eligible=@(Get-FvProp $manifest 'files' | Where-Object {
            $_.role -match '_LOG\.txt$' -and $_.member -match '^diagnostics/[0-9]{3}\.txt$' -and
            $zip.GetEntry([string]$_.member) -and $_.original_bytes -eq (Get-Item -LiteralPath $LogPath).Length
        })
        if ($eligible.Count -ne 1) { return $null }
        $unavailable=@(Get-FvProp $manifest 'unavailable' | Where-Object { $_.member -eq $eligible[0].member -or $_.role -eq $eligible[0].role })
        if ($unavailable.Count) { return $null }
        return [pscustomobject]@{ Zip=$zips[0].FullName; Member=[string]$eligible[0].member; Size=[long]$zip.GetEntry([string]$eligible[0].member).Length }
    } finally { $zip.Dispose() }
}
function New-FvClosedLogCopy($Session,$Receipt,[bool]$AllowUnredacted=$false) {
    $log=Resolve-FvArtifact $Session (Get-FvProp $Receipt 'process_log')
    if (-not $log) { throw 'This run has no available process log.' }
    $mapped=Get-FvRedactedLogMember $Session $log
    $share=Join-Path $Session.output 'GUI_SHARING'; [IO.Directory]::CreateDirectory($share) | Out-Null
    $stamp=[DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss')
    if ($mapped) {
        $destination=Join-Path $share ("FV_$($Session.id)_redacted_$stamp.txt")
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip=[IO.Compression.ZipFile]::OpenRead($mapped.Zip)
        try { $input=$zip.GetEntry($mapped.Member).Open(); $output=[IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try { $input.CopyTo($output) } finally { $input.Dispose(); $output.Dispose() }
        } finally { $zip.Dispose() }
        return [pscustomobject]@{ Path=$destination; Privacy='Best-effort redacted'; Complete=($Session.state -eq 'finished'); CapturedUtc=[DateTime]::UtcNow.ToString('o') }
    }
    if (-not $AllowUnredacted) { return [pscustomobject]@{ Path=$null; Privacy='UNREDACTED approval required'; Complete=$false; CapturedUtc=[DateTime]::UtcNow.ToString('o') } }
    $destination=Join-Path $share ("FV_$($Session.id)_UNREDACTED_$stamp.txt")
    $input=[IO.File]::Open($log,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    $output=[IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $length=$input.Length; $buffer=New-Object byte[] 65536; $copied=[long]0
        while ($copied -lt $length) { $n=$input.Read($buffer,0,[int][math]::Min($buffer.Length,$length-$copied)); if ($n -le 0) { break }; $output.Write($buffer,0,$n); $copied+=$n }
    } finally { $input.Dispose(); $output.Dispose() }
    return [pscustomobject]@{ Path=$destination; Privacy='UNREDACTED'; Complete=($Session.state -eq 'finished'); CapturedUtc=[DateTime]::UtcNow.ToString('o') }
}
function Invoke-FvMailDraft([string]$To,[string]$Subject,[string]$Body,[string]$Attachment) {
    # Simple MAPI shows the user's composition dialog. It never calls SMTP.
    if (-not ('FvMapi' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FvMapi {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct Recipient { public uint reserved, recipClass; public IntPtr name, address; public uint entrySize; public IntPtr entryId; }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct FileDesc { public uint reserved, flags, position; public IntPtr path, name, fileType; }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct Message { public uint reserved; public IntPtr subject, noteText, messageType, dateReceived, conversationId; public uint flags; public IntPtr originator; public uint recipCount; public IntPtr recipients; public uint fileCount; public IntPtr files; }
    [DllImport("MAPI32.DLL", CharSet=CharSet.Unicode, EntryPoint="MAPISendMailW")]
    static extern uint Send(IntPtr session, IntPtr uiParam, ref Message message, uint flags, uint reserved);
    public static uint Compose(string to, string subject, string body, string attachment) {
        IntPtr rp=IntPtr.Zero, fp=IntPtr.Zero, toName=IntPtr.Zero, toAddress=IntPtr.Zero, sub=IntPtr.Zero, note=IntPtr.Zero, path=IntPtr.Zero, name=IntPtr.Zero;
        try {
            toName=Marshal.StringToHGlobalUni(to); toAddress=Marshal.StringToHGlobalUni("SMTP:"+to);
            Recipient r=new Recipient {recipClass=1,name=toName,address=toAddress}; rp=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Recipient))); Marshal.StructureToPtr(r,rp,false);
            sub=Marshal.StringToHGlobalUni(subject); note=Marshal.StringToHGlobalUni(body);
            Message m=new Message {subject=sub,noteText=note,recipCount=1,recipients=rp};
            if (!String.IsNullOrEmpty(attachment)) {
                path=Marshal.StringToHGlobalUni(attachment); name=Marshal.StringToHGlobalUni(System.IO.Path.GetFileName(attachment));
                FileDesc f=new FileDesc {position=UInt32.MaxValue,path=path,name=name}; fp=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(FileDesc))); Marshal.StructureToPtr(f,fp,false);
                m.fileCount=1; m.files=fp;
            }
            return Send(IntPtr.Zero,IntPtr.Zero,ref m,0x00000009,0);
        } finally {
            if (fp!=IntPtr.Zero) Marshal.FreeHGlobal(fp); if (rp!=IntPtr.Zero) Marshal.FreeHGlobal(rp);
            foreach (IntPtr p in new [] {toName,toAddress,sub,note,path,name}) if (p!=IntPtr.Zero) Marshal.FreeHGlobal(p);
        }
    }
}
'@
    }
    try {
        $code=[FvMapi]::Compose($To,$Subject,$Body,$Attachment)
        if ($code -eq 0) { return 'Mail client accepted the user-reviewed composition; delivery is unverified.' }
        if ($code -eq 1) { return 'Composition cancelled by user; prepared attachment retained.' }
    } catch { $code='unavailable' }
    $uri='mailto:'+[Uri]::EscapeDataString($To)+'?subject='+[Uri]::EscapeDataString($Subject)+'&body='+[Uri]::EscapeDataString($Body)
    try { Start-Process $uri | Out-Null; if ($Attachment) { Start-Process explorer.exe -ArgumentList (('/select,'+(ConvertTo-FvArg $Attachment))) | Out-Null }
        return "Mail draft opened; attach the prepared file manually. MAPI result: $code" }
    catch { return "No compatible mail client opened. Use your mail app manually; prepared attachment retained at $Attachment. MAPI result: $code" }
}
