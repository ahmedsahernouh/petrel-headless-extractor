# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic retention-view contract tests; no original project is opened."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from geoviewer_contract import canonical
from geoviewer_contract_retention import (
    MAX_VIEW_BYTES, build_retention_view, load_retention_view,
)


GENERATION = "test-generation"
MANIFEST = "a" * 64


class RetentionViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifacts = [
            dict(artifact_key=f"artifact:{n}", path_base="exports", relative_path=f"data/{n}.csv",
                 expected_bytes=n + 1, recorded_sha256=None, index_section="files",
                 original_status="exported", extra_original_field="left to source contract")
            for n in range(4)
        ]
        self.dispositions = [
            dict(artifact_key=f"artifact:{n}", status=status, reason=f"Decision {n}",
                 evidence_reference=f"retention-log#row={n}")
            for n, status in enumerate(("retained", "pruned", "unavailable", "external_reference"))
        ]

    def build(self) -> dict:
        return build_retention_view(generation=GENERATION, manifest_sha256=MANIFEST,
                                    artifacts=self.artifacts, dispositions=self.dispositions)

    def save(self, view: dict, name: str = "retention.json") -> Path:
        path = self.root / name
        path.write_bytes(canonical(view))
        return path

    def load(self, path: Path) -> dict:
        return load_retention_view(path, generation=GENERATION, manifest_sha256=MANIFEST,
                                   artifacts=self.artifacts)

    def test_valid_mixed_retention_preserves_original_state(self) -> None:
        view = self.build()
        rows = self.load(self.save(view))
        self.assertEqual(view["counts"], dict(original_artifacts=4, retained=1, pruned=1,
                                               unavailable=1, external_reference=1))
        self.assertEqual(set(rows), {a["artifact_key"] for a in self.artifacts})
        self.assertEqual(rows["artifact:1"]["source"]["original_status"], "exported")
        self.assertEqual(rows["artifact:1"]["retention"]["status"], "pruned")
        self.assertNotIn("available", rows["artifact:0"])

    def test_relocation_never_opens_original_artifacts(self) -> None:
        path = self.save(self.build())
        moved = self.root / "moved"
        moved.mkdir()
        relocated = moved / path.name
        path.rename(relocated)
        self.assertEqual(len(self.load(relocated)), 4)
        self.assertFalse((self.root / "data").exists())

    def test_wrong_generation_or_manifest_hash(self) -> None:
        path = self.save(self.build())
        for generation, manifest in (("wrong", MANIFEST), (GENERATION, "b" * 64)):
            with self.subTest(generation=generation, manifest=manifest):
                with self.assertRaises(ValueError):
                    load_retention_view(path, generation=generation, manifest_sha256=manifest,
                                        artifacts=self.artifacts)

    def test_missing_unknown_duplicate_inventory(self) -> None:
        for bad in (self.dispositions[:-1],
                    [*self.dispositions[:-1], {**self.dispositions[-1], "artifact_key": "unknown"}],
                    [*self.dispositions[:-1], self.dispositions[0]]):
            with self.assertRaises(ValueError):
                build_retention_view(generation=GENERATION, manifest_sha256=MANIFEST,
                                     artifacts=self.artifacts, dispositions=bad)
        view = self.build()
        for mutation in (lambda v: v["artifacts"].pop(),
                         lambda v: v["artifacts"][-1]["source"].update(artifact_key="unknown"),
                         lambda v: v["artifacts"][-1]["source"].update(artifact_key="artifact:0")):
            changed = copy.deepcopy(view)
            mutation(changed)
            with self.assertRaises(ValueError):
                self.load(self.save(changed))

    def test_tampered_source_row_and_count(self) -> None:
        for field, value in (("relative_path", "elsewhere.csv"), ("expected_bytes", 900),
                             ("expected_bytes", True),
                             ("original_status", "failed"), ("recorded_sha256", "b" * 64)):
            changed = copy.deepcopy(self.build())
            changed["artifacts"][0]["source"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(self.save(changed))
        changed = self.build()
        changed["counts"]["retained"] = 2
        with self.assertRaises(ValueError):
            self.load(self.save(changed))

    def test_invalid_state_reason_and_provenance(self) -> None:
        for field, value in (("status", "missing"), ("status", []), ("reason", "  "),
                             ("evidence_reference", "")):
            dispositions = copy.deepcopy(self.dispositions)
            dispositions[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_retention_view(generation=GENERATION, manifest_sha256=MANIFEST,
                                     artifacts=self.artifacts, dispositions=dispositions)
            changed = copy.deepcopy(self.build())
            changed["artifacts"][0]["retention"][field] = value
            with self.assertRaises(ValueError):
                self.load(self.save(changed))

    def test_linked_path_and_bounds(self) -> None:
        path = self.save(self.build())
        link = self.root / "linked.json"
        try:
            os.symlink(path, link)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(ValueError):
                self.load(link)
        huge = self.root / "oversize.json"
        with huge.open("wb") as handle:
            handle.truncate(MAX_VIEW_BYTES + 1)
        with self.assertRaises(ValueError):
            self.load(huge)
        with self.assertRaises(ValueError):
            build_retention_view(generation=GENERATION, manifest_sha256=MANIFEST,
                                 artifacts=self.artifacts * 50_001, dispositions=[])

    def test_duplicate_json_member_rejected(self) -> None:
        raw = canonical(self.build()).decode().replace('"schema":', '"schema":"shadow","schema":', 1)
        path = self.root / "duplicate.json"
        path.write_text(raw, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load(path)


if __name__ == "__main__":
    unittest.main()
