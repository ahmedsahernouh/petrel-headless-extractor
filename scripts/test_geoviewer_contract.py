# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic structural and failure tests for the additive source contract."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from geoviewer_contract import POINTER, publish_contract
from validate_geoviewer_contract import main as validate_main, validate_contract


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.package = self.run / "data" / "package"
        self.exports = self.run / "EXPORTS"
        self.package.mkdir(parents=True)
        self.exports.mkdir()
        self.context = {"project_name": "Same", "objects": [
            {"object_id": "grid-a", "name": "Same", "category": "SurfaceSubject"},
            {"object_id": "grid-b", "name": "Same", "category": "SurfaceAttrSubject", "parent_id": "grid-a"},
        ]}
        write(self.package / "01_project_metadata/project_context.json", self.context)
        self.native = {"objects": [
            {"object_id": "grid-a", "name": "Same", "subject_type": "SurfaceSubject", "status": "missing_metadata",
             "blob_type": "RegValGrid2", "unit_status": "unresolved", "declared_template_unit": "ms", "crs_status": "unresolved"},
            {"object_id": "grid-b", "name": "Same", "subject_type": "SurfaceAttrSubject", "status": "decoded",
             "blob_type": "RegValGrid2", "parent_id": "grid-a", "unit": "m", "crs_status": "unresolved"},
        ]}
        write(self.package / "07_workflows_reports/native_recovery/native_recovery_report.json", self.native)
        self.rows = []
        self.add_row("grid-a", "grids/surface.xyz", b"1 2 3\n", "XYZ")
        self.add_row("grid-a", "grids/metadata.json", b"{}", "JSON")
        self.add_row("grid-a", "grids/node_definitions.csv", b"node,defined\n", "CSV")
        self.add_row("grid-b", "grids/property.csv", b"node,value\n", "CSV")
        self.index = {"files": self.rows, "partial_files": [], "status": "delivery_indexed"}
        write(self.exports / "FILE_INDEX.json", self.index)

    def add_row(self, identity: str, path: str, raw: bytes, fmt: str) -> None:
        target = self.exports / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        self.rows.append(dict(object_id=identity, name="Same", category="grids", path=path,
                              format=fmt, bytes=len(raw), status="exported", numerical_qc="exact_readback",
                              integrity="receipt_sha256", sha256=hashlib.sha256(raw).hexdigest()))

    def publish(self) -> dict:
        write(self.exports / "FILE_INDEX.json", self.index)
        return publish_contract(self.package, self.exports, self.index)

    def table(self, name: str) -> list[dict]:
        pointer = json.loads((self.exports / POINTER).read_text())
        folder = (self.exports / pointer["manifest"]).parent
        return [json.loads(line) for line in (folder / (name + ".jsonl")).read_text().splitlines()]

    def test_multiple_representations_one_entity_and_roles(self) -> None:
        self.publish()
        entities = self.table("entities")
        self.assertEqual(len(entities), 2)
        self.assertEqual(sorted(len(x["artifact_keys"]) for x in entities), [1, 3])
        roles = {a["relative_path"]: a["role"] for a in self.table("artifacts")}
        self.assertEqual(roles["grids/property.csv"], "property_values")
        self.assertEqual(roles["grids/node_definitions.csv"], "node_mask")
        self.assertEqual(roles["grids/surface.xyz"], "geometry_values")

    def test_duplicate_names_and_native_uuid_not_uwi(self) -> None:
        self.publish()
        entities = self.table("entities")
        self.assertEqual(len({x["entity_key"] for x in entities}), 2)
        self.assertTrue(all(x["external_uwi"] is None for x in entities))
        self.assertEqual({x["name"] for x in entities}, {"Same"})

    def test_same_uuid_in_second_project_revision_does_not_merge(self) -> None:
        self.publish()
        first_key = next(x["entity_key"] for x in self.table("entities") if x["source_id"] == "grid-a")
        self.context["project_name"] = "Other project"
        write(self.package / "01_project_metadata/project_context.json", self.context)
        self.publish()
        second_key = next(x["entity_key"] for x in self.table("entities") if x["source_id"] == "grid-a")
        self.assertNotEqual(first_key, second_key)

    def test_parent_exact_but_property_not_qualified(self) -> None:
        self.publish()
        links = self.table("relationships")
        self.assertTrue(links)
        self.assertTrue(all(x["status"] == "exact_id_reference" for x in links))
        result = validate_contract(self.exports / POINTER, "paleoscan")
        prop = next(x for x in result["assessments"] if x["category"] == "surface_property")
        self.assertIn("NODE_REGISTRATION_UNVERIFIED", prop["reason_codes"])
        self.assertEqual(result["counts"]["receiving_qualified"], 0)

    def test_recorded_declaration_does_not_resolve_unit(self) -> None:
        self.publish()
        result = validate_contract(self.exports / POINTER)
        grid = next(x for x in result["assessments"] if x["category"] == "surface_geometry")
        self.assertIn("UNRESOLVED_VALUE_UNIT", grid["reason_codes"])
        self.assertTrue(any(x["field"] == "declared_template_unit" for x in self.table("metadata_evidence")))

    def test_sibling_package_path_and_relocation(self) -> None:
        source = self.package / "native_data" / "fallback.csv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x,y\n")
        self.rows[0]["path"] = os.path.relpath(source, self.exports).replace("\\", "/")
        self.rows[0]["bytes"] = source.stat().st_size
        self.rows[0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.publish()
        self.assertEqual(self.table("artifacts")[0]["path_base"], "package")
        moved = Path(self.temp.name) / "moved"
        shutil.move(str(self.run), str(moved))
        result = validate_contract(moved / "EXPORTS" / POINTER)
        self.assertFalse(any(x["code"] == "ARTIFACT_UNAVAILABLE" for x in result["diagnostics"]))

    def test_escape_and_link_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside.csv"
        outside.write_bytes(b"outside")
        self.rows[0]["path"] = os.path.relpath(outside, self.exports).replace("\\", "/")
        with self.assertRaises(ValueError):
            self.publish()
        self.rows[0]["path"] = "grids/link.csv"
        link = self.exports / self.rows[0]["path"]
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.publish()

    def test_pruning_preserves_historical_export_and_blocks_current(self) -> None:
        self.publish()
        (self.exports / "grids/surface.xyz").unlink()
        result = validate_contract(self.exports / POINTER)
        self.assertTrue(any(x["code"] == "ARTIFACT_UNAVAILABLE" for x in result["diagnostics"]))
        self.assertEqual(self.table("artifacts")[0]["original_status"], "exported")

    def test_partial_row_does_not_become_full(self) -> None:
        row = self.rows.pop(0)
        row["status"] = "checkpoint_only_partial"
        self.index["partial_files"].append(row)
        self.publish()
        result = validate_contract(self.exports / POINTER)
        self.assertTrue(any(x["code"] == "PARTIAL_ARTIFACT" for x in result["diagnostics"]))
        grid = next(x for x in result["assessments"] if x["category"] == "surface_geometry")
        self.assertIn("NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD", grid["reason_codes"])

    def test_stale_index_generation_and_table_detected(self) -> None:
        first = self.publish()
        self.rows[0]["name"] = "changed"
        second = self.publish()
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_contract(self.exports / first["manifest"])
        self.assertNotEqual(first["generation"], second["generation"])
        table = (self.exports / second["manifest"]).parent / "entities.jsonl"
        table.write_bytes(table.read_bytes() + b"{}\n")
        with self.assertRaisesRegex(ValueError, "Table hash"):
            validate_contract(self.exports / POINTER)

    def test_commit_interruption_leaves_previous_pointer(self) -> None:
        first = self.publish()
        with patch("geoviewer_contract.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.publish()
        current = json.loads((self.exports / POINTER).read_text())
        self.assertEqual(first, current)
        validate_contract(self.exports / POINTER)

    def test_hash_scope_opt_in(self) -> None:
        self.publish()
        file = self.exports / "grids/surface.xyz"
        file.write_bytes(b"9 8 7\n")
        unchecked = validate_contract(self.exports / POINTER)
        self.assertFalse(any(x["code"] == "HASH_MISMATCH" for x in unchecked["diagnostics"]))
        checked = validate_contract(self.exports / POINTER, hash_artifacts=True)
        self.assertTrue(any(x["code"] == "HASH_MISMATCH" for x in checked["diagnostics"]))

    def test_aggregate_well_membership_uses_exact_row_selector(self) -> None:
        heads = self.package / "02_wells/well_headers/native_well_heads.csv"
        heads.parent.mkdir(parents=True)
        heads.write_text("well_name,model_subject_offset,trajectory_provider_object_id,trajectory_provider_link_status\n"
                         "Same,100,provider-a,native_Model_ptd_definite_survey_provider_exact\n"
                         "Same,200,provider-b,name_candidate\n", encoding="utf-8")
        self.add_row("well_headers", "well_headers/all.csv", heads.read_bytes(), "CSV")
        self.rows[-1]["category"] = "well_headers"
        self.publish()
        members = [x for x in self.table("entities") if x["source_lane"] == "derived_member"]
        self.assertEqual({x["membership_selector"]["value"] for x in members}, {"100", "200"})
        self.assertTrue(all(x["native_uuid"] is None and x["external_uwi"] is None for x in members))
        links = [x for x in self.table("relationships") if x["relation"] == "trajectory_provider"]
        self.assertEqual({x["status"] for x in links}, {"exact_id_reference", "candidate"})

    def test_verified_source_hash_identity_survives_path_and_row_order(self) -> None:
        hashes = {"08_native_project/project_file/A.pet": "a" * 64,
                  "08_native_project/ptd_store/Model.ptd": "b" * 64,
                  "08_native_project/ptd_store/Data.ptd": "c" * 64}
        self.native.update(source_hashes_before=hashes, source_hashes_after=hashes, source_unchanged=True)
        write(self.package / "07_workflows_reports/native_recovery/native_recovery_report.json", self.native)
        self.publish()
        original = {x["source_id"]: x["entity_key"] for x in self.table("entities")}
        changed = dict(hashes)
        changed["08_native_project/project_file/A.pet"] = "a" * 64
        self.native["source_hashes_before"] = {"08_native_project/project_file/B.pet": changed.pop("08_native_project/project_file/A.pet"), **changed}
        self.native["source_hashes_after"] = dict(self.native["source_hashes_before"])
        write(self.package / "07_workflows_reports/native_recovery/native_recovery_report.json", self.native)
        self.index["files"] = list(reversed(self.index["files"]))
        self.publish()
        self.assertEqual(original, {x["source_id"]: x["entity_key"] for x in self.table("entities")})
        pointer = json.loads((self.exports / POINTER).read_text())
        manifest = json.loads((self.exports / pointer["manifest"]).read_text())
        self.assertEqual(manifest["source_fingerprint"], {"pet": "a" * 64, "model": "b" * 64, "data": "c" * 64})
        self.assertTrue(manifest["engine_script_sha256"])

    def test_taxonomy_qa_scalar_log_and_companion_identity(self) -> None:
        self.context["objects"].extend([
            {"object_id": "log-one", "name": "Log", "category": "WellLogSubject"},
            {"object_id": "qa-one", "name": "QA", "category": "QualityAssuranceSubject"},
        ])
        write(self.package / "01_project_metadata/project_context.json", self.context)
        self.add_row("log-one", "logs/data.csv", b"x,y\n", "CSV")
        self.rows[-1]["category"] = "logs"
        self.add_row("qa-one", "grids/qa.csv", b"x,y\n", "CSV")
        self.add_row("companion_123", "companion/data.csv", b"x,y\n", "CSV")
        self.rows[-1]["category"] = "companion_logs"
        self.publish()
        entities = {x["source_id"]: x for x in self.table("entities")}
        self.assertEqual(entities["log-one"]["category"], "well_log")
        self.assertEqual(entities["qa-one"]["category"], "qa_scalar")
        self.assertEqual(entities["companion_123"]["source_lane"], "companion")
        self.assertIsNone(entities["companion_123"]["native_uuid"])
        self.assertEqual(next(a["role"] for a in self.table("artifacts") if a["relative_path"] == "grids/qa.csv"), "property_values")

    def test_cells_are_not_full_payload_and_conflicting_units_block(self) -> None:
        self.rows = []
        self.add_row("grid-a", "grids/ActualName_grid-a_cells.csv", b"i,j,defined\n", "CSV")
        self.index["files"] = self.rows
        self.native["objects"][0]["unit"] = "m"
        self.native["objects"].append({"object_id": "grid-a", "unit": "ft", "unit_status": "validated_metric_profile",
                                       "crs_status": "verified", "status": "decoded"})
        write(self.package / "07_workflows_reports/native_recovery/native_recovery_report.json", self.native)
        self.publish()
        self.assertEqual(self.table("artifacts")[0]["role"], "cell_topology")
        result = validate_contract(self.exports / POINTER)
        grid = next(x for x in result["assessments"] if x["category"] == "surface_geometry")
        self.assertIn("NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD", grid["reason_codes"])
        self.assertIn("CONFLICTING_UNIT", grid["reason_codes"])

    def test_hashing_streams_payload_and_cli_never_overwrites(self) -> None:
        payload = b"x" * (2 * 1024 * 1024)
        target = self.exports / "grids/surface.xyz"
        target.write_bytes(payload)
        self.rows[0]["bytes"] = len(payload)
        self.rows[0]["sha256"] = hashlib.sha256(payload).hexdigest()
        self.publish()
        original = Path.read_bytes
        def guarded(path):
            if path == target:
                raise AssertionError("Bulk payload read_bytes called")
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            validate_contract(self.exports / POINTER, hash_artifacts=True)
        output = Path(self.temp.name) / "validation.json"
        self.assertEqual(validate_main([str(self.exports / POINTER), "--output", str(output)]), 0)
        before = output.read_bytes()
        self.assertEqual(validate_main([str(self.exports / POINTER), "--output", str(output)]), 2)
        self.assertEqual(output.read_bytes(), before)

    def test_dependency_order_excludes_folder_parent_but_blocks_missing_well(self) -> None:
        self.native["objects"].append({"object_id": "log-one", "subject_type": "WellLogSubject",
                                       "status": "decoded", "well_id": "well-one", "unit": "m",
                                       "unit_status": "validated_metric_profile", "crs_status": "verified"})
        write(self.package / "07_workflows_reports/native_recovery/native_recovery_report.json", self.native)
        self.add_row("log-one", "logs/samples.csv", b"md,value\n", "CSV")
        self.rows[-1]["category"] = "logs"
        self.publish()
        result = validate_contract(self.exports / POINTER, "opendtect")
        log = next(x for x in result["assessments"] if x["category"] == "well_log")
        self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE", log["reason_codes"])
        self.assertEqual(result["profile_revision"], "opendtect-7.0.10-odbind-1.0.0-preparation/7")
        self.assertEqual(result["counts"]["receiving_qualified"], 0)
        self.assertLess(result["dependency_order"].index(next(x for x in result["dependency_order"] if ":well-one" in x)),
                        result["dependency_order"].index(log["entity_key"]))


if __name__ == "__main__":
    unittest.main()
