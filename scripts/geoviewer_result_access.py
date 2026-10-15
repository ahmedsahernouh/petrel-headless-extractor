# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Portable per-run report links and optional, verified Windows desktop shortcuts."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote


_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$unicodeLinkSource = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class ShellLinkObject { }

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("000214F9-0000-0000-C000-000000000046")]
interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                 int length, IntPtr findData, uint flags);
    void GetIDList(out IntPtr idList);
    void SetIDList(IntPtr idList);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int length);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int length);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int length);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetHotkey(out short hotkey);
    void SetHotkey(short hotkey);
    void GetShowCmd(out int command);
    void SetShowCmd(int command);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                         int length, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
    void Resolve(IntPtr window, uint flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("0000010b-0000-0000-C000-000000000046")]
interface IPersistFile {
    void GetClassID(out Guid classId);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string fileName);
}

public static class UnicodeShellLink {
    public static void Save(string link, string target, string marker) {
        var item = new ShellLinkObject();
        var shortcut = (IShellLinkW)item;
        shortcut.SetPath(target);
        shortcut.SetArguments("");
        shortcut.SetDescription(marker);
        shortcut.SetWorkingDirectory(System.IO.Path.GetDirectoryName(target));
        ((IPersistFile)item).Save(link, true);
        Marshal.ReleaseComObject(item);
    }
    public static string[] Read(string link) {
        var item = new ShellLinkObject();
        ((IPersistFile)item).Load(link, 0);
        var shortcut = (IShellLinkW)item;
        var target = new StringBuilder(32768);
        var arguments = new StringBuilder(32768);
        var description = new StringBuilder(1024);
        shortcut.GetPath(target, target.Capacity, IntPtr.Zero, 4);
        shortcut.GetArguments(arguments, arguments.Capacity);
        shortcut.GetDescription(description, description.Capacity);
        Marshal.ReleaseComObject(item);
        return new string[] { target.ToString(), arguments.ToString(), description.ToString() };
    }
}
'@
$request = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
if ($request.action -eq 'desktop') {
    @{ desktop = [Environment]::GetFolderPath('DesktopDirectory') } |
        ConvertTo-Json -Compress
    exit 0
}
if ($request.action -eq 'inspect') {
    Add-Type -TypeDefinition $unicodeLinkSource -ErrorAction Stop
    $values = [UnicodeShellLink]::Read([string]$request.link)
    @{ target = $values[0]; arguments = $values[1]; description = $values[2] } |
        ConvertTo-Json -Compress
    exit 0
}
if ($request.action -ne 'shortcuts') { throw 'Unknown result-access action' }
$shell = New-Object -ComObject WScript.Shell
$made = @()
$warnings = @()
$unicodeLinkLoaded = $false
foreach ($spec in $request.items) {
    $link = [string]$spec.link
    $target = [string]$spec.target
    $marker = [string]$spec.marker
    $targetType = [string]$spec.target_type
    $unicodeTarget = $target -match '[^\u0000-\u007f]'
    $existing = $null
    try {
        if ($unicodeTarget -and -not $unicodeLinkLoaded) {
            Add-Type -TypeDefinition $unicodeLinkSource -ErrorAction Stop
            $unicodeLinkLoaded = $true
        }
        $existing = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            if ($existing.PSIsContainer -or
                (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
                $warnings += "Shortcut collision: $link"
                continue
            }
            if ($unicodeTarget) {
                $oldValues = [UnicodeShellLink]::Read($link)
                $oldTarget = $oldValues[0]
                $oldArguments = $oldValues[1]
                $oldDescription = $oldValues[2]
            } else {
                $old = $shell.CreateShortcut($link)
                $oldTarget = $old.TargetPath
                $oldArguments = $old.Arguments
                $oldDescription = $old.Description
            }
            if ($oldDescription -cne $marker -or
                $oldTarget -ine $target -or
                -not [string]::IsNullOrEmpty($oldArguments)) {
                $warnings += "Shortcut collision: $link"
                continue
            }
        }
        if ($unicodeTarget) {
            [UnicodeShellLink]::Save($link, $target, $marker)
            $checkValues = [UnicodeShellLink]::Read($link)
            $checkTarget = $checkValues[0]
            $checkArguments = $checkValues[1]
            $checkDescription = $checkValues[2]
        } else {
            $shortcut = $shell.CreateShortcut($link)
            $shortcut.TargetPath = $target
            $shortcut.Arguments = ''
            $shortcut.Description = $marker
            $shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($target)
            $shortcut.Save()
            $check = $shell.CreateShortcut($link)
            $checkTarget = $check.TargetPath
            $checkArguments = $check.Arguments
            $checkDescription = $check.Description
        }
        if ($checkTarget -ine $target -or
            -not [string]::IsNullOrEmpty($checkArguments) -or
            $checkDescription -cne $marker) {
            $warnings += "Shortcut readback failed: $link"
            continue
        }
        if (($targetType -eq 'file' -and
             -not (Test-Path -LiteralPath $target -PathType Leaf)) -or
            ($targetType -eq 'directory' -and
             -not (Test-Path -LiteralPath $target -PathType Container)) -or
            ($targetType -ne 'file' -and $targetType -ne 'directory')) {
            $warnings += "Shortcut target missing after readback: $link"
            continue
        }
        $made += $link
    } catch {
        if ($null -ne $existing) {
            $warnings += "Shortcut collision or unreadable existing link: $link ($($_.Exception.Message))"
        } else {
            $warnings += "Shortcut unavailable: $link ($($_.Exception.Message))"
        }
    }
}
@{ shortcuts = @($made); warnings = @($warnings) } |
    ConvertTo-Json -Compress -Depth 4
"""


def _run_powershell(request: dict) -> dict:
    """Only static PowerShell code is executed; paths and labels go through JSON stdin."""
    encoded = base64.b64encode(_POWERSHELL.encode('utf-16-le')).decode('ascii')
    completed = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        input=json.dumps(request, ensure_ascii=True), text=True, encoding='utf-8',
        capture_output=True, timeout=15, check=False,
    )
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or
                           f'PowerShell exited {completed.returncode}')
    try:
        return json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError('PowerShell returned invalid result-access data') from exc


def _slug(label: str) -> str:
    # Keep a readable name while removing Windows filename metacharacters.
    value = re.sub(r'[^\w\- ]+', ' ', str(label), flags=re.UNICODE)
    value = re.sub(r'\s+', ' ', value).strip(' ._-')[:30].rstrip(' ._-')
    return value or 'Run'


def _page_content(report: Path, exports: Path | None, label: str,
                  status: str, marker: str) -> str:
    report_href = html.escape(quote(report.name, safe=''), quote=True)
    links = [f'<li><a href="{report_href}">Open report</a></li>']
    if exports is not None:
        files_href = html.escape(quote(exports.name, safe='') + '/', quote=True)
        links.append(f'<li><a href="{files_href}">Open exported files</a></li>')
    return ('<!doctype html>\n'
            f'<!-- {marker} -->\n'
            '<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>FieldViewer results</title></head><body>'
            f'<main><h1>FieldViewer results: {html.escape(str(label))}</h1>'
            f'<p>Run status: {html.escape(str(status))}</p>'
            f'<ul>{"".join(links)}</ul></main></body></html>\n')


def _owned_page(path: Path, marker: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open('r', encoding='utf-8') as stream:
            head = stream.readline() + stream.readline()
    except (OSError, UnicodeError):
        return False
    return f'<!-- {marker} -->' in head


def _publish_page(path: Path, content: str, marker: str) -> bool:
    """Create without replacement, or refresh a page observed to be owned."""
    existed = path.exists() or path.is_symlink()
    if existed and not _owned_page(path, marker):
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.open_results_',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if not existed:
            # Windows rename refuses an existing destination and also works on
            # output volumes without hard-link support. POSIX rename replaces,
            # so use non-overwriting hard-link creation there instead.
            try:
                if os.name == 'nt':
                    os.rename(temporary, path)
                    temporary = None
                else:
                    os.link(temporary, path)
            except FileExistsError:
                return False
        else:
            # Recheck immediately before replacing; this is not a cross-process
            # compare-and-swap guarantee for an existing owned page.
            if not _owned_page(path, marker):
                return False
            os.replace(temporary, path)
            temporary = None
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_result_access(report_path, exports_path=None, *, label='',
                          status='in_progress', desktop=None) -> dict:
    """Publish a relocatable results page and optional user-Desktop shortcuts.

    The report must already exist. Supplied exports must be a real sibling
    directory. ``desktop=False`` disables shortcuts; a path overrides the Desktop
    location for controlled tests. Desktop failures are nonfatal warnings.
    """
    report_input = Path(report_path)
    if report_input.is_symlink() or not report_input.is_file():
        raise FileNotFoundError(f'Report is missing or is not a regular file: {report_input}')
    report = report_input.resolve(strict=True)
    exports = None
    if exports_path is not None:
        exports_input = Path(exports_path)
        if exports_input.is_symlink() or not exports_input.is_dir():
            raise FileNotFoundError(f'Exports directory is missing or invalid: {exports_input}')
        exports = exports_input.resolve(strict=True)
        if exports.parent != report.parent:
            raise ValueError('Exports directory must have the same output parent as the report')
    identity = hashlib.sha256(os.fsencode(str(report))).hexdigest()[:12]
    marker = f'FIELDVIEWER_RESULT_ACCESS_V1:{identity}:{hashlib.sha256(os.fsencode(report.name)).hexdigest()[:12]}'
    page = report.parent / f'OPEN_RESULTS_{identity}.html'
    warnings = []
    published = _publish_page(page, _page_content(report, exports, label, status, marker), marker)
    if not published:
        warnings.append(f'Result entry page collision; existing file was not changed: {page}')
    result = {'entry_page': str(page) if published else '',
              'desktop_shortcuts': [], 'warnings': warnings}
    if desktop is False or os.name != 'nt':
        return result
    if desktop is None:
        try:
            desktop_value = _run_powershell({'action': 'desktop'}).get('desktop', '')
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result['warnings'].append(f'User Desktop unavailable: {exc}')
            return result
        desktop_dir = Path(desktop_value) if desktop_value else None
    else:
        desktop_dir = Path(desktop)
    if desktop_dir is None or not desktop_dir.is_dir() or desktop_dir.is_symlink():
        result['warnings'].append(f'User Desktop unavailable: {desktop_dir}')
        return result
    name = _slug(label)
    specs = [{'link': str(desktop_dir / f'FieldViewer Report {name} {identity}.lnk'),
              'target': str(report), 'target_type': 'file',
              'marker': f'FieldViewer result access v1; {identity}; report'}]
    if exports is not None:
        specs.append({'link': str(desktop_dir / f'FieldViewer Files {name} {identity}.lnk'),
                      'target': str(exports), 'target_type': 'directory',
                      'marker': f'FieldViewer result access v1; {identity}; files'})
    try:
        response = _run_powershell({'action': 'shortcuts', 'items': specs})
        result['desktop_shortcuts'] = list(response.get('shortcuts') or [])
        result['warnings'].extend(response.get('warnings') or [])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result['warnings'].append(f'User Desktop shortcuts unavailable: {exc}')
    return result
