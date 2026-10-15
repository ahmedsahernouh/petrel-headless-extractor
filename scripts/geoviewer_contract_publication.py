# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Normal-path contract/assessment publication and small-receipt binding checks."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import uuid

from geoviewer_contract import POINTER, SCHEMA as CONTRACT_SCHEMA, SHARDED_SCHEMA, MAX_TABLE_BYTES, _read, _resolve, _unlinked, digest, publish_contract
from geoviewer_io import atomic_json
from validate_geoviewer_contract import verified_profile_assessments
from geoviewer_preparation_report import publish_human_report, MAX_REPORT_BYTES
from geoviewer_assessment_shards import (SCHEMA as SHARDED_ASSESSMENT_SCHEMA,
    ENCODING as SHARDED_ASSESSMENT_ENCODING,
    write_sharded_assessment, verify_sharded_assessment)

SCHEMA = 'geoviewer.preparation-publication/1'
PROFILES = ('source', 'opendtect', 'paleoscan')


class AssessmentTooLarge(ValueError):
    pass


def _atomic_bounded_assessment(path, value, *, measure_full=True):
    """Keep validation JSON within the existing reader bound without a giant string."""
    path = Path(path).absolute(); _unlinked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex[:12] + '.partial')
    total = 0
    hasher = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, indent=2, default=str)
    try:
        with pending.open('xb') as stream:
            for chunk in encoder.iterencode(value):
                raw = chunk.encode('utf-8')
                total += len(raw)
                if total > MAX_TABLE_BYTES and not measure_full:
                    raise AssessmentTooLarge('Assessment exceeds explicit 256 MiB flat byte bound')
                if total <= MAX_TABLE_BYTES:
                    if stream.write(raw) != len(raw):
                        raise OSError('Incomplete assessment write')
                    hasher.update(raw)
            total += 1
            if total <= MAX_TABLE_BYTES:
                if stream.write(b'\n') != 1:
                    raise OSError('Incomplete assessment newline write')
                hasher.update(b'\n')
            if total > MAX_TABLE_BYTES:
                raise AssessmentTooLarge(f'Assessment JSON exceeds explicit 256 MiB byte bound: {total} bytes')
            stream.flush(); os.fsync(stream.fileno())
        # Existing run-level readability aliases are refreshed on publication.
        # Standalone reports use a private path and an exclusive final link below.
        os.replace(pending, path)
        return dict(bytes=total, sha256=hasher.hexdigest())
    finally:
        pending.unlink(missing_ok=True)


def _exclusive_promote(source, target):
    """Commit a staged file without replacing an existing destination.

    Windows rename is exclusive and also works on filesystems without hardlinks.
    POSIX rename replaces, so use an exclusive link there and let stage cleanup
    remove its other name after the final report has been committed.
    """
    _unlinked(source); _unlinked(target)
    identity = source.stat()
    if os.name == 'nt':
        os.rename(source, target)
    else:
        os.link(source, target)
    return identity.st_dev, identity.st_ino


def _still_owned(source, target, identity):
    """Only roll back a member if its file identity still matches our draft."""
    if not target.exists():
        return False
    if source.exists():
        return os.path.samefile(source, target)
    current = target.stat(follow_symlinks=False)
    return bool(identity[1] and current.st_ino == identity[1] and current.st_dev == identity[0])


def write_bounded_assessment(path, assessment):
    """Read back a private flat/sharded draft before exposing the final report.

    The final manifest is promoted last, exclusively. Any failed promotion
    removes only members still identified as this call's files.
    """
    path = Path(path).absolute(); _unlinked(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=path.name + '.', suffix='.assessment-stage', dir=path.parent))
    staged = stage / path.name
    promoted = []
    outcome = None
    try:
        try:
            written = _atomic_bounded_assessment(staged, assessment, measure_full=False)
            format_name = 'flat-json/1'
        except AssessmentTooLarge:
            written = write_sharded_assessment(staged, assessment)
            format_name = written['format']
        readback, readback_hash = _read(staged)
        if readback_hash != written['sha256']:
            raise ValueError('Assessment draft readback hash mismatch')
        if format_name == 'flat-json/1':
            if readback != assessment:
                raise ValueError('Assessment draft readback differs from intended report')
        else:
            verify_sharded_assessment(staged, readback,
                entities={item['entity_key'] for item in assessment['assessments']},
                artifacts={item['artifact_key'] for item in assessment['artifact_availability']},
                expected_report=assessment)
            for member in written['members']:
                source = stage / member['path']
                target = path.parent / member['path']
                identity = _exclusive_promote(source, target)
                promoted.append((source, target, identity))
        _exclusive_promote(staged, path)
        outcome = dict(written, format=format_name)
        return outcome
    except BaseException:
        for source, target, identity in reversed(promoted):
            try:
                if _still_owned(source, target, identity):
                    target.unlink()
            except OSError:
                # Leave uncertain bytes unaccepted; the final manifest is absent.
                pass
        raise
    finally:
        try:
            shutil.rmtree(stage)
        except OSError as exc:
            # A committed report remains valid. Surface the private-stage residue
            # in the return receipt without turning success into a false failure.
            if outcome is not None:
                outcome['stage_cleanup_warning'] = f'{stage}: {exc}'


def publish_preparation(package, destination, run, inventory, *, file_index=None, conversion_enabled=True):
    """Inventory-only publication never materializes or promotes dataset artifacts.

    Existing accepted/partial FILE_INDEX rows remain authoritative in conversion
    mode. Missing final-index input is a failure, not an empty successful delivery.
    """
    package, destination, run = (Path(p).absolute() for p in (package, destination, run))
    for path in (package, destination, run):
        _unlinked(path)
    if not run.is_dir():
        raise ValueError('Run directory must already exist')
    if conversion_enabled and file_index is None:
        raise ValueError('Conversion publication requires a committed index')
    destination.mkdir(parents=True, exist_ok=True)
    if not conversion_enabled:
        existing, _ = _read(destination/'FILE_INDEX.json')
        if any(index and (index.get('files') or index.get('partial_files')) for index in (file_index, existing)):
            raise ValueError('Inventory-only publication cannot replace existing payload records')
        file_index = dict(schema='geoviewer.inventory-index/1', status='inventory_only',
                          files=[], partial_files=[], dataset_conversion_enabled=False,
                          scope='Inventory/metadata only; no converted dataset claimed')
        atomic_json(destination/'FILE_INDEX.json', file_index)
    pointer = publish_contract(package, destination, file_index, inventory)
    return publish_current_preparation(destination, run, expected_pointer=pointer,
                                       conversion_enabled=conversion_enabled)


def publish_current_preparation(destination, run, *, expected_pointer=None, conversion_enabled=True):
    """Publish assessments for the current, already committed generation.

    This permits bounded downstream recovery without generating the same large
    source catalogue twice. The normal validator binds every source and shard.
    """
    destination, run = Path(destination).absolute(), Path(run).absolute()
    _unlinked(destination); _unlinked(run)
    if not destination.is_dir() or not run.is_dir():
        raise ValueError('Committed EXPORTS and run directories are required')
    pointer, _ = _read(destination / POINTER)
    if pointer.get('schema') not in (CONTRACT_SCHEMA, SHARDED_SCHEMA):
        raise ValueError('Unsupported source contract pointer')
    if expected_pointer is not None and pointer != expected_pointer:
        raise ValueError('Current contract pointer changed before preparation publication')
    generation = pointer['generation']
    folder = run/'interoperability'/generation
    _unlinked(folder)
    folder.mkdir(parents=True, exist_ok=False)
    with verified_profile_assessments(destination/POINTER, PROFILES) as assessment_inputs:
        assessments = {}
        for profile in PROFILES:
            assessment = assessment_inputs[profile]
            if assessment['generation'] != generation or assessment['manifest_sha256'] != pointer['manifest_sha256']:
                raise ValueError('Assessment no longer matches the published generation')
            path = folder/('validation_'+profile+'.json')
            written = write_bounded_assessment(path, assessment)
            assessments[profile] = dict(path=str(path), relative_path=path.relative_to(run).as_posix(),
                sha256=written['sha256'], format=written['format'],
                profile_revision=assessment['profile_revision'], counts=assessment['counts'])
            if written['format'] != 'flat-json/1':
                assessments[profile]['members'] = [dict(member,
                    relative_path=(path.parent / member['path']).relative_to(run).as_posix())
                    for member in written['members']]
            # Compatibility/readability alias; RUN_RESULT binds the immutable copy above.
            if written['format'] == 'flat-json/1':
                _atomic_bounded_assessment(run/('interoperability_validation_'+profile+'.json'), assessment)
            else:
                atomic_json(run/('interoperability_validation_'+profile+'.json'), dict(
                    schema='geoviewer.assessment-alias/1',
                    path=path.relative_to(run).as_posix(), sha256=written['sha256'],
                    scope='Complete profile assessment is in the bound manifest and JSONL members'))
        human_report = publish_human_report(folder/'PREPARATION.html',
            _resolve(destination,pointer['manifest']),assessment_inputs,generation,pointer['manifest_sha256'])
        human_report['relative_path'] = Path(human_report['path']).relative_to(run).as_posix()
        import os
        bundle = dict(schema=SCHEMA, pointer=str(destination/POINTER), path_base='run_result_parent',
            pointer_relative_path=os.path.relpath(destination/POINTER, run).replace('\\','/'),
            pointer_sha256=digest((destination/POINTER).read_bytes()), manifest_sha256=pointer['manifest_sha256'],
            generation=generation, assessments=assessments, human_report=human_report,
            dataset_conversion_enabled=conversion_enabled,
            scope='Committed source catalogue and preparation assessments; no receiving execution')
        verify_publication_binding(bundle, run)
    return bundle


def verify_publication_binding(bundle, run):
    """Check current pointer and immutable assessment bytes; never hash bulk payloads.

    `run` is the directory containing RUN_RESULT.json. Absolute paths in the
    receipt are display provenance only, so a complete supported layout can move.
    """
    run = Path(run).absolute()
    _unlinked(run)
    if bundle.get('schema') != SCHEMA or bundle.get('path_base') != 'run_result_parent':
        raise ValueError('Unsupported preparation publication binding')
    pointer_path = _resolve(run, bundle['pointer_relative_path'], allow_parent=True)
    boundary = run.parent.resolve()
    if boundary == Path(boundary.anchor) or not pointer_path.is_relative_to(boundary) or pointer_path.name != POINTER:
        raise ValueError('Publication pointer escapes the bounded run layout')
    pointer, pointer_hash = _read(pointer_path)
    if (pointer_hash != bundle['pointer_sha256'] or pointer.get('generation') != bundle['generation'] or
            pointer.get('manifest_sha256') != bundle['manifest_sha256']):
        raise ValueError('Stale publication pointer or generation')
    manifest_path = _resolve(pointer_path.parent, pointer['manifest'])
    manifest, manifest_hash = _read(manifest_path)
    if manifest_hash != bundle['manifest_sha256'] or manifest.get('generation') != bundle['generation']:
        raise ValueError('Publication manifest binding failed')
    if set(bundle['assessments']) != set(PROFILES):
        raise ValueError('Incomplete assessment publication')
    expected_entities = expected_artifacts = None
    for profile, recorded in bundle['assessments'].items():
        path = _resolve(run, recorded['relative_path'])
        report, actual = _read(path)
        if (actual != recorded['sha256'] or report.get('generation') != bundle['generation'] or
                report.get('manifest_sha256') != bundle['manifest_sha256'] or report.get('profile') != profile or
                report.get('profile_revision') != recorded['profile_revision'] or report.get('counts') != recorded['counts']):
            raise ValueError('Assessment binding failed: '+profile)
        if recorded.get('format') == SHARDED_ASSESSMENT_ENCODING:
            if report.get('schema') != SHARDED_ASSESSMENT_SCHEMA:
                raise ValueError('Assessment shard format binding failed: '+profile)
            if expected_entities is None:
                from validate_geoviewer_contract import _table_rows
                root = manifest_path.parent
                entity_rows, _, _ = _table_rows(root, 'entities', manifest['tables']['entities'], pointer['schema'])
                artifact_rows, _, _ = _table_rows(root, 'artifacts', manifest['tables']['artifacts'], pointer['schema'])
                expected_entities = {row['entity_key'] for row in entity_rows}
                expected_artifacts = {row['artifact_key'] for row in artifact_rows}
                if len(expected_entities) != len(entity_rows) or len(expected_artifacts) != len(artifact_rows):
                    raise ValueError('Duplicate catalogue entity/artifact identity')
                del entity_rows, artifact_rows
            verify_sharded_assessment(path, report, entities=expected_entities,
                                      artifacts=expected_artifacts)
            members = [dict(part, relative_path=(path.parent / part['path']).relative_to(run).as_posix())
                       for field in report['list_order'] for part in report['lists'][field]['members']]
            if members != recorded.get('members'):
                raise ValueError('Assessment member bundle binding failed: '+profile)
        elif recorded.get('format') in (None, 'flat-json/1'):
            if report.get('schema') == SHARDED_ASSESSMENT_SCHEMA:
                raise ValueError('Unbound sharded assessment: '+profile)
        else:
            raise ValueError('Unknown assessment format: '+profile)
    human = bundle.get('human_report')
    if human is not None:
        path = _resolve(run,human['relative_path'])
        if type(human.get('bytes')) is not int or not 0 <= human['bytes'] <= MAX_REPORT_BYTES:
            raise ValueError('Human report size binding failed')
        hasher = hashlib.sha256(); total = 0
        with path.open('rb') as stream:
            while block := stream.read(1024*1024):
                total += len(block); hasher.update(block)
                if total > human['bytes']:raise ValueError('Human report binding failed')
        if (hasher.hexdigest()!=human['sha256'] or total!=human['bytes']
                or human['generation']!=bundle['generation'] or human['manifest_sha256']!=bundle['manifest_sha256']):
            raise ValueError('Human report binding failed')
        names=set();all_bytes=total
        for detail in human.get('details',[]):
            name=detail['filename']
            if not isinstance(name,str) or Path(name).name!=name or name in names or not name.endswith('.html'):
                raise ValueError('Human report detail identity failed')
            names.add(name);member=_resolve(path.parent,name)
            if type(detail.get('bytes')) is not int or not 0 <= detail['bytes'] <= MAX_REPORT_BYTES-all_bytes:
                raise ValueError('Human report detail size bound failed')
            checked=0;h=hashlib.sha256()
            with member.open('rb') as stream:
                while block:=stream.read(1024*1024):
                    checked+=len(block);h.update(block)
                    if checked>detail['bytes']:raise ValueError('Human report detail binding failed')
            if checked!=detail['bytes'] or h.hexdigest()!=detail['sha256']:
                raise ValueError('Human report detail binding failed')
            all_bytes+=checked
        if all_bytes!=human.get('total_bytes',human['bytes']):raise ValueError('Human report total size failed')
    return dict(status='verified', generation=bundle['generation'], assessments=len(PROFILES),
                scope='Pointer/manifest/assessment and any bound human-report bytes; no fresh artifact or source validation')
