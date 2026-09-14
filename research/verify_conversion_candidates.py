"""Bounded research probes, not a released converter or SEG-Y conformance test.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Run with the standalone runtime and a NEW --output directory. Optional
--sample-zgy files are opened read-only; only six traces per file are decoded.
No user-supplied ZGY is exported: unknown domain/units require separate review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import struct
import warnings

warnings.filterwarnings("ignore", message="seismic store access is not available:")
import lasio
import numpy as np
import segyio
from openzgy.api import (SampleDataType, UnitDimension, ZgyCompressFactory,
                         ZgyReader, ZgyWriter)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def synthetic_seismic(root, kind):
    shape = (64, 64, 64) if kind == "zfp_float32" else (8, 9, 65)
    rng = np.random.default_rng(614)
    source_data = rng.normal(0, 20, shape).astype(np.float32)
    source, target = root / f"{kind}.zgy", root / f"{kind}.segy"
    # Rotated affine geometry; coordinate reference is explicitly local/unknown.
    origin = np.array([10000.125, 20000.25])
    di, dj = np.array([15., 20.]), np.array([20., -15.])
    corners = [origin, origin + (shape[0]-1)*di,
               origin + (shape[1]-1)*dj,
               origin + (shape[0]-1)*di + (shape[1]-1)*dj]
    options = dict(size=shape, zstart=120., zinc=2.,
                   annotstart=(101, 201), annotinc=(2, 3),
                   corners=[tuple(p) for p in corners],
                   zunitdim=UnitDimension.time, zunitname="ms", zunitfactor=.001,
                   hunitdim=UnitDimension.length, hunitname="m", hunitfactor=1.)
    if kind == "scaled_int8":
        options.update(datatype=SampleDataType.int8, datarange=(-128., 127.))
    elif kind == "zfp_float32":
        options.update(datatype=SampleDataType.float,
                       compressor=ZgyCompressFactory("ZFP", snr=30))
    else:
        options.update(datatype=SampleDataType.float)
    with ZgyWriter(str(source), **options) as writer:
        writer.write((0, 0, 0), source_data)
    before = digest(source)
    max_source_error = 0.
    with ZgyReader(str(source)) as reader:
        spec = segyio.spec()
        spec.ilines = np.arange(shape[0])*2 + 101
        spec.xlines = np.arange(shape[1])*3 + 201
        spec.samples = np.arange(shape[2])*2 + 120
        spec.format, spec.sorting = 5, 2
        with segyio.create(str(target), spec) as writer:
            writer.text[0] = segyio.tools.create_text_header({
                1: "RESEARCH FIXTURE ONLY - NOT AN APPROVED DELIVERABLE",
                2: "TIME: MILLISECONDS. XY: LOCAL METRES. CRS: UNKNOWN.",
                3: "IEEE FLOAT32. IL BYTE 189, XL 193, CDP X/Y 181/185.",
                4: "COORDINATE SCALAR -1000. NO ORIGINAL ACQUISITION HEADERS."})
            writer.bin.update({segyio.BinField.Interval: 2000,
                               segyio.BinField.MeasurementSystem: 1,
                               segyio.BinField.SEGYRevision: 256,
                               segyio.BinField.TraceFlag: 1})
            for i in range(shape[0]):
                block = np.empty((1, shape[1], shape[2]), dtype=np.float32)
                reader.read((i, 0, 0), block)
                max_source_error = max(max_source_error,
                                       float(np.max(np.abs(block[0]-source_data[i]))))
                for j in range(shape[1]):
                    n = i*shape[1]+j
                    x, y = reader.indexToWorld((i, j))
                    writer.trace[n] = block[0, j]
                    writer.header[n] = {
                        segyio.TraceField.TRACE_SEQUENCE_LINE: n+1,
                        segyio.TraceField.TRACE_SEQUENCE_FILE: n+1,
                        segyio.TraceField.TraceIdentificationCode: 1,
                        segyio.TraceField.INLINE_3D: int(spec.ilines[i]),
                        segyio.TraceField.CROSSLINE_3D: int(spec.xlines[j]),
                        segyio.TraceField.CDP_X: round(x*1000),
                        segyio.TraceField.CDP_Y: round(y*1000),
                        segyio.TraceField.SourceGroupScalar: -1000,
                        segyio.TraceField.CoordinateUnits: 1,
                        segyio.TraceField.TRACE_SAMPLE_COUNT: shape[2],
                        segyio.TraceField.TRACE_SAMPLE_INTERVAL: 2000,
                        segyio.TraceField.DelayRecordingTime: 120}
        with segyio.open(str(target), "r", strict=True) as reopened:
            np.testing.assert_array_equal(reopened.ilines, spec.ilines)
            np.testing.assert_array_equal(reopened.xlines, spec.xlines)
            np.testing.assert_array_equal(reopened.samples, spec.samples)
            for i in range(shape[0]):
                block = np.empty((1, shape[1], shape[2]), dtype=np.float32)
                reader.read((i, 0, 0), block)
                np.testing.assert_array_equal(reopened.iline[int(spec.ilines[i])], block[0])
                for j in range(shape[1]):
                    hdr = reopened.header[i*shape[1]+j]
                    xy = [hdr[segyio.TraceField.CDP_X]/1000,
                          hdr[segyio.TraceField.CDP_Y]/1000]
                    np.testing.assert_allclose(xy, origin+i*di+j*dj, atol=.0005, rtol=0)
        # Independently parse binary bytes and trace values, without segyio.
        raw = target.read_bytes()
        assert len(raw) == 3600 + shape[0]*shape[1]*(240+4*shape[2])
        assert struct.unpack_from(">HHH", raw, 3216) == (2000, 2000, shape[2])
        assert struct.unpack_from(">H", raw, 3224)[0] == 5
        for i in range(shape[0]):
            block = np.empty((1, shape[1], shape[2]), dtype=np.float32)
            reader.read((i, 0, 0), block)
            for j in range(shape[1]):
                offset = 3600+(i*shape[1]+j)*(240+4*shape[2])
                assert struct.unpack_from(">ii", raw, offset+188) == (101+i*2, 201+j*3)
                decoded = np.frombuffer(raw, dtype=">f4", count=shape[2], offset=offset+240)
                np.testing.assert_array_equal(decoded, block[0, j])
    assert before == digest(source)
    if kind == "zfp_float32":
        assert max_source_error > 0, "Fixture must exercise actual lossy compression"
    return dict(case=kind, status="passed", traces=shape[0]*shape[1],
                samples_compared=int(np.prod(shape)), decoded_zgy_to_segy_exact=True,
                raw_byte_checks_passed=True, axes_and_rotated_xy_passed=True,
                max_error_from_pre_storage_fixture=max_source_error,
                source_unchanged=True, source_sha256=before, output_sha256=digest(target))


def sample_probe(path):
    before = digest(path)
    with ZgyReader(str(path)) as reader:
        shape = (min(2, reader.size[0]), min(3, reader.size[1]), reader.size[2])
        values = np.empty(shape, dtype=np.float32)
        reader.read((0, 0, 0), values)
        record = dict(case="local_demo_read_probe", status="passed", source_sha256=before,
                      source_bytes=path.stat().st_size, size=list(reader.size),
                      samples_read=int(values.size), finite_samples=int(np.isfinite(values).sum()),
                      storage_type=str(reader.datatype), zstart=reader.zstart, zinc=reader.zinc,
                      zunitdim=str(reader.zunitdim), zunitname=reader.zunitname,
                      hunitdim=str(reader.hunitdim), hunitname=reader.hunitname,
                      segy_written=False, crs_verified=False,
                      decision="Read feasibility only; domain, units and receiving-system profile unresolved")
    record["source_unchanged"] = before == digest(path)
    assert record["source_unchanged"]
    return record


def well_probe(root):
    csv = root / "well.csv"
    data = np.array([[1000., 25., 2.25], [1000.5, np.nan, 2.5], [1001., 75., 2.75]])
    np.savetxt(csv, data, delimiter=",", header="DEPT,GR,RHOB", comments="", fmt="%.9g")
    parsed = np.genfromtxt(csv, delimiter=",", skip_header=1)
    las = lasio.LASFile()
    las.well.WELL = "SYNTHETIC_RESEARCH_ONLY"
    las.well.NULL = -999.25
    for i, (mnemonic, unit) in enumerate([("DEPT", "M"), ("GR", "API"), ("RHOB", "G/C3")]):
        las.append_curve(mnemonic, parsed[:, i], unit=unit)
    target = root / "well.las"
    las.write(str(target), version=2.0, fmt="%.9g")
    reopened = lasio.read(target)
    np.testing.assert_allclose(reopened.data, data, rtol=0, atol=0, equal_nan=True)
    assert [c.unit for c in reopened.curves] == ["M", "API", "G/C3"]
    assert float(reopened.well.STEP.value) == .5
    return dict(case="csv_to_las2", status="passed", rows=3, curves_including_index=3,
                values_units_nulls_and_step_passed=True, input_schema_explicit=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-zgy", action="append", default=[], type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = dict(purpose="research_only", production_converter_implemented=False,
                  python=platform.python_version(), platform=platform.platform(),
                  dependencies={p: importlib.metadata.version(p) for p in
                                ("numpy", "segyio", "pyzgy", "zfpy", "lasio")},
                  limitations=["No Petrel or other application re-import validation",
                               "No depth-domain SEG-Y profile or full format conformance test",
                               "No large-volume performance validation", "No user project conversion"], cases=[])
    for kind in ("float32", "scaled_int8", "zfp_float32"):
        result["cases"].append(synthetic_seismic(args.output, kind))
    result["cases"].append(well_probe(args.output))
    result["cases"].extend(sample_probe(p) for p in args.sample_zgy)
    result["status"] = "passed"
    (args.output / "results.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
