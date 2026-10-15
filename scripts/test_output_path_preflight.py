# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Offline path-planning regressions. No real Petrel source is opened.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import contextlib
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standalone_petrel_extract as core
import petrel_native_recovery as native


class OutputPathTests(unittest.TestCase):
    def plan(self, root, label='Example_project_with_long_name'):
        return core.plan_run_paths(root, label, timestamp='20260921_114208', token='123abc')

    def test_short_root_keeps_label_and_identity(self):
        plan = self.plan(Path('C:/FV'))
        self.assertFalse(plan['label_shortened'])
        self.assertTrue(plan['stem'].endswith('_20260921_114208_123abc'))
        self.assertEqual(set(plan['object_path_plans']), set(native.TYPES))

    def test_source_aware_companion_budget_covers_genuine_demo_path(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'Petrel2010 demo project.pet';source.write_bytes(b'fixture')
            source.with_suffix('.ptd').mkdir()
            companion=source.parent/'Petrel2010 demo project.sim/SECTOR_MODELLING-E100_SECTOR_MODEL/SECTOR_MODELLING-E100_SECTOR_MODEL.ECLEND'
            companion.parent.mkdir(parents=True);companion.write_bytes(b'fixture')
            root=Path('C:/Users/sample_user12/Petrel_Extracts')
            old=core.plan_run_paths(root,source.stem,timestamp='20261008_024409',token='c560bc')
            plan=core.plan_run_paths(root,source.stem,timestamp='20261008_024409',token='c560bc',project_file=source)
            from geoviewer_output_paths import fits_file
            self.assertGreater(plan['label_characters_removed'],old['label_characters_removed'])
            package=Path(plan['planned_native_root']).parent
            self.assertTrue(fits_file(package/'09_source_companions'/companion.relative_to(source.parent)))
            self.assertEqual(plan['source_files_inventoried'],2)
            self.assertTrue(plan['stem'].endswith('_20261008_024409_c560bc'))

    def test_native_snapshot_and_conversion_suffixes_are_reserved(self):
        from geoviewer_output_paths import source_reservations
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'a.pet';source.write_bytes(b'p');store=source.with_suffix('.ptd');store.mkdir()
            (store/'Data.ptd').write_bytes(b'd');(source.parent/'curve.las').write_bytes(b'l')
            (source.parent/'sheet.xlsx').write_bytes(b'x')
            paths,_=source_reservations(source)
            self.assertIn(Path('08_native_project/ptd_store/Data.ptd'),paths)
            self.assertIn(Path('10_converted_ascii/las/curve.summary.json'),paths)
            self.assertIn(Path('10_converted_ascii/workbooks/sheet/workbook.summary.json'),paths)

    def test_impossible_source_relative_path_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'p.pet';source.write_bytes(b'p');source.with_suffix('.ptd').mkdir()
            deep=source.parent/('a'*85)/('b'*85)/'c.txt';deep.parent.mkdir(parents=True);deep.write_bytes(b'x')
            output=source.parent.parent/'unused_path_guard_output'
            with self.assertRaisesRegex(core.g.InputError,'shorter output root'):
                core.plan_run_paths(output,'p',project_file=source)
            self.assertFalse(output.exists())

    def test_utf16_and_parent_directory_limits(self):
        from geoviewer_output_paths import windows_length, fits_file
        self.assertEqual(windows_length('\U0001f600'),2)
        self.assertFalse(fits_file(Path('C:/')/('d'*246)/'a'))

    def test_shapefile_temporary_paths_and_colocated_toolkit_exclusion(self):
        from geoviewer_output_paths import source_reservations
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'p.pet';source.write_bytes(b'p');source.with_suffix('.ptd').mkdir()
            (source.parent/'shape.shp').write_bytes(b's')
            (source.parent/'run_portable_petrel_extract.bat').write_bytes(b'b')
            (source.parent/'toolkit.json').write_bytes(b'{}')
            scripts=source.parent/'scripts';scripts.mkdir();(scripts/'invoke_portable_petrel_extract.ps1').write_bytes(b'p')
            deep=scripts/('x'*100)/('y'*100)/'excluded.bin';deep.parent.mkdir(parents=True);deep.write_bytes(b'x')
            paths,_=source_reservations(source)
            self.assertIn(Path('10_converted_ascii/gis/shp_conversion_00000000/shape.summary.json'),paths)
            self.assertFalse(any('excluded.bin' in str(p) for p in paths))

    def test_incomplete_source_enumeration_fails_closed(self):
        from geoviewer_output_paths import source_reservations
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'p.pet';source.write_bytes(b'p');source.with_suffix('.ptd').mkdir()
            def inaccessible(root, **kwargs):
                kwargs['onerror'](PermissionError('access denied'))
                return iter([])
            with patch('geoviewer_paths.os.walk',side_effect=inaccessible):
                with self.assertRaisesRegex(OSError,'inventory is incomplete'):
                    source_reservations(source)

    def test_one_character_boundary_shortens_label_not_object_id(self):
        label = 'x' * 28
        found = None
        for length in range(1, 180):
            try:
                plan = self.plan(Path('C:/') / ('r' * length), label)
            except core.g.InputError:
                continue
            if plan['label_characters_removed'] == 1:
                found = plan
                break
        self.assertIsNotNone(found)
        native_root = Path(found['planned_native_root'])
        full_id = str(uuid.UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))
        token = native.compact_object_id(full_id)
        for kind, plan in found['object_path_plans'].items():
            self.assertTrue(plan['final_name'].endswith(token) or plan['final_name'].endswith(uuid.UUID(full_id).hex))
            for name in native._object_relative_paths(kind):
                self.assertLess(len(str(native_root / plan['final_name'] / name)), native.WINDOWS_PATH_LIMIT)
        old_root = Path(str(native_root).replace('x'*27 + '_20260921', 'x'*28 + '_20260921'))
        with self.assertRaises(native.QCError):
            native.object_output_plan(old_root, dict(object_id=full_id, name='objectxx', blob_type='RegValGrid2'))

    def test_unc_budget_uses_complete_server_and_share(self):
        root = Path(r'\\server.example.local\shared_data\Trials')
        plan = self.plan(root)
        self.assertTrue(plan['planned_native_root'].startswith(str(root)))
        self.assertTrue(Path(plan['planned_native_root']).is_relative_to(root))

    def test_impossible_root_fails_without_creating_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / ('r' * 230)
            with self.assertRaisesRegex(core.g.InputError, 'shorter output root'):
                self.plan(root)
            self.assertFalse(root.exists())

    def test_no_path_io_during_planning(self):
        with patch.object(Path, 'mkdir', side_effect=AssertionError('No writes')), \
             patch.object(Path, 'open', side_effect=AssertionError('No reads')), \
             patch.object(Path, 'stat', side_effect=AssertionError('No probes')):
            self.plan(Path('C:/FV'))

    def test_invalid_identity_or_relative_root_rejected(self):
        with self.assertRaises(core.g.InputError):
            self.plan(Path('relative'))
        with self.assertRaises(core.g.InputError):
            core.plan_run_paths(Path('C:/FV'), 'project', token='../bad')

    def test_package_reservation_matches_powershell_layout(self):
        script = (Path(__file__).parent / 'new_export_package.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"', script)
        self.assertIn('$exportId = "p_${timestamp}_$([guid]::NewGuid().ToString(\'N\').Substring(0,6))"', script)
        self.assertRegex(self.plan(Path('C:/FV'))['planned_native_root'], r'p_\d{8}_\d{6}_[0-9a-f]{6}')

    def test_launcher_rejects_impossible_root_before_hash_or_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source/Demo.pet'
            source.parent.mkdir()
            source.write_bytes(b'fixture only')
            source.with_suffix('.ptd').mkdir()
            output = Path(folder) / ('r' * 230)
            display = MagicMock(elapsed=0.0)
            console = MagicMock()
            console.start.return_value = display
            argv = ['extractor', '--project-file', str(source), '--output-root', str(output)]
            with patch.object(sys, 'argv', argv), patch.object(core, 'preflight', return_value={}), \
                 patch.object(core.progress, 'ConsoleProgress', return_value=console), \
                 patch.object(core.g, 'sha256', side_effect=AssertionError('No source hashes')) as hashes, \
                 patch.object(core.g, 'dispatch', side_effect=AssertionError('No extraction')) as dispatch, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(core.main(), 1)
            hashes.assert_not_called()
            dispatch.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
