# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Source-aware reservations for the Windows PowerShell 5.1 output profile.

This performs read-only directory inventory, never hashes or decodes a project.
It preserves source-relative names and refuses paths outside the tested profile.
"""
from pathlib import Path
import hashlib
import json
from geoviewer_paths import project_files

FILE_LIMIT = 259
DIRECTORY_LIMIT = 247
WORKBOOK_FILENAME_RESERVE = 80


def windows_length(path):
    return len(str(path).encode('utf-16-le')) // 2


def fits_file(path):
    path = Path(path)
    return windows_length(path) <= FILE_LIMIT and windows_length(path.parent) <= DIRECTORY_LIMIT


class ExportPathBudgetError(ValueError):
    """No safe readable alias fits; the validated original must remain visible."""


def export_alias_path(directory, preferred_name, *, identity, staging_suffix=''):
    """Plan an alias without I/O, retaining full identity in a stable digest.

    Full object IDs, display names and source provenance remain in FILE_INDEX.
    Already qualified readable names are unchanged. Only overlong names shrink;
    the entire category/object/package-relative artifact identity is hashed.
    """
    directory = Path(directory)
    if Path(preferred_name).name != preferred_name or preferred_name in ('', '.', '..'):
        raise ValueError('Export alias must be a basename')
    def qualifies(path):
        return fits_file(path) and fits_file(path.with_name(path.name + staging_suffix))
    preferred = directory / preferred_name
    if qualifies(preferred):
        return preferred
    suffix = Path(preferred_name).suffix
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True,
                                      separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    minimum = directory / (digest + suffix)
    if not qualifies(minimum):
        raise ExportPathBudgetError('Readable export alias cannot fit the Windows path profile')
    available = FILE_LIMIT - windows_length(minimum) - windows_length(staging_suffix) - 1
    label = Path(preferred_name).stem
    while label and windows_length(label) > available:
        label = label[:-1]
    target = directory / ((label + '_' if label else '') + digest + suffix)
    if not qualifies(target):
        raise ExportPathBudgetError('Readable export alias exceeds the Windows path profile')
    return target


def source_reservations(project_file, mode='convert'):
    """Return package-relative destinations without reading source payloads."""
    source = Path(project_file).resolve(strict=True)
    store = source.with_suffix('.ptd')
    root = source.parent
    reservations = [Path('08_native_project/project_file') / source.name]
    launcher=root/'run_portable_petrel_extract.bat'
    colocated=all(p.is_file() for p in (launcher,root/'toolkit.json',root/'scripts/invoke_portable_petrel_extract.ps1'))
    toolkit_dirs=['.venv','.agents','scripts','00_manifest','runtime','bootstrap','build']
    if launcher.is_file() and (root/'PetrelExtractor/STANDALONE.txt').is_file():
        colocated=True;toolkit_dirs=['PetrelExtractor']
    excluded_roots=[root/p for p in toolkit_dirs] if colocated else []
    excluded_names={'run_portable_petrel_extract.bat'} if launcher.is_file() else set()
    if colocated:
        excluded_names.update({'README.md','AGENTS.md','requirements-core.txt','requirements-geodata.txt',
                               'toolkit.json','STANDALONE.txt','THIRD_PARTY_NOTICES.md','requirements-standalone-lock.txt'})
    def enumeration_error(exc):
        raise OSError('Source path inventory is incomplete; cannot plan safe output: '+str(exc)) from exc
    files = list(project_files(root, onerror=enumeration_error, exclude_roots=excluded_roots))
    for path in files:
        if not fits_file(path):
            raise OSError('Source path exceeds the qualified Windows file/directory profile; use a verified shorter input copy: '+str(path))
        # The selected native store is inventoried independently of companions.
        if path.is_relative_to(store):
            if path.suffix.lower() not in {'.lock', '.zgy', '.sgy', '.segy'}:
                reservations.append(Path('08_native_project/ptd_store') / path.relative_to(store))
            continue
        if path.suffix.lower() == '.pet' or any(p.suffix.lower() == '.ptd' for p in path.parents if p.is_relative_to(root)):
            continue
        if mode == 'inventory' or path.suffix.lower() in {'.zgy', '.sgy', '.segy'}:
            continue
        if path.parent==root and path.name in excluded_names:
            continue
        relative = path.relative_to(root)
        # Reserve every non-seismic companion. Eligibility can depend on bytes,
        # which this preflight deliberately does not read.
        reservations.append(Path('09_source_companions') / relative)
        if mode != 'convert':
            continue
        suffix = path.suffix.lower()
        if suffix in {'.las', '.shp'}:
            destination = Path('10_converted_ascii') / ('las' if suffix == '.las' else 'gis') / relative
            for ending in ('.csv', '.geojson', '.prj', '.summary.json') if suffix == '.shp' else ('.csv', '.summary.json'):
                reservations.append(destination.with_suffix(ending))
                if suffix == '.shp':
                    reservations.append((destination.parent/'shp_conversion_00000000'/destination.name).with_suffix(ending))
        elif suffix in {'.xlsx', '.xlsm'}:
            destination = Path('10_converted_ascii/workbooks') / relative.with_suffix('')
            reservations.extend([destination / ('s' * WORKBOOK_FILENAME_RESERVE), destination / 'workbook.summary.json'])
        elif suffix in {'', '.txt', '.prn', '.asc', '.ascii', '.dat'}:
            reservations.append((Path('02_wells/well_tops') / relative).with_suffix('.summary.json'))
    reservations.extend(Path(p) for p in (
        '00_manifest/export_manifest.csv.restore.tmp',
        '07_workflows_reports/portable_extractor/portable_extraction_run_summary.json',
        '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
        'q_00000000.partial/manifest_before.csv', 'q_00000000.partial/QUARANTINE.json'))
    return reservations, len(files)


def reservation_failure(package, relative_paths):
    for relative in relative_paths:
        path = Path(package) / relative
        if not fits_file(path):
            return dict(relative_path=str(relative), file_characters=windows_length(path),
                        directory_characters=windows_length(path.parent),
                        file_limit=FILE_LIMIT, directory_limit=DIRECTORY_LIMIT)
    return None
