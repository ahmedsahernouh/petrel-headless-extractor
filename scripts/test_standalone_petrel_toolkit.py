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
    with zipfile.ZipFile(args.zip) as z:
        for member in z.infolist():
            if not (relocated/member.filename).resolve().is_relative_to(relocated):raise ValueError('Unsafe ZIP member')
        z.extractall(relocated)
    package=next(relocated.iterdir());bat=package/'run_portable_petrel_extract.bat'
    env=os.environ.copy();win=Path(os.environ['SystemRoot'])
    env.update(PATH=str(win/'System32')+';'+str(win/'System32/WindowsPowerShell/v1.0'),
               PYTHONHOME=str(relocated/'NONEXISTENT_SYSTEM_PYTHON'),PYTHONPATH=str(relocated/'FORBIDDEN_IMPORTS'),
               PYTHON=str(relocated/'python_missing.exe'),PETREL_MCP_PYTHON=str(relocated/'python_missing.exe'),
               HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',PIP_NO_INDEX='1')
    checks=[]
    def run(label,arguments,expected=0):
        # cmd executes only this fixed BAT; paths are Windows-quoted without shell-built deletion.
        command='"'+str(bat)+'"'+''.join(' "'+str(a)+'"' for a in arguments)
        command_line='"'+str(win/'System32/cmd.exe')+'" /d /s /c "'+command+'"'
        proc=subprocess.run(command_line,cwd=relocated,env=env,
                            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=1800)
        (evidence/(label+'.txt')).write_text(proc.stdout,encoding='utf-8')
        passed=(proc.returncode==expected) if expected==0 else proc.returncode!=0
        checks.append({'name':label,'passed':passed,'exit_code':proc.returncode,'log':label+'.txt'})
        print(label,proc.returncode,flush=True)
        if not passed:raise AssertionError(label+'\n'+proc.stdout[-4000:])
        return proc.stdout
    run('bundle_check',['--check','-NoPause'])
    source=relocated/'Test Project & Spaces';source.mkdir();store=source/'Fixture.ptd';store.mkdir()
    project=source/'Fixture.pet';project.write_text('Synthetic read-only acceptance fixture')
    with sqlite3.connect(store/'Data.ptd') as db:
        db.executescript('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT, time_stamp TEXT); CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB);')
    (source/'checkshots.txt').write_text('Well\tMD\tTWT\nTEST\t100\t25\n')
    output=relocated/'Extracted Results';original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    run('bat_convert_spaces',[project,output,'convert','-NoPause'])
    result=next(output.rglob('RUN_RESULT.json'));payload=json.loads(result.read_text())
    assert payload['status']=='passed' and payload['source_mutated'] is False
    assert payload['extraction_audit']['status']=='passed' and payload['qc_audit']['status']=='passed'
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in original.items())
    run('reject_output_inside_source',[project,source/'bad','convert','-NoPause'],expected=1)
    assert not (source/'bad').exists()
    orphan=source/'MissingStore.pet';orphan.write_text('missing matching ptd')
    run('reject_missing_store',[orphan,output,'convert','-NoPause'],expected=1)
    unsupported=source/'Unsupported.pet';unsupported.write_text('unsupported native layout fixture')
    (source/'Unsupported.ptd').mkdir();(source/'Unsupported.ptd/Data.ptd').write_bytes(b'not a validated SQLite store')
    run('reject_unvalidated_native_layout',[unsupported,output,'convert','-NoPause'],expected=1)
    for index,real in enumerate(args.project,1):
        run('real_project_'+str(index),[real.resolve(),relocated/('Real Results '+str(index)),'convert','-NoPause'])
    py=package/'runtime/python.exe';ps=win/'System32/WindowsPowerShell/v1.0/powershell.exe'
    for label,cmd in [
        ('synthetic_toolkit_smoke',[str(ps),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'scripts/test_portable_petrel_toolkit.ps1'),'-PythonPath',str(py)]),
        ('native_spatial_controls',[str(py),'-B',str(package/'scripts/test_petrel_native_spatial_zero_gui.py')]),
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
            'network_physically_disconnected':False,'machine':'same Windows host, isolated relocated package; not a second physical machine'}
    (evidence/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Acceptance passed: '+str(evidence/'acceptance.json'))


if __name__=='__main__':main()
