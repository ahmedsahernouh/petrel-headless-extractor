# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Package the dedicated same-machine test lab. Website: https://saherlabs.dev/"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stage = args.output.resolve()
    stage.mkdir(parents=True, exist_ok=False)
    addon = stage / 'GeoViewerTestLab'
    addon.mkdir()
    for name in ('GeoViewer_prepare_tests.bat', 'GeoViewer_profile_tests.bat', 'GeoViewer_run_tests.bat'):
        shutil.copy2(ROOT / name, stage)
    for name in ('geoviewer_test_lab.py', 'geoviewer_test_worker.py', 'geoviewer_campaign.py', 'geoviewer_case_diagnostics.py',
                 'geoviewer_process_metrics.py', 'geoviewer_trace.py', 'launch_geoviewer_test_lab.ps1'):
        shutil.copy2(ROOT / 'scripts' / name, addon)
    for name in ('TEST_LAB.md', 'CASE_DIAGNOSTICS.md'):
        shutil.copy2(ROOT / 'docs' / name, addon)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / name, addon)
    files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob('*') if p.is_file()}
    (addon / 'TEST_LAB_MANIFEST.json').write_text(json.dumps(dict(component='dedicated_test_lab', extractor_version='1.0.0', files=files), indent=2))
    archive = stage.with_name(stage.name + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
        for file in stage.rglob('*'):
            if file.is_file(): z.write(file, file.relative_to(stage).as_posix())
    archive.with_suffix('.zip.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    print(archive)


if __name__ == '__main__': main()
