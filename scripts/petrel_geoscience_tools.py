# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Maintained file-only Petrel operations with source and artifact receipts.

CLI: python scripts/petrel_geoscience_tools.py OPERATION < request.json
Scientific acceptance and Petrel compatibility are not inferred from checksums.
Optional scientific dependencies are imported only by the operation using them.
"""
from __future__ import annotations

import csv
import petrel_progress as progress
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "petrel-geoscience-1"
OPERATIONS = ("extract_portable_project", "qc_data_package", "compare_export_packages", "surface_calculate", "process_seismic_volume")


class InputError(ValueError):
    pass


def sha256(path: Path) -> str:
    return progress.hash_file(path)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def path_arg(args: dict, key: str, *, directory: bool = False) -> Path:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{key}: a path is required")
    p = Path(value)
    p = (ROOT / p).resolve() if not p.is_absolute() else p.resolve()
    if not (p.is_dir() if directory else p.is_file()):
        raise InputError(f"{key}: missing {'directory' if directory else 'file'}: {p}")
    return p


def number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f"{name}: finite number required")
    return float(value)


def is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def safe_files(directory: Path) -> list[Path]:
    result = []
    for base, dirs, names in os.walk(directory, followlinks=False):
        for name in dirs + names:
            p = Path(base) / name
            if is_link(p):
                raise InputError(f"Linked source entries are not supported: {p}")
        result.extend(Path(base) / name for name in names)
    return sorted(result)


def contained_file(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not name:
        raise InputError("Missing manifest artifact path")
    path = (directory / name.replace("\\", "/")).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise InputError(f"Manifest path escapes package or is missing: {name}")
    return path


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def manifest_records(package: Path) -> list[dict]:
    p = package / "00_manifest/export_manifest.csv"
    if not p.is_file():
        raise InputError(f"Missing export manifest: {p}")
    records = csv_rows(p)
    if not records:
        raise InputError("Export manifest is empty")
    for row in records:
        contained_file(package, row.get("export_file", ""))
        if not re.fullmatch(r"[0-9a-fA-F]{64}", row.get("sha256", "")):
            raise InputError("Manifest row lacks a SHA-256: " + row.get("export_file", ""))
    return records


def version_context(args: dict) -> dict:
    version = args.get("petrel_version", "unknown")
    if not isinstance(version, str) or not version.strip():
        raise InputError("petrel_version must be a non-empty string, or unknown")
    targets = args.get("target_versions", [version])
    if not isinstance(targets, list) or not all(isinstance(v, str) and v for v in targets):
        raise InputError("target_versions must be a list of version strings")
    return {"petrel_version":version,"target_versions":targets,
            "version_scope":args.get("version_scope", "External file processing; source release is caller-declared"),
            "compatibility":"No Petrel runtime or cross-version compatibility is established by this operation"}


class Run:
    def __init__(self, operation: str, args: dict, inputs: list[Path], source_dirs: list[Path] = (), expected_hashes: dict | None = None):
        self.operation, self.args = operation, args
        if not isinstance(args.get("dry_run", False), bool):
            raise InputError("dry_run must be boolean")
        self.dry_run = args.get("dry_run", False)
        value = args.get("output_dir")
        if not isinstance(value, str) or not value.strip():
            raise InputError("output_dir is required and must be new")
        self.output = (ROOT / value).resolve()
        if self.output.exists():
            raise InputError("Output directory already exists; choose a new directory")
        if any(part.lower().endswith((".ptd", ".pet")) for part in self.output.parts):
            raise InputError("Output cannot be inside a Petrel native store")
        if any(self.output.is_relative_to(p.resolve()) for p in source_dirs):
            raise InputError("Output cannot be inside a source package or project directory")
        self.inputs = sorted(set(p.resolve() for p in inputs))
        if any(p.is_relative_to(self.output) for p in self.inputs):
            raise InputError("Output would contain a source input")
        self.version = version_context(args)
        with progress.hash_batch("Hashing source files", self.inputs):
            self.before = [{"path":str(p),"sha256":sha256(p),"bytes":p.stat().st_size} for p in self.inputs]
        for record in self.before:
            expected = (expected_hashes or {}).get(record["path"])
            if expected is not None and expected != record["sha256"]:
                raise InputError("Source changed during preflight: " + record["path"])
        if not self.dry_run:
            self.output.mkdir(parents=True, exist_ok=False)

    def plan(self, details: dict) -> dict:
        return {"operation":self.operation,"status":"dry_run","contract_version":CONTRACT,
                "version_context":self.version,"output_dir":str(self.output),"input_count":len(self.inputs),
                "runtime_gui_used":False,"petrel_process_launched":False,"source_mutated":False,
                "validation_scope":"input/dependency/geometry preflight only; no output created","plan":details}

    def finish(self, result: dict, summary: dict, *, status: str = "passed") -> dict:
        with progress.hash_batch("Rechecking source files", [Path(r["path"]) for r in self.before if Path(r["path"]).is_file()]):
            changed = [r["path"] for r in self.before if not Path(r["path"]).is_file() or sha256(Path(r["path"])) != r["sha256"]]
        if changed:
            raise RuntimeError("Source changed during processing: " + repr(changed))
        write_json(self.output / "result.json", result)
        files = safe_files(self.output)
        with progress.hash_batch("Hashing output artifacts", files):
            artifacts = [{"path":p.relative_to(self.output).as_posix(),"sha256":sha256(p),"bytes":p.stat().st_size}
                         for p in files]
        write_json(self.output / "artifact_manifest.json", artifacts)
        receipt = {"contract_version":CONTRACT,"operation":self.operation,"status":status,
                   "version_context":self.version,"parameters":self.args,"inputs":self.before,
                   "artifact_manifest_sha256":sha256(self.output / "artifact_manifest.json"),
                   "source_integrity":"passed","runtime_gui_used":False,"petrel_process_launched":False,
                   "source_mutated":False,"created_at_utc":datetime.now(timezone.utc).isoformat(),
                   "scientific_status":"not_established_by_execution","summary":summary}
        write_json(self.output / "receipt.json", receipt)
        return {**{k:receipt[k] for k in ("contract_version","operation","status","version_context","source_integrity","runtime_gui_used","petrel_process_launched","source_mutated","scientific_status","summary")},
                "output_dir":str(self.output),"report_path":str(self.output / "result.json"),
                "receipt_path":str(self.output / "receipt.json"),"artifact_manifest_path":str(self.output / "artifact_manifest.json")}


def verify_receipt(payload: dict) -> dict:
    """Re-read files, rather than trusting a child's success string."""
    try:
        out = Path(payload["output_dir"]).resolve()
        path = Path(payload["receipt_path"]).resolve()
        if path != out / "receipt.json":
            raise InputError("Receipt must be inside its output directory")
        receipt = read_json(path)
        if receipt["contract_version"] != CONTRACT or receipt["operation"] != payload["operation"]:
            raise InputError("Receipt operation/contract mismatch")
        if receipt["status"] != "passed" or payload.get("status") != "passed":
            raise InputError("Operation did not pass")
        for key in ("contract_version", "version_context", "source_integrity", "source_mutated", "petrel_process_launched", "runtime_gui_used", "scientific_status", "summary"):
            if payload.get(key) != receipt.get(key):
                raise InputError("Payload differs from receipt: " + key)
        if not receipt.get("inputs"):
            raise InputError("Input evidence missing")
        if receipt["source_integrity"] != "passed" or receipt["source_mutated"] is not False or receipt["petrel_process_launched"] is not False or receipt["runtime_gui_used"] is not False:
            raise InputError("Runtime or source boundary failed")
        manifest = out / "artifact_manifest.json"
        if sha256(manifest) != receipt["artifact_manifest_sha256"]:
            raise InputError("Artifact manifest changed")
        artifacts = read_json(manifest)
        if not artifacts or "result.json" not in {r["path"] for r in artifacts}:
            raise InputError("Result artifact missing")
        with progress.hash_batch("Verifying artifact receipt", [contained_file(out, r["path"]) for r in artifacts]):
            for item in artifacts:
                p = contained_file(out, item["path"])
                if sha256(p) != item["sha256"] or p.stat().st_size != item["bytes"]:
                    raise InputError("Artifact integrity failed: " + item["path"])
        with progress.hash_batch("Verifying input receipt", [Path(r["path"]) for r in receipt["inputs"]]):
            for item in receipt["inputs"]:
                if sha256(Path(item["path"])) != item["sha256"]:
                    raise InputError("Input has changed: " + item["path"])
        return {"status":"passed","artifacts_checked":len(artifacts),"inputs_checked":len(receipt["inputs"]),
                "scope":"execution receipt and file integrity; not scientific acceptance"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status":"failed","reason":str(exc)}


def qc_depth(values) -> dict:
    import numpy as np
    a = np.asarray(values, dtype=float); d = np.diff(a)
    return {"rows":int(a.size),"nonfinite":int((~np.isfinite(a)).sum()),
            "repeated_intervals":int((d == 0).sum()),
            "monotonic":bool(a.size > 0 and np.isfinite(a).all() and (np.all(d > 0) or np.all(d < 0))),
            "too_sparse_for_trend_qc":bool(a.size < 3)}


def qc_data_package(args: dict) -> dict:
    import numpy as np
    package = path_arg(args, "export_package", directory=True)
    files = safe_files(package)
    las_files = sorted((package / "02_wells/well_logs_las").rglob("*.las"))
    if las_files:
        import lasio
    run = Run("qc_data_package", args, files, [package])
    records = manifest_records(package)
    if run.dry_run:
        return run.plan({"manifest_rows":len(records),"las_files":len(las_files)})
    hash_failures = []
    for row in records:
        p = contained_file(package, row["export_file"])
        if sha256(p).lower() != row["sha256"].lower():
            hash_failures.append(row["export_file"])
    las_results = []
    for p in las_files:
        try:
            las = lasio.read(str(p)); a = np.asarray(las.data, dtype=float)
            if a.ndim != 2 or a.shape[1] < 1:
                raise InputError("LAS lacks an index column")
            curves = [{"name":c.mnemonic,"unit":c.unit,"undefined":int((~np.isfinite(a[:,i])).sum()),
                       "entirely_undefined":bool(a.shape[0] and (~np.isfinite(a[:,i])).all())}
                      for i,c in enumerate(las.curves)]
            las_results.append({"file":p.relative_to(package).as_posix(),"status":"parsed","depth":qc_depth(a[:,0]),"curves":curves})
        except Exception as exc:
            las_results.append({"file":p.relative_to(package).as_posix(),"status":"parse_failed","error":str(exc)})
    head_path = package / "02_wells/well_headers/native_well_heads.csv"
    heads = csv_rows(head_path) if head_path.is_file() else []
    names = Counter(r.get("well_name", "") for r in heads)
    trajectory_path = package / "02_wells/trajectories/native_well_trajectory_records.csv"
    tracks = defaultdict(list)
    if trajectory_path.is_file():
        for row in csv_rows(trajectory_path):
            tracks[row["object_id"]].append(row)
    trajectory_qc = []
    for oid, rows_ in tracks.items():
        rows_.sort(key=lambda r:int(r["record_index"]))
        vals = [r.get("md", "") for r in rows_]
        trajectory_qc.append({"object_id":oid,"stations":len(rows_),
                              "md_qc":qc_depth([float(x) if x else float("nan") for x in vals]) if any(vals) else {"status":"not_available"}})
    crs_path = package / "01_project_metadata/native_crs_summary.json"
    result = {"package":str(package),"manifest_hash_failures":hash_failures,"las":las_results,
              "duplicate_well_names":{k:v for k,v in names.items() if k and v > 1},
              "well_heads":len(heads),"xy_crosscheck_statuses":dict(Counter(r.get("xy_trajectory_crosscheck_status", "unknown") for r in heads)),
              "trajectories":trajectory_qc,"crs_evidence":read_json(crs_path) if crs_path.exists() else {"status":"unavailable"},
              "boundary":"Parsing and numeric checks do not prove geological correctness. No names, units, datums or source rows were repaired."}
    summary = {"manifest_rows":len(records),"manifest_hash_failures":len(hash_failures),"las_files":len(las_results),
               "las_parse_failures":sum(r["status"] == "parse_failed" for r in las_results),
               "sparse_las_files":sum(r.get("depth", {}).get("too_sparse_for_trend_qc", False) for r in las_results),
               "well_heads":len(heads),"duplicate_well_names":result["duplicate_well_names"],"trajectory_objects":len(tracks)}
    return run.finish(result, summary, status="failed" if hash_failures else "passed")


def compare_hashes(a: dict, b: dict) -> dict:
    return {"added":sorted(b.keys()-a.keys()),"removed":sorted(a.keys()-b.keys()),
            "changed":sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
            "unchanged_count":sum(a[k] == b[k] for k in a.keys() & b.keys())}


def compare_export_packages(args: dict) -> dict:
    a = path_arg(args, "baseline_package", directory=True); b = path_arg(args, "current_package", directory=True)
    scope = args.get("scope", "native")
    if scope not in ("native", "manifest"):
        raise InputError("scope must be native or manifest")
    maps=[]; inputs=[]; expected={}
    for package in (a,b):
        manifest = package / "00_manifest/export_manifest.csv"
        if not manifest.is_file():
            raise InputError("Missing export manifest: " + str(manifest))
        expected[str(manifest)] = sha256(manifest)
        records = manifest_records(package)
        selected = [r for r in records if scope == "manifest" or r.get("source_object_type") == "petrel_native_store"]
        if not selected:
            raise InputError("No files for requested comparison scope")
        mapping={}
        for row in selected:
            p = contained_file(package, row["export_file"]); key = p.relative_to(package).as_posix()
            actual = sha256(p)
            if actual.lower() != row["sha256"].lower():
                raise InputError("Source package manifest is stale: " + key)
            if key in mapping:
                raise InputError("Duplicate artifact path in comparison scope: " + key)
            mapping[key]=actual; inputs.append(p); expected[str(p)]=actual
        inputs.append(manifest); maps.append(mapping)
    run = Run("compare_export_packages", args, inputs, [a,b], expected_hashes=expected)
    if run.dry_run:
        return run.plan({"scope":scope,"baseline_files":len(maps[0]),"current_files":len(maps[1])})
    result={"scope":scope,"baseline":str(a),"current":str(b),"delta":compare_hashes(*maps),
            "boundary":"Relative-file/hash comparison. A rename appears as removed plus added. No geological or object-identity equivalence is inferred."}
    summary={k:(v if isinstance(v,int) else len(v)) for k,v in result["delta"].items()}
    return run.finish(result, summary)


def grid_difference(a, b):
    import numpy as np
    xa,ya,ga=a; xb,yb,gb=b
    if ga.shape != gb.shape or xa.shape != xb.shape or ya.shape != yb.shape or not np.allclose(xa,xb,rtol=0,atol=1e-8) or not np.allclose(ya,yb,rtol=0,atol=1e-8):
        raise InputError("Grid geometry mismatch; no implicit regridding")
    valid = np.isfinite(ga) & np.isfinite(gb)
    with np.errstate(over='ignore', invalid='ignore'):
        difference = np.where(valid, ga-gb, np.nan)
    if not np.array_equal(np.isfinite(difference), valid):
        raise InputError("Surface arithmetic overflow; finite input cells must stay finite")
    return difference


def metadata_check(a: dict, b: dict) -> list[str]:
    keys = ("crs", "xy_units", "z_domain", "z_units", "vertical_reference")
    if not isinstance(a,dict) or not isinstance(b,dict):
        raise InputError("metadata_a and metadata_b must be objects")
    missing=[]
    for k in keys:
        va,vb=a.get(k),b.get(k)
        if va in (None,"","unknown") or vb in (None,"","unknown"):
            missing.append(k)
        elif not isinstance(va,str) or not isinstance(vb,str) or va != vb:
            raise InputError("Conflicting declared metadata: " + k)
    return missing


def array_stats(a) -> dict:
    import numpy as np
    v = np.asarray(a)[np.isfinite(a)].astype(np.float64)
    return {"total":int(a.size),"finite":int(v.size),"undefined":int(a.size-v.size),
            "min":float(v.min()) if v.size else None,"max":float(v.max()) if v.size else None,
            "mean":float(v.mean()) if v.size else None}


def surface_calculate(args: dict) -> dict:
    import numpy as np
    from convert_petrel_grid import read_zmap, write_zmap
    a=path_arg(args,"input_a");b=path_arg(args,"input_b")
    if a.suffix.lower() not in (".dat",".zmap") or b.suffix.lower() not in (".dat",".zmap"):
        raise InputError("This tool accepts regular ZMAP grids only")
    missing=metadata_check(args.get("metadata_a",{}),args.get("metadata_b",{}))
    run=Run("surface_calculate",args,[a,b])
    ga,gb=read_zmap(a),read_zmap(b); d=grid_difference(ga,gb)
    if not np.isfinite(d).any():
        raise InputError("No shared finite grid cells")
    if run.dry_run:
        return run.plan({"operation":"A minus B","shape":list(d.shape),"unresolved_metadata":missing})
    x,y,_=ga
    write_zmap(run.output/'difference.dat',x,y,d,'DIAGNOSTIC_A_MINUS_B')
    np.save(run.output/'difference.npy',d)
    np.save(run.output/'x.npy',x);np.save(run.output/'y.npy',y)
    rx,ry,back=read_zmap(run.output/'difference.dat')
    if not np.array_equal(np.isfinite(back),np.isfinite(d)) or not np.allclose(rx,x,rtol=0,atol=1e-8) or not np.allclose(ry,y,rtol=0,atol=1e-8):
        raise RuntimeError("Surface round-trip mask or geometry changed")
    error=float(np.max(np.abs(back[np.isfinite(d)]-d[np.isfinite(d)])))
    if error > 5.1e-5:
        raise RuntimeError("Surface output precision failed")
    positions=np.argwhere(np.isfinite(d));n=min(101,len(positions))
    for index in np.linspace(0,len(positions)-1,n,dtype=int):
        j,i=positions[index]
        if abs(float(d[j,i])-(float(ga[2][j,i])-float(gb[2][j,i]))) > 1e-10:
            raise RuntimeError("Scalar difference check failed")
    summary={"operation":"A minus B","statistics":array_stats(d),"roundtrip_max_abs_error":error,
             "scalar_checks":n,"unresolved_metadata":missing,"interpretation":"diagnostic_difference_only"}
    return run.finish({**summary,"metadata_a":args.get('metadata_a',{}),"metadata_b":args.get('metadata_b',{}),
                       "metadata_origin":"caller declarations; not independently confirmed",
                       "boundary":"No isochrone, true-thickness, depth-error, CRS transform or geological acceptance inferred."},summary)


@contextmanager
def open_cube(path: Path, geometry: Path | None = None):
    import numpy as np
    if path.suffix.lower()=='.zgy':
        import pyzgy
        with pyzgy.open(str(path)) as f:
            axes=[np.asarray(f.ilines),np.asarray(f.xlines),np.asarray(f.samples)]
            yield axes,lambda k:np.asarray(f.iline[int(f.ilines[k])]),{"source":"ZGY annotations","crs":"unknown","z_domain":"unknown","z_units":"unknown"}
    elif path.suffix.lower()=='.npy':
        if geometry is None:
            raise InputError("NumPy cubes require geometry_json with inline, xline and sample arrays")
        a=np.load(path,mmap_mode='r',allow_pickle=False);g=read_json(geometry)
        if a.ndim!=3 or a.dtype.kind not in 'fiu':
            raise InputError("Numeric 3D NumPy cube required")
        axes=[np.asarray(g.get(k,[]),dtype=float) for k in ('inline','xline','sample')]
        for axis,n in zip(axes,a.shape):
            if axis.ndim!=1 or len(axis)!=n or not n or not np.isfinite(axis).all() or (n>1 and not (np.all(np.diff(axis)>0) or np.all(np.diff(axis)<0))):
                raise InputError("Cube annotation axes are missing, invalid or disagree with shape")
        yield axes,lambda k:np.asarray(a[k]),g.get('metadata',{})
    else:
        raise InputError("Only ZGY or NumPy cubes are supported")


def amplitude_mask(block, low: float, high: float):
    import numpy as np
    if not low < high:
        raise InputError("lower_limit must be less than upper_limit")
    keep=np.isfinite(block)&(block>low)&(block<high)
    values=block[keep]
    if block.dtype.kind in 'iu' and block.dtype.itemsize >= 8 and values.size:
        if values.max() > 2**53 or (block.dtype.kind == 'i' and values.min() < -(2**53)):
            raise InputError('Retained 64-bit integers exceed the exact float64 range; explicit numeric conversion required')
    return np.where(keep,block,np.nan)


def process_seismic_volume(args: dict) -> dict:
    import numpy as np
    source=path_arg(args,'input_path')
    geo=path_arg(args,'geometry_json') if args.get('geometry_json') else None
    operation=args.get('operation','amplitude_mask')
    if operation not in ('amplitude_mask','compare_geometry'):
        raise InputError("operation must be amplitude_mask or compare_geometry; subtraction is not implemented")
    inputs=[source]+([geo] if geo else [])
    expected_hashes={str(p):sha256(p) for p in inputs}
    with open_cube(source,geo) as (axes,reader,meta):
        shape=tuple(len(x) for x in axes)
        if operation=='compare_geometry':
            other=path_arg(args,'input_b'); gb=path_arg(args,'geometry_b_json') if args.get('geometry_b_json') else None
            inputs += [other]+([gb] if gb else [])
            expected_hashes.update({str(p):sha256(p) for p in [other]+([gb] if gb else [])})
            with open_cube(other,gb) as (axes_b,_,meta_b):
                matches={k:(a.shape==b.shape and bool(np.allclose(a,b,rtol=0,atol=1e-8))) for k,a,b in zip(('inline','xline','sample'),axes,axes_b)}
                result={'matching_axes':matches,'metadata_a':meta,'metadata_b':meta_b,'difference_ready':False,
                        'boundary':'This check compares annotation axes only. World geometry, units, domain and null conventions must also be established before subtraction.'}
                run=Run('process_seismic_volume',args,inputs,expected_hashes=expected_hashes)
                if run.dry_run:return run.plan(result)
                return run.finish(result,{'matching_axes':matches,'difference_ready':False})
        low=number(args.get('lower_limit'),'lower_limit');high=number(args.get('upper_limit'),'upper_limit')
        if not low<high:raise InputError('lower_limit must be less than upper_limit')
        first=reader(0)
        dtype=np.result_type(first.dtype,np.float32)
        estimated=math.prod(shape)*dtype.itemsize
        maximum=args.get('max_output_bytes',2_000_000_000)
        if isinstance(maximum,bool) or not isinstance(maximum,int) or maximum<=0 or estimated>maximum:
            raise InputError('Output exceeds max_output_bytes or limit is invalid')
        run=Run('process_seismic_volume',args,inputs,expected_hashes=expected_hashes)
        if run.dry_run:return run.plan({'shape':list(shape),'estimated_output_bytes':estimated,'limits':[low,high]})
        volume=np.lib.format.open_memmap(run.output/'amplitude_mask.npy',mode='w+',dtype=dtype,shape=shape)
        kept=total=0
        for k in range(shape[0]):
            block=reader(k)
            if block.shape!=shape[1:]:raise RuntimeError('Unexpected inline shape')
            masked=amplitude_mask(block,low,high);volume[k]=masked
            kept+=int(np.isfinite(masked).sum());total+=block.size
            if k==shape[0]//2:
                np.save(run.output/'input_middle_inline.npy',block);np.save(run.output/'masked_middle_inline.npy',masked)
        volume.flush();del volume
        output=np.load(run.output/'amplitude_mask.npy',mmap_mode='r',allow_pickle=False)
        checked=0
        for k in sorted(set([0,shape[0]//2,shape[0]-1])):
            block=reader(k);expected=np.isfinite(block)&(block>low)&(block<high);got=output[k]
            if not np.array_equal(np.isfinite(got),expected) or not np.array_equal(got[expected],block[expected]):
                raise RuntimeError('Seismic read-back values or mask differ')
            checked+=block.size
        # Sidecar uses the same schema accepted by open_cube; output is chainable.
        write_json(run.output/'geometry.json',{**{k:a.tolist() for k,a in zip(('inline','xline','sample'),axes)},'metadata':meta})
        summary={'shape':list(shape),'total_samples':int(total),'kept_samples':kept,'undefined_samples':int(total-kept),
                 'limits':[low,high],'inclusive':False,'reopened_samples_checked':int(checked),'petrel_reimport':'not_tested'}
        return run.finish({**summary,'boundary':'Caller-selected amplitude mask; no denoising quality or geological meaning established. Source annotation axes are preserved.'},summary)


def extract_portable_project(args: dict) -> dict:
    source=path_arg(args,'project_file');store=source.with_suffix('.ptd')
    if source.suffix.lower()!='.pet' or not store.is_dir():
        raise InputError('Exact .pet file and matching .ptd directory required')
    mode=args.get('companion_mode','convert')
    if args.get('report_only'): mode='inventory'
    if mode not in ('inventory','copy','convert'):raise InputError('Invalid companion_mode')
    # The existing pipeline may inspect companions; include that entire source lane.
    progress.phase(2, 'Initial source integrity hashes')
    inputs=safe_files(source.parent)
    if args.get('reference_seismic'):
        from petrel_seismic_integrity import SEISMIC_SUFFIXES
        neighbors={p.resolve() for p in source.parent.glob('*.ptd') if p.resolve()!=store}
        inputs=[p for p in inputs if p.suffix.lower() not in SEISMIC_SUFFIXES
                and not (p.suffix.lower()=='.pet' and p!=source)
                and not any(parent in neighbors for parent in p.parents)
                and not (not source.is_relative_to(ROOT) and p.is_relative_to(ROOT))]
        if source not in inputs: inputs.append(source)
    run=Run('extract_portable_project',args,inputs,[source.parent])
    if run.dry_run:return run.plan({'source':str(source),'matching_store':str(store),'companion_mode':mode})
    command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/invoke_portable_petrel_extract.ps1'),
             '-ProjectFile',str(source),'-OutputRoot',str(run.output/'package'),'-ProjectName',source.stem,
             '-PetrelVersion',run.version['petrel_version'],'-CompanionMode',mode,'-PythonPath',sys.executable]
    if args.get('report_only'):command.append('-ReportOnly')
    if args.get('reference_seismic'):command.append('-ReferenceSeismic')
    progress.phase(3, 'Native project copy and inventory')
    try:
        code = progress.run_pipeline(command, ROOT, run.output/'extraction.log', args.get('timeout_seconds',1800))
    except subprocess.TimeoutExpired:
        raise RuntimeError('Extraction timed out; partial output retained, process tree stopped')
    if code:raise RuntimeError(f'Portable pipeline failed ({code}); see {run.output / "extraction.log"}')
    progress.phase(9, 'Package validation and final source integrity hashes')
    packages=list((run.output/'package').iterdir())
    if len(packages)!=1:raise RuntimeError('Expected exactly one extraction package')
    pkg=packages[0];summary=read_json(pkg/'07_workflows_reports/portable_extractor/portable_extraction_run_summary.json')
    if summary.get('validation_status')!='passed':raise RuntimeError('Extraction validation did not pass')
    records=manifest_records(pkg)
    with progress.hash_batch('Verifying package files', [contained_file(pkg,r['export_file']) for r in records]):
        bad=[r['export_file'] for r in records if sha256(contained_file(pkg,r['export_file'])).lower()!=r['sha256'].lower()]
    if bad:raise RuntimeError('Extracted package hash mismatch: '+repr(bad))
    spatial_path=pkg/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
    spatial=read_json(spatial_path) if spatial_path.is_file() else {}
    compact={'export_package':str(pkg),'manifest_rows':len(records),'validation_status':'passed',
             'native_object_status_counts':spatial.get('object_status_counts',{}),'native_well_head_rows':spatial.get('native_well_head_rows',0),
             'dashboard':str(pkg/'PROJECT_REPORT.html'),
             'scope':'Supported extraction only; per-object unavailable/failed-closed states remain visible in the package.'}
    return run.finish({'pipeline_summary':summary,'summary':compact},compact)


def dispatch(operation: str, args: dict) -> dict:
    if operation not in OPERATIONS or not isinstance(args,dict):raise InputError('Unknown operation or invalid request')
    timeout=args.get('timeout_seconds',1800)
    if isinstance(timeout,bool) or not isinstance(timeout,int) or not 1<=timeout<=7200:
        raise InputError('timeout_seconds must be an integer between 1 and 7200')
    return globals()[operation](args)


def main() -> int:
    operation=sys.argv[1] if len(sys.argv)>1 else ''
    try:
        result=dispatch(operation,json.load(sys.stdin))
    except (InputError,ImportError) as exc:
        result={'operation':operation,'status':'blocked','error':str(exc),'runtime_gui_used':False,'petrel_process_launched':False}
    except Exception as exc:
        result={'operation':operation,'status':'failed','error':str(exc),'runtime_gui_used':False,'petrel_process_launched':False}
    print('SummaryJson:'+json.dumps(result,ensure_ascii=True,allow_nan=False))
    return 0 if result['status'] in ('passed','dry_run') else 1


if __name__=='__main__':
    raise SystemExit(main())
