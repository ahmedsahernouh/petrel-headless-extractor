# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
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
import geoviewer_io as gio

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numpy', 'lasio', 'openpyxl', 'pandas', 'shapefile', 'zmapio', 'zfpy', 'pyzgy', 'segyio', 'matplotlib', 'PIL')


def preflight():
    if not Path(sys.executable).resolve().is_relative_to(ROOT / 'runtime'):
        raise g.InputError('Use the bundled runtime through FieldViewer_data_extractor.bat')
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


def plan_run_paths(output, label, *, timestamp=None, token=None, project_file=None, companion_mode='convert'):
    """Budget native object paths before source hashing or output creation.

    Only the display label is shortened. The run timestamp, random suffix and
    full native object identities are retained. With an exact project, inventory
    source-relative snapshot/companion destinations without reading payloads.
    """
    from petrel_native_recovery import object_output_plan, QCError, TYPES, _object_relative_paths

    output = Path(output)
    if not output.is_absolute():
        raise g.InputError('Path planning requires a resolved absolute output root')
    timestamp = timestamp or datetime.now().strftime('%Y%m%d_%H%M%S')
    token = token or uuid.uuid4().hex[:6]
    if not re.fullmatch(r'\d{8}_\d{6}', timestamp) or not re.fullmatch(r'[a-f0-9]{6}', token):
        raise g.InputError('Invalid run timestamp or identity suffix')
    name = (re.sub(r'[^a-zA-Z0-9_-]+', '_', label).strip('_') or 'project')[:28]
    suffix = '_' + timestamp + '_' + token
    original_stem = name + suffix
    # Keep this width synchronized with new_export_package.ps1. It is a
    # reservation, not the identity of the package later created by PowerShell.
    package_placeholder = 'p_00000000_000000_000000'
    from geoviewer_output_paths import source_reservations, reservation_failure
    reservations, inventoried_files = source_reservations(project_file, companion_mode) if project_file else ([], 0)
    last_error = None
    for length in range(len(name), 0, -1):
        stem = name[:length] + suffix
        native = output / (stem + '_data') / 'extraction/package' / package_placeholder / 'native_data'
        # Reserve the longest delivery category with its compact identity name
        # and temporary suffix, before creating any output. Actual display labels
        # are fitted at delivery time without dropping identity from the index.
        failure = reservation_failure(output / (stem + '_EXPORTS'),
            [Path('polygon_missing_vertices') / ('f'*64+'.csv.00000000.partial')])
        if failure:
            last_error = 'Readable export paths exceed Windows profile: ' + repr(failure)
            continue
        failure = reservation_failure(native.parent, reservations)
        if failure:
            last_error = 'Source-relative output exceeds Windows profile: ' + repr(failure)
            continue
        plans = {}
        try:
            for kind in TYPES:
                plans[kind] = object_output_plan(native, {
                    'object_id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',
                    'name': 'objectxx', 'blob_type': kind})
        except QCError as exc:
            last_error = exc
            continue
        native_relatives=[]
        for kind, plan in plans.items():
            native_relatives.extend(Path(plan['final_name'])/p for p in _object_relative_paths(kind))
            staging=plan['staging_name'] or '.g000000.partial'
            native_relatives.extend(Path(staging)/p for p in _object_relative_paths(kind, staging=True))
        failure=reservation_failure(native,native_relatives)
        if failure:
            last_error='Native output exceeds Windows UTF-16 path profile: '+repr(failure)
            continue
        return dict(stem=stem, original_stem=original_stem,
                    label_shortened=stem != original_stem,
                    label_characters_removed=len(original_stem)-len(stem),
                    output_root=str(output), planned_native_root=str(native),
                    object_path_plans=plans,
                    source_files_inventoried=inventoried_files, source_path_reservations=len(reservations),
                    scope='Native object paths and, when supplied, source-relative native snapshots, '
                          'companions, supported derivatives, readable exports and shallow quarantine; Windows bounded-path profile')
    raise g.InputError('Output path preflight failed before extraction. Even the shortest run label '
                       'does not fit. Choose a shorter output root. ' + str(last_error))


def elapsed_budgets(args):
    """Validate finite defaults, then map explicit completion mode to ``None``."""
    from geoviewer_lanes import seconds
    values=(args.native_budget_seconds,args.companion_budget_seconds,args.seismic_budget_seconds)
    for value in values:
        seconds(value)
    return (None,None,None) if args.run_to_completion else values


def publish_run_access(report_path, destination=None, *, has_exports=False,
                       label='', status='in_progress', display, diagnostics=None):
    """Expose each available report without making desktop access an extraction gate."""
    if report_path is None or not Path(report_path).is_file():
        return None
    try:
        from geoviewer_result_access import publish_result_access
        access = publish_result_access(report_path, destination if has_exports else None,
                                       label=label, status=status)
    except Exception as exc:
        access = {'entry_page': None, 'desktop_shortcuts': [],
                  'warnings': ['Result shortcuts unavailable: ' + str(exc)]}
    if access.get('entry_page'):
        display.message('OPEN RESULTS: ' + str(access['entry_page']), flush=True)
    for shortcut in access.get('desktop_shortcuts', []):
        display.message('DESKTOP SHORTCUT: ' + str(shortcut), flush=True)
    for warning in access.get('warnings', []):
        display.message('Result access warning: ' + str(warning), flush=True)
    if diagnostics:
        diagnostics.event('result_access', severity='warning' if access.get('warnings') else 'info',
                          result_status=status, access=access)
    return access


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--project-file')
    parser.add_argument('--output-root')
    parser.add_argument('--mode', choices=['inventory','copy','convert'], default='convert')
    parser.add_argument('--report-only', action='store_true', help='Full report/inventory and temporary previews; no retained dataset conversion')
    hashing = parser.add_mutually_exclusive_group()
    hashing.add_argument('--full-hash', dest='full_hash', action='store_true', default=True, help='Full seismic SHA-256 (default)')
    hashing.add_argument('--no-full-hash', dest='full_hash', action='store_false', help='Skip full seismic hashes; retain numerical QC')
    parser.add_argument('--petrel-version', default='unknown')
    parser.add_argument('--seismic-reference-map',help='Exact project-bound JSON relocation map; user-declared associations remain labelled')
    parser.add_argument('--label', default='')
    parser.add_argument('--native-budget-seconds', type=int, default=7200, help='Cumulative native numeric decoder budget')
    parser.add_argument('--companion-budget-seconds', type=int, default=600, help='Independent optional companion-stage budget')
    parser.add_argument('--seismic-budget-seconds', type=int, default=7200, help='Independent seismic conversion/integrity budget')
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument('--run-to-completion', dest='run_to_completion', action='store_true', default=True,
                        help='Finish the project without an elapsed-time cutoff (default); cancellation and safety limits remain active')
    timing.add_argument('--bounded', dest='run_to_completion', action='store_false',
                        help='Apply the independent native, companion and seismic stage budgets; no competing outer deadline')
    return parser


def main():
    args = argument_parser().parse_args()
    if args.report_only: args.mode = 'inventory'
    run = None
    diagnostics = None
    context = {}
    report_path = None
    display = progress.ConsoleProgress(stages=1 if args.check else 12).start()
    success = False
    end_status = None
    stages = []
    incremental = None
    destination = None
    enabled = not args.report_only and args.mode == 'convert'
    try:
        native_budget,companion_budget,seismic_budget=elapsed_budgets(args)
        display.message('Runtime policy: ' + ('run to completion; cancellation remains available.'
                        if args.run_to_completion else 'independent stage budgets; no outer extraction deadline.'), flush=True)
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
        path_plan = plan_run_paths(output, args.label or source.stem, project_file=source, companion_mode=args.mode)
        stem = path_plan['stem']
        if path_plan['label_shortened']:
            display.message('Shortened the run folder label by ' + str(path_plan['label_characters_removed']) +
                            ' characters to leave room for project and companion outputs. Project and object identities are retained.', flush=True)
        report_path = output / (stem + '_REPORT.html')
        run = output / (stem + '_data')
        run.mkdir(parents=True, exist_ok=False)
        gio.output_probe(run)
        from geoviewer_diagnostics import Diagnostics
        from geoviewer_metadata import inspect_project
        from geoviewer_delivery import partial_report, publish_exports, decorate, diagnostics_section
        log_path=output/(stem+'_LOG.txt');event_path=output/(stem+'_EVENTS.jsonl')
        diagnostics=Diagnostics(log_path,event_path)
        from geoviewer_support import register_run, environment_info
        register_run(diagnostics,run,report_path)
        diagnostics.event('environment',**environment_info(run))
        diagnostics.event('started',request=vars(args),preflight=doctor)
        diagnostics.event('output_path_plan', **path_plan)
        gio.atomic_json(run/'OUTPUT_PATH_PLAN.json', path_plan)
        def optional(category, action):
            try:
                return action()
            except Exception as exc:
                if gio.systemic(exc): raise
                gio.output_probe(run)
                details=gio.error_details(exc,category=category)
                stages.append(dict(category=category,status='partial',reason=str(exc)))
                diagnostics.event('optional_operation_failed',severity='error',**details)
                display.message(category+' incomplete: '+str(exc)+'; continuing independent work.',flush=True)
                return None
        bootstrap=os.environ.get('GEOVIEWER_BOOTSTRAP_LOG')
        if bootstrap:
            diagnostics.event('bootstrap_log',path=bootstrap)
            try:
                (run/'BOOTSTRAP_LOG.txt').write_text(Path(bootstrap).read_text(encoding='utf-8-sig',errors='replace'),encoding='utf-8')
            except OSError as exc:diagnostics.event('bootstrap_log_copy_unavailable',reason=str(exc))
        context=inspect_project(source)
        diagnostics.event('native_preflight',layout=context['layout'],saved_version=context['saved_version'],findings=context['findings'])
        optional('initial_report',lambda:partial_report(report_path,context,'Extraction in progress','Metadata inventory is ready. Converted files and final QC are not ready yet.',log_path))
        display.message('REPORT (updates as extraction completes): '+str(report_path),flush=True)
        publish_run_access(report_path, label=args.label or source.stem,
                           status='in_progress', display=display, diagnostics=diagnostics)
        g.write_json(run/'preflight.json',doctor)
        g.write_json(run/'request.json',vars(args))
        common = {'petrel_version':args.petrel_version,'version_scope':'Standalone external extraction; source release unverified unless independently established'}
        display.message('Extracting supported project evidence; source files stay unchanged.', flush=True)
        from geoviewer_incremental import IncrementalDelivery
        destination=output/(stem+'_EXPORTS')
        def checkpoint():
            return IncrementalDelivery(run,report_path,destination,context,log_path,event_path,enabled=enabled)
        incremental=checkpoint().start()
        try:
            extraction = g.dispatch('extract_portable_project', {**common,'project_file':str(source),'output_dir':str(run/'extraction'),'companion_mode':args.mode,'report_only':args.report_only,'reference_seismic':True,
                'timeout_seconds':None,'run_to_completion':args.run_to_completion,
                'native_budget_seconds':native_budget,'companion_budget_seconds':companion_budget})
        finally:
            incremental.stop()
        # No background writer remains when full report publication begins.
        previous=incremental
        incremental=checkpoint()
        incremental.findings.extend(previous.findings)
        incremental.worker_errors.extend(previous.worker_errors)
        progress.phase(10, 'Extraction receipt and source hash verification')
        audit = g.verify_receipt(extraction)
        if audit['status'] != 'passed':
            raise RuntimeError('Extraction receipt failed: ' + repr(audit))
        package = extraction['summary']['export_package']
        optional('native_export_checkpoint',lambda:incremental.refresh(package=package))
        stage_path=Path(package)/'01_project_metadata/pipeline_stages.json'
        if stage_path.is_file(): stages.extend(g.read_json(stage_path))
        display.message('Checking package hashes, well names, logs and trajectory evidence...', flush=True)
        progress.phase(11, 'Package quality control')
        qc = g.dispatch('qc_data_package', {**common,'export_package':package,'output_dir':str(run/'qc')})
        progress.phase(12, 'Final QC receipt verification')
        qc_audit = g.verify_receipt(qc)
        if qc_audit['status'] != 'passed':
            raise RuntimeError('QC receipt failed: ' + repr(qc_audit))
        from petrel_project_seismic import convert_project, deliver_report
        inventory_path=Path(package)/'01_project_metadata/project_seismic_inventory.json'
        inventory = g.read_json(inventory_path) if inventory_path.is_file() else {'objects':[],'counts':{}}
        if args.seismic_reference_map:
            from petrel_project_seismic import apply_reference_map
            apply_reference_map(inventory,args.seismic_reference_map)
            diagnostics.event('seismic_reference_mapping',map_path=args.seismic_reference_map,
                              workload=inventory['workload'],identity_scope='user_declared; equivalence not established')
        inventory['full_seismic_hash']=args.full_hash
        base_report=Path(package)/'PROJECT_REPORT.html'
        if not base_report.is_file():
            base_report=run/'REPORT_BASE.html'
            partial_report(base_report,context,'Partial report','The full visual report was not generated. Accepted exports and the metadata inventory remain available.',log_path)
        def update_report(complete=False):
            gio.atomic_json(run/'seismic_results.json',inventory)
            optional('report_update',lambda:deliver_report(base_report,report_path,inventory,complete))
            optional('converted_export_checkpoint',lambda:incremental.refresh(package=package,inventory=inventory))
        update_report()
        if report_path.is_file():display.message('REPORT (available; conversion results still updating): ' + str(report_path),flush=True)
        progress.phase(12,'Project seismic conversion and report completion')
        with progress.keep_stage(12):
            convert_project(inventory,run/'seismic',enabled,args.full_hash,on_update=update_report,
                            budget_seconds=seismic_budget,run_to_completion=args.run_to_completion)
        update_report(complete=True)
        progress.phase(12,'Publishing converted-file index and final report')
        export_index=optional('export_index',lambda:publish_exports(package,inventory,destination,
            checkpoint_index=incremental.current_index)) if enabled else None
        if export_index is not None and report_path.is_file():
            from geoviewer_incremental import START,END
            text=report_path.read_text(encoding='utf-8')
            if START in text and END in text:
                gio.atomic_text(report_path,text[:text.index(START)]+text[text.index(END)+len(END):])
        if export_index is not None:
            optional('final_export_index_page',lambda:gio.atomic_text(destination/'INCREMENTAL_INDEX.html',incremental._html(export_index)))
        interoperability = None
        if export_index is not None or not enabled:
            from geoviewer_contract_publication import publish_preparation
            interoperability = optional('interoperability_contract', lambda:publish_preparation(
                Path(package), destination, run, inventory, file_index=export_index, conversion_enabled=enabled))
        workflow_receipt=Path(package)/'01_project_metadata/workflow_recovery.json'
        if workflow_receipt.is_file():context['workflows']=g.read_json(workflow_receipt)
        preparation_report = interoperability.get('human_report',{}).get('path') if interoperability else None
        optional('report_decoration',lambda:decorate(report_path,context,export_index,destination,log_path,event_path,preparation_report))
        for row in inventory.get('objects',[]):
            diagnostics.event('seismic_outcome',object_id=row['id'],status=row['status'],reason=row.get('reason'),result=row.get('result'))
        for evidence in ('07_workflows_reports/native_recovery/native_recovery_report.json','07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'):
            path=Path(package)/evidence
            if path.is_file():
                for row in g.read_json(path).get('objects',[]):
                    diagnostics.event('native_object_outcome',**row)
        seismic_gaps=any((enabled and row.get('association')!='unlinked_companion' and
                         row.get('status') in ('unavailable','conversion_failed','unsupported','missing','interrupted_budget','not_attempted_budget'))
                        or (args.full_hash and row.get('hash_status')=='not_completed')
                        for row in inventory.get('objects',[]))
        if seismic_gaps:stages.append(dict(category='seismic',status='partial',reason='Some seismic objects could not be converted; consult their individual availability and conversion reasons.'))
        if export_index and export_index.get('findings'):stages.append(dict(category='delivery',status='partial',reason='; '.join(export_index['findings'])))
        if any(e['event'] in ('preview_failed','child_log_lost','malformed_child_event') for e in diagnostics.problems):
            stages.append(dict(category='optional_output_or_diagnostics',status='partial',reason='A preview or diagnostic stream was incomplete; details are in the diagnostic summary.'))
        optional('run_log_pointer',lambda:gio.atomic_text(run/'RUN_LOG.txt','Elapsed: '+progress.duration(display.elapsed)+'\nDetailed log: '+str(diagnostics.text_path)+'\nStructured events: '+str(diagnostics.events_path)+'\n'))
        end_status='completed_with_gaps' if any(row.get('status') in ('failed','partial') for row in stages) else 'completed'
        optional('diagnostic_report',lambda:diagnostics_section(report_path,end_status,stages,diagnostics.summary_path,diagnostics.text_path,diagnostics.events_path))
        if any(row.get('status') in ('failed','partial') for row in stages):end_status='completed_with_gaps'
        result_access = publish_run_access(report_path, destination,
            has_exports=bool((export_index or {}).get('files') or incremental.current_index.get('files')),
            label=args.label or source.stem, status=end_status, display=display, diagnostics=diagnostics)
        result = {'status':end_status,'toolkit_version':doctor['version'],'elapsed_seconds':round(display.elapsed, 3),'extraction':extraction,
                  'extraction_audit':audit,'qc':qc,'qc_audit':qc_audit,
                  'source_mutated':False,'petrel_process_launched':False,
                  'report_included':report_path.is_file(),'dataset_conversion_enabled':not args.report_only and args.mode=='convert',
                  'full_report':str(report_path),'seismic':inventory,'full_seismic_hash':args.full_hash,
                  'result_access':result_access,
                  'scientific_acceptance':'not_established','project_context':context,
                  'interoperability_contract':interoperability,
                  'file_index':str(destination/'FILE_INDEX.csv') if export_index is not None or incremental.current_index.get('files') else None,
                  'process_log':str(diagnostics.text_path),'structured_events':str(diagnostics.events_path),
                  'diagnostic_summary':str(diagnostics.summary_path),'category_outcomes':stages}
        if any(row.get('status') in ('failed','partial') for row in stages):end_status='completed_with_gaps'
        result['status']=end_status
        gio.atomic_json(run/'RUN_RESULT.json',result)
        diagnostics.event('completed',status=end_status,elapsed_seconds=round(display.elapsed,3),package=package,report=str(report_path),qc=qc_audit)
        success = True
        display.message('COMPLETED WITH GAPS: validated exports are available; see report diagnostics.' if end_status=='completed_with_gaps' else 'SUCCESS: report and package QC completed. See per-dataset conversion and integrity results.', flush=True)
        display.message('Run folder: ' + str(run))
        display.message('HTML report: ' + str(report_path) if report_path.is_file() else 'HTML report unavailable; open the diagnostic summary: '+str(diagnostics.summary_path))
        display.message('Process log: ' + str(log_path))
        if export_index is not None:display.message('Extracted file index: ' + str(destination/'FILE_INDEX.csv'))
        display.message('Seismic outcomes: ' + json.dumps(inventory.get('counts',{})))
        display.message('QC report: ' + qc['report_path'])
        display.message('Read QC findings and unresolved CRS/units before using the data.')
        return 10 if end_status=='completed_with_gaps' else 0
    except (Exception,KeyboardInterrupt) as exc:
        end_status='cancelled' if isinstance(exc,KeyboardInterrupt) else 'failed'
        failure = {'status':end_status,'error':gio.error_details(exc),'elapsed_seconds':round(display.elapsed, 3),'traceback':traceback.format_exc(),
                   'source_integrity':'See completed receipts; uncompleted checks are not a claim of unchanged sources.'}
        if diagnostics:diagnostics.event(end_status,severity='error',**failure)
        if run is not None and run.exists():
            checkpoint_stopped = incremental is None
            if incremental is not None:
                try:
                    incremental.stop()
                    checkpoint_stopped = True
                    # Collect any final committed object after an orderly child stop.
                    previous=incremental
                    incremental=checkpoint()
                    incremental.findings.extend(previous.findings)
                    incremental.worker_errors.extend(previous.worker_errors)
                    saved_inventory=g.read_json(run/'seismic_results.json') if (run/'seismic_results.json').is_file() else None
                    retained=incremental.refresh(inventory=saved_inventory,stopped_pipeline=True)
                    failure['file_index']=str(destination/'FILE_INDEX.csv') if retained.get('files') else None
                except Exception as secondary:
                    if diagnostics:diagnostics.event('checkpoint_retention_failed',severity='error',**gio.error_details(secondary))
            try:
                gio.atomic_json(run/'RUN_RESULT.json',failure)
                gio.atomic_text(run/'RUN_LOG.txt',failure['traceback'])
            except OSError as secondary:
                if diagnostics:diagnostics.event('failure_receipt_unavailable',severity='error',**gio.error_details(secondary))
                display.message('Could not write run receipt: '+str(secondary),file=sys.stderr)
            if report_path and checkpoint_stopped:
                from geoviewer_delivery import partial_report
                try:
                    partial_report(report_path,context,'Extraction '+end_status+' — retained partial report',str(exc),diagnostics.text_path if diagnostics else None,preserve=True)
                    display.message('PARTIAL REPORT: '+str(report_path))
                except OSError as secondary:
                    if diagnostics:diagnostics.event('failure_report_unavailable',severity='error',**gio.error_details(secondary))
                publish_run_access(report_path, destination,
                    has_exports=bool(incremental is not None and incremental.current_index.get('files')),
                    label=args.label or source.stem, status=end_status, display=display, diagnostics=diagnostics)
            display.message('Failure evidence: ' + str(run/'RUN_RESULT.json'))
        display.message('ERROR: ' + str(exc),file=sys.stderr)
        if not diagnostics: traceback.print_exc()
        return 130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if incremental is not None:
            try:incremental.stop()
            except Exception as exc:
                if diagnostics:diagnostics.event('checkpoint_stop_failed',severity='error',**gio.error_details(exc))
        display.close(success=success,status=end_status.replace('_',' ') if end_status else None)
        if diagnostics:
            from geoviewer_support import finish_run
            finish_run(diagnostics,run,report_path)


if __name__ == '__main__':
    raise SystemExit(main())
