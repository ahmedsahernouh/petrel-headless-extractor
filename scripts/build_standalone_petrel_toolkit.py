# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Build an offline Windows x64 extractor from official runtime and locked wheels.

Inputs: downloaded Python archive, wheel directory and reviewed requirements lock.
Outputs: new package folder, ZIP, SHA-256 and full file/dependency provenance.
No source project data or locally installed Python directory is copied.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.6.1'
PACKAGE_FOLDER = 'PetrelExtractor'
PYTHON_VERSION = '3.13.15'
PYTHON_URL = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embeddable-amd64.zip'
PYTHON_SHA256 = '791ada5e20aba24524f8d939cdeb069976d632a699fe5cb65274b23f4545e68a'
OFFICIAL_MANIFEST = 'https://www.python.org/ftp/python/3.13.15/windows-3.13.15.json'
SCRIPTS = [
    'doctor_portable_petrel_toolkit.ps1', 'invoke_portable_petrel_extract.ps1',
    'test_portable_petrel_toolkit.ps1', 'test_petrel_native_spatial_zero_gui.py',
    'petrel_mcp_dependencies.ps1', 'new_export_package.ps1',
    'export_petrel_native_project_zero_gui.ps1', 'export_petrel_native_semantic_zero_gui.py',
    'export_petrel_native_spatial_zero_gui.py', 'portable_petrel_companion_extract.py',
    'register_petrel_file_exports.ps1', 'validate_export_package.ps1',
    'report_petrel_project_audit.py', 'petrel_geoscience_tools.py',
    'standalone_petrel_extract.py', 'launch_standalone_petrel.ps1',
    'repair_standalone_dependencies.ps1',
    'test_companion_large_files.py', 'petrel_progress.py', 'test_petrel_progress.py',
    'petrel_file_convert.py', 'launch_zgy_conversion.ps1', 'test_petrel_binary_conversion.py',
    'petrel_native_binary.py', 'petrel_native_recovery.py', 'test_petrel_native_recovery.py',
    'petrel_visual_report.py', 'test_petrel_visual_report.py',
    'petrel_project_seismic.py', 'petrel_seismic_integrity.py', 'test_project_seismic.py',
]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-archive',type=Path,required=True)
    parser.add_argument('--wheels',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    args=parser.parse_args()
    archive=args.runtime_archive.resolve();wheels=args.wheels.resolve()
    if digest(archive)!=PYTHON_SHA256:
        raise ValueError('Official Python archive SHA-256 mismatch')
    # Explorer adds a directory named after the ZIP during Extract All. Keep both
    # that name and the archive's own root short enough for legacy Windows paths.
    name=PACKAGE_FOLDER
    zip_name='PetrelExtractor-'+VERSION+'-win64.zip'
    package=args.output_root.resolve()/name
    package.mkdir(parents=True,exist_ok=False)
    scripts=package/'scripts';scripts.mkdir()
    manifests=package/'00_manifest';manifests.mkdir()
    runtime=package/'runtime';runtime.mkdir()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (runtime/item.filename).resolve().is_relative_to(runtime):
                raise ValueError('Archive member escapes runtime')
        z.extractall(runtime)
    # Isolated paths ignore global Python, PYTHONPATH, the registry and user packages.
    (runtime/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../scripts\n',encoding='ascii')
    lock=ROOT/'portable_petrel_toolkit/requirements-standalone-lock.txt'
    subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-index',
                    '--find-links',str(wheels),'--require-hashes','--no-deps','--no-compile',
                    '--only-binary=:all:','--platform','win_amd64','--python-version','3.13',
                    '--implementation','cp','--abi','cp313','--target',str(runtime/'Lib/site-packages'),
                    '-r',str(lock)],check=True)
    # Wheel console-script launchers refer to the build interpreter; omit these unused files.
    unused=runtime/'Lib/site-packages/bin'
    if unused.exists():
        assert unused.resolve().is_relative_to(package.resolve())
        shutil.rmtree(unused)
    for file in SCRIPTS:shutil.copy2(ROOT/'scripts'/file,scripts/file)
    for file in ['AGENTS.md','LICENSE','requirements-core.txt','requirements-geodata.txt']:
        shutil.copy2(ROOT/'portable_petrel_toolkit'/file,package/file)
    shutil.copy2(lock,package/'requirements-standalone-lock.txt')
    launcher=package.parent/'run_portable_petrel_extract.bat'
    shutil.copy2(ROOT/'run_portable_petrel_extract.bat',launcher)
    shutil.copy2(ROOT/'docs/BINARY_EXTRACTION_PURPOSE.md',package/'BINARY_EXTRACTION_PURPOSE.md')
    shutil.copy2(ROOT/'docs/native_binary_audit.json',package/'native_binary_audit.json')
    shutil.copy2(ROOT/'docs/ZGY_TO_SEGY.md',package/'ZGY_TO_SEGY.md')
    shutil.copy2(ROOT/'docs/NATIVE_LOGS_SURFACES.md',package/'NATIVE_LOGS_SURFACES.md')
    shutil.copy2(ROOT/'docs/VISUAL_REPORT.md',package/'VISUAL_REPORT.md')
    shutil.copy2(ROOT/'portable_petrel_toolkit/STANDALONE_README.md',package/'README.md')
    skill=package/'.agents/skills/petrel-portable-extractor';skill.mkdir(parents=True)
    shutil.copy2(ROOT/'portable_petrel_toolkit/.agents/skills/petrel-portable-extractor/SKILL.md',skill/'SKILL.md')
    metadata=json.loads((ROOT/'portable_petrel_toolkit/toolkit.json').read_text())
    metadata.update(version=VERSION,distribution='standalone_windows_x64',python_required=False,
                    internet_required=False,bundled_python=PYTHON_VERSION,entrypoint='../run_portable_petrel_extract.bat',
                    receipt_contract='petrel-geoscience-1',automatic_package_qc=True,
                    automatic_dependency_repair=True,dependency_install_source='verified_offline_cache')
    write_json(package/'toolkit.json',metadata)
    (package/'STANDALONE.txt').write_text('Offline distribution. Keep the BAT, scripts, manifests and bootstrap cache together. The BAT installs runtime on first use.\n',encoding='ascii')
    write_json(manifests/'runtime_provenance.json',{'python_version':PYTHON_VERSION,'url':PYTHON_URL,
                'official_manifest_url':OFFICIAL_MANIFEST,'sha256':PYTHON_SHA256,'architecture':'Windows x64',
                'archive_hash_verified':True,'built_at_utc':datetime.now(timezone.utc).isoformat()})
    inventory=[]
    for wheel in sorted(wheels.glob('*.whl')):
        with zipfile.ZipFile(wheel) as z:
            member=next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))
            meta=email.message_from_bytes(z.read(member))
        inventory.append({'name':meta['Name'],'version':meta['Version'],'wheel':wheel.name,
                          'sha256':digest(wheel),'license':str(meta.get('License-Expression') or meta.get('License','unspecified')),
                          'license_files':meta.get_all('License-File',[])})
    write_json(manifests/'dependency_inventory.json',inventory)
    notes='# Third-party notices\n\nPython is distributed under its included license at `runtime/LICENSE.txt`.\n\nDependency license texts and notices are retained in `runtime/Lib/site-packages`, including each distribution metadata directory. Exact wheel hashes and license metadata are in `00_manifest/dependency_inventory.json`.\n\n'
    notes+='\n'.join('- '+x['name']+' '+x['version'] for x in inventory)+'\n'
    (package/'THIRD_PARTY_NOTICES.md').write_text(notes,encoding='utf-8')
    # Ship one compressed runtime cache. PowerShell installs it on first launch,
    # so Python itself can be missing and Explorer never expands dependency trees.
    cache=package/'bootstrap/runtime.zip';cache.parent.mkdir()
    with zipfile.ZipFile(cache,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(runtime.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(package).as_posix())
    write_json(manifests/'dependency_repair.json',{
        'version':VERSION,'scope':'runtime_only','network_required':False,
        'archive':{'path':cache.relative_to(package).as_posix(),'size_bytes':cache.stat().st_size,'sha256':digest(cache)},
        'log':'build/dependencies/last_check.json'})
    records=[{'path':p.relative_to(package).as_posix(),'size_bytes':p.stat().st_size,'sha256':digest(p)}
             for p in sorted(package.rglob('*')) if p.is_file() and p!=cache]
    # Exercise Explorer's normal Downloads/ZIP-stem/archive-root nesting, allowing
    # a longer user name. Leave headroom under the classic 260-character limit.
    explorer_root=Path('C:/Users/Example Windows Username/Downloads')/Path(zip_name).stem/name
    longest_explorer_path=max(len(str(explorer_root/row['path'])) for row in records)
    if longest_explorer_path>=240:
        raise ValueError('Release layout exceeds Windows extraction path budget: '+str(longest_explorer_path))
    write_json(manifests/'toolkit_files.json',{'toolkit':'portable-petrel-project-extractor','version':VERSION,
                'distribution':'standalone_windows_x64','files':records,'file_count_excluding_this_manifest':len(records),
                'launcher':{'path':launcher.name,'size_bytes':launcher.stat().st_size,'sha256':digest(launcher)}})
    check=subprocess.run([str(runtime/'python.exe'),'-B',str(scripts/'standalone_petrel_extract.py'),'--check'],capture_output=True,text=True)
    (args.output_root/(name+'_build_check.txt')).write_text(check.stdout+'\n'+check.stderr,encoding='utf-8')
    if check.returncode:raise RuntimeError('Bundled runtime preflight failed: '+check.stderr)
    zip_path=package.parent/zip_name
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.write(launcher,launcher.name)
        for p in sorted(package.rglob('*')):
            if p.is_file() and not p.is_relative_to(runtime):
                z.write(p,name+'/'+p.relative_to(package).as_posix(),
                        compress_type=zipfile.ZIP_STORED if p==cache else zipfile.ZIP_DEFLATED)
    sha=digest(zip_path)
    zip_path.with_suffix('.zip.sha256').write_text(sha+'  '+zip_path.name+'\n',encoding='ascii')
    result={'package':str(package),'zip':str(zip_path),'sha256':sha,'zip_bytes':zip_path.stat().st_size,
            'files':len(records),'python':PYTHON_VERSION,'dependencies':len(inventory),'preflight':'passed',
            'version':VERSION,'max_simulated_explorer_path':longest_explorer_path,
            'runtime_install':'verified_offline_cache','automatic_dependency_repair':True,
            'runtime_cache_sha256':digest(cache)}
    write_json(args.output_root/(name+'_build.json'),result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
