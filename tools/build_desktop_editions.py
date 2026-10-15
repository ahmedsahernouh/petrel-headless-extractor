#!/usr/bin/env python3
"""Build optional Desktop and Headless editions around a pinned published ZIP.

This does not rebuild or modify the extractor. Requires only Python stdlib.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

SCHEMA = 'fieldviewer.edition/1'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')

def safe_name(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or str(p) != name.rstrip('/'):
        raise ValueError('Unsafe archive member: '+name)
    return name.casefold()

def build(engine_zip, expected_hash, output, source, gui_revision, source_revision, engine_tag='v1.0.0-rc1009'):
    engine_zip, output, source = map(Path, (engine_zip, output, source))
    raw = engine_zip.read_bytes()
    if digest(raw) != expected_hash.lower():
        raise ValueError('Engine ZIP SHA-256 mismatch')
    if not gui_revision.strip() or not source_revision.strip():
        raise ValueError('Explicit GUI and source revisions required')
    with zipfile.ZipFile(engine_zip) as engine:
        entries = engine.infolist()
        seen = set()
        shared = []
        for info in entries:
            key = safe_name(info.filename)
            if key in seen or key in ('edition.json','package_manifest.json','shared_engine_manifest.json') or key.startswith('desktop/') or key=='fieldviewer_desktop.bat':
                raise ValueError('Duplicate or conflicting engine member: '+info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Symbolic link is not admitted')
            seen.add(key)
            data = engine.read(info.filename)
            shared.append({'path':info.filename,'bytes':len(data),'sha256':digest(data)})
        if engine.testzip() is not None:
            raise ValueError('Invalid engine archive CRC')
        if 'fieldviewer/toolkit.json' not in seen or 'fieldviewer_data_extractor.bat' not in seen:
            raise ValueError('Expected standalone FieldViewer layout is missing')
        version = json.loads(engine.read('FieldViewer/toolkit.json').decode('utf-8-sig'))['version']
        shared.sort(key=lambda row:row['path'])
        shared_data = encoded({'schema':'fieldviewer.shared-engine/1','archive_sha256':expected_hash.lower(),'files':shared})
        gui_files = {'FieldViewer_Desktop.bat':source/'FieldViewer_Desktop.bat',
                     'DESKTOP_README.md':source/'docs/DESKTOP_GUI.md'}
        desktop = source/'desktop'
        for path in sorted(desktop.rglob('*')):
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError('Symbolic GUI file is not admitted')
            relative=path.relative_to(desktop)
            if any(part.startswith('.') for part in relative.parts):
                continue
            if path.suffix.lower() not in ('.ps1','.psm1','.cs','.png','.ico','.svg','.json','.md') or 'tests' in path.name.lower() or '__pycache__' in path.parts:
                continue
            if path.suffix.lower() in ('.png','.ico','.svg') and relative.parts[0]!='assets':
                continue
            gui_files[path.relative_to(source).as_posix()] = path
        if 'desktop/FieldViewer.Desktop.ps1' not in gui_files:
            raise ValueError('Desktop entrypoint is missing')
        additions = {name:path.read_bytes() for name,path in gui_files.items()}
        for name in additions:
            if safe_name(name) in seen:
                raise ValueError('GUI cannot replace an engine member')
        output.mkdir(parents=True, exist_ok=True)
        targets = {edition:output/f'FieldViewer-{edition}-GUI-preview-win64.zip' for edition in ('Headless','Desktop')}
        if any(p.exists() or p.with_suffix(p.suffix+'.sha256').exists() for p in targets.values()):
            raise FileExistsError('Edition output already exists; use a fresh build directory')
        receipts=[]
        for edition, target in targets.items():
            extra = dict(additions) if edition=='Desktop' else {}
            extra['SHARED_ENGINE_MANIFEST.json']=shared_data
            extra['EDITION.json']=encoded({'schema':SCHEMA,'edition':edition,
                'engine_version':version,'engine_tag':engine_tag,'engine_zip_sha256':expected_hash.lower(),
                'gui_revision':gui_revision if edition=='Desktop' else None,
                'source_revision':source_revision,'release_status':'development_candidate',
                'shared_engine_manifest_sha256':digest(shared_data)})
            manifest = shared+[{'path':name,'bytes':len(data),'sha256':digest(data)} for name,data in sorted(extra.items())]
            extra['PACKAGE_MANIFEST.json']=encoded({'schema':'fieldviewer.package/1','edition':edition,
                'self_exclusion':'PACKAGE_MANIFEST.json is not self-hashed; the ZIP checksum binds it.',
                'files':sorted(manifest,key=lambda row:row['path'])})
            with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                # ZipInfo may be mutated by writestr; never reuse the reader's objects.
                for info in entries:
                    archive.writestr(copy.copy(info),engine.read(info.filename))
                for name,data in sorted(extra.items()):
                    archive.writestr(name,data)
            with zipfile.ZipFile(target) as check:
                if check.testzip() is not None:
                    raise ValueError('Edition CRC failure')
                for row in manifest:
                    b=check.read(row['path'])
                    if len(b)!=row['bytes'] or digest(b)!=row['sha256']:
                        raise ValueError('Edition member hash failure: '+row['path'])
            file_hash=digest(target.read_bytes())
            target.with_suffix(target.suffix+'.sha256').write_text(f'{file_hash}  {target.name}\n',encoding='ascii')
            receipts.append({'edition':edition,'path':str(target),'bytes':target.stat().st_size,'sha256':file_hash,
                'shared_engine_members':len(shared),'shared_engine_manifest_sha256':digest(shared_data)})
    receipt={'schema':'fieldviewer.edition-build/1','engine_zip_sha256':expected_hash.lower(),
             'source_revision':source_revision,'gui_revision':gui_revision,'engine_tag':engine_tag,
             'scope':'Archive parity only; installation, UI and clean-Windows acceptance are separate.', 'editions':receipts}
    (output/'BUILD_RECEIPT.json').write_bytes(encoded(receipt))
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-zip',required=True,type=Path)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--gui-revision',required=True)
    parser.add_argument('--source-revision',required=True)
    parser.add_argument('--engine-tag',default='v1.0.0-rc1009')
    a=parser.parse_args()
    print(json.dumps(build(a.engine_zip,a.expected_sha256,a.output,a.source,a.gui_revision,a.source_revision,a.engine_tag),indent=2))

if __name__=='__main__':
    main()
