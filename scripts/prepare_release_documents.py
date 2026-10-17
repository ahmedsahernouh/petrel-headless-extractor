# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Refresh release documentation without changing the accepted application files."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import shutil
import zipfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(source: Path, expected: str, output: Path,
            release_version: str | None = None, refresh_guides: bool = False) -> dict:
    if sha(source.read_bytes()) != expected.lower():
        raise ValueError('Input archive SHA-256 differs from the reviewed package.')
    if output.exists():
        raise FileExistsError(output)
    replacements = {
        'FieldViewer/CITATION.cff': (ROOT/'CITATION.cff').read_bytes(),
        'FieldViewer/SECURITY.md': (ROOT/'SECURITY.md').read_bytes(),
        'FieldViewer/LICENSING.md': (ROOT/'docs/LICENSING.md').read_bytes(),
        'FieldViewer/BRANDING.md': (ROOT/'docs/BRANDING.md').read_text('utf-8')
            .replace('../LICENSE', 'LICENSE').replace('../NOTICE', 'NOTICE').encode('utf-8'),
    }
    notes=(ROOT/'docs/RELEASE_NOTES.md').read_text('utf-8')
    if release_version:
        notes=notes.replace('(DESKTOP_BETA2_VALIDATION.md)',
            '(https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/v'+release_version+'/docs/DESKTOP_BETA2_VALIDATION.md)')
    notes=re.sub(r'## Package\n.*?(?=\n## |\Z)',
                 '## Download\n\nDownload the package and checksum from the release page.\n',
                 notes,flags=re.S)
    replacements['FieldViewer/RELEASE_NOTES.md']=notes.encode('utf-8')
    replacements['FieldViewer/README.md']=(ROOT/'portable_petrel_toolkit/STANDALONE_README.md').read_bytes()
    with zipfile.ZipFile(source) as src:
        if src.testzip() is not None:
            raise ValueError('Input ZIP integrity check failed.')
        for name in ('LICENSE', 'NOTICE'):
            archived=src.read('FieldViewer/'+name).decode('utf-8-sig').replace('\r\n','\n')
            canonical=(ROOT/name).read_text('utf-8-sig')
            if archived != canonical:
                raise ValueError('Accepted licensing file differs: '+name)
        for name in ('Start Here.pdf', 'Start Here.html'):
            if refresh_guides:
                replacements[name]=(ROOT/name).read_bytes()
            elif src.read(name) != (ROOT/name).read_bytes():
                raise ValueError('Guide differs from accepted package: '+name)
        if refresh_guides:
            documentation=json.loads(src.read('DOCUMENTATION_MANIFEST.json'))
            documentation['files']=[{'path':name,'bytes':len(replacements[name]),
                                      'sha256':sha(replacements[name])}
                                     for name in ('Start Here.pdf','Start Here.html')]
            replacements['DOCUMENTATION_MANIFEST.json']=(json.dumps(documentation,indent=2)+'\n').encode()
        if release_version:
            metadata=json.loads(src.read('FieldViewer/toolkit.json'))
            metadata.update(release_version=release_version,release_tag='v'+release_version)
            replacements['FieldViewer/toolkit.json']=(json.dumps(metadata,indent=2)+'\n').encode()
        manifest = json.loads(src.read('FieldViewer/00_manifest/toolkit_files.json'))
        indexed = {row['path']: row for row in manifest['files']}
        for name, data in replacements.items():
            if not name.startswith('FieldViewer/'):
                continue
            relative = name.removeprefix('FieldViewer/')
            if relative not in indexed:
                row = {'path': relative, 'size_bytes': len(data), 'sha256': sha(data)}
                manifest['files'].append(row)
            else:
                indexed[relative].update(size_bytes=len(data), sha256=sha(data))
        manifest['files'].sort(key=lambda row: row['path'])
        manifest['file_count_excluding_this_manifest'] = len(manifest['files'])
        replacements['FieldViewer/00_manifest/toolkit_files.json'] = (
            json.dumps(manifest, indent=2)+'\n').encode('utf-8')
        output.mkdir(parents=True, exist_ok=False)
        archive = output/('FieldViewer-'+release_version+'-win64.zip' if release_version else source.name)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
            for info in src.infolist():
                dst.writestr(copy.copy(info), replacements.get(info.filename, src.read(info.filename)))
            for name in sorted(set(replacements)-set(src.namelist())):
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 13, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                dst.writestr(info, replacements[name])
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(archive) as dst:
        assert dst.testzip() is None
        untouched = set(src.namelist())-set(replacements)
        assert all(src.read(name) == dst.read(name) for name in untouched)
        executable = [name for name in src.namelist()
                      if name.endswith(('.py', '.ps1', '.bat', '.zip'))]
        assert all(src.read(name) == dst.read(name) for name in executable)
        with zipfile.ZipFile(io.BytesIO(dst.read('FieldViewer/bootstrap/runtime.zip'))) as runtime:
            for row in manifest['files']:
                name = 'FieldViewer/'+row['path']
                data = dst.read(name) if name in dst.namelist() else runtime.read(row['path'])
                assert len(data) == row['size_bytes'] and sha(data) == row['sha256'], name
    digest = sha(archive.read_bytes())
    (output/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n', 'ascii')
    for suffix in ('pdf', 'html'):
        shutil.copy2(ROOT/f'Start Here.{suffix}', output/f'Start.Here.{suffix}')
    result = {'archive': archive.name, 'sha256': digest, 'bytes': archive.stat().st_size,
              'base_archive_sha256': expected, 'zip_crc': 'passed',
              'managed_files_verified': len(manifest['files']),
              'executable_members_byte_identical': len(executable),
              'changed_members': sorted(replacements), 'application_changed': False}
    (output/'PACKAGE_ACCEPTANCE.json').write_text(json.dumps(result, indent=2)+'\n', 'utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-version')
    parser.add_argument('--refresh-guides', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.expected_sha256, args.output,
                             args.release_version,args.refresh_guides), indent=2))
