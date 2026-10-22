# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Headless discovery, source-bound handoffs and offline native project creation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from geoviewer_contract import _resolve, _unlinked, MAX_ROW_BYTES, MAX_TABLE_BYTES, MAX_TABLE_ROWS
from geoviewer_receiving_profiles import PROFILE_REVISION, PROFILES, _bounded_json, MAX_DECISIONS_BYTES
from geoviewer_contract_retention import MAX_VIEW_BYTES
from validate_geoviewer_contract import _load_contract, _recheck_bindings, assess_catalogue

SCHEMA = 'fieldviewer.connector-handoff/1'
WORKFLOW_SCHEMA = 'fieldviewer.native-project-workflow/1'
WORKFLOW_MARKER = 'NATIVE_WORKFLOW.json'
MAX_WORKFLOW_RECEIPT = 2 * 1024 * 1024


def discover():
    """Describe implemented preparation capabilities, never runtime availability."""
    from fieldviewer_paleoscan_segy import PROFILE as PALEOSCAN_SEGY_PROFILE
    result = {
        'schema': 'fieldviewer.connectors/1',
        'architecture': 'headless_first; desktop_is_optional_consumer',
        'transport': 'local_python_api_and_json_cli',
        'platform_scope': {
            'preparation': 'Python stdlib; Windows tested; other OS acceptance pending',
            'extraction_and_desktop': 'Windows x64',
        },
        'connectors': [dict(
            id=name, profile_revision=PROFILE_REVISION[name], receiving_profile=PROFILES[name],
            operations=['prepare_handoff', 'verify_handoff'], access='read_only_source',
            operation_contracts={
                'prepare_handoff': {'input': 'current_source_contract', 'output': SCHEMA},
                'verify_handoff': {'input': 'handoff_bundle_and_explicit_current_source_contract',
                                   'output': 'fieldviewer.handoff-intake/1'}},
            input_schema=['geoviewer.source-contract/1', 'geoviewer.source-contract/2'],
            output_schema=SCHEMA, runtime_probed=False, imports_supported=False,
            imports_supported_scope='Application-driven imports; offline native file writers are separate operations',
        ) for name in sorted(PROFILES)],
    }
    paleo = next(c for c in result['connectors'] if c['id'] == 'paleoscan')
    paleo['operations'] += ['prepare_paleoscan_segy', 'verify_paleoscan_segy',
                            'compile_paleoscan_template', 'create_paleoscan_project', 'verify_paleoscan_project']
    paleo['operation_contracts'].update({
        'prepare_paleoscan_segy': {'input': 'verified_handoff_current_source_contract_exact_entity_and_artifact',
            'output': 'fieldviewer.paleoscan-segy-preparation/1', 'adapter_profile': PALEOSCAN_SEGY_PROFILE,
            'runtime_boundary': 'zero_gui_python_stdlib', 'writes': 'new_output_directory_only'},
        'verify_paleoscan_segy': {'input': 'prepared_directory_handoff_current_source_and_review_inputs',
            'output': 'fieldviewer.paleoscan-segy-intake/1', 'runtime_boundary': 'zero_gui_python_stdlib',
            'writes': 'none'}})
    paleo['native_headless_import'] = 'experimental_direct_project_file_writer; application_open_not_verified'
    paleo['headless_capabilities'] = {
        'prepare_files': 'implemented', 'verify_files': 'implemented',
        'paleoscan_installation_required_for_file_operations': False,
        'desktop_required_for_file_operations': False,
        'native_project_import': 'experimental_direct_native_file_creation',
        'direct_native_project_writer': 'experimental_2018_seismic_file_writer; native_open_not_yet_verified',
        'native_writer_dependency': 'Portable version-71 project settings template; no PaleoScan application or SDK',
        'gui_pilot_scope': 'Receiving validation only; the connector does not call the GUI'}
    paleo['operation_contracts'].update({
        'compile_paleoscan_template': {'input': 'explicit_reference_project_empty_settings',
            'output': 'fieldviewer.paleoscan2018-project-template/1', 'writes': 'new_template_file_only'},
        'create_paleoscan_project': {'input': 'prepared_segy_current_handoff_contract_and_portable_template',
            'output': 'fieldviewer.paleoscan-native-project/1',
            'runtime_boundary': 'zero_gui_python_stdlib_no_paleoscan_installation',
            'writes': 'new_candidate_project_only', 'native_open_verified': False},
        'verify_paleoscan_project': {'input': 'candidate_project_and_current_prepared_source_contract_handoff_template',
            'output': 'fieldviewer.paleoscan-native-project-verification/1', 'writes': 'none'}})
    od=next(c for c in result['connectors'] if c['id']=='opendtect')
    od['operations']+=['create_opendtect_project','verify_opendtect_project']
    od['headless_capabilities']={'direct_native_survey_writer':'implemented_initial_7.0.10_cbvs_profile',
        'opendtect_installation_required':False,'desktop_required':False,
        'application_open_verified':False,'scope':'Single-component regular 3D time seismic; local XY or declared EPSG:20439, no reprojection',
        'supported_projected_crs':['EPSG:20439']}
    od['operation_contracts'].update({
        'create_opendtect_project':{'input':'current_source_contract_handoff_exact_entity_and_artifact',
            'output':'fieldviewer.opendtect-native-survey/1',
            'runtime_boundary':'zero_gui_python_stdlib_no_opendtect_installation','writes':'new_data_root_and_survey_only'},
        'verify_opendtect_project':{'input':'generated_survey_current_source_contract_and_handoff',
            'output':'fieldviewer.opendtect-native-survey-verification/1','writes':'none'}})
    for connector in result['connectors']:
        connector['operations'] += ['create_native_workflow', 'verify_native_workflow']
        connector['operation_contracts'].update({
            'create_native_workflow': {
                'python_api': 'fieldviewer_connectors.create_native_workflow',
                'cli_operation': 'create-native-project',
                'input': 'current_source_contract_exact_entity_artifact_and_explicit_target_inputs',
                'output': WORKFLOW_SCHEMA, 'writes': 'new_workflow_directory_only',
                'runtime_boundary': 'zero_gui_python_stdlib_no_target_application',
                'completion_marker': WORKFLOW_MARKER,
                'template_required': connector['id'] == 'paleoscan'},
            'verify_native_workflow': {
                'python_api': 'fieldviewer_connectors.verify_native_workflow',
                'cli_operation': 'verify-native-project',
                'input': 'workflow_current_source_contract_and_explicit_review_template_inputs',
                'output': 'fieldviewer.native-project-workflow-verification/1', 'writes': 'none'}})
    return result


def _digest_file(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def _review_bindings(decisions, retention_view):
    """Bind optional review documents and cited local evidence across preparation."""
    bindings = []
    for path, maximum in ((decisions, MAX_DECISIONS_BYTES), (retention_view, MAX_VIEW_BYTES)):
        if path is None:
            continue
        path = Path(path).absolute()
        value = _bounded_json(path, maximum)
        bindings.append((path, _digest_file(path), maximum))
        if path == decisions:
            if not isinstance(value.get('entities'), dict):
                raise ValueError('Preparation decisions need an entities map')
            for record in value['entities'].values():
                if not isinstance(record, dict) or not isinstance(record.get('evidence'), list):
                    raise ValueError('Preparation decision needs an evidence list')
                for ref in record.get('evidence', []):
                    if not isinstance(ref, dict):
                        raise ValueError('Invalid decision evidence reference')
                    candidate = _resolve(path.parent, ref['path'])
                    # The normal decision validator enforces reference bounds and digest.
                    from geoviewer_receiving_profiles import MAX_REFERENCE_BYTES
                    bindings.append((candidate, ref['sha256'], MAX_REFERENCE_BYTES))
    _recheck_bindings(bindings)
    return bindings


def _write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _entity_plans(catalogue, assessment):
    """Regenerable preparation rows shared by publication and verified intake."""
    artifacts = {}
    for artifact in catalogue['tables']['artifacts']:
        for owner in artifact.get('owner_keys', [artifact['entity_key']]):
            artifacts.setdefault(owner, []).append(artifact)
    order = {key: index for index, key in enumerate(assessment['preparation_dependency_order'])}
    by_key = {row['entity_key']: row for row in assessment['assessments']}
    relationships = {}
    for relation in catalogue['tables']['relationships']:
        relationships.setdefault(relation['source_key'], []).append(relation)
    for key, row in by_key.items():
        receiving = row.get('receiving_assessment') or {}
        yield dict(entity_key=key, category=row['category'],
            preparation_ordinal=order.get(key), source_state=row['source_state'],
            receiving_state=row['receiving_state'], source_reason_codes=row['reason_codes'],
            receiving_reason_codes=receiving.get('reason_codes', []),
            relationships=relationships.get(key, []),
            qualified_for_planned_mapping=(row['source_state'] == 'source_prerequisites_satisfied'
                and row['receiving_state'] == 'qualified_for_planned_mapping'),
            execution_allowed=False, artifacts=[{
                field: artifact.get(field) for field in (
                    'artifact_key', 'role', 'format', 'path_base', 'relative_path',
                    'expected_bytes', 'recorded_sha256', 'available', 'current_identity_scope',
                    'current_retention', 'original_status', 'index_section')
            } for artifact in artifacts.get(key, [])])


def prepare_handoff(contract, target, output, *, hash_artifacts=False, decisions=None, retention_view=None):
    """Write a new preparation bundle; HANDOFF.json is its final commit marker.

    No target application is started and no payload is copied or transformed.
    Paths remain source-contract-relative, so this bundle needs the source run.
    """
    if target not in PROFILES:
        raise ValueError('Unknown connector target: ' + str(target))
    decisions = Path(decisions).absolute() if decisions else None
    retention_view = Path(retention_view).absolute() if retention_view else None
    catalogue, bindings = _load_contract(Path(contract), bind_missing_receipts=True)
    output = Path(output).absolute()
    _unlinked(output)
    output = output.resolve(strict=False)
    protected = [*catalogue['roots'].values()]
    protected += [p for p in (decisions, retention_view) if p is not None]
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in protected):
        raise ValueError('Handoff output must be separate from the source run and review inputs')
    reviews = _review_bindings(decisions, retention_view)
    assessment = assess_catalogue(**catalogue, profile=target, hash_artifacts=hash_artifacts,
                                  decisions=decisions, retention_view=retention_view)
    # Never reuse a previous or interrupted bundle; it may belong to another caller.
    output.mkdir(parents=True, exist_ok=False)
    from geoviewer_contract_publication import write_bounded_assessment
    write_bounded_assessment(output / 'assessment.json', assessment)
    with (output / 'entities.jsonl').open('xb') as stream:
        total_bytes = 0
        for row_number, plan in enumerate(_entity_plans(catalogue, assessment), 1):
            raw = (json.dumps(plan, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            if len(raw) > MAX_ROW_BYTES:
                raise ValueError('Handoff entity exceeds 2 MiB row bound; assessment retained without commit marker')
            total_bytes += len(raw)
            if total_bytes > MAX_TABLE_BYTES or row_number > MAX_TABLE_ROWS:
                raise ValueError('Handoff plan exceeds explicit table bound; no committed handoff')
            stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    files = [dict(path=p.name, bytes=p.stat().st_size, sha256=_digest_file(p))
             for p in sorted(output.iterdir()) if p.is_file()]
    _recheck_bindings(bindings + reviews)
    result = dict(schema=SCHEMA, target=target,
        generation=assessment['generation'], manifest_sha256=assessment['manifest_sha256'],
        project_revision=assessment['project_revision'], profile_revision=assessment['profile_revision'],
        receiving_profile=assessment['receiving_profile'], counts=assessment['counts'],
        state='inventory_empty' if not assessment['assessments'] else 'prepared_with_blockers' if any(not (
            row['source_state'] == 'source_prerequisites_satisfied'
            and row['receiving_state'] == 'qualified_for_planned_mapping')
            for row in assessment['assessments']) else 'prepared_for_review',
        execution_allowed=False, imports_executed=0, receiving_qualified=False,
        artifact_hashing_requested=bool(hash_artifacts),
        source_contract_hint=str(Path(contract).absolute()),
        path_policy='artifact paths use original source-contract bases; payloads are not bundled',
        review_inputs=[dict(path=str(path), sha256=sha) for path, sha, _ in reviews],
        scope='Preparation only; historical numerical QC is not new receiving readback',
        files=files)
    pending = output / 'HANDOFF.pending.json'
    _write_json(pending, result)
    from geoviewer_contract_publication import _exclusive_promote
    _exclusive_promote(pending, output / 'HANDOFF.json')
    pending.unlink(missing_ok=True)
    return result


def _workflow_members(target):
    if target not in ('opendtect', 'paleoscan'):
        raise ValueError('Unknown native workflow target: ' + str(target))
    return {'handoff', 'native'} | ({'prepared'} if target == 'paleoscan' else set())


def _workflow_inputs(contract, output, template, decisions, retention_view):
    """Bind source/review inputs and refuse overlapping workflow destinations."""
    catalogue, bindings = _load_contract(Path(contract), bind_missing_receipts=True)
    reviews = _review_bindings(decisions, retention_view)
    protected = [*catalogue['roots'].values(), *(path for path, _, _ in reviews)]
    if template is not None:
        from fieldviewer_paleoscan_project import _template, MAX_TEMPLATE
        from xml.etree.ElementTree import ParseError
        try:
            _, sha, _ = _template(template)
        except ParseError as exc:
            raise ValueError('Invalid portable project-settings XML') from exc
        bindings.append((Path(template), sha, MAX_TEMPLATE))
        protected.append(Path(template))
    if any(output.is_relative_to(Path(p).resolve()) or Path(p).resolve().is_relative_to(output)
           for p in protected):
        raise ValueError('Workflow output must be separate from source, review/evidence and template inputs')
    return bindings + reviews


def _workflow_inventory(output, bindings, marker=None):
    expected = {path.relative_to(output).as_posix() for path, _, _ in bindings if path.is_relative_to(output)}
    if marker is not None:
        expected.add(marker)
    actual = set()
    for path in output.rglob('*'):
        _unlinked(path)
        if path.is_file():
            actual.add(path.relative_to(output).as_posix())
    if actual != expected:
        raise ValueError('Native workflow file inventory changed')


def _workflow_snapshot(output, contract, target, *, template, decisions, retention_view, committed):
    """Reconstruct a workflow receipt from verified stages, never stored hints."""
    from fieldviewer_handoff_intake import _read_json, _member_bindings, MAX_HANDOFF_BYTES
    from fieldviewer_handoff_intake import verify_handoff
    members = _workflow_members(target)
    allowed = members | ({WORKFLOW_MARKER} if committed else set())
    if {p.name for p in output.iterdir()} != allowed:
        raise ValueError('Unexpected/missing native workflow member')
    if target == 'paleoscan' and template is None:
        raise ValueError('PaleoScan workflow requires an explicit portable settings template')
    if target == 'opendtect' and template is not None:
        raise ValueError('OpendTect workflow does not accept a PaleoScan template')
    bindings = _workflow_inputs(contract, output, template, decisions, retention_view)
    options = dict(decisions=decisions, retention_view=retention_view)
    handoff_marker = output/'handoff/HANDOFF.json'
    handoff_record, handoff_sha = _read_json(handoff_marker, MAX_HANDOFF_BYTES)
    handoff = verify_handoff(output/'handoff', contract, hash_artifacts=True, **options)
    if handoff['target'] != target or handoff['handoff_sha256'] != handoff_sha:
        raise ValueError('Native workflow target differs from its handoff')
    bindings += [(handoff_marker, handoff_sha, MAX_HANDOFF_BYTES)]
    bindings += _member_bindings(output/'handoff', handoff_record)
    native = output/'native'
    prepared = None
    if target == 'opendtect':
        from fieldviewer_opendtect_project import verify_survey
        marker = native/'OPENDTECT_PROJECT.json'
        record, sha = _read_json(marker, MAX_WORKFLOW_RECEIPT)
        checked = verify_survey(native, output/'handoff', contract, **options)
        project_name, dataset = record['survey_directory'], record['dataset_name']
        entity, artifact = record['entity_key'], record['artifact_key']
        source_sha = record['source_sha256']
        gaps = record['upstream_entity_preparation']
    else:
        from fieldviewer_paleoscan_project import verify_project
        from fieldviewer_paleoscan_segy import verify_paleoscan_segy
        marker = native/'NATIVE_PROJECT.json'
        record, sha = _read_json(marker, MAX_WORKFLOW_RECEIPT)
        prepared = verify_paleoscan_segy(output/'prepared', output/'handoff', contract, **options)
        transform = prepared['transformation']
        prepared_size = 3600 + transform['traces'] * (240 + 4 * transform['samples_per_trace'])
        bindings += [(output/'prepared/RECEIVER.json', prepared['receiver_marker_sha256'], MAX_HANDOFF_BYTES),
            (output/'prepared/volume.segy', transform['derivative_sha256'], prepared_size)]
        checked = verify_project(native, output/'prepared', output/'handoff', contract, template, **options)
        project_name, dataset = record['project_directory'], record['volume_name']
        entity, artifact = prepared['entity_key'], prepared['artifact_key']
        source_sha = prepared['transformation']['source_sha256']
        gaps = prepared['upstream_entity_preparation']
    bindings.append((marker, sha, MAX_WORKFLOW_RECEIPT))
    bindings += [(_resolve(native, row['path']), row['sha256'], row['bytes']) for row in record['files']]
    receipt = dict(schema=WORKFLOW_SCHEMA, state='offline_verified_candidate', target=target,
        entity_key=entity, artifact_key=artifact, writer_profile=checked['profile'],
        project_name=project_name, dataset_name=dataset, native_project='native/'+project_name,
        source_generation=handoff['generation'], source_manifest_sha256=handoff['manifest_sha256'],
        source_artifact_sha256=source_sha, native_marker_sha256=sha,
        template_sha256=_digest_file(Path(template)) if template is not None else None,
        upstream_entity_preparation=gaps,
        stage_verifications=dict(handoff=handoff, prepared=prepared, native=checked),
        native_samples_verified=checked['native_samples_verified'],
        native_sample_bits_exact=checked['native_sample_bits_exact'],
        application_used=False, application_required=False, receiving_open_verified=False,
        runtime_boundary='zero_gui_python_stdlib_no_target_application',
        scope='Single selected seismic volume under the versioned writer profile; upstream scientific gaps remain. No application-open or full-project acceptance.')
    _recheck_bindings(bindings)
    _workflow_inventory(output, bindings, WORKFLOW_MARKER if committed else None)
    if {p.name for p in output.iterdir()} != allowed:
        raise ValueError('Native workflow members changed during verification')
    return receipt, bindings


def create_native_workflow(contract, target, entity_key, artifact_key, output, *,
                           project_name='Prepared_Project', dataset_name='Seismic',
                           component_name='amplitude', template=None, decisions=None, retention_view=None):
    """Prepare, write and verify one native candidate; publish its marker last."""
    members = _workflow_members(target)
    if target == 'paleoscan':
        if template is None:
            raise ValueError('PaleoScan workflow requires an explicit portable settings template')
        if component_name != 'amplitude':
            raise ValueError('PaleoScan workflow does not accept a component-name override')
        from fieldviewer_paleoscan_project import _name, create_project
    else:
        if template is not None:
            raise ValueError('OpendTect workflow does not accept a PaleoScan template')
        from fieldviewer_opendtect_project import _name, create_survey
        _name(component_name)
    _name(project_name); _name(dataset_name)
    template = Path(template).absolute() if template is not None else None
    decisions = Path(decisions).absolute() if decisions is not None else None
    retention_view = Path(retention_view).absolute() if retention_view is not None else None
    output = Path(output).absolute(); _unlinked(output); output = output.resolve(strict=False)
    bindings = _workflow_inputs(contract, output, template, decisions, retention_view)
    output.mkdir(parents=True, exist_ok=False)
    options = dict(decisions=decisions, retention_view=retention_view)
    prepare_handoff(contract, target, output/'handoff', hash_artifacts=True, **options)
    if target == 'opendtect':
        create_survey(output/'handoff', contract, entity_key, artifact_key, output/'native',
            survey_name=project_name, dataset_name=dataset_name, component_name=component_name, **options)
    else:
        from fieldviewer_paleoscan_segy import prepare_paleoscan_segy
        prepare_paleoscan_segy(output/'handoff', contract, entity_key, artifact_key, output/'prepared', **options)
        create_project(output/'prepared', output/'handoff', contract, template, output/'native',
            project_name=project_name, volume_name=dataset_name, **options)
    result, final_bindings = _workflow_snapshot(output, contract, target, template=template,
        decisions=decisions, retention_view=retention_view, committed=False)
    if result['entity_key'] != entity_key or result['artifact_key'] != artifact_key:
        raise ValueError('Native workflow selection changed; no completion marker')
    _recheck_bindings(bindings + final_bindings)
    pending = output/'NATIVE_WORKFLOW.pending.json'
    _write_json(pending, result)
    _recheck_bindings(bindings + final_bindings)
    _workflow_inventory(output, final_bindings, pending.name)
    if {p.name for p in output.iterdir()} != members | {pending.name}:
        raise ValueError('Unexpected native workflow member; no completion marker')
    from geoviewer_contract_publication import _exclusive_promote
    _exclusive_promote(pending, output/WORKFLOW_MARKER)
    pending.unlink(missing_ok=True)
    return result


def verify_native_workflow(output, contract, *, template=None, decisions=None, retention_view=None):
    """Read-only verification against explicit current source/review/template inputs."""
    from fieldviewer_handoff_intake import _read_json, _value_hash
    template = Path(template).absolute() if template is not None else None
    decisions = Path(decisions).absolute() if decisions is not None else None
    retention_view = Path(retention_view).absolute() if retention_view is not None else None
    output = Path(output).absolute(); _unlinked(output)
    stored, sha = _read_json(output/WORKFLOW_MARKER, MAX_WORKFLOW_RECEIPT)
    expected, bindings = _workflow_snapshot(output, contract, stored['target'], template=template,
        decisions=decisions, retention_view=retention_view, committed=True)
    if _value_hash(stored) != _value_hash(expected):
        raise ValueError('Native workflow receipt differs from current source/stages')
    _recheck_bindings(bindings + [(output/WORKFLOW_MARKER, sha, MAX_WORKFLOW_RECEIPT)])
    _workflow_inventory(output, bindings, WORKFLOW_MARKER)
    return dict(schema='fieldviewer.native-project-workflow-verification/1', target=expected['target'],
        state='offline_verified_candidate', workflow_marker_sha256=sha,
        writer_profile=expected['writer_profile'], native_project=expected['native_project'],
        native_samples_verified=expected['native_samples_verified'],
        native_sample_bits_exact=expected['native_sample_bits_exact'],
        application_used=False, application_required=False, receiving_open_verified=False,
        upstream_entity_preparation=expected['upstream_entity_preparation'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('discover', help='Describe versioned preparation connectors as JSON')
    for name in ('create-native-project', 'verify-native-project'):
        operation = sub.add_parser(name, help='Prepare/write/verify a source-bound native project workflow without applications')
        for field in ('contract', 'output'):
            operation.add_argument('--'+field, type=Path, required=True)
        for field in ('template', 'decisions', 'retention-view'):
            operation.add_argument('--'+field, type=Path)
        if name.startswith('create'):
            operation.add_argument('--target', choices=('opendtect', 'paleoscan'), required=True)
            operation.add_argument('--entity-key', required=True)
            operation.add_argument('--artifact-key', required=True)
            operation.add_argument('--project-name', default='Prepared_Project')
            operation.add_argument('--dataset-name', default='Seismic')
            operation.add_argument('--component-name', default='amplitude')
    handoff = sub.add_parser('prepare-handoff', help='Prepare a new read-only handoff bundle; no import')
    handoff.add_argument('contract', type=Path)
    handoff.add_argument('--target', choices=tuple(PROFILES), required=True)
    handoff.add_argument('--output', type=Path, required=True)
    handoff.add_argument('--hash-artifacts', action='store_true')
    handoff.add_argument('--decisions', type=Path)
    handoff.add_argument('--retention-view', type=Path)
    intake = sub.add_parser('verify-handoff', help='Verify bundle and current source/review inputs; no import')
    intake.add_argument('bundle', type=Path)
    intake.add_argument('--contract', type=Path, required=True)
    intake.add_argument('--decisions', type=Path)
    intake.add_argument('--retention-view', type=Path)
    intake.add_argument('--no-artifact-hashes', action='store_true',
                        help='Metadata-only intake; explicitly omit fresh bulk hashing')
    for name in ('prepare-paleoscan-segy', 'verify-paleoscan-segy'):
        operation = sub.add_parser(name, help='Headless source-bound scalar-1 file preparation/verification; no native import')
        if name.startswith('verify'):
            operation.add_argument('prepared', type=Path)
        operation.add_argument('--bundle', type=Path, required=True)
        operation.add_argument('--contract', type=Path, required=True)
        operation.add_argument('--decisions', type=Path)
        operation.add_argument('--retention-view', type=Path)
        if name.startswith('prepare'):
            operation.add_argument('--entity-key', required=True)
            operation.add_argument('--artifact-key', required=True)
            operation.add_argument('--output', type=Path, required=True)
    template = sub.add_parser('compile-paleoscan-template', help='Compile reusable empty project settings; no application')
    template.add_argument('reference_project', type=Path)
    template.add_argument('--output', type=Path, required=True)
    project = sub.add_parser('create-paleoscan-project', help='Write an experimental native 2018 project offline; no application')
    for name in ('prepared', 'bundle', 'contract', 'template', 'output'):
        project.add_argument('--'+name, type=Path, required=True)
    project.add_argument('--project-name', default='Prepared_PaleoScan')
    project.add_argument('--volume-name', default='Seismic')
    project.add_argument('--decisions', type=Path)
    project.add_argument('--retention-view', type=Path)
    native_check = sub.add_parser('verify-paleoscan-project', help='Read-only native file and sample verification; no application')
    for name in ('output', 'prepared', 'bundle', 'contract', 'template'):
        native_check.add_argument('--'+name, type=Path, required=True)
    native_check.add_argument('--decisions', type=Path)
    native_check.add_argument('--retention-view', type=Path)
    for name in ('create-opendtect-project','verify-opendtect-project'):
        operation=sub.add_parser(name,help='Offline native OpendTect survey/CBVS writing or read-only verification')
        for field in ('bundle','contract','output'):
            operation.add_argument('--'+field,type=Path,required=True)
        operation.add_argument('--decisions',type=Path)
        operation.add_argument('--retention-view',type=Path)
        if name.startswith('create'):
            operation.add_argument('--entity-key',required=True)
            operation.add_argument('--artifact-key',required=True)
            operation.add_argument('--survey-name',default='Prepared_OpendTect')
            operation.add_argument('--dataset-name',default='Seismic')
            operation.add_argument('--component-name',default='amplitude')
    args = parser.parse_args(argv)
    try:
        # Keep inherited progress records out of the single JSON response stream.
        from contextlib import redirect_stdout
        with redirect_stdout(sys.stderr):
            if args.operation == 'discover':
                result = discover()
            elif args.operation == 'create-native-project':
                result = create_native_workflow(args.contract, args.target, args.entity_key, args.artifact_key,
                    args.output, project_name=args.project_name, dataset_name=args.dataset_name,
                    component_name=args.component_name, template=args.template,
                    decisions=args.decisions, retention_view=args.retention_view)
            elif args.operation == 'verify-native-project':
                result = verify_native_workflow(args.output, args.contract, template=args.template,
                    decisions=args.decisions, retention_view=args.retention_view)
            elif args.operation == 'prepare-handoff':
                result = prepare_handoff(args.contract, args.target, args.output,
                    hash_artifacts=args.hash_artifacts, decisions=args.decisions, retention_view=args.retention_view)
            elif args.operation == 'verify-handoff':
                from fieldviewer_handoff_intake import verify_handoff
                result = verify_handoff(args.bundle, args.contract,
                    decisions=args.decisions, retention_view=args.retention_view,
                    hash_artifacts=not args.no_artifact_hashes)
            elif args.operation == 'compile-paleoscan-template':
                from fieldviewer_paleoscan_project import compile_template
                result = compile_template(args.reference_project, args.output)
            elif args.operation == 'create-paleoscan-project':
                from fieldviewer_paleoscan_project import create_project
                result = create_project(args.prepared,args.bundle,args.contract,args.template,args.output,
                    project_name=args.project_name,volume_name=args.volume_name,
                    decisions=args.decisions,retention_view=args.retention_view)
            elif args.operation == 'verify-paleoscan-project':
                from fieldviewer_paleoscan_project import verify_project
                result = verify_project(args.output,args.prepared,args.bundle,args.contract,args.template,
                    decisions=args.decisions,retention_view=args.retention_view)
            elif args.operation == 'create-opendtect-project':
                from fieldviewer_opendtect_project import create_survey
                result=create_survey(args.bundle,args.contract,args.entity_key,args.artifact_key,args.output,
                    survey_name=args.survey_name,dataset_name=args.dataset_name,component_name=args.component_name,
                    decisions=args.decisions,retention_view=args.retention_view)
            elif args.operation == 'verify-opendtect-project':
                from fieldviewer_opendtect_project import verify_survey
                result=verify_survey(args.output,args.bundle,args.contract,decisions=args.decisions,retention_view=args.retention_view)
            else:
                from fieldviewer_paleoscan_segy import prepare_paleoscan_segy, verify_paleoscan_segy
                if args.operation == 'prepare-paleoscan-segy':
                    result = prepare_paleoscan_segy(args.bundle, args.contract,
                        args.entity_key, args.artifact_key, args.output,
                        decisions=args.decisions, retention_view=args.retention_view)
                else:
                    result = verify_paleoscan_segy(args.prepared, args.bundle, args.contract,
                        decisions=args.decisions, retention_view=args.retention_view)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({'schema': 'fieldviewer.connector-error/1', 'operation': args.operation,
                          'state': 'failed', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
