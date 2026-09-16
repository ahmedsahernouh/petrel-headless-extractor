# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Offline standalone launcher: verify bundle, extract, audit receipts and run QC."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import traceback
import uuid
import warnings
from datetime import datetime
from pathlib import Path

import petrel_geoscience_tools as g
import petrel_progress as progress

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numpy', 'lasio', 'openpyxl', 'pandas', 'shapefile', 'zmapio', 'zfpy', 'pyzgy', 'segyio', 'matplotlib', 'PIL')


def preflight():
    if not Path(sys.executable).resolve().is_relative_to(ROOT / 'runtime'):
        raise g.InputError('Use the bundled runtime through GeoViewer_data_extractor.bat')
    manifest = g.read_json(ROOT / '00_manifest/toolkit_files.json')
    if manifest.get('launcher'):
        launcher=ROOT.parent/manifest['launcher']['path']
        if not launcher.is_file() or g.sha256(launcher)!=manifest['launcher']['sha256']:
            raise g.InputError('Main BAT integrity failed; extract the complete release ZIP')
    with progress.hash_batch('Checking bundled files', [g.contained_file(ROOT, r['path']) for r in manifest['files']]):
        for row in manifest['files']:
            path = g.contained_file(ROOT, row['path'])
            if path.stat().st_size != row['size_bytes'] or g.sha256(path) != row['sha256']:
                raise g.InputError('Bundle integrity failed: ' + row['path'])
    modules = {}
    for name in MODULES:
        with warnings.catch_warnings():
            # Local files do not use the optional authenticated SeismicStore
            # backend. Suppress only its exact missing-module warning; report
            # the absent capability explicitly below. Other warnings stay visible.
            warnings.filterwarnings('ignore',
                message="^seismic store access is not available: No module named 'sdglue'$",
                category=UserWarning, module=r'^openzgy\.impl\.file$')
            module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(ROOT / 'runtime'):
            raise g.InputError('Dependency escaped bundled runtime: ' + name)
        modules[name] = str(path.relative_to(ROOT))
    numpy = importlib.import_module('numpy')
    zfpy = importlib.import_module('zfpy')
    sample = numpy.arange(64, dtype=numpy.float32).reshape(4, 4, 4)
    decoded = zfpy.decompress_numpy(zfpy.compress_numpy(sample))
    if not numpy.array_equal(sample, decoded):
        raise g.InputError('Bundled ZFP compression round-trip failed')
    for path in sys.path:
        if path and not Path(path).resolve().is_relative_to(ROOT):
            raise g.InputError('Python search path escaped package: ' + path)
    return {'status':'ready', 'version':manifest['version'], 'python':sys.version.split()[0],
            'files_verified':len(manifest['files']), 'dependencies':modules,
            'runtime':str(Path(sys.executable)), 'external_python_used':False,
            'petrel_required':False, 'internet_required':False,
            'zfp_round_trip':'passed',
            'optional_capabilities':{'seismicstore_cloud':{
                'status':'not_configured','required_for_local_files':False,
                'reason':'Cloud SeismicStore requires its separate vendor client and authentication.'}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--project-file')
    parser.add_argument('--output-root')
    parser.add_argument('--mode', choices=['inventory','copy','convert'], default='convert')
    parser.add_argument('--report-only', action='store_true', help='Full report/inventory and temporary previews; no retained dataset conversion')
    parser.add_argument('--full-hash', action='store_true', help='Full seismic SHA-256; off by default')
    parser.add_argument('--petrel-version', default='unknown')
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    if args.report_only: args.mode = 'inventory'
    run = None
    diagnostics = None
    context = {}
    report_path = None
    display = progress.ConsoleProgress(stages=1 if args.check else 12).start()
    success = False
    try:
        display.message('Checking bundled runtime and file hashes...', flush=True)
        doctor = preflight()
        display.message('Dependencies ready. ZFP compression check passed.', flush=True)
        display.message('Optional cloud SeismicStore: not configured (not needed for local files).', flush=True)
        if args.check:
            display.message(json.dumps(doctor, indent=2)); success = True; return 0
        source = g.path_arg({'project_file':args.project_file}, 'project_file')
        if source.suffix.lower() != '.pet' or not source.with_suffix('.ptd').is_dir():
            raise g.InputError('Exact .pet file and matching .ptd directory required')
        if not args.output_root:
            raise g.InputError('Output root required')
        output = Path(args.output_root).resolve()
        if output.is_relative_to(source.parent) or any(p.lower().endswith(('.ptd','.pet')) for p in output.parts):
            raise g.InputError('Output root must be outside the source project and native stores')
        name = re.sub(r'[^a-zA-Z0-9_-]+','_',args.label or source.stem).strip('_') or 'project'
        name = name[:28]
        stem = name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
        report_path = output / (stem + '_REPORT.html')
        run = output / (stem + '_data')
        run.mkdir(parents=True, exist_ok=False)
        from geoviewer_diagnostics import Diagnostics
        from geoviewer_metadata import inspect_project
        from geoviewer_delivery import partial_report, publish_exports, decorate
        log_path=output/(stem+'_LOG.txt');event_path=output/(stem+'_EVENTS.jsonl')
        diagnostics=Diagnostics(log_path,event_path)
        diagnostics.event('started',request=vars(args),preflight=doctor)
        bootstrap=os.environ.get('GEOVIEWER_BOOTSTRAP_LOG')
        if bootstrap:
            diagnostics.event('bootstrap_log',path=bootstrap)
            try:
                (run/'BOOTSTRAP_LOG.txt').write_text(Path(bootstrap).read_text(encoding='utf-8-sig',errors='replace'),encoding='utf-8')
            except OSError as exc:diagnostics.event('bootstrap_log_copy_unavailable',reason=str(exc))
        context=inspect_project(source)
        diagnostics.event('native_preflight',layout=context['layout'],saved_version=context['saved_version'],findings=context['findings'])
        partial_report(report_path,context,'Extraction in progress','Metadata inventory is ready. Converted files and final QC are not ready yet.',log_path)
        display.message('REPORT (updates as extraction completes): '+str(report_path),flush=True)
        g.write_json(run/'preflight.json',doctor)
        g.write_json(run/'request.json',vars(args))
        common = {'petrel_version':args.petrel_version,'version_scope':'Standalone external extraction; source release unverified unless independently established'}
        display.message('Extracting supported project evidence; source files stay unchanged.', flush=True)
        extraction = g.dispatch('extract_portable_project', {**common,'project_file':str(source),'output_dir':str(run/'extraction'),'companion_mode':args.mode,'report_only':args.report_only,'reference_seismic':True,'timeout_seconds':7200})
        progress.phase(10, 'Extraction receipt and source hash verification')
        audit = g.verify_receipt(extraction)
        if audit['status'] != 'passed':
            raise RuntimeError('Extraction receipt failed: ' + repr(audit))
        package = extraction['summary']['export_package']
        display.message('Checking package hashes, well names, logs and trajectory evidence...', flush=True)
        progress.phase(11, 'Package quality control')
        qc = g.dispatch('qc_data_package', {**common,'export_package':package,'output_dir':str(run/'qc')})
        progress.phase(12, 'Final QC receipt verification')
        qc_audit = g.verify_receipt(qc)
        if qc_audit['status'] != 'passed':
            raise RuntimeError('QC receipt failed: ' + repr(qc_audit))
        from petrel_project_seismic import convert_project, deliver_report
        inventory = g.read_json(Path(package)/'01_project_metadata/project_seismic_inventory.json')
        inventory['full_seismic_hash']=args.full_hash
        def update_report(complete=False):
            g.write_json(run/'seismic_results.json',inventory)
            deliver_report(Path(package)/'PROJECT_REPORT.html',report_path,inventory,complete)
        update_report()
        display.message('FULL REPORT (ready now): ' + str(report_path),flush=True)
        progress.phase(12,'Project seismic conversion and report completion')
        enabled=not args.report_only and args.mode=='convert'
        with progress.keep_stage(12):
            convert_project(inventory,run/'seismic',enabled,args.full_hash,on_update=update_report)
        update_report(complete=True)
        progress.phase(12,'Publishing converted-file index and final report')
        destination=output/(stem+'_EXPORTS')
        export_index=publish_exports(package,inventory,destination) if enabled else None
        workflow_receipt=Path(package)/'01_project_metadata/workflow_recovery.json'
        if workflow_receipt.is_file():context['workflows']=g.read_json(workflow_receipt)
        decorate(report_path,context,export_index,destination,log_path,event_path)
        for row in inventory.get('objects',[]):
            diagnostics.event('seismic_outcome',object_id=row['id'],status=row['status'],reason=row.get('reason'),result=row.get('result'))
        for evidence in ('07_workflows_reports/native_recovery/native_recovery_report.json','07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'):
            path=Path(package)/evidence
            if path.is_file():
                for row in g.read_json(path).get('objects',[]):diagnostics.event('native_object_outcome',**row)
        result = {'status':'passed','toolkit_version':doctor['version'],'elapsed_seconds':round(display.elapsed, 3),'extraction':extraction,
                  'extraction_audit':audit,'qc':qc,'qc_audit':qc_audit,
                  'source_mutated':False,'petrel_process_launched':False,
                  'report_included':True,'dataset_conversion_enabled':not args.report_only and args.mode=='convert',
                  'full_report':str(report_path),'seismic':inventory,'full_seismic_hash':args.full_hash,
                  'scientific_acceptance':'not_established','project_context':context,
                  'file_index':str(destination/'FILE_INDEX.csv') if enabled else None,
                  'process_log':str(log_path),'structured_events':str(event_path)}
        g.write_json(run/'RUN_RESULT.json',result)
        diagnostics.event('completed',elapsed_seconds=round(display.elapsed,3),package=package,report=str(report_path),qc=qc_audit)
        (run/'RUN_LOG.txt').write_text('Elapsed: '+progress.duration(display.elapsed)+'\nDetailed log: '+str(log_path)+'\nStructured events: '+str(event_path)+'\n',encoding='utf-8')
        success = True
        display.message('SUCCESS: report and package QC completed. See per-dataset conversion and integrity results.', flush=True)
        display.message('Run folder: ' + str(run))
        display.message('HTML report: ' + str(report_path))
        display.message('Process log: ' + str(log_path))
        if enabled:display.message('Extracted file index: ' + str(destination/'FILE_INDEX.csv'))
        display.message('Seismic outcomes: ' + json.dumps(inventory.get('counts',{})))
        display.message('QC report: ' + qc['report_path'])
        display.message('Read QC findings and unresolved CRS/units before using the data.')
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        failure = {'status':'failed','error':str(exc),'elapsed_seconds':round(display.elapsed, 3),'traceback':traceback.format_exc()}
        if run is not None and run.exists():
            g.write_json(run/'RUN_RESULT.json',failure)
            (run/'RUN_LOG.txt').write_text(failure['traceback'],encoding='utf-8')
            if diagnostics:diagnostics.event('failed',**failure)
            if report_path:
                from geoviewer_delivery import partial_report
                partial_report(report_path,context,'Extraction failed — partial metadata report',str(exc),log_path if diagnostics else None)
                display.message('PARTIAL REPORT: '+str(report_path))
            display.message('Failure evidence: ' + str(run/'RUN_RESULT.json'))
        display.message('ERROR: ' + str(exc),file=sys.stderr)
        return 130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        display.close(success=success)
        if diagnostics:diagnostics.close()


if __name__ == '__main__':
    raise SystemExit(main())
