"""Single-file, read-only conversions. Outputs are new directories with QC receipts.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Contract: no Petrel/Ocean, no native store mutation, no implicit resampling.
Unsupported metadata stops conversion; partial outputs retain a failure receipt.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import petrel_progress as progress

VERSION = '0.3.0'
ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = [
    dict(id='zgy-to-segy', input='Petrel ZGY binary seismic cube', output='SEG-Y + metadata JSON', status='beta',
         limits='Regular 3D time-domain cube; s/ms/us; integer microsecond interval and millisecond origin; CRS may remain explicitly unknown'),
]



class InputError(ValueError):
    pass


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False)+'\n', encoding='utf-8')


def source_path(value):
    p = Path(value).expanduser().resolve(strict=True)
    if not p.is_file():
        raise InputError('Input must be a file')
    return p


def open_zgy(source):
    with source.open('rb') as stream:
        if stream.read(4) != b'VBS\x00':
            raise InputError('ZGY binary signature not recognized')
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message="^seismic store access is not available: No module named 'sdglue'$")
        from openzgy.api import ZgyReader
    return ZgyReader(str(source))


def zgy_metadata(reader):
    return dict(size=list(reader.size), datatype=str(reader.datatype),
                zstart=float(reader.zstart), zinc=float(reader.zinc),
                zunit_dimension=reader.zunitdim.name, zunit_name=reader.zunitname,
                zunit_factor=float(reader.zunitfactor),
                horizontal_dimension=reader.hunitdim.name, horizontal_unit=reader.hunitname,
                horizontal_factor=float(reader.hunitfactor),
                inline_start=float(reader.annotstart[0]), inline_step=float(reader.annotinc[0]),
                crossline_start=float(reader.annotstart[1]), crossline_step=float(reader.annotinc[1]),
                corners=[list(map(float, p)) for p in reader.corners], crs='unknown')


def exact_integer(value, label, lower, upper):
    if not math.isfinite(value) or abs(value-round(value)) > 1e-6 or not lower <= value <= upper:
        raise InputError(f'{label} ({value}) cannot be represented by this SEG-Y profile. No rounding/resampling was applied.')
    return round(value)


def seismic_plan(meta, options):
    domain = options.get('domain') or meta['zunit_dimension']
    if domain != 'time':
        raise InputError('This release exports time-domain ZGY only. Resolve unknown domain; depth needs a separate validated profile.')
    # Known source dimensions cannot be contradicted by a command-line label.
    if meta['zunit_dimension'] not in ('unknown', 'time'):
        raise InputError('Source declares a non-time domain; relabelling it as time is not conversion.')
    unit = options.get('vertical_unit') or meta['zunit_name'].strip().lower()
    factors = {'s':1000., 'ms':1., 'us':.001}
    if unit not in factors:
        raise InputError('Vertical units unresolved: specify s, ms or us from independent project evidence.')
    if meta['zunit_dimension'] == 'time' and meta['zunit_factor'] > 0:
        if not math.isclose(meta['zunit_factor'], factors[unit]/1000, rel_tol=1e-6):
            raise InputError('Vertical units conflict with the declared source SI unit factor.')
    hunit = options.get('horizontal_unit') or meta['horizontal_unit'].strip().lower()
    aliases = {'metres':'m','meters':'m','metre':'m','meter':'m','feet':'ft','foot':'ft'}
    hunit = aliases.get(hunit, hunit)
    if hunit not in ('m','ft') or meta['horizontal_dimension'] not in ('length','unknown'):
        raise InputError('Horizontal units must be m or ft. Angular/unknown units need resolution; no reprojection is performed.')
    if meta['horizontal_dimension'] == 'length' and meta['horizontal_factor'] > 0:
        if not math.isclose(meta['horizontal_factor'], 1 if hunit=='m' else .3048, rel_tol=1e-6):
            raise InputError('Horizontal units conflict with the source SI unit factor.')
    ni, nx, ns = meta['size']
    if min(ni,nx) < 2 or not 1 <= ns <= 32767 or ni*nx > 2147483647:
        raise InputError('Profile requires at least 2 inlines and 2 crosslines, 1..32767 samples and at most 2^31-1 traces.')
    dt = exact_integer(meta['zinc']*factors[unit]*1000, 'Sample interval in microseconds', 1, 65535)
    delay = exact_integer(meta['zstart']*factors[unit], 'Sample origin in milliseconds', -32768, 32767)
    axes=[]
    for count, start, step, label in [(ni,meta['inline_start'],meta['inline_step'],'inline'),
                                     (nx,meta['crossline_start'],meta['crossline_step'],'crossline')]:
        start=exact_integer(start,label+' start',-2147483648,2147483647)
        step=exact_integer(step,label+' step',1,2147483647)
        exact_integer(start+(count-1)*step,label+' end',-2147483648,2147483647)
        axes.append((start,step))
    corners=np.asarray(meta['corners'],dtype=np.float64)
    if not np.isfinite(corners).all() or not np.allclose(corners[3],corners[1]+corners[2]-corners[0],rtol=0,atol=.001):
        raise InputError('Invalid/non-affine XY geometry')
    di=(corners[1]-corners[0])/(ni-1); dj=(corners[2]-corners[0])/(nx-1)
    if abs(float(np.linalg.det(np.stack([di,dj])))) < 1e-10:
        raise InputError('Degenerate XY geometry; no coordinates will be invented')
    scale=next((s for s in (1000,100) if float(np.max(np.abs(corners)))*s <= 2147483646),None)
    if scale is None:
        raise InputError('XY exceeds coordinate range at required precision (0.01 source unit or better)')
    crs=options.get('crs') or 'unknown'
    if len(crs)>4000:
        raise InputError('CRS text too long; use an identifier or short description')
    return dict(size=[ni,nx,ns], interval_us=dt, origin_ms=delay, domain='time',
                source_vertical_unit=unit, horizontal_unit=hunit, crs=crs,
                crs_verified=False, coordinate_scale=scale, max_coordinate_rounding=.5/scale,
                inline=list(axes[0]),crossline=list(axes[1]),
                expected_bytes=3600+ni*nx*(240+4*ns),
                profile='SEG-Y rev1-compatible, big endian IEEE float32, regular 3D poststack time',
                sample_count_limit=32767, original_acquisition_headers_recovered=False,
                metadata_overrides={k:v for k,v in options.items() if k in ('domain','vertical_unit','horizontal_unit','crs') and v},
                validation_boundary='Numerical/structural QC; receiving-application import and geological validity not established')


def blocks(shape):
    ni,nx,ns=shape
    nj=min(64,nx)
    step_i=max(1,min(64,ni,(64*1024*1024)//(nj*ns*4)))
    for i in range(0,ni,step_i):
        for j in range(0,nx,nj):
            yield i,j,(min(step_i,ni-i),min(nj,nx-j),ns)


def convert_zgy(source, run, options):
    import segyio
    with open_zgy(source) as reader:
        meta=zgy_metadata(reader); plan=seismic_plan(meta,options)
        write_json(run/'source_metadata.json',meta);write_json(run/'conversion_metadata.json',plan)
        if shutil.disk_usage(run).free < plan['expected_bytes'] + 64*1024*1024:
            raise InputError(f"Insufficient disk space; SEG-Y needs {plan['expected_bytes']} bytes plus working space")
        ni,nx,ns=plan['size']; scale=plan['coordinate_scale']
        spec=segyio.spec(); spec.format=5;spec.sorting=2
        # Unstructured allocation avoids allocating an axis array for huge grids.
        spec.tracecount=ni*nx;spec.samples=np.arange(ns,dtype=np.float64)*plan['interval_us']/1000+plan['origin_ms']
        pending=run/'volume.partial.segy';target=run/'volume.segy'
        phase_start=time.monotonic();done=0
        progress.phase(3,'Converting ZGY to SEG-Y')
        with segyio.create(str(pending),spec) as writer:
            crs_ascii=plan['crs'].encode('ascii','replace').decode().replace('\n',' ').replace('\r',' ')
            writer.text[0]=segyio.tools.create_text_header({
                1:'PETREL HEADLESS EXTRACTOR 0.3.0 - https://saherlabs.dev/',
                2:'NEW CUBE EXCHANGE FILE. ORIGINAL ACQUISITION HEADERS NOT RECOVERED.',
                3:'TIME DOMAIN; SAMPLE INTERVAL MICROSECONDS; ORIGIN MILLISECONDS.',
                4:'IEEE FLOAT32 BIG ENDIAN; INLINE 189; CROSSLINE 193; CDP X/Y 181/185.',
                5:f"XY UNITS {plan['horizontal_unit']}; COORDINATE SCALAR {-scale}.",
                6:'CRS (USER DECLARED, NOT VERIFIED): '+crs_ascii[:39],
                7:'SEE conversion_metadata.json FOR COMPLETE CRS AND VALIDATION LIMITS.'})
            writer.bin.update({segyio.BinField.Interval:plan['interval_us'],segyio.BinField.IntervalOriginal:plan['interval_us'],
                               segyio.BinField.Samples:ns,segyio.BinField.SamplesOriginal:ns,segyio.BinField.Format:5,
                               segyio.BinField.SortingCode:2,segyio.BinField.MeasurementSystem:1 if plan['horizontal_unit']=='m' else 2,
                               segyio.BinField.SEGYRevision:256,segyio.BinField.TraceFlag:1})
            for i,j,shape in blocks(plan['size']):
                block=np.empty(shape,dtype=np.float32);reader.read((i,j,0),block)
                if not np.isfinite(block).all():
                    raise InputError('Non-finite amplitudes encountered; this profile does not silently replace samples')
                for a in range(shape[0]):
                    for b in range(shape[1]):
                        n=(i+a)*nx+j+b;xy=reader.indexToWorld((i+a,j+b))
                        writer.trace[n]=block[a,b]
                        writer.header[n]={segyio.TraceField.TRACE_SEQUENCE_LINE:n+1,segyio.TraceField.TRACE_SEQUENCE_FILE:n+1,
                            segyio.TraceField.TraceIdentificationCode:1,
                            segyio.TraceField.INLINE_3D:plan['inline'][0]+(i+a)*plan['inline'][1],
                            segyio.TraceField.CROSSLINE_3D:plan['crossline'][0]+(j+b)*plan['crossline'][1],
                            segyio.TraceField.CDP_X:round(xy[0]*scale),segyio.TraceField.CDP_Y:round(xy[1]*scale),
                            segyio.TraceField.SourceGroupScalar:-scale,segyio.TraceField.CoordinateUnits:1,
                            segyio.TraceField.TRACE_SAMPLE_COUNT:ns,segyio.TraceField.TRACE_SAMPLE_INTERVAL:plan['interval_us'],
                            segyio.TraceField.DelayRecordingTime:plan['origin_ms']}
                done+=shape[0]*shape[1];progress.items('Writing traces',done,ni*nx,phase_start,'traces')
        progress.phase(4,'Verifying every amplitude and trace geometry');phase_start=time.monotonic();done=0
        if pending.stat().st_size != plan['expected_bytes']:
            raise InputError('SEG-Y file size mismatch')
        with segyio.open(str(pending),'r',ignore_geometry=True) as output:
            if output.tracecount != ni*nx or output.bin[segyio.BinField.Interval] != plan['interval_us']:
                raise InputError('SEG-Y header/count mismatch')
            for i,j,shape in blocks(plan['size']):
                block=np.empty(shape,dtype=np.float32);reader.read((i,j,0),block)
                for a in range(shape[0]):
                    for b in range(shape[1]):
                        n=(i+a)*nx+j+b;hdr=output.header[n]
                        if not np.array_equal(output.trace[n],block[a,b]):
                            raise InputError(f'Amplitude mismatch at trace {n}')
                        xy=reader.indexToWorld((i+a,j+b))
                        expected={segyio.TraceField.INLINE_3D:plan['inline'][0]+(i+a)*plan['inline'][1],
                            segyio.TraceField.CROSSLINE_3D:plan['crossline'][0]+(j+b)*plan['crossline'][1],
                            segyio.TraceField.CDP_X:round(xy[0]*scale),segyio.TraceField.CDP_Y:round(xy[1]*scale),
                            segyio.TraceField.SourceGroupScalar:-scale,segyio.TraceField.TRACE_SAMPLE_COUNT:ns,
                            segyio.TraceField.TRACE_SAMPLE_INTERVAL:plan['interval_us'],segyio.TraceField.DelayRecordingTime:plan['origin_ms']}
                        if any(hdr[k]!=v for k,v in expected.items()):
                            raise InputError(f'Trace geometry/header mismatch at trace {n}')
                done+=shape[0]*shape[1];progress.items('Verified traces',done,ni*nx,phase_start,'traces')
        return dict(traces=ni*nx,samples_verified=ni*nx*ns,all_decoded_samples_exact=True,
                    headers_verified=True,max_array_block_bytes=64*1024*1024,
                    prior_compression_loss_recovered=False,profile=plan),[(pending,target)]


CONVERTERS={'zgy-to-segy':convert_zgy}
SUFFIXES={'.zgy':'zgy-to-segy'}


def execute(source,output_root,operation,options):
    source=source_path(source);output=Path(output_root).expanduser().resolve()
    if output.is_relative_to(source.parent) or any(p.lower().endswith(('.ptd','.pet')) for p in output.parts):
        raise InputError('Choose an output root outside the source directory and native Petrel stores')
    if operation not in CONVERTERS:raise InputError('Unsupported conversion operation')
    expected={'zgy-to-segy':('.zgy',)}
    if source.suffix.lower() not in expected[operation]:raise InputError('Input extension does not match operation')
    # ZGY header applicability check before full source hashing or writing outputs.
    if operation=='zgy-to-segy':
        with open_zgy(source) as reader:plan=seismic_plan(zgy_metadata(reader),options)
        print(f"SEG-Y output estimate: {plan['expected_bytes']:,} bytes. CRS: {plan['crs']} (not verified).",flush=True)
    name=re.sub(r'[^A-Za-z0-9_-]+','_',source.stem)[:64] or 'file'
    run=output/(name+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();receipt=dict(version=VERSION,operation=operation,source=str(source),
        runtime_boundary='zero_gui_python',petrel_launched=False,status='running',scientific_validation='not_established',
        started_at=datetime.now(timezone.utc).isoformat(),options=options)
    write_json(run/'RUN_RESULT.json',receipt)
    try:
        sources=[source]
        progress.phase(2,'Hashing selected source files')
        before={str(p):progress.hash_file(p) for p in sources}
        progress.phase(3,'Converting '+operation)
        summary,pending=CONVERTERS[operation](source,run,options)
        progress.phase(5,'Checking source preservation and final output hashes')
        after={str(p):progress.hash_file(p) for p in sources}
        if before!=after:raise InputError('Source changed during conversion; output is not accepted')
        # Rename only after numerical QC and source preservation checks pass.
        for temporary,final in pending:temporary.rename(final)
        artifacts=[dict(path=p.relative_to(run).as_posix(),size_bytes=p.stat().st_size,sha256=progress.hash_file(p))
                   for p in sorted(run.iterdir()) if p.is_file() and p.name!='RUN_RESULT.json']
        receipt.update(status='passed',source_hashes_before=before,source_hashes_after=after,source_unchanged=True,
                       summary=summary,artifacts=artifacts,elapsed_seconds=round(time.monotonic()-started,3))
        write_json(run/'RUN_RESULT.json',receipt)
        print('SUCCESS: '+str(run),flush=True)
        return receipt
    except BaseException as exc:
        receipt.update(status='cancelled' if isinstance(exc,KeyboardInterrupt) else 'failed',error=str(exc) or type(exc).__name__,
                       elapsed_seconds=round(time.monotonic()-started,3),partial_outputs_not_accepted=True)
        write_json(run/'RUN_RESULT.json',receipt)
        print('Failure evidence: '+str(run/'RUN_RESULT.json'),flush=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input');parser.add_argument('--output-root');parser.add_argument('--operation',choices=list(CONVERTERS))
    parser.add_argument('--domain',choices=['time','depth']);parser.add_argument('--vertical-unit',choices=['s','ms','us'])
    parser.add_argument('--horizontal-unit',choices=['m','ft']);parser.add_argument('--crs')
    parser.add_argument('--inspect',action='store_true');parser.add_argument('--capabilities',action='store_true')
    parser.add_argument('--interactive',action='store_true');args=parser.parse_args()
    display=progress.ConsoleProgress(stages=1 if args.inspect or args.capabilities else 5).start();success=False
    try:
        from standalone_petrel_extract import preflight
        preflight()
        if args.capabilities:
            display.message(json.dumps(dict(version=VERSION,conversions=CAPABILITIES),indent=2));success=True;return 0
        if not args.input and args.interactive:args.input=input('Full path to the input file: ').strip().strip('"')
        if not args.input:raise InputError('Input file required')
        source=source_path(args.input);operation=args.operation or SUFFIXES.get(source.suffix.lower())
        if not operation:raise InputError('Unsupported input. Use --capabilities for available formats.')
        if args.inspect:
            if source.suffix.lower()!='.zgy':raise InputError('Metadata inspection currently accepts ZGY')
            with open_zgy(source) as reader:display.message(json.dumps(zgy_metadata(reader),indent=2))
            success=True;return 0
        if operation=='zgy-to-segy' and args.interactive:
            with open_zgy(source) as reader:meta=zgy_metadata(reader)
            display.message(json.dumps(meta,indent=2))
            if meta['zunit_dimension']=='unknown' and not args.domain:args.domain=input('Verified domain (time/depth): ').strip()
            if not meta['zunit_name'] and not args.vertical_unit:args.vertical_unit=input('Verified vertical unit (s/ms/us for time): ').strip()
            if not meta['horizontal_unit'] and not args.horizontal_unit:args.horizontal_unit=input('Verified horizontal unit (m/ft): ').strip()
            if not args.crs:args.crs=input('CRS identifier [Enter keeps unknown]: ').strip() or 'unknown'
        if not args.output_root and args.interactive:
            args.output_root=input('Output root [Enter for your user folder/Petrel_Conversions]: ').strip().strip('"')
        output=args.output_root or str(Path.home()/'Petrel_Conversions')
        execute(source,output,operation,vars(args));success=True;return 0
    except (Exception,KeyboardInterrupt) as exc:
        display.message('ERROR: '+(str(exc) or 'Cancelled'));return 130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:display.close(success)


if __name__=='__main__':raise SystemExit(main())
