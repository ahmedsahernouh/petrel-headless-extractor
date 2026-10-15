# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for exact parents and usable source dependency payloads.

FIELDVIEWER_CONTRACT_MODULE_ROOT may point at a frozen release's scripts folder
to reproduce a bug without copying or changing that release. All package data
created here is synthetic and temporary.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
MODULE_ROOT = Path(os.environ.get("FIELDVIEWER_CONTRACT_MODULE_ROOT", SCRIPT_DIR)).resolve()
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(MODULE_ROOT))

import test_geoviewer_receiving_profiles as profile_fixtures  # noqa: E402
from geoviewer_contract_retention import build_retention_view  # noqa: E402
from validate_geoviewer_contract import _load_contract, assess_catalogue  # noqa: E402


class DependencyGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The fixture's TemporaryDirectory() must not use the system C: temp.
        cls._prior_tempdir = tempfile.tempdir
        target = Path(os.environ.get("FIELDVIEWER_TEST_TEMP", "D:/FV_RC/contract_dependency_tmp"))
        target.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(target)

    @classmethod
    def tearDownClass(cls):
        tempfile.tempdir = cls._prior_tempdir

    def setUp(self):
        self.fx = profile_fixtures.ProfileIntegrationTests("runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.ready_log()
        self.assertEqual(len([obj for obj in self.fx.native["objects"]
                              if obj.get("object_id") == "well-one"]), 1)
        self.catalogue, _ = _load_contract(self.fx.exports / "INTEROPERABILITY_CURRENT.json")
        self.log_key = self.fx.keys["log-one"]
        self.well_key = self.fx.keys["well-one"]
        self.grid_key = self.fx.keys["grid-a"]

    def assess(self, *, profile="source", decisions=None, catalogue=None,
               hash_artifacts=False, retention_view=None):
        return assess_catalogue(**copy.deepcopy(catalogue or self.catalogue), profile=profile,
                                decisions=decisions, hash_artifacts=hash_artifacts,
                                retention_view=retention_view)

    def log(self, report):
        return next(row for row in report["assessments"] if row["entity_key"] == self.log_key)

    def links(self, catalogue=None):
        catalogue = catalogue or self.catalogue
        return [row for row in catalogue["tables"]["relationships"]
                if row["source_key"] == self.log_key and row["relation"] == "source_well"]

    def add_dependency_artifact(self, *, owner=None, role="data"):
        """Give a synthetic target one accepted copy of an existing file."""
        owner = owner or self.well_key
        sample = next(row for row in self.catalogue["tables"]["artifacts"]
                      if row["entity_key"] == self.log_key and row["role"] == "data")
        row = copy.deepcopy(sample)
        relative = "synthetic_dependency/well-data.csv"
        target = self.fx.exports / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((self.fx.exports / sample["relative_path"]).read_bytes())
        row.update(artifact_key="synthetic-dependency-copy", entity_key=owner,
                   owner_keys=[owner], role=role, relative_path=relative)
        self.catalogue["tables"]["artifacts"].append(row)
        return row

    def test_existing_bound_well_log_remains_receiving_qualified_without_well_head(self):
        self.assertFalse(any(row["entity_key"] == self.well_key
                             for row in self.catalogue["tables"]["artifacts"]))
        report = self.assess(profile="opendtect", decisions=self.fx.decisions())
        self.assertEqual(self.log(report)["receiving_state"], "qualified_for_planned_mapping")

    def test_source_well_links_require_one_well_and_every_citation_exact(self):
        decision = self.fx.decisions()
        original = copy.deepcopy(self.links()[0])
        variants = {
            "candidate_only": [dict(original, status="candidate")],
            "exact_plus_candidate": [original, dict(original, status="candidate")],
            "two_distinct": [original, dict(original, target_key=self.grid_key)],
            "wrong_category": [dict(original, target_key=self.grid_key)],
            "no_link": [],
        }
        for name, replacement in variants.items():
            with self.subTest(name=name):
                catalogue = copy.deepcopy(self.catalogue)
                catalogue["tables"]["relationships"] = [row for row in catalogue["tables"]["relationships"]
                    if not (row["source_key"] == self.log_key and row["relation"] == "source_well")]
                catalogue["tables"]["relationships"].extend(replacement)
                source = self.log(self.assess(catalogue=catalogue))
                receiving = self.log(self.assess(profile="opendtect", decisions=decision, catalogue=catalogue))
                self.assertIn("SOURCE_WELL_UNRESOLVED", source["reason_codes"])
                self.assertNotEqual(receiving["receiving_state"], "qualified_for_planned_mapping")

        duplicate = copy.deepcopy(self.catalogue)
        duplicate["tables"]["relationships"].append(dict(original, locator="synthetic-duplicate-citation"))
        receiving = self.log(self.assess(profile="opendtect", decisions=decision, catalogue=duplicate))
        self.assertEqual(receiving["receiving_state"], "qualified_for_planned_mapping")

    def test_wrong_category_cannot_be_bound_as_existing_well(self):
        catalogue = copy.deepcopy(self.catalogue)
        self.links(catalogue)[0]["target_key"] = self.grid_key
        decision = self.fx.decisions(record={"well_target": dict(source_entity_key=self.grid_key,
                            target_id="synthetic-existing", state="existing")})
        result = self.log(self.assess(profile="opendtect", decisions=decision, catalogue=catalogue))
        self.assertNotEqual(result["receiving_state"], "qualified_for_planned_mapping")
        self.assertIn("SOURCE_WELL_UNRESOLVED", result["reason_codes"])

    def test_paleoscan_planned_well_needs_a_qualified_parent_mapping(self):
        planned = dict(source_entity_key=self.well_key, target_id="future-well", state="planned")
        decision = self.fx.decisions(profile="paleoscan", record={"well_target": planned})
        result = self.log(self.assess(profile="paleoscan", decisions=decision))
        self.assertNotEqual(result["receiving_state"], "qualified_for_planned_mapping")
        self.assertIn("PLANNED_WELL_MAPPING_NOT_QUALIFIED", result["receiving_assessment"]["reason_codes"])

    def test_paleoscan_reference_requires_full_accepted_seismic_payload(self):
        catalogue = copy.deepcopy(self.catalogue)
        source = next(row for row in catalogue["tables"]["entities"] if row["entity_key"] == self.grid_key)
        source["source_status"] = "decoded"
        cube_key = self.fx.keys["grid-b"]
        cube = next(row for row in catalogue["tables"]["entities"] if row["entity_key"] == cube_key)
        cube["category"] = "seismic"
        cube["source_status"] = "decoded"
        grid = dict(dimensions=[2, 3], origin_xy=[100, 200], i_basis=[1, 0], j_basis=[0, 1],
                    index_order="i_major_j_minor")
        values = dict(unit="m", unit_status="validated_metric_profile", crs="EPSG:32639",
                      crs_status="verified", depth_unit="m", horizontal_unit="m",
                      vertical_domain="depth", vertical_unit="m", vertical_reference="MSL",
                      surface_role="horizon_geometry", grid_registration=grid)
        metadata = catalogue["tables"]["metadata_evidence"]
        metadata[:] = [row for row in metadata if not (row["entity_key"] == self.grid_key
                                                      and row["field"] in values)]
        template = metadata[0]
        for field, value in values.items():
            metadata.append(dict(template, entity_key=self.grid_key, field=field, raw_value=value,
                                 normalized_value=value, evidence_status="recorded_source_value",
                                 locator="synthetic-reviewed-source/" + field))
        seismic_fixture = profile_fixtures.SeismicPreparationTests("runTest")
        seismic_fixture.setUp()
        seismic_fields = dict(seismic_conversion=seismic_fixture.plan,
                              seismic_source=dict(size=[2, 3, 4], zstart=0, zinc=2),
                              conversion_qc=seismic_fixture.qc)
        for field, value in seismic_fields.items():
            metadata.append(dict(template, entity_key=cube_key, field=field, raw_value=value,
                                 normalized_value=value, evidence_status="recorded_source_value",
                                 locator="synthetic-conversion-receipt/" + field))
        catalogue["tables"]["relationships"].append(dict(source_key=self.grid_key,
            target_key=cube_key, relation="reference_seismic", status="exact_id_reference",
            locator="synthetic-exact-reference"))
        artifact = next(row for row in catalogue["tables"]["artifacts"] if row["entity_key"] == cube_key)
        artifact["role"] = "metadata"
        survey = dict(target_id="survey", crs="EPSG:32639", horizontal_unit="m",
                      vertical_domain="depth", vertical_unit="m", vertical_reference="MSL")
        decision = self.fx.decisions(profile="paleoscan", source_id="grid-a", record=dict(
            survey=survey, reference_volume_binding=dict(source_entity_key=cube_key,
                target_volume_id="reference-volume", method="synthetic exact identity review")))
        def surface(cat):
            report = self.assess(profile="paleoscan", decisions=decision, catalogue=cat)
            return next(row for row in report["assessments"] if row["entity_key"] == self.grid_key)

        metadata_only = surface(catalogue)
        self.assertNotEqual(metadata_only["receiving_state"], "qualified_for_planned_mapping")
        self.assertIn("REFERENCE_VOLUME_PAYLOAD_UNAVAILABLE",
                      metadata_only["receiving_assessment"]["reason_codes"])

        artifact["role"] = "full_payload"
        full = surface(catalogue)
        self.assertEqual(full["receiving_state"], "qualified_for_planned_mapping",
                         full["receiving_assessment"]["reason_codes"])

        artifact["numerical_qc"] = "unvalidated"
        bad_qc = surface(catalogue)
        self.assertIn("REFERENCE_VOLUME_PAYLOAD_UNAVAILABLE",
                      bad_qc["receiving_assessment"]["reason_codes"])

    def test_source_dependency_needs_accepted_full_role_qc_and_digest(self):
        row = self.add_dependency_artifact()
        self.assertNotIn("REQUIRED_DEPENDENCY_UNAVAILABLE", self.log(self.assess())["reason_codes"])
        variants = {
            "metadata_role": {"role": "metadata"},
            "wrong_role": {"role": "geometry_values"},
            "partial_section": {"index_section": "partial_files"},
            "not_exported": {"original_status": "checkpoint_only_partial"},
            "bad_qc": {"numerical_qc": "unvalidated"},
            "missing_digest": {"recorded_sha256": None},
        }
        for name, changes in variants.items():
            with self.subTest(name=name):
                catalogue = copy.deepcopy(self.catalogue)
                next(item for item in catalogue["tables"]["artifacts"]
                     if item["artifact_key"] == row["artifact_key"]).update(changes)
                result = self.log(self.assess(catalogue=catalogue))
                self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE", result["reason_codes"])

    def test_missing_and_same_size_tampered_dependency_do_not_satisfy_hash_check(self):
        row = self.add_dependency_artifact()
        dep = self.fx.exports / row["relative_path"]
        original = dep.read_bytes()
        try:
            dep.write_bytes(b"x" * len(original))
            self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE",
                          self.log(self.assess(hash_artifacts=True))["reason_codes"])
            dep.unlink()
            self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE",
                          self.log(self.assess())["reason_codes"])
        finally:
            dep.parent.mkdir(parents=True, exist_ok=True)
            dep.write_bytes(original)

    def test_pruned_dependency_copy_is_not_available(self):
        row = self.add_dependency_artifact()
        artifacts = self.catalogue["tables"]["artifacts"]
        dispositions = [dict(artifact_key=artifact["artifact_key"],
                             status="pruned" if artifact["artifact_key"] == row["artifact_key"] else "retained",
                             reason="Synthetic retention declaration",
                             evidence_reference="fixture-retention") for artifact in artifacts]
        view = build_retention_view(generation=self.catalogue["manifest"]["generation"],
                manifest_sha256=self.catalogue["pointer"]["manifest_sha256"],
                artifacts=artifacts, dispositions=dispositions)
        path = Path(self.fx.temp.name) / "retention.json"
        path.write_text(json.dumps(view), encoding="utf-8")
        result = self.log(self.assess(retention_view=path))
        self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE", result["reason_codes"])

    def test_other_exact_dependency_kinds_need_full_qualified_payload(self):
        self.add_dependency_artifact()
        for kind in ("trajectory_provider", "materialized_payload"):
            with self.subTest(kind=kind):
                catalogue = copy.deepcopy(self.catalogue)
                # Grid-a's surviving mask and metadata cannot substitute for
                # its geometry payload in either dependency relation.
                next(row for row in catalogue["tables"]["artifacts"]
                     if row["entity_key"] == self.grid_key and row["role"] == "geometry_values")["role"] = "metadata"
                catalogue["tables"]["relationships"].append(dict(
                    source_key=self.log_key, target_key=self.grid_key, relation=kind,
                    status="exact_id_reference", locator="synthetic-dependency"))
                self.assertIn("REQUIRED_DEPENDENCY_UNAVAILABLE",
                              self.log(self.assess(catalogue=catalogue))["reason_codes"])


if __name__ == "__main__":
    unittest.main()
