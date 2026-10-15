# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded extraction stages and isolated seismic work. https://saherlabs.dev/"""
from __future__ import annotations
import argparse
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

import geoviewer_io as gio
import petrel_progress as progress
from geoviewer_diagnostics import event


def seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Budget seconds must be finite and positive')
    return float(value)


def remaining(state, lane, limit):
    if limit is None:
        return None
    return max(0.0, seconds(limit) - sum(r['elapsed_seconds'] for r in state.get('attempts', []) if r['lane'] == lane))


def run_stage(command, *, lane, limit, state_path, log_path, category=None):
    """One child tree; elapsed limits accumulate per lane, independent of others."""
    state_path=Path(state_path); log_path=Path(log_path)
    state=json.loads(state_path.read_text(encoding='utf-8-sig')) if state_path.exists() else {'schema':'fieldviewer.stage-budgets/1','attempts':[]}
    available=remaining(state,lane,limit)
    row={'lane':lane,'command':command,'limit_seconds':limit,'remaining_before':available,
         'started_at_utc':datetime.now(timezone.utc).isoformat(),'status':'running','elapsed_seconds':0,
         'category':category or lane,'stage_id':uuid.uuid4().hex,
         'run_id':os.environ.get('GEOVIEWER_RUN_ID'), 'pipeline_run_id':os.environ.get('GEOVIEWER_PIPELINE_RUN_ID'),
         'runner_pid':os.getpid(),'parent_pid':os.getppid(),'log_path':str(log_path)}
    state['attempts'].append(row); gio.atomic_json(state_path,state)
    event('pipeline_stage_started',**row)
    if available is not None and available<=0:
        row.update(status='not_attempted_budget',exit_code=124)
    else:
        started=time.monotonic()
        try:
            log_path.parent.mkdir(parents=True,exist_ok=True)
            code=progress.run_pipeline(command,Path(__file__).resolve().parents[1],log_path,available)
            row.update(status='completed' if code==0 else 'completed_with_gaps' if code==10 else 'failed',exit_code=code)
        except subprocess.TimeoutExpired:
            row.update(status='interrupted_budget',exit_code=124,reason='Stage time budget exhausted; unfinished files are not accepted outputs')
        except BaseException as exc:
            row.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'failed',exit_code=1,error=gio.error_details(exc))
            raise
        finally:
            row['elapsed_seconds']=round(time.monotonic()-started,3)
            row['ended_at_utc']=datetime.now(timezone.utc).isoformat()
            gio.atomic_json(state_path,state)
    gio.atomic_json(state_path,state)
    event('stage_budget_outcome',severity='warning' if row['exit_code'] else 'info',**row)
    return row['exit_code']


def companion_snapshot(package):
    """Preserve the manifest and require fresh companion destinations."""
    package=Path(package).resolve(strict=True)
    paths=['09_source_companions','10_converted_ascii','02_wells/well_tops',
           '00_manifest/companion_source_inventory.csv','99_unexported_or_manual/portable_unsupported_inventory.csv',
           '07_workflows_reports/portable_extractor/companion_capability_report.json']
    for rel in paths:
        p=gio.contained(package,package/rel)
        if p.exists() and (p.is_file() or any(p.iterdir())):
            raise ValueError('Companion stage requires fresh output: '+str(p))
    return paths,(package/'00_manifest/export_manifest.csv').read_bytes()


def quarantine_companions(package, snapshot):
    """Retain incomplete stage evidence outside all accepted-data folders."""
    package=Path(package).resolve(strict=True);paths,manifest=snapshot
    token=uuid.uuid4().hex[:8]
    target=gio.contained(package,package/('q_'+token+'.partial'))
    target.mkdir(parents=True,exist_ok=False)
    moved=[]; retained=[]
    # Rename stage-owned trees to SHORTER siblings: retaining a failed tree must
    # never make its payload paths deeper, or PS5 cleanup can fail afterwards.
    short_trees={'09_source_companions':'s','10_converted_ascii':'a','02_wells/well_tops':'t'}
    short_files={'00_manifest/companion_source_inventory.csv':'inventory.csv',
                 '99_unexported_or_manual/portable_unsupported_inventory.csv':'unsupported.csv',
                 '07_workflows_reports/portable_extractor/companion_capability_report.json':'capability.json'}
    for rel in paths:
        source=gio.contained(package,package/rel)
        dest=gio.contained(package,package/('q_'+token+'_'+short_trees[rel])) if rel in short_trees else gio.contained(target,target/short_files[rel])
        if not source.exists():continue
        if len(str(dest))>len(str(source)):raise ValueError('Quarantine must not lengthen an evidence path')
        dest.parent.mkdir(parents=True,exist_ok=True)
        source.rename(dest);moved.append(rel);retained.append({'original':rel,'retained':str(dest.relative_to(package))})
    # The child alone owned manifest additions in this stage; restore its baseline.
    saved=target/'manifest_before.csv';saved.write_bytes(manifest)
    pending=package/'00_manifest/export_manifest.csv.restore.tmp';pending.write_bytes(manifest)
    pending.replace(package/'00_manifest/export_manifest.csv')
    gio.atomic_json(target/'QUARANTINE.json',{'status':'incomplete_companion_stage','moved':moved,'retained_paths':retained,
        'scope':'No incomplete companion file is registered or used for native-top calibration'})
    return str(target)


def seismic_job(source, output, options, *, timeout):
    """Bound one conversion/hash process; leave partial work for diagnostics."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    folder=output/('_job_'+uuid.uuid4().hex[:12]);folder.mkdir()
    request={'source':str(source),'output':str(output),'options':options}
    gio.atomic_json(folder/'request.json',request)
    command=[sys.executable,str(Path(__file__).resolve()),'--seismic-request',str(folder/'request.json'),
             '--response',str(folder/'result.json')]
    code=progress.run_pipeline(command,Path(__file__).resolve().parents[1],folder/'worker.log',
                               None if timeout is None else seconds(timeout))
    if code:raise RuntimeError('Seismic child exit '+str(code)+'; see '+str(folder/'worker.log'))
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    if result.get('status')!='passed':raise RuntimeError('Seismic child failed: '+str(result.get('error')))
    return result['result']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--category');parser.add_argument('--lane');parser.add_argument('--seconds',type=float);parser.add_argument('--run-to-completion',action='store_true');parser.add_argument('--state',type=Path)
    parser.add_argument('--log',type=Path);parser.add_argument('--companions-package',type=Path)
    parser.add_argument('--seismic-request',type=Path);parser.add_argument('--response',type=Path)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if args.seismic_request:
        request=json.loads(args.seismic_request.read_text(encoding='utf-8'))
        try:
            if request['options'].pop('_hash_only',False):
                from petrel_seismic_integrity import readonly_source, file_state
                source=Path(request['source'])
                with readonly_source(source):
                    before=file_state(source)
                    observer=progress.HashFileEvents(source,before['size_bytes'])
                    observer.emit('started')
                    try:
                        digest=progress.hash_file(source,on_bytes=observer.advance)
                        if before!=file_state(source):raise ValueError('Source changed while hashing')
                        observer.emit('completed',sha256=digest)
                    except Exception as exc:
                        observer.emit('failed',error_type=type(exc).__name__,reason=str(exc))
                        raise
                result={'sha256':digest}
            else:
                from petrel_file_convert import execute
                result=execute(request['source'],request['output'],'zgy-to-segy',request['options'])
            gio.atomic_json(args.response,{'status':'passed','result':result});return 0
        except Exception as exc:
            gio.atomic_json(args.response,{'status':'failed','error':gio.error_details(exc)})
            raise
    command=args.command[1:] if args.command and args.command[0]=='--' else args.command
    if not command or not args.state or not args.log:parser.error('Stage command, state and log required')
    snapshot=companion_snapshot(args.companions_package) if args.companions_package else None
    if not args.run_to_completion and args.seconds is None:parser.error('--seconds is required unless --run-to-completion is selected')
    code=run_stage(command,lane=args.lane,limit=None if args.run_to_completion else args.seconds,state_path=args.state,log_path=args.log,category=args.category)
    if snapshot is not None and code not in (0,10):
        location=quarantine_companions(args.companions_package,snapshot)
        event('companion_stage_quarantined',severity='warning',path=location,exit_code=code)
    return code


if __name__=='__main__':raise SystemExit(main())
