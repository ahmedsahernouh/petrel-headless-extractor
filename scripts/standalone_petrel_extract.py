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

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numpy', 'lasio', 'openpyxl', 'pandas', 'shapefile', 'zmapio', 'zfpy', 'pyzgy')


def preflight():
    if not Path(sys.executable).resolve().is_relative_to(ROOT / 'runtime'):
        raise g.InputError('Use the bundled runtime through run_portable_petrel_extract.bat')
    manifest = g.read_json(ROOT / '00_manifest/toolkit_files.json')
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
    parser.add_argument('--petrel-version', default='unknown')
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    run = None
    try:
        print('Checking bundled runtime and file hashes...', flush=True)
        doctor = preflight()
        print('Dependencies ready. ZFP compression check passed.', flush=True)
        print('Optional cloud SeismicStore: not configured (not needed for local files).', flush=True)
        if args.check:
            print(json.dumps(doctor, indent=2)); return 0
        source = g.path_arg({'project_file':args.project_file}, 'project_file')
        if source.suffix.lower() != '.pet' or not source.with_suffix('.ptd').is_dir():
            raise g.InputError('Exact .pet file and matching .ptd directory required')
        if not args.output_root:
            raise g.InputError('Output root required')
        output = Path(args.output_root).resolve()
        if output.is_relative_to(source.parent) or any(p.lower().endswith(('.ptd','.pet')) for p in output.parts):
            raise g.InputError('Output root must be outside the source project and native stores')
        name = re.sub(r'[^a-zA-Z0-9_-]+','_',args.label or source.stem).strip('_') or 'project'
        run = output / (name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
        run.mkdir(parents=True, exist_ok=False)
        g.write_json(run/'preflight.json',doctor)
        g.write_json(run/'request.json',vars(args))
        common = {'petrel_version':args.petrel_version,'version_scope':'Standalone external extraction; source release unverified unless independently established'}
        print('Extracting supported project evidence; source files stay unchanged.', flush=True)
        extraction = g.dispatch('extract_portable_project', {**common,'project_file':str(source),'output_dir':str(run/'extraction'),'companion_mode':args.mode})
        audit = g.verify_receipt(extraction)
        if audit['status'] != 'passed':
            raise RuntimeError('Extraction receipt failed: ' + repr(audit))
        package = extraction['summary']['export_package']
        print('Checking package hashes, well names, logs and trajectory evidence...', flush=True)
        qc = g.dispatch('qc_data_package', {**common,'export_package':package,'output_dir':str(run/'qc')})
        qc_audit = g.verify_receipt(qc)
        if qc_audit['status'] != 'passed':
            raise RuntimeError('QC receipt failed: ' + repr(qc_audit))
        result = {'status':'passed','toolkit_version':doctor['version'],'extraction':extraction,
                  'extraction_audit':audit,'qc':qc,'qc_audit':qc_audit,
                  'source_mutated':False,'petrel_process_launched':False,
                  'scientific_acceptance':'not_established'}
        g.write_json(run/'RUN_RESULT.json',result)
        (run/'RUN_LOG.txt').write_text('Extraction and QC execution passed.\nSource files unchanged.\nPackage: '+package+'\nDashboard: '+extraction['summary']['dashboard']+'\nQC: '+qc['report_path']+'\n',encoding='utf-8')
        print('SUCCESS: extraction, source preservation and package QC execution passed.')
        print('Run folder: ' + str(run))
        print('HTML report: ' + extraction['summary']['dashboard'])
        print('QC report: ' + qc['report_path'])
        print('Read QC findings and unresolved CRS/units before using the data.')
        return 0
    except Exception as exc:
        failure = {'status':'failed','error':str(exc),'traceback':traceback.format_exc()}
        if run is not None and run.exists():
            g.write_json(run/'RUN_RESULT.json',failure)
            (run/'RUN_LOG.txt').write_text(failure['traceback'],encoding='utf-8')
            print('Failure evidence: ' + str(run/'RUN_RESULT.json'))
        print('ERROR: ' + str(exc),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
