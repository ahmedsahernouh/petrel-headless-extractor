#!/usr/bin/env python3
# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Focused tests for the evidence-gated native spatial decoder."""

from __future__ import annotations

import importlib.util
import math
import json
import hashlib
import sqlite3
import struct
import sys
import tempfile
import argparse
import unittest
import uuid
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("export_petrel_native_spatial_zero_gui.py")
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("petrel_native_spatial", SCRIPT)
assert SPEC and SPEC.loader
decoder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decoder
SPEC.loader.exec_module(decoder)


BASE_NAMES = [
    "Version",
    "Type",
    "Id",
    "Ref",
    "Size",
    "use",
    "expr",
    "value",
    "data",
    "http://www.slb.com/Petrel/2011/03/Serialization",
]


def dictionary(names: list[str]) -> bytes:
    result = bytearray(b"BXML\x01")
    for name in names:
        raw = name.encode("ascii")
        result += b"\xA1" + decoder.encode_uleb128(len(raw)) + raw
    result += b"\xA0"
    return bytes(result)


def literal_lz4_envelope(payload: bytes) -> bytes:
    length = len(payload)
    block = bytearray([min(length, 15) << 4])
    if length >= 15:
        remaining = length - 15
        while remaining >= 255:
            block.append(255)
            remaining -= 255
        block.append(remaining)
    block += payload
    return b"LZ4\x01" + struct.pack("<I", len(block)) + b"TEST" + bytes(block)


def points_payload(points: list[tuple[float, float, float]], checkpoint: tuple[int, bytes] | None = None) -> bytes:
    from test_petrel_native_recovery import element,array,frame,vint
    children=[element('user_data',attrs={'Size':0}),
        element('vertices',children=[array('double',[v for p in points for v in p],'<f8')],attrs={'Size':len(points)}),
        element('has_attr',False),element('has_object_ids',False)]
    doc=element('data',children=children,attrs={'Type':'Points3','Version':[1,2,0,1,1],'xmlns':BASE_NAMES[-1]})
    if checkpoint:
        cut=doc.index(struct.pack('<3d',*points[0]))+checkpoint[0]
        return b'BXML\x01'+b'\xa0'+vint(cut)+doc[:cut]+b'\xa0'+vint(len(doc)-cut)+doc[cut:]+b'\xa2'
    return frame(doc)


class NativeSpatialDecoderTests(unittest.TestCase):
    def test_version2_and_3_inactive_identity_slots_are_fully_counted(self):
        from test_petrel_native_recovery import element,array,frame
        def fixture(active=False,version=3,count=1,extra=False):
            fields=[element('is_active',active)]+([element('unknown_field',0)] if extra else [])
            item=element('item',children=fields,attrs={'Version':version})
            return frame(element('data',children=[element('user_data',attrs={'Size':0}),
                element('vertices',children=[array('double',[1.,2.,3.],'<f8')],attrs={'Size':1}),
                element('has_attr',False),element('has_object_ids',True),
                element('object_ids',children=[item],attrs={'Size':count})],
                attrs={'Type':'Points3','Version':[1,2,0,1,1],'xmlns':BASE_NAMES[-1]}))
        for version in (2,3):
            points,metadata=decoder.decode_points3(fixture(version=version))
            self.assertEqual(points,[(1.,2.,3.)])
            self.assertEqual(metadata['object_id_validation']['inactive_identity_slots'],1)
            self.assertEqual(metadata['object_id_validation']['validated_slot_count'],1)
        for options in ({'active':True},{'active':0},{'version':1},{'version':99},{'count':2},{'extra':True}):
            with self.subTest(options=options),self.assertRaises(decoder.DecodeError):
                decoder.decode_points3(fixture(**options))

    def test_legacy_identity_slots_preserve_geometry_without_claiming_id_export(self):
        from test_petrel_native_recovery import element,array,frame
        fields=['is_active','identifier_first','identifier_second','data_key','last_update',
                'os_lmd_database','os_lmd_petrel','suspended_key']
        def fixture(version=2,size=1,missing=False):
            item=element('item',children=[element(k,False if k=='is_active' else 0)
                        for k in (fields[:-1] if missing else fields)],attrs={'Version':version})
            return frame(element('data',children=[element('user_data',attrs={'Size':0}),
                element('vertices',children=[array('double',[1.,2.,3.],'<f8')],attrs={'Size':1}),
                element('has_attr',False),element('has_object_ids',True),
                element('object_ids',children=[item],attrs={'Size':size})],
                attrs={'Type':'Points3','Version':[1,2,0,1,1],'xmlns':BASE_NAMES[-1]}))
        points,metadata=decoder.decode_points3(fixture())
        self.assertEqual(points,[(1.,2.,3.)]);self.assertEqual(metadata['object_id_slot_versions'],[2])
        self.assertEqual(metadata['object_ids_status'],'validated_serialized_values_available_for_export')
        for options in ({'size':2},{'version':99},{'missing':True}):
            with self.subTest(options=options), self.assertRaises(decoder.DecodeError):
                decoder.decode_points3(fixture(**options))

    def test_csv_writer_preserves_previous_byte_semantics_and_uses_large_buffer(self):
        rows = [
            {"preferred": "alpha", "number": 1, "extra": "caf\u00e9"},
            {"preferred": "line\nbreak", "number": None, "extra": "quoted, value"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "old.csv"
            with expected.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = __import__("csv").DictWriter(
                    handle,
                    fieldnames=["preferred", "number", "extra"],
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(rows)

            actual = root / "new.csv"
            with mock.patch.object(decoder, "open", wraps=open, create=True) as opened:
                self.assertTrue(decoder.write_csv_if_rows(actual, rows, ("preferred", "number")))
            self.assertEqual(actual.read_bytes(), expected.read_bytes())
            self.assertEqual(opened.call_args.kwargs["buffering"], 1024 * 1024)

    def test_csv_writer_empty_rows_remove_stale_derivative(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "stale.csv"
            destination.write_bytes(b"previous")
            self.assertFalse(decoder.write_csv_if_rows(destination, [], ("field",)))
            self.assertFalse(destination.exists())

    def test_csv_writer_failure_preserves_previous_final_and_has_no_false_success(self):
        events = []
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "output.csv"
            destination.write_bytes(b"previous-final")
            with mock.patch.object(decoder, "_csv_write_event", side_effect=lambda kind, **fields: events.append((kind, fields))):
                with mock.patch.object(decoder.csv.DictWriter, "writerows", side_effect=OSError("synthetic write failure")):
                    with self.assertRaisesRegex(OSError, "synthetic write failure"):
                        decoder.write_csv_if_rows(destination, [{"field": "new"}], ("field",))
            self.assertEqual(destination.read_bytes(), b"previous-final")
            self.assertFalse(list(destination.parent.glob(f".{destination.name}.*.tmp")))
            self.assertIn("csv_write_failed", [kind for kind, _ in events])
            self.assertNotIn("csv_write_completed", [kind for kind, _ in events])

    def test_csv_writer_emits_bounded_chunk_progress_and_completion(self):
        events = []
        rows = [{"field": index} for index in range(5)]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "output.csv"
            with mock.patch.object(decoder, "CSV_WRITE_BATCH_ROWS", 2), mock.patch.object(
                decoder, "CSV_PROGRESS_INTERVAL_SECONDS", 0.0
            ), mock.patch.object(
                decoder, "_csv_write_event", side_effect=lambda kind, **fields: events.append((kind, fields))
            ):
                self.assertTrue(decoder.write_csv_if_rows(destination, rows, ("field",)))
        progress = [fields for kind, fields in events if kind == "csv_write_progress"]
        self.assertEqual([entry["rows"] for entry in progress], [2, 4])
        self.assertTrue(all(entry["total_rows"] == 5 for entry in progress))
        self.assertTrue(all(entry["bytes"] is None or entry["bytes"] >= 0 for entry in progress))
        completed = [fields for kind, fields in events if kind == "csv_write_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["rows"], 5)
        self.assertEqual(completed[0]["total_rows"], 5)
        self.assertGreater(completed[0]["bytes"], 0)

    def test_optional_model_linkage_failure_preserves_independent_points(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            native = package/'08_native_project/ptd_store'; native.mkdir(parents=True)
            # Repeated magic is not the observed one-magic, multi-block stream.
            model = native/'Model.ptd'
            model.write_bytes(literal_lz4_envelope(b'first') + literal_lz4_envelope(b'second'))
            names = package/'02_wells/well_headers/native_borehole_references.csv'
            names.parent.mkdir(parents=True); names.write_text('name\nSynthetic well\n')
            mappings, evidence = decoder.trajectory_name_candidates(package, [str(uuid.uuid4())])
            self.assertEqual(mappings, {})
            self.assertEqual(evidence['status'], 'failed_closed')
            self.assertIn('LZ4 envelope size mismatch', evidence['error'])
            self.assertEqual(decoder.trajectory_name_candidates(package, [])[1]['status'], 'not_needed')
            data = native/'Data.ptd'
            with sqlite3.connect(data) as db:
                db.executescript('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT, time_stamp TEXT); CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB);')
                db.execute('INSERT INTO data VALUES (1,?,"points",1,"Points3","")', (str(uuid.uuid4()),))
                db.execute('INSERT INTO blob_parts VALUES (1,0,?)', (literal_lz4_envelope(points_payload([(1.,2.,3.),(4.,5.,6.)])),))
            db.close()
            before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (data,model)}
            args = argparse.Namespace(export_package=str(package),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02)
            self.assertEqual(decoder.run(args), 0)
            report = json.loads((package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text())
            self.assertEqual(report['point_vertex_rows'], 2)
            self.assertEqual(report['model_well_head_decode']['status'], 'failed_closed')
            self.assertEqual(before, {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (data,model)})

    def test_lz4_literal_envelope(self) -> None:
        payload = b"BXML\x01 synthetic payload"
        decoded, evidence = decoder.decompress_lz4_block(literal_lz4_envelope(payload))
        self.assertEqual(decoded, payload)
        self.assertEqual(evidence["decompressed_size"], len(payload))

    def test_failed_points_records_attempt_and_checkpoint_without_accepted_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            native = package/'08_native_project/ptd_store'; native.mkdir(parents=True)
            with sqlite3.connect(native/'Data.ptd') as db:
                db.executescript('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT, time_stamp TEXT); CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB);')
                db.execute('INSERT INTO data VALUES (1,?,"points",1,"Points3","")', (str(uuid.uuid4()),))
                db.execute('INSERT INTO blob_parts VALUES (1,0,?)', (literal_lz4_envelope(b'BXML\x01 bad layout'),))
            db.close()
            args = argparse.Namespace(export_package=str(package), data_file=None, well_tops_validation_csv=None, validation_tolerance=.02)
            decoder.run(args)
            row = json.loads((package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text())['objects'][0]
            self.assertEqual(row['status'], 'failed_closed')
            self.assertEqual(row['attempted_decoder_profile'], 'decode_points3')
            self.assertEqual(row['failure_checkpoint'], 'decode_points3')
            self.assertEqual(row['envelope']['envelope'], 'LZ4_v1_raw_block')
            self.assertNotIn('decoder_profile', row)

    def test_chunked_spatial_payload_and_envelope_evidence(self) -> None:
        points = [(1., 2., 3.), (4., 5., 6.)]
        payload = points_payload(points)
        blob = literal_lz4_envelope(payload[:17])+literal_lz4_envelope(payload[17:])[4:]
        decoded, evidence = decoder.decompress_lz4_block(blob)
        self.assertEqual(decoded, payload)
        self.assertEqual(decoder.decode_points3(decoded)[0], points)
        self.assertEqual(evidence['block_count'], 2)
        self.assertEqual(evidence['envelope'], 'LZ4_v1_block_stream')
        self.assertEqual(evidence['blocks'][1]['header_offset'], len(literal_lz4_envelope(payload[:17])))

    def test_points3_checkpoint_inside_double(self) -> None:
        points = [(450_000.0 + i, 2_900_000.0 + i * 2, -1_000.0 - i) for i in range(20)]
        # A legal length frame splits inside Y of point 12 without changing bytes.
        payload = points_payload(points, checkpoint=(12 * 24 + 8 + 5, b"\xA0\x12\x34"))
        decoded, evidence = decoder.decode_points3(payload)
        self.assertEqual(decoded, points)
        self.assertEqual(evidence["declared_point_count"], 20)
        self.assertEqual(evidence['decoder'],'length_framed_typed_NBFX')

    def test_polygons3_declared_counts(self) -> None:
        from test_petrel_native_recovery import frame, polygon_doc
        points = [(100.0, 200.0, -10.0), (110.0, 200.0, -11.0), (100.0, 200.0, -10.0)]
        payload = frame(polygon_doc([points],[True]),split=True)
        polygons, evidence = decoder.decode_polygons3(payload)
        self.assertEqual(polygons, [points])
        self.assertEqual(evidence["outer_item_count"], 1)

    def test_polygon_native_order_empty_segments_and_null_gaps(self):
        from test_petrel_native_recovery import frame, polygon_doc
        parts=[[(8.,1.,2.),(2.,9.,3.)],[],[(1.,1.,1.),(decoder.NATIVE_FLOAT_MAX_SENTINEL,2.,2.),(3.,3.,3.)]]
        polygons,meta=decoder.decode_polygons3(frame(polygon_doc(parts,[True,False,False]),split=True))
        self.assertEqual(polygons,[parts[0],[],[parts[2][0],None,parts[2][2]]])
        self.assertEqual([row['segment_id'] for row in meta['segments']],[0,1,2])
        self.assertEqual([row['native_serialization_id'] for row in meta['segments']],[2,3,4])
        self.assertTrue(meta['segments'][0]['is_closed_native'])
        self.assertEqual(meta['segments'][2]['missing_vertex_slots'],1)

    def test_polygon_length_frames_inside_double_do_not_change_values(self):
        from test_petrel_native_recovery import polygon_doc,vint
        points=[(420000.,2810000.,-100.),(421000.,2810100.,-100.),(420500.,2810300.,-100.)]
        doc=polygon_doc([points]);start=doc.index(struct.pack('<d',points[0][0]))
        for cut in range(start+1,start+24):
            payload=b'BXML\x01'+b'\xa0'+vint(cut)+doc[:cut]+b'\xa0'+vint(len(doc)-cut)+doc[cut:]+b'\xa2'
            with self.subTest(cut=cut):self.assertEqual(decoder.decode_polygons3(payload)[0],[points])

    def test_polygon_rejects_wrong_version_count_and_truncated_document(self):
        from test_petrel_native_recovery import frame,polygon_doc
        parts=[[(0.,0.,0.),(1.,1.,1.)]]
        for doc in [polygon_doc(parts,outer_count=2),polygon_doc(parts,inner_version=[99]),polygon_doc(parts)[:-1]]:
            with self.assertRaises(decoder.DecodeError):decoder.decode_polygons3(frame(doc))

    def test_polygon_large_jumps_and_binary_marker_bytes_are_not_reordered(self):
        from test_petrel_native_recovery import frame,polygon_doc
        tiny=struct.unpack('<d',b'\xa0\x42\x20\x01\x93\0\0\0')[0]
        parts=[[(0.,tiny,0.),(1e6,2e6,3.),(2.,1.,4.)]]
        self.assertEqual(decoder.decode_polygons3(frame(polygon_doc(parts)))[0],parts)

    def test_polygon_attribute_scope_is_explicit(self):
        from test_petrel_native_recovery import frame,polygon_doc
        parts=[[(1.,2.,3.),(4.,5.,6.)]]
        polygons,metadata=decoder.decode_polygons3(frame(polygon_doc(parts,attributes=True)))
        self.assertEqual(polygons,parts);self.assertEqual(metadata['attributes_status'],'not_exported')

    def test_polygon_export_retains_segment_and_vertex_indices(self):
        from test_petrel_native_recovery import frame,polygon_doc,envelope
        import csv
        with tempfile.TemporaryDirectory() as folder:
            package=Path(folder);store=package/'08_native_project/ptd_store';store.mkdir(parents=True)
            parts=[[(1.,1.,1.),(3.,2.,1.)],[],[(5.,5.,5.),(decoder.NATIVE_FLOAT_MAX_SENTINEL,0.,0.),(6.,6.,6.)]]
            data=store/'Data.ptd'
            with sqlite3.connect(data) as db:
                db.executescript('CREATE TABLE data(data_pk INTEGER,droid TEXT,name TEXT,version INTEGER,blob_type TEXT,time_stamp TEXT);CREATE TABLE blob_parts(data_fk INTEGER,part INTEGER,blob_data BLOB);')
                db.execute('INSERT INTO data VALUES(1,?,"Segments",1,"Polygons3","")',(str(uuid.uuid4()),))
                db.execute('INSERT INTO blob_parts VALUES(1,0,?)',(envelope(frame(polygon_doc(parts,[True,False,False]),split=True)),))
            db.close();before=hashlib.sha256(data.read_bytes()).hexdigest()
            args=argparse.Namespace(export_package=str(package),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02)
            self.assertEqual(decoder.run(args),0)
            with (package/'05_spatial/polygons/native_polygons_vertices.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
            self.assertEqual([(row['segment_id'],row['vertex_index']) for row in rows],[('0','0'),('0','1'),('2','0'),('2','2')])
            self.assertEqual(rows[0]['is_closed_native'],'yes')
            self.assertEqual(rows[0]['is_closed_by_repeated_xyz'],'no')
            self.assertEqual(before,hashlib.sha256(data.read_bytes()).hexdigest())

    def test_explicit_trajectory_checkpoint_inside_marker(self) -> None:
        names = BASE_NAMES + [
            "ExplicitTrajectoryProviderData",
            "records",
            "item",
            "x",
            "y",
            "z",
            "md",
            "inclination",
            "azimuth",
        ]
        item = decoder.field_marker(names, "item")
        fields = {name: decoder.field_marker(names, name) for name in decoder.TRAJECTORY_FIELDS["ExplicitTrajectoryProviderData"]}
        body = bytearray(decoder.field_marker(names, "records") + b"\x06\x08\x88\x02")
        expected = []
        for index in range(2):
            values = {
                "x": 450_000.0 + index,
                "y": 2_900_000.0 + index,
                "z": -100.0 * index,
                "md": 100.0 * index,
                "inclination": 0.1 * index,
                "azimuth": 1.0 + index,
            }
            expected.append(values)
            body += item
            for name, value in values.items():
                marker = fields[name]
                if index == 1 and name == "z":
                    marker = marker[:1] + b"\xA0\x80\x20" + marker[1:]
                body += marker + b"\x93" + struct.pack("<d", value)
            body += b"\x01"
        # Bare marker fragments cannot establish a complete, typed object.
        # Previously this permissive path admitted even wrong envelope types.
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_trajectory(dictionary(names) + bytes(body), "ExplicitTrajectoryProviderData")

    def test_model_well_heads_and_trajectory_xy_crosscheck(self) -> None:
        names = BASE_NAMES + [
            "WellTraceSubject",
            "name",
            "definite_survey_provider_tag_",
            "well_head_",
            "double",
        ]
        subject = decoder.field_marker(names, "WellTraceSubject")
        name = decoder.field_marker(names, "name")
        provider = decoder.field_marker(names, "definite_survey_provider_tag_")
        well_head = decoder.field_marker(names, "well_head_")
        double = decoder.field_marker(names, "double")
        provider_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

        def model_subject(well_name: str, x: float, y: float, linked: bool) -> bytes:
            raw_name = well_name.encode("utf-8")
            provider_value = b"\xB1" + provider_id.bytes_le if linked else b"\xA9"
            return (
                subject
                + name
                + b"\x99"
                + decoder.encode_uleb128(len(raw_name))
                + raw_name
                + provider
                + provider_value
                + well_head
                + b"\x03"
                + double
                + b"\x01\x93\x02"
                + struct.pack("<2d", x, y)
                + b"\x01"
            )

        payload = dictionary(names) + model_subject("Well A", 450_000.0, 2_900_000.0, True) + model_subject(
            "Well B", 451_000.0, 2_901_000.0, False
        )
        rows, evidence = decoder.decode_model_well_heads_payload(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(evidence["status"], "decoded")
        self.assertEqual(rows[0]["well_name"], "Well A")
        self.assertEqual(rows[0]["trajectory_provider_object_id"], str(provider_id))
        self.assertEqual((rows[0]["x"], rows[0]["y"]), (450_000.0, 2_900_000.0))
        self.assertEqual(rows[1]["trajectory_provider_object_id"], "")

        trajectory_rows = [
            {
                "object_id": str(provider_id),
                "provider_type": "ExplicitTrajectoryProviderData",
                "record_index": 0,
                "x": 450_000.001,
                "y": 2_900_000.001,
                "z": 75.0,
            }
        ]
        enriched, mappings, crosscheck = decoder.enrich_well_heads_with_trajectory(rows, trajectory_rows, 0.02)
        self.assertEqual(enriched[0]["z"], 75.0)
        self.assertEqual(
            enriched[0]["xy_trajectory_crosscheck_status"],
            "matched_first_native_trajectory_station_within_tolerance",
        )
        self.assertEqual(crosscheck["matched_XY_rows"], 1)
        self.assertEqual(mappings[str(provider_id)]["well_name"], "Well A")
        self.assertEqual(
            enriched[1]["xy_trajectory_crosscheck_status"],
            "no_definitive_trajectory_provider_reference_in_Model_ptd",
        )


if __name__ == "__main__":
    unittest.main()
