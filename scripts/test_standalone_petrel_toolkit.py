# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Acceptance tests on a relocated ZIP using its actual BAT and bundled Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip',type=Path,required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--project',type=Path,action='append',default=[])
    args=parser.parse_args();evidence=args.evidence_dir.resolve();evidence.mkdir(parents=True,exist_ok=False)
    relocated=Path(tempfile.mkdtemp(prefix='Petrel Offline Test '))
    # Match Explorer Extract All: destination/ZIP-stem/archive-root. The original
    # acceptance harness omitted the ZIP-stem layer and missed long-path failures.
    extraction_root=relocated/'Downloads'/args.zip.stem
    extraction_root.mkdir(parents=True)
    with zipfile.ZipFile(args.zip) as z:
        roots={Path(m.filename).parts[0] for m in z.infolist()}
        assert roots=={'PetrelExtractor'},roots
        longest_extracted_path=max(len(str(extraction_root/m.filename)) for m in z.infolist())
        assert longest_extracted_path<240, longest_extracted_path
        for member in z.infolist():
            if not (extraction_root/member.filename).resolve().is_relative_to(extraction_root):raise ValueError('Unsafe ZIP member')
    # Use Windows PowerShell's .NET ZIP extractor, not Python's long-path-aware
    # extraction alone, for this Windows distribution regression.
    unzip_script=evidence/'extract_windows.ps1'
    unzip_script.write_text('param([string]$Archive,[string]$Destination)\n$ErrorActionPreference="Stop"\nAdd-Type -AssemblyName System.IO.Compression.FileSystem\n[System.IO.Compression.ZipFile]::ExtractToDirectory($Archive,$Destination)\n',encoding='utf-8')
    win=Path(os.environ['SystemRoot'])
    unzipped=subprocess.run([str(win/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(unzip_script),'-Archive',str(args.zip.resolve()),'-Destination',str(extraction_root)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=600)
    (evidence/'windows_extraction.txt').write_text(unzipped.stdout,encoding='utf-8')
    assert unzipped.returncode==0,unzipped.stdout
    package=extraction_root/'PetrelExtractor';bat=package/'run_portable_petrel_extract.bat'
    env=os.environ.copy();win=Path(os.environ['SystemRoot'])
    env.update(PATH=str(win/'System32')+';'+str(win/'System32/WindowsPowerShell/v1.0'),
               PYTHONHOME=str(relocated/'NONEXISTENT_SYSTEM_PYTHON'),PYTHONPATH=str(relocated/'FORBIDDEN_IMPORTS'),
               PYTHON=str(relocated/'python_missing.exe'),PETREL_MCP_PYTHON=str(relocated/'python_missing.exe'),
               HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',PIP_NO_INDEX='1')
    checks=[]
    def run(label,arguments,expected=0,input_text=None):
        # cmd executes only this fixed BAT; paths are Windows-quoted without shell-built deletion.
        command='"'+str(bat)+'"'+''.join(' "'+str(a)+'"' for a in arguments)
        command_line='"'+str(win/'System32/cmd.exe')+'" /d /s /c "'+command+'"'
        stdin_args={'stdin':subprocess.DEVNULL} if input_text is None else {'input':input_text}
        proc=subprocess.run(command_line,cwd=relocated,env=env,
                            **stdin_args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=1800)
        (evidence/(label+'.txt')).write_text(proc.stdout,encoding='utf-8')
        passed=(proc.returncode==expected) if expected==0 else proc.returncode!=0
        checks.append({'name':label,'passed':passed,'exit_code':proc.returncode,'log':label+'.txt'})
        print(label,proc.returncode,flush=True)
        if not passed:raise AssertionError(label+'\n'+proc.stdout[-4000:])
        return proc.stdout
    # The outer ZIP has no Python at all: the actual BAT must bootstrap offline.
    assert not (package/'runtime').exists()
    first=run('bundle_check',['--check','-NoPause'])
    log=package/'build/dependencies/last_check.json'
    install=json.loads(log.read_text())
    assert install['status']=='repaired' and install['installed_file_count']>1000
    assert install['network_used'] is False and install['system_python_modified'] is False
    assert 'ZFP compression check passed' in first
    assert "No module named 'sdglue'" not in first and "No module named 'zfpy'" not in first
    manifest=json.loads((package/'00_manifest/toolkit_files.json').read_text())
    installed_path_length=max(len(str(package/row['path'])) for row in manifest['files'])
    assert installed_path_length<240,installed_path_length
    runtime_files={row['path']:row for row in manifest['files'] if row['path'].startswith('runtime/')}
    assert install['installed_file_count']==len(runtime_files)
    for label,relative,corrupt in [
        ('repair_missing_dependency','runtime/Lib/site-packages/shapefile/__init__.py',False),
        ('repair_corrupt_dependency','runtime/Lib/site-packages/lasio/__init__.py',True),
        ('repair_missing_python','runtime/python.exe',False)]:
        target=package/relative
        # pyshp distributions can provide either a module file or a package.
        if relative.endswith('shapefile/__init__.py') and not target.exists():
            relative='runtime/Lib/site-packages/shapefile.py';target=package/relative
        saved=target.read_bytes()
        try:
            if corrupt:target.write_bytes(b'broken dependency test')
            else:target.unlink()
            run(label,['--check','-NoPause'])
            assert target.read_bytes()==saved
            repaired=json.loads(log.read_text())
            assert repaired['installed_files']==[relative],repaired
        finally:target.write_bytes(saved)
    # A healthy installation can run without the cache; a broken one cannot.
    cache=package/'bootstrap/runtime.zip';cache_bytes=cache.read_bytes()
    dependency=package/'runtime/Lib/site-packages/lasio/__init__.py';dependency_bytes=dependency.read_bytes()
    try:
        cache.unlink()
        run('healthy_runtime_without_cache',['--check','-NoPause'])
        dependency.unlink()
        message=run('reject_missing_repair_cache',['--check','-NoPause'],expected=1)
        assert 'offline dependency cache is missing or damaged' in message and not dependency.exists()
        cache.write_bytes(b'corrupt repair cache test')
        message=run('reject_corrupt_repair_cache',['--check','-NoPause'],expected=1)
        assert 'offline dependency cache is missing or damaged' in message and not dependency.exists()
    finally:
        cache.write_bytes(cache_bytes);dependency.write_bytes(dependency_bytes)
    # Do not replace a runtime used by an active extraction.
    python=package/'runtime/python.exe'
    busy=subprocess.Popen([str(python),'-B','-c','import sys; print("ready",flush=True); sys.stdin.readline()'],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        assert busy.stdout.readline().strip()=='ready'
        dependency.unlink()
        message=run('reject_repair_while_runtime_in_use',['--check','-NoPause'],expected=1)
        assert 'An extractor is using this runtime' in message and not dependency.exists()
    finally:
        dependency.write_bytes(dependency_bytes)
        busy.communicate('\n',timeout=30)
    # Reproduce an interrupted Explorer extraction, where the BAT is present but
    # its PowerShell target is missing. This must produce a useful BAT-level error.
    launcher=package/'scripts/launch_standalone_petrel.ps1';launcher_bytes=launcher.read_bytes()
    try:
        launcher.unlink()
        message=run('reject_incomplete_extraction',['--check','-NoPause'],expected=1)
        assert 'This standalone extraction is incomplete' in message
        assert 'Path too long' in message
    finally:launcher.write_bytes(launcher_bytes)
    source=relocated/'Test Project & Spaces';source.mkdir();store=source/'Fixture.ptd';store.mkdir()
    project=source/'Fixture.pet';project.write_text('Synthetic read-only acceptance fixture')
    with sqlite3.connect(store/'Data.ptd') as db:
        db.executescript('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT, time_stamp TEXT); CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB);')
    (source/'checkshots.txt').write_text('Well\tMD\tTWT\nTEST\t100\t25\n')
    output=relocated/'Extracted Results';original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    progress_output=run('bat_convert_spaces',[project,output,'convert','-NoPause'])
    assert '12/12 stages complete | Complete | Elapsed' in progress_output
    assert progress_output.index('SUCCESS:') < progress_output.index('12/12 stages complete')
    assert all('Stage '+str(n)+'/12:' in progress_output for n in range(1,13))
    result=next(output.rglob('RUN_RESULT.json'));payload=json.loads(result.read_text())
    assert payload['status']=='passed' and payload['source_mutated'] is False
    assert payload['extraction_audit']['status']=='passed' and payload['qc_audit']['status']=='passed'
    assert payload['elapsed_seconds'] > 0
    assert 'Elapsed:' in (result.parent/'RUN_LOG.txt').read_text()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in original.items())
    prompted=run('interactive_project_prompt',[],input_text=str(project)+'\n'+str(relocated/'Prompted Results')+'\n\n')
    # ConsoleHost omits Read-Host labels when redirected; verify the inputs were
    # actually consumed and produced a valid run in the requested destination.
    assert 'SUCCESS:' in prompted
    prompt_result=next((relocated/'Prompted Results').rglob('RUN_RESULT.json'))
    prompt_payload=json.loads(prompt_result.read_text())
    prompt_request=json.loads((prompt_result.parent/'request.json').read_text())
    assert prompt_payload['status']=='passed' and prompt_payload['source_mutated'] is False
    assert prompt_request['project_file']==str(project)
    assert prompt_request['output_root']==str(relocated/'Prompted Results')
    run('reject_output_inside_source',[project,source/'bad','convert','-NoPause'],expected=1)
    assert not (source/'bad').exists()
    orphan=source/'MissingStore.pet';orphan.write_text('missing matching ptd')
    run('reject_missing_store',[orphan,output,'convert','-NoPause'],expected=1)
    unsupported=source/'Unsupported.pet';unsupported.write_text('unsupported native layout fixture')
    (source/'Unsupported.ptd').mkdir();(source/'Unsupported.ptd/Data.ptd').write_bytes(b'not a validated SQLite store')
    failed_output=run('reject_unvalidated_native_layout',[unsupported,output,'convert','-NoPause'],expected=1)
    assert 'Stopped before completion' in failed_output
    assert '12/12 stages complete' not in failed_output
    for index,real in enumerate(args.project,1):
        run('real_project_'+str(index),[real.resolve(),relocated/('Real Results '+str(index)),'convert','-NoPause'])
    py=package/'runtime/python.exe';ps=win/'System32/WindowsPowerShell/v1.0/powershell.exe'
    for label,cmd in [
        ('synthetic_toolkit_smoke',[str(ps),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'scripts/test_portable_petrel_toolkit.ps1'),'-PythonPath',str(py)]),
        ('native_spatial_controls',[str(py),'-B',str(package/'scripts/test_petrel_native_spatial_zero_gui.py')]),
        ('large_companion_controls',[str(py),'-B',str(package/'scripts/test_companion_large_files.py')]),
        ('progress_controls',[str(py),'-B',str(package/'scripts/test_petrel_progress.py')]),
        ('portable_doctor',[str(ps),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'scripts/doctor_portable_petrel_toolkit.ps1')])]:
        p=subprocess.run(cmd,cwd=relocated,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=1800)
        (evidence/(label+'.txt')).write_text(p.stdout,encoding='utf-8')
        checks.append({'name':label,'passed':p.returncode==0,'exit_code':p.returncode,'log':label+'.txt'})
        if p.returncode:raise AssertionError(label+'\n'+p.stdout[-4000:])
        print(label,'passed',flush=True)
    # A corrupt delivered script must stop before any project output is created.
    target=package/'scripts/portable_petrel_companion_extract.py';saved=target.read_bytes()
    try:
        target.write_bytes(saved+b'\n# altered\n')
        run('reject_bundle_tamper',[project,relocated/'Tamper Results','convert','-NoPause'],expected=1)
        assert not (relocated/'Tamper Results').exists()
    finally:target.write_bytes(saved)
    run('bundle_check_after_tests',['--check','-NoPause'])
    results=[]
    for path in relocated.rglob('RUN_RESULT.json'):
        results.append({'path':str(path),'result':json.loads(path.read_text())})
    report={'status':'passed','zip':str(args.zip.resolve()),'relocated_root':str(relocated),'package_root':str(package),
            'checks':checks,'runs':results,'system_python_available_on_test_path':False,
            'python_environment_poisoned':True,'network_proxy_unavailable':True,
            'network_physically_disconnected':False,'machine':'same Windows host, isolated relocated package; not a second physical machine',
            'explorer_style_nesting':True,'windows_dotnet_zip_extraction':True,'longest_extracted_path':longest_extracted_path,
            'longest_installed_path':installed_path_length,'first_run_runtime_absent':True,
            'offline_installed_files':install['installed_file_count'],'zfp_round_trip':'passed'}
    (evidence/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Acceptance passed: '+str(evidence/'acceptance.json'))


if __name__=='__main__':main()
