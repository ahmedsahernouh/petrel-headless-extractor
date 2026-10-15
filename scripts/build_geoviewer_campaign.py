# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Package the separate batch add-on. Website: https://saherlabs.dev/"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New add-on staging directory')
    args = parser.parse_args()
    stage = args.output.resolve()
    stage.mkdir(parents=True, exist_ok=False)
    addon = stage / 'GeoViewerBatch'; addon.mkdir()
    for name in ('GeoViewer_batch_tests.bat', 'GeoViewer_crawl_projects.bat', 'GeoViewer_run_extractions.bat'):
        shutil.copy2(ROOT / name, stage)
    for name in ('geoviewer_campaign.py', 'geoviewer_case_diagnostics.py', 'geoviewer_process_metrics.py',
                 'launch_geoviewer_campaign.ps1', 'campaign_config.json'):
        shutil.copy2(ROOT / 'scripts' / name, addon)
    shutil.copy2(ROOT / 'docs' / 'BATCH_TRIALS.md', addon / 'README.md')
    shutil.copy2(ROOT / 'docs' / 'CASE_DIAGNOSTICS.md', addon)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / name, addon)
    hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in stage.rglob('*') if p.is_file()}
    (addon / 'ADDON_MANIFEST.json').write_text(json.dumps(dict(
        extractor_version='1.0.0', component='batch_trials_addon', files=hashes), indent=2), encoding='utf-8')
    archive = stage.with_name(stage.name + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
        for file in stage.rglob('*'):
            if file.is_file():
                z.write(file, file.relative_to(stage).as_posix())
    archive.with_suffix('.zip.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() +
                                                '  ' + archive.name + '\n', encoding='ascii')
    print(archive)


if __name__ == '__main__':
    main()
