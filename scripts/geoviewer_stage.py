"""Record isolated category outcomes; stop only for demonstrated shared failures.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import argparse
import errno
import json
from pathlib import Path
from geoviewer_diagnostics import event
from geoviewer_io import atomic_json, output_probe


def record(package, category, code, report_path=None):
    package = Path(package)
    path = package/'01_project_metadata/pipeline_stages.json'
    stages = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
    report = {}
    if report_path and Path(report_path).is_file():
        try: report = json.loads(Path(report_path).read_text(encoding='utf-8-sig'))
        except (ValueError,OSError) as exc: report = {'reason':'Unreadable category receipt: '+str(exc)}
    error = report.get('error') or {}
    if not isinstance(error,dict): error = {}
    fatal = (report.get('source_unchanged') is False or error.get('errno') in (errno.ENOSPC,errno.EROFS,errno.ENOMEM)
             or error.get('winerror') in (8,14,21,39,112))
    gaps = bool(code or report.get('has_gaps') or report.get('status') in ('failed','unsupported_layout','not_available')
                or any(k not in ('decoded','empty_supported_object','missing_metadata') and v
                       for k,v in report.get('object_status_counts',{}).items()))
    if code:
        try: output_probe(package)
        except OSError as exc:
            fatal=True; error.update(message=str(exc),operation='output_health_probe')
    row=dict(category=category,exit_code=code,status='failed' if fatal else 'partial' if gaps else 'completed',
             fatal=fatal,reason=report.get('reason') or error.get('message') or
             ('Category did not finish; independent categories will continue' if code else None),
             report_path=str(report_path) if report_path else None,error=error,
             object_status_counts=report.get('object_status_counts',{}))
    stages.append(row); atomic_json(path,stages)
    event('category_outcome',severity='error' if fatal else 'warning' if gaps else 'info',**row)
    if gaps: print(category+': '+str(row['reason'] or 'Some objects were not recovered; see object diagnostics'),flush=True)
    return 1 if fatal else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',required=True,type=Path)
    parser.add_argument('--category',required=True)
    parser.add_argument('--code',required=True,type=int)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    raise SystemExit(record(args.package,args.category,args.code,args.report))
