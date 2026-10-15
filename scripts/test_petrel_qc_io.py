# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused regression and security tests for package-QC I/O reuse."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_geoscience_tools as tools


class QcIoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Petrel QC IO ")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, names=("data/a.bin", "data/b.bin"), *, hashes=None, folder="package") -> Path:
        package = self.root / folder
        (package / "00_manifest").mkdir(parents=True)
        rows = []
        for index, name in enumerate(names):
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((f"fixture-{index}\n").encode())
            rows.append({"export_file":name, "sha256":tools.sha256(path)})
        for index, digest in (hashes or {}).items():
            rows[index]["sha256"] = digest
        with (package / "00_manifest/export_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("export_file", "sha256"))
            writer.writeheader()
            writer.writerows(rows)
        return package

    def args(self, package: Path, output_name="qc") -> dict:
        return {"export_package":str(package), "output_dir":str(self.root / output_name),
                "petrel_version":"test-fixture"}

    def test_qc_result_matches_baseline_contract(self):
        package = self.package()
        response = tools.qc_data_package(self.args(package))
        result = tools.read_json(Path(response["report_path"]))
        expected = {
            "package":str(package.resolve()), "manifest_hash_failures":[], "manifest_omissions":[], "las":[],
            "native_artifact_qc":{"status":"not_present", "receipt":None, "accepted_objects":0,
                "declared_artifacts":0, "unique_artifacts":0, "checked_artifacts":0,
                "matched_artifacts":0, "duplicate_declarations":0, "failures":[]},
            "duplicate_well_names":{}, "well_heads":0, "xy_crosscheck_statuses":{},
            "trajectories":[], "crs_evidence":{"status":"unavailable"},
            "boundary":"Parsing and numeric checks do not prove geological correctness. No names, units, datums or source rows were repaired.",
        }
        self.assertEqual(result, expected)
        self.assertEqual(response["status"], "passed")
        self.assertEqual(response["summary"], {
            "manifest_rows":2, "manifest_hash_failures":0, "manifest_omitted_transients":0, "las_files":0,
            "las_parse_failures":0, "sparse_las_files":0, "well_heads":0,
            "duplicate_well_names":{}, "trajectory_objects":0,
            "native_artifacts_checked":0, "native_artifacts_matched":0, "native_artifact_failures":0,
            "native_las_files":0, "companion_las_files":0,
        })

    def test_reuses_initial_hashes_and_resolves_package_root_once(self):
        package = self.package()
        target_paths = {os.path.normcase(str(path.resolve())) for path in tools.safe_files(package)}
        hash_counts = Counter()
        real_sha256 = tools.sha256

        def counted_hash(path):
            normalized = os.path.normcase(str(path))
            if normalized in target_paths:
                hash_counts[normalized] += 1
            return real_sha256(path)

        with patch.object(tools, "sha256", side_effect=counted_hash):
            tools.qc_data_package(self.args(package))

        self.assertEqual(set(hash_counts.values()), {2})

    def test_manifest_validation_reuses_supplied_canonical_root(self):
        package = self.package()
        real_contained_file = tools.contained_file
        with patch.object(tools, "contained_file", wraps=real_contained_file) as contained:
            tools.manifest_records(package, resolved_package=package.resolve())
        self.assertEqual(contained.call_count, 2)
        self.assertTrue(all(call.kwargs.get("resolved_directory") == package.resolve()
                            for call in contained.call_args_list))

    def test_tampered_artifact_is_reported_without_extra_qc_hash(self):
        package = self.package()
        (package / "data/a.bin").write_bytes(b"tampered\n")
        response = tools.qc_data_package(self.args(package))
        self.assertEqual(response["status"], "failed")
        self.assertEqual(tools.read_json(Path(response["report_path"]))["manifest_hash_failures"], ["data/a.bin"])

    def test_well_formed_wrong_manifest_hash_is_reported(self):
        package = self.package(hashes={1:"0" * 64})
        response = tools.qc_data_package(self.args(package))
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["summary"]["manifest_hash_failures"], 1)

    def test_missing_and_traversal_manifest_paths_are_rejected(self):
        for index, name in enumerate(("data/missing.bin", "../outside.bin")):
            package = self.package(folder=f"package-invalid-{index}")
            manifest = package / "00_manifest/export_manifest.csv"
            manifest.write_text(f"export_file,sha256\n{name},{'0' * 64}\n", encoding="utf-8")
            with self.subTest(name=name), self.assertRaisesRegex(tools.InputError, "escapes package or is missing"):
                tools.qc_data_package(self.args(package, f"qc-invalid-{index}"))

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_junction_escape_is_rejected_before_hashing(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.bin").write_bytes(b"outside\n")
        package = self.package()
        link = package / "linked"
        created = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                                 capture_output=True, text=True)
        if created.returncode:
            self.skipTest("Unable to create test junction: " + created.stderr.strip())
        with self.assertRaisesRegex(tools.InputError, "Linked source entries are not supported"):
            tools.qc_data_package(self.args(package))

    def test_mutation_after_initial_hash_is_caught_by_finish(self):
        package = self.package()
        real_manifest_records = tools.manifest_records

        def mutate_after_initial_hash(*args, **kwargs):
            records = real_manifest_records(*args, **kwargs)
            (package / "data/a.bin").write_bytes(b"changed during QC\n")
            return records

        with patch.object(tools, "manifest_records", side_effect=mutate_after_initial_hash):
            with self.assertRaisesRegex(RuntimeError, "Source changed during processing"):
                tools.qc_data_package(self.args(package))


if __name__ == "__main__":
    unittest.main(verbosity=2)
