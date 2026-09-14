# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Regressions for bounded probes and the companion copy/conversion size gate."""
from __future__ import annotations

import csv
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import portable_petrel_companion_extract as companion
import report_petrel_project_audit as audit


class ProbePath:
    """Model a huge file whose prefix is readable, but full-file reads fail."""
    def __init__(self, prefix, limit):
        self.prefix, self.limit, self.read_sizes = prefix, limit, []

    def read_bytes(self):
        raise MemoryError('Full-file allocation is forbidden')

    def read_text(self, **kwargs):
        raise MemoryError('Full-file allocation is forbidden')

    def open(self, mode):
        assert mode == 'rb'
        owner = self
        class Stream(io.BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= owner.limit:
                    raise AssertionError(f'Unbounded/oversized probe: {size}')
                owner.read_sizes.append(size)
                return super().read(size)
        return Stream(self.prefix)


class LargeFileTests(unittest.TestCase):
    def test_binary_and_text_detection_are_bounded(self):
        binary = ProbePath(b'C' * 3200 + b'\x00' * 65536, companion.TEXT_SAMPLE_BYTES)
        self.assertFalse(companion.looks_text(binary))
        self.assertEqual(binary.read_sizes, [65536])
        self.assertTrue(companion.looks_text(ProbePath(b'Well MD\nA 10\n', 65536)))

    def test_well_top_header_is_bounded(self):
        path = ProbePath(b'BEGIN HEADER\nWell\nSurface\nMD\nEND HEADER\n', companion.HEADER_SAMPLE_BYTES)
        self.assertTrue(companion.is_petrel_well_tops_ascii(path))
        self.assertEqual(path.read_sizes, [65536, 262144])

    def test_long_single_line_profile_does_not_allocate_whole_file(self):
        path = ProbePath(b'x' * (companion.TEXT_PROFILE_BYTES + 20), companion.TEXT_PROFILE_BYTES + 1)
        profile = companion.profile_text(path)
        self.assertEqual(profile['scope'], 'prefix_only')
        self.assertIsNone(profile['line_count'])
        self.assertIsNone(profile['nonempty_line_count'])
        self.assertEqual(profile['sample_bytes'], companion.TEXT_PROFILE_BYTES)
        self.assertLessEqual(len(profile['first_nonempty_line']), 300)

    def test_small_text_profile_retains_complete_counts(self):
        profile = companion.profile_text(ProbePath(b'Well\tMD\n\nA\t10\n', companion.TEXT_PROFILE_BYTES + 1))
        self.assertEqual(profile['scope'], 'complete_file')
        self.assertEqual(profile['line_count'], 3)
        self.assertEqual(profile['nonempty_line_count'], 2)
        self.assertEqual(profile['likely_delimiter'], 'tab')

    def test_png_dimensions_read_only_header(self):
        path = ProbePath(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 800, 600), 24)
        self.assertEqual(audit.png_dimensions(path), (800, 600))
        self.assertEqual(path.read_sizes, [24])

    def test_size_gate_prevents_probes_copy_and_conversion(self):
        with tempfile.TemporaryDirectory(prefix='Petrel Size Gate ') as temporary:
            root = Path(temporary)
            source = root/'source'; source.mkdir()
            project = source/'Fixture.pet'; project.write_text('fixture')
            (source/'Fixture.ptd').mkdir()
            oversized = source/'Oversized.las'; oversized.write_bytes(b'x' * 4096)
            package = root/'output'; (package/'00_manifest').mkdir(parents=True)
            (package/'00_manifest/export_manifest.csv').write_text('export_file,sha256\n')
            before = companion.sha256(oversized)
            argv = ['companion', '--project-file', str(project), '--export-package', str(package),
                    '--project-name', 'Fixture', '--mode', 'convert', '--max-file-bytes', '1024']
            with patch.object(sys, 'argv', argv), \
                 patch.object(companion, 'looks_text', side_effect=AssertionError('Oversized text probe')), \
                 patch.object(companion, 'convert_las', side_effect=AssertionError('Oversized conversion')), \
                 patch.object(companion.shutil, 'copy2', side_effect=AssertionError('Oversized copy')):
                self.assertEqual(companion.main(), 0)
            with (package/'00_manifest/companion_source_inventory.csv').open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row['status'], 'skipped_size_limit')
            self.assertEqual(row['preserved_file'], '')
            self.assertEqual(row['converted_files'], '')
            self.assertEqual(row['text_profile_scope'], 'not_sampled_size_limit')
            self.assertEqual(row['sha256'], before)
            self.assertEqual(companion.sha256(oversized), before)
            report = json.loads((package/'07_workflows_reports/portable_extractor/companion_capability_report.json').read_text())
            self.assertEqual(report['counts']['converted_source_files'], 0)
            self.assertEqual(report['counts']['skipped_size_limit'], 1)
            self.assertTrue((package/'99_unexported_or_manual/portable_unsupported_inventory.csv').exists())


if __name__ == '__main__':
    unittest.main()
