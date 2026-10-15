# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Additive, evidence-bounded source catalogue for accepted GeoViewer deliveries."""
from __future__ import annotations

import hashlib
import csv
import io
import json
from geoviewer_io import read_up_to, ContractProgress, contract_rows
import math
import os
from pathlib import Path, PurePosixPath
import re
import uuid
from geoviewer_surface_registration import derive_surface_evidence
from geoviewer_metadata_evidence import annotate_rows
from geoviewer_seismic_delivery import seismic_invalidations
from geoviewer_native_receipt import read_native_receipt


SCHEMA = "geoviewer.source-contract/1"
SHARDED_SCHEMA = "geoviewer.source-contract/2"
SHARDED_ENCODING = "ordered-jsonl-shards/1"
POINTER = "INTEROPERABILITY_CURRENT.json"
TABLES = ("entities", "artifacts", "relationships", "metadata_evidence", "gaps")
_SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_SIDECAR_BYTES = 1024 * 1024
MAX_HEAD_BYTES = 32 * 1024 * 1024
# Writer and reader share the same resource contract. Invalid generations must
# fail before the committed pointer can move to an unreadable table.
MAX_TABLE_BYTES = 256 * 1024 * 1024
MAX_TABLE_ROWS = 1_000_000
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_TABLE_SHARDS = 8
MAX_SHARDED_TABLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SHARDED_TABLE_ROWS = 2_000_000

# Native receipts can contain large decoder-only diagnostics. Keep every field
# consumed below or by derive_surface_evidence; never renumber objects.
NATIVE_OBJECT_FIELDS = frozenset('''
object_id name object_name category subject_type blob_type status parent_id ancestry
well_id reference_seismic_id reason missing_vertex_slots segments attributes_status
attribute_vectors attribute_value_count attribute_failure source_blob_sha256 data_pk
decoder_profile outer_item_count decoded_nonempty_parts empty_or_undecoded_parts
segment_order vertex_order attribute_binding_status attribute_binding_details
unit unit_status depth_unit horizontal_unit declared_template_unit measurement
crs_status native_unit_evidence project_units unit_resolution_gaps template_id
numeric_round_trip node_size cell_mask_preserved node_table_scope well_name_linkage
crs vertical_domain vertical_unit vertical_reference time_convention md_unit
md_reference log_kind index_kind sampling_kind null_semantics md_reference_status
sampling_regularity sampling interval_records curve_kind index_type index_role
index_shape index_encoding index_base value_role value_shape value_encoding
native_null_value null_count md_strictly_increasing interval_boundary_semantics
category_lookup category_lookup_status native_source_locator native_decoder_profile
research_gaps native_category_legend log_array_diagnostics
trajectory_station_evidence angle_units_status coordinate_units_status native_flags
typed_envelope_profile native_settings native_settings_semantics surface_role
grid_registration node_registration well_identity track_semantics head_semantics
marker_semantics checkshot_semantics polygon_topology object_version node_count
coordinate_storage_profile defined_nodes origin increments maximum i_basis j_basis
rotation_radians ordering dataset_exported
'''.split())


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> tuple[dict, str | None]:
    if not path.is_file():
        return {}, None
    _unlinked(path)
    with path.open('rb') as handle:
        raw = read_up_to(handle, MAX_TABLE_BYTES + 1)
    if len(raw) > MAX_TABLE_BYTES:
        raise ValueError(f'Contract metadata exceeds explicit byte bound: {path}')
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Receipt must be an object: {path}")
    return data, digest(raw)


def _unlinked(path: Path) -> None:
    """Do not follow a link/junction at any existing component of an input path."""
    for component in (path, *path.parents):
        if component.exists() and (component.is_symlink() or component.is_junction()):
            raise ValueError(f"Linked path is not permitted: {component}")


def _resolve(base: Path, relative: str, *, allow_parent: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Path must be a nonempty POSIX relative path")
    part = PurePosixPath(relative)
    if part.is_absolute() or any(x in ("", ".") or ":" in x for x in relative.split("/")):
        raise ValueError(f"Invalid relative path: {relative}")
    if not allow_parent and ".." in part.parts:
        raise ValueError(f"Path escapes declared base: {relative}")
    target = base.joinpath(*part.parts)
    _unlinked(target)
    return target.resolve(strict=False)


def _category(raw: dict) -> str:
    values = [str(raw.get(k) or "") for k in ("subject_type", "category", "blob_type")]
    exact = {"SurfaceAttrSubject": "surface_property", "QualityAssuranceAttrSubject": "qa_scalar",
             "QualityAssuranceSubject": "qa_scalar", "SurfaceSubject": "surface_geometry",
             "WellLogSubject": "well_log", "FloatWellLog": "well_log", "IntWellLog": "well_log",
             "WellTraceSubject": "well", "WellSubject": "well", "WellTrace3": "trajectory",
             "ExplicitTrajectoryProviderData": "trajectory", "MdInclAzimTrajectoryProviderData": "trajectory",
             "XYZTrajectoryProviderData": "trajectory", "RegValGrid2": "numeric_grid_unclassified",
             "ValGrid2": "numeric_grid_unclassified", "grids": "numeric_grid_unclassified",
             "logs": "well_log", "seismic": "seismic", "seismic_volume": "seismic", "SeismicSubject": "seismic",
             "trajectories": "trajectory", "well_headers": "well", "well": "well",
             "polygons": "culture_polygon", "polygon_attributes": "culture_polygon", "polygon_parts": "culture_polygon", "PolygonsSubject": "culture_polygon",
             "points": "culture_point", "Polygons3": "culture_polygon",
             "Points3": "culture_point", "point_identities": "culture_point", "point_attributes": "culture_point",
             "point_missing_vertices": "culture_point", "polygon_missing_vertices": "culture_polygon"}
    for value in values:
        if value in exact and value not in ("grids", "logs"):
            return exact[value]
    for value in values:
        if value in exact:
            return exact[value]
    return values[0] or values[1] or values[2] or "unknown"


def _role(row: dict) -> str:
    if row.get('category') in ('point_missing_vertices', 'polygon_missing_vertices'):
        return 'missing_vertex_evidence'
    if row.get('category') in ('polygon_attributes', 'point_attributes'):
        return 'native_attribute_values'
    if row.get('category') == 'polygon_parts':
        return 'polygon_part_metadata'
    if row.get('category') == 'point_identities':
        return 'point_identity_metadata'
    name = Path(str(row.get("path") or "")).stem.lower()
    category = _category(row)
    if "preview" in name:
        return "preview"
    if "cell_definition" in name or "cell_mask" in name:
        return "cell_mask"
    if "node_definition" in name or "node_mask" in name:
        return "node_mask"
    if name == "cells" or name.endswith("_cells") or "cell_topology" in name:
        return "cell_topology"
    if name.endswith("metadata") or str(row.get("format", "")).upper() == "JSON":
        return "metadata"
    if category in ("surface_property", "qa_scalar"):
        return "property_values"
    if category == "surface_geometry":
        return "geometry_values"
    if category == "seismic" and str(row.get("format", "")).upper() == "SEGY":
        return "full_payload"
    return "data" if category in ("well_log", "trajectory", "well", "culture_polygon", "culture_point") else "unclassified_data"


def _source_identity(native: dict, context: dict) -> tuple[str, str, dict]:
    before, after = native.get("source_hashes_before"), native.get("source_hashes_after")
    if isinstance(before, dict) and before == after and native.get("source_unchanged") is True:
        roles = {}
        seen_roles = set()
        for path, value in before.items():
            # Receipts may move between hosts. Interpret only the leaf role, not
            # a host path; never select the first of several potential sources.
            leaf = str(path).replace("\\", "/").rsplit("/", 1)[-1].lower()
            role = "pet" if leaf.endswith(".pet") else {"model.ptd": "model", "data.ptd": "data"}.get(leaf)
            if role in seen_roles:
                raise ValueError(f"Ambiguous source hash role: {role}")
            if role:
                seen_roles.add(role)
            if role and isinstance(value, str) and _SHA.fullmatch(value.lower()):
                roles[role] = value.lower()
        if set(roles) == {"pet", "model", "data"}:
            return digest(canonical(roles))[:24], "verified_pet_model_data_receipt_hashes", roles
    # Content-only fallback avoids run paths, timestamps, export order and filenames.
    # It is collision-prone for distinct projects with identical sparse metadata.
    objects = []
    for item in context.get("objects", []):
        if isinstance(item, dict):
            objects.append({k: item.get(k) for k in ("object_id", "category", "parent_id", "model_version")})
    material = {"project_name": context.get("project_name"),
                "objects": sorted(objects, key=lambda x: canonical(x))}
    return digest(canonical(material))[:24], "content_only_unresolved_source_revision", {}


def _base_and_path(package: Path, destination: Path, run: Path, row: dict) -> tuple[str, str]:
    raw = str(row.get("path") or "")
    target = _resolve(destination, raw, allow_parent=True)
    if target.is_relative_to(destination):
        return "exports", target.relative_to(destination).as_posix()
    if target.is_relative_to(package):
        return "package", target.relative_to(package).as_posix()
    if _category(row) == "seismic" and target.is_relative_to(run):
        # `run` is the portable common ancestor, which can also contain other
        # projects. A generated seismic fallback must belong to this data run.
        if package.parent.name == 'package' and package.parent.parent.name == 'extraction':
            seismic = package.parent.parent.parent / 'seismic'
        elif package.name == 'report':
            seismic = package.parent / 'seismic'
        else:
            seismic = None
        if seismic is not None and target.is_relative_to(seismic):
            return "run", target.relative_to(run).as_posix()
    raise ValueError(f"Index path outside allowed scope: {raw}")


def validate_seismic_index(file_index):
    """Reject contradictory acceptance without erasing recorded quarantine."""
    invalid = {row['object_id'] for row in seismic_invalidations(file_index)}
    for row in file_index.get('files', []):
        if (_category(row) == 'seismic' and row.get('status') == 'exported'
                and str(row.get('object_id')) in invalid):
            raise ValueError('Accepted seismic row contradicts invalid_seismic: '+str(row['object_id']))


def build_catalogue(package: Path, destination: Path, file_index: dict,
                    inventory: dict | None = None, *,
                    bound_inputs: dict[Path, tuple[dict, str | None]] | None = None,
                    bound_heads: bytes | None = None, bound_heads_known: bool = False) -> dict:
    """Normalize an existing delivery in memory; read only and never hash bulk payloads.

    This shared builder preserves the normal publisher's tables. It neither creates
    a generation nor treats its result as an originally committed catalogue.
    """
    _unlinked(Path(package).absolute()); _unlinked(Path(destination).absolute())
    package = Path(package).resolve(strict=True)
    destination = Path(destination).resolve(strict=True)
    if not package.is_dir() or not destination.is_dir():
        raise ValueError("Package and EXPORTS directories must exist")
    if not isinstance(file_index, dict) or not isinstance(file_index.get("files"), list):
        raise ValueError("Accepted FILE_INDEX object with files list required")
    if inventory is not None and not isinstance(inventory, dict):
        raise ValueError("Inventory must be an object")
    validate_seismic_index(file_index)
    run = Path(os.path.commonpath((str(package), str(destination))))
    if run in (package, destination, Path(package.anchor)):
        raise ValueError("Package and EXPORTS require a bounded shared run directory")
    _unlinked(package); _unlinked(destination)
    def read_input(path: Path) -> tuple[dict, str | None]:
        if bound_inputs is not None and path in bound_inputs:
            return bound_inputs[path]
        if path.name == 'native_recovery_report.json' and path.is_file():
            _unlinked(path)
            value, sha256, _ = read_native_receipt(path, NATIVE_OBJECT_FIELDS)
            return value, sha256
        return _read(path)

    context, context_hash = read_input(package / "01_project_metadata/project_context.json")
    native_rel = "07_workflows_reports/native_recovery/native_recovery_report.json"
    native, native_hash = read_input(package / native_rel)
    if not native_hash:
        native_rel = "07_workflows_reports/native_recovery/native_recovery.json"
        native, native_hash = read_input(package / native_rel)
    spatial_rel = "07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json"
    spatial, spatial_hash = read_input(package / spatial_rel)
    index_hash = digest(canonical(file_index))
    on_disk = destination / "FILE_INDEX.json"
    if on_disk.is_file():
        _unlinked(on_disk)
        saved, _ = read_input(on_disk)
        if digest(canonical(saved)) != index_hash:
            raise ValueError("FILE_INDEX argument differs from committed FILE_INDEX.json")
    else:
        raise ValueError("Committed FILE_INDEX.json is required")
    revision, revision_scope, source_hashes = _source_identity(native, context)
    project_key = "revision:" + revision
    entities: dict[str, dict] = {}
    relationships: list[dict] = []
    evidence: list[dict] = []
    gaps: list[dict] = []
    artifacts: list[dict] = []
    artifact_keys: set[str] = set()
    metadata_bindings: list[dict] = []
    consumed_metadata: list[dict] = []
    seismic_transports: dict[str, dict] = {}
    seismic_owners: dict[str, list[str]] = {}
    # WellTrace3 payloads reuse their WellTraceSubject UUID. A native subject
    # and its stored track are distinct entities despite that shared source ID.
    legacy_tracks = set()
    for item in spatial.get('objects', []):
        if not isinstance(item, dict) or item.get('blob_type') != 'WellTrace3':
            continue
        tag = item.get('object_id')
        if (not isinstance(tag, str) or not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', tag)
                or tag == '00000000-0000-0000-0000-000000000000'):
            raise ValueError('Legacy trajectory requires canonical native UUID')
        if tag in legacy_tracks:
            raise ValueError('Duplicate legacy trajectory identity in spatial receipt')
        if item.get('well_id') not in (None, '', tag):
            raise ValueError('Legacy trajectory well identity conflicts with shared native UUID')
        legacy_tracks.add(tag)
    legacy_subjects = {tag: [] for tag in legacy_tracks}
    for item in context.get('objects', []):
        if isinstance(item, dict) and item.get('object_id') in legacy_subjects:
            legacy_subjects[item['object_id']].append(item)

    def entity(object_id: str, raw: dict, locator: str, lane: str = "native") -> dict:
        if not object_id:
            raise ValueError(f"Missing source object ID at {locator}")
        key = f"{project_key}:{lane}:{object_id}"
        if key not in entities:
            entities[key] = dict(entity_key=key, project_revision=project_key,
                                 source_id=object_id, native_uuid=object_id if lane in ("native", "native_trajectory") and re.fullmatch(
                                     r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", object_id) else None,
                                 external_uwi=None, source_lane=lane, name=raw.get("name") or raw.get("object_name") or object_id,
                                 category=_category(raw), source_status=raw.get("status") or "unknown",
                                 source_locator=locator, artifact_keys=[])
        current = entities[key]
        if current['name'] == object_id and (raw.get('name') or raw.get('object_name')):
            current['name'] = raw.get('name') or raw['object_name']
        if current["category"] in ("unknown", "numeric_grid_unclassified") and _category(raw) not in ("unknown", "numeric_grid_unclassified"):
            current["category"] = _category(raw)
        return current

    for number, item in enumerate(contract_rows(context.get("objects", []), "catalogue_discovery", source="project_context")):
        if isinstance(item, dict) and item.get("object_id"):
            node = entity(str(item["object_id"]), item, f"01_project_metadata/project_context.json#/objects/{number}")
            if isinstance(item.get('category'), str):
                node['native_subject_type'] = item['category']
            if item.get("parent_id"):
                parent = entity(str(item["parent_id"]), {"status": "referenced"},
                                f"01_project_metadata/project_context.json#/objects/{number}/parent_id")
                relationships.append(dict(source_key=node["entity_key"], target_key=parent["entity_key"],
                                          relation="native_parent", status="exact_id_reference",
                                          locator=f"01_project_metadata/project_context.json#/objects/{number}/parent_id"))
    for receipt_name, receipt in ((native_rel, native), (spatial_rel, spatial)):
        for number, item in enumerate(contract_rows(receipt.get("objects", []), "metadata_assembly", source=receipt_name)):
            if not isinstance(item, dict) or not item.get("object_id"):
                continue
            source_id = str(item["object_id"])
            locator = f"{receipt_name}#/objects/{number}"
            legacy_track = receipt_name == spatial_rel and item.get('blob_type') == 'WellTrace3'
            node = entity(source_id, {**item, 'category': 'trajectory', 'subject_type': 'WellTrace3'} if legacy_track else item,
                          locator, 'native_trajectory' if legacy_track else 'native')
            if legacy_track:
                node['identity_role'] = 'legacy_stored_track'
                node['identity_scope'] = 'Role-qualified legacy payload sharing its native WellTraceSubject UUID'
                subjects = legacy_subjects[source_id]
                if len(subjects) == 1 and subjects[0].get('category') == 'WellTraceSubject':
                    well = entity(source_id, subjects[0], locator + '/shared_WellTraceSubject_UUID')
                    relationships.append(dict(source_key=node['entity_key'],target_key=well['entity_key'],
                        relation='source_well',status='exact_id_reference',locator=locator+'/object_id',
                        identity_rule='WellTrace3 shared native UUID with one typed WellTraceSubject',
                        corroborating_locator=well['source_locator']))
                else:
                    gaps.append(dict(entity_key=node['entity_key'],field='source_well',code='LEGACY_WELL_IDENTITY_UNRESOLVED',
                        reason='Exactly one typed WellTraceSubject with the payload UUID is required; names are not identity',searched_scope=locator))
            node["source_status"] = item.get("status") or node["source_status"]
            if item.get('blob_type') in ('Points3', 'Polygons3'):
                missing_slots = item.get('missing_vertex_slots')
                count_locator = locator + '/missing_vertex_slots'
                count_method = 'explicit_native_decoder_slot_count'
                segments = item.get('segments')
                if (missing_slots is None and item.get('blob_type') == 'Polygons3'
                        and isinstance(segments, list) and all(isinstance(s, dict)
                        and type(s.get('missing_vertex_slots')) is int and s['missing_vertex_slots'] >= 0 for s in segments)):
                    missing_slots = sum(s['missing_vertex_slots'] for s in segments)
                    count_locator = locator + '/segments'
                    count_method = 'sum_recorded_native_part_missing_vertex_slots'
                if type(missing_slots) is int and missing_slots >= 0:
                    evidence.append(dict(entity_key=node['entity_key'], field='missing_vertex_slots',
                        raw_value=missing_slots, locator=count_locator, resolution='source_count_evidence', method=count_method))
                    if missing_slots:
                        gaps.append(dict(entity_key=node['entity_key'], field='geometry_vertices',
                            code='NATIVE_VERTEX_GAPS', reason='Native missing vertex slots remain; auxiliary raw components do not complete geometry or authorize bridging parts', searched_scope=count_locator))
            if item.get('blob_type') == 'Points3' and item.get('attributes_status', 'not_present') != 'not_present':
                raw_decoded = item.get('attributes_status') == 'raw_typed_values_decoded'
                evidence.append(dict(entity_key=node['entity_key'], field='point_attribute_vectors',
                    raw_value={k:item[k] for k in ('attributes_status','attribute_value_count',
                        'attribute_failure','source_blob_sha256') if k in item},
                    locator=locator, resolution='source_recorded; association_and_semantics_unresolved'))
                for ordinal, vector in enumerate(item.get('attribute_vectors', [])):
                    if (not isinstance(vector, dict) or type(vector.get('attribute_vector_index')) is not int
                            or vector['attribute_vector_index'] != ordinal):
                        raise ValueError('Point attribute evidence lacks exact native vector order')
                    evidence.append(dict(entity_key=node['entity_key'], field=f'point_attribute_vector:{ordinal}',
                        raw_value=vector, locator=locator + f'/attribute_vectors/{ordinal}',
                        resolution='source_recorded', scope='source_attribute_vector_observation'))
                gaps.append(dict(entity_key=node['entity_key'], field='point_attributes',
                    code='POINT_ATTRIBUTE_ASSOCIATION_UNRESOLVED' if raw_decoded else 'POINT_ATTRIBUTES_NOT_EXPORTED',
                    reason=('Raw typed values preserved; names, point association, null/default application, units and roles remain unqualified'
                            if raw_decoded else 'Attached native attributes unsupported; geometry preservation does not export their values or meanings'),
                    searched_scope=locator))
            if item.get('blob_type') == 'Polygons3' and isinstance(item.get('segments'), list):
                part_evidence = {k: item[k] for k in ('object_id', 'data_pk', 'blob_type', 'decoder_profile',
                    'outer_item_count', 'decoded_nonempty_parts', 'empty_or_undecoded_parts',
                    'attributes_status', 'segment_order', 'vertex_order') if k in item}
                part_evidence['part_count'] = len(item['segments'])
                part_evidence['part_metadata_field_prefix'] = 'polygon_part:'
                evidence.append(dict(entity_key=node['entity_key'], field='polygon_geometry_evidence',
                    raw_value=part_evidence, locator=locator, resolution='source_recorded'))
                for ordinal, segment in enumerate(item['segments']):
                    if not isinstance(segment, dict) or type(segment.get('part_index')) is not int or segment['part_index'] != ordinal:
                        raise ValueError('Polygon segment evidence lacks exact native collection order')
                    evidence.append(dict(entity_key=node['entity_key'], field=f'polygon_part:{ordinal}',
                        raw_value=segment, locator=locator + f'/segments/{ordinal}',
                        resolution='source_recorded', scope='source_polygon_part_observation'))
                gaps.append(dict(entity_key=node['entity_key'], field='polygon_ring_roles',
                    code='POLYGON_RING_ROLES_UNRESOLVED', reason='Native closure flags do not establish shell/hole or part connectivity semantics', searched_scope=locator))
                if item.get('attributes_status') != 'not_present':
                    raw_decoded = item.get('attributes_status') == 'raw_typed_values_decoded'
                    evidence.append(dict(entity_key=node['entity_key'], field='polygon_attribute_vectors',
                        raw_value={k:item[k] for k in ('attributes_status','attribute_value_count',
                            'attribute_failure','attribute_binding_status','attribute_binding_details','source_blob_sha256') if k in item},
                        locator=locator, resolution='source_recorded; association_and_semantics_unresolved'))
                    for ordinal, vector in enumerate(item.get('attribute_vectors', [])):
                        if not isinstance(vector, dict) or type(vector.get('attribute_vector_index')) is not int or vector['attribute_vector_index'] != ordinal:
                            raise ValueError('Polygon attribute evidence lacks exact native vector order')
                        evidence.append(dict(entity_key=node['entity_key'], field=f'polygon_attribute_vector:{ordinal}',
                            raw_value=vector, locator=locator + f'/attribute_vectors/{ordinal}',
                            resolution='source_recorded', scope='source_attribute_vector_observation'))
                    gaps.append(dict(entity_key=node['entity_key'], field='polygon_attributes',
                        code='POLYGON_ATTRIBUTE_BINDINGS_UNRESOLVED' if raw_decoded else 'POLYGON_ATTRIBUTES_NOT_EXPORTED',
                        reason=('Raw typed values preserved; names/IDs require exact native registry/Model agreement, while part associations/default application/units and ring meanings remain unqualified'
                                if raw_decoded else 'Native attached properties remain unsupported; geometry preservation does not export their values or meanings'), searched_scope=locator))
            for field in ("unit", "unit_status", "depth_unit", "horizontal_unit", "declared_template_unit", "measurement", "crs_status",
                          "native_unit_evidence", "project_units", "unit_resolution_gaps",
                          "template_id", "blob_type", "subject_type", "numeric_round_trip", "node_size",
                          "cell_mask_preserved", "node_table_scope", "well_id", "well_name_linkage",
                          "crs", "vertical_domain", "vertical_unit", "vertical_reference", "time_convention",
                          "md_unit", "md_reference", "log_kind", "index_kind", "sampling_kind", "null_semantics",
                          "md_reference_status", "sampling_regularity", "sampling", "interval_records",
                          "curve_kind", "index_type", "index_role", "index_shape", "index_encoding", "index_base",
                          "value_role", "value_shape", "value_encoding", "native_null_value", "null_count",
                          "md_strictly_increasing", "interval_boundary_semantics", "category_lookup", "category_lookup_status",
                          "native_source_locator", "native_decoder_profile", "research_gaps",
                          "native_category_legend", "log_array_diagnostics",
                          "trajectory_station_evidence", "angle_units_status", "coordinate_units_status", "native_flags",
                          "typed_envelope_profile", "native_settings", "native_settings_semantics",
                          "surface_role", "grid_registration", "node_registration", "well_identity",
                          "track_semantics", "head_semantics", "marker_semantics", "checkshot_semantics", "polygon_topology"):
                value = item.get(field)
                if value is not None and value != "":
                    evidence.append(dict(entity_key=node["entity_key"], field=field, raw_value=value,
                                         locator=locator + "/" + field, resolution="source_recorded"))
            parent = item.get("parent_id")
            ancestry = item.get("ancestry")
            if not parent and isinstance(ancestry, list) and ancestry and isinstance(ancestry[0], dict):
                parent = ancestry[0].get("parent")
            for relation, target in (("native_parent", parent), ("source_well", item.get("well_id")),
                                     ("reference_seismic", item.get("reference_seismic_id"))):
                if legacy_track and relation == 'source_well':
                    continue  # Only the typed same-UUID rule above establishes this link.
                if target:
                    target_node = entity(str(target), {"category": "unknown", "status": "referenced"}, locator + "/" + relation)
                    relationships.append(dict(source_key=node["entity_key"], target_key=target_node["entity_key"],
                                              relation=relation, status="exact_id_reference", locator=locator))
            if item.get("unit_status") == "unresolved" or item.get("crs_status") == "unresolved":
                for field in ("unit", "crs"):
                    if item.get(field + "_status") == "unresolved":
                        gaps.append(dict(entity_key=node["entity_key"], field=field, code="SOURCE_SEMANTICS_UNRESOLVED",
                                         reason=item.get("reason") or "Source receipt leaves semantics unresolved", searched_scope=locator))
    for row in derive_surface_evidence(native, project_key, native_rel):
        if row['entity_key'] in entities:
            evidence.append(row)
    well_metadata = spatial.get('model_well_head_decode', {}).get('native_well_metadata', {})
    well_subjects = well_metadata.get('subjects', []) if well_metadata.get('schema') == 'geoviewer.native-well-evidence/1' else []
    well_model_hash = well_metadata.get('source_model_sha256')
    if well_model_hash is not None and (not isinstance(well_model_hash, str) or not _SHA.fullmatch(well_model_hash)
            or (source_hashes.get('model') and source_hashes['model'] != well_model_hash)):
        raise ValueError('Native well metadata source Model hash conflicts with source revision')
    well_subject_map = {}
    for number, item in enumerate(well_subjects):
        tag = item.get('object_id') if isinstance(item, dict) else None
        if not isinstance(tag, str) or not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', tag) or tag == '00000000-0000-0000-0000-000000000000' or tag in well_subject_map:
            raise ValueError('Native well metadata must have unique canonical UUIDs')
        well_subject_map[tag] = item
        locator = f'{spatial_rel}#/model_well_head_decode/native_well_metadata/subjects/{number}'
        well = entity(tag, {'category': 'well', 'name': item['name'], 'status': 'metadata_only'}, locator)
        evidence.append(dict(entity_key=well['entity_key'],field='native_well_metadata',raw_value=item,
                             locator=locator,source_model_sha256=well_model_hash,
                             resolution='typed_native_declarations_not_calibrated_datums'))
        if item.get('definite_provider_id'):
            provider = entity(item['definite_provider_id'], {'category': 'trajectory', 'status': 'referenced'}, locator,
                              'native_trajectory' if item['definite_provider_id'] in legacy_tracks else 'native')
            # A stored head does not depend on a track to exist. Keep this native
            # declaration separate from the track's directional source_well link.
            relationships.append(dict(source_key=well['entity_key'],target_key=provider['entity_key'],
                relation='declared_definite_provider',status='exact_id_reference',locator=locator+'/definite_provider_id'))
    for number, item in enumerate(contract_rows((inventory or {}).get("native_objects", []), "catalogue_discovery", source="native_inventory")):
        if isinstance(item, dict) and item.get("object_id"):
            node = entity(str(item['object_id']), {**item, 'category': 'seismic', 'status': 'metadata_only'},
                          f"inventory#/native_objects/{number}")
            node['native_definition'] = {k: item[k] for k in ('subject_type', 'virtual', 'references') if k in item}
    # New literal native declarations come from the hashed project-context
    # receipt, not a similarly named transport or an unbound external inventory.
    native_seismic = context.get('seismic_objects', [])
    native_by_id = {}
    for item in context.get('model_subject_identities', []):
        if isinstance(item, dict) and item.get('object_id'):
            native_by_id.setdefault(item['object_id'], []).append(item)
    # A malformed record can contain a competing UUID even when interpretation
    # failed before it entered the subject list. Preserve that ambiguity.
    unknown_model_identity = False
    for error in context.get('model_record_errors', []):
        candidates = error.get('candidate_object_ids', []) if isinstance(error, dict) else []
        if not candidates:
            unknown_model_identity = True
        for tag in candidates:
            if isinstance(tag, str):
                native_by_id.setdefault(tag, []).append({'interpretation_failed': True})
    seismic_counts = {}
    for item in native_seismic:
        if isinstance(item, dict):
            tag = item.get('object_id')
            seismic_counts[tag] = seismic_counts.get(tag, 0) + 1
    reference_kinds = {
        'realized_from_tag': ('declared_realized_from', {'SeismicSubject', 'VirtualSeismicSubject'}),
        'line_geometry/element': ('declared_line_geometry', set()),
        'vintage_tag': ('declared_vintage', {'VintageSubject'}),
        'domain_template_tag': ('declared_domain_template', {'ContTemplateSubject', 'DiscTemplateSubject'}),
    }
    model_identity = context.get('model_source_identity', {})
    model_digest = model_identity.get('sha256')
    model_bound = isinstance(model_digest, str) and bool(_SHA.fullmatch(model_digest))
    if model_bound and source_hashes.get('model') and model_digest != source_hashes['model']:
        raise ValueError('Native seismic declaration Model hash conflicts with source revision')
    for number, item in enumerate(native_seismic):
        if not isinstance(item, dict) or not item.get('object_id'):
            continue
        declaration = item.get('native_declarations')
        if not isinstance(declaration, dict) or declaration.get('schema') != 'geoviewer.native-seismic-evidence/1':
            continue  # Historical context remains readable without this addition.
        tag = item['object_id']
        locator = f'01_project_metadata/project_context.json#/seismic_objects/{number}/native_declarations'
        node = entity(tag, {**item, 'category': 'seismic', 'status': 'metadata_only'}, locator)
        candidates = native_by_id.get(tag, [])
        source_bound = (model_bound and context.get('model_readable') is True and not unknown_model_identity
                        and seismic_counts[tag] == 1 and len(candidates) == 1
                        and candidates[0].get('subject_type') == item.get('subject_type')
                        and type(declaration.get('record_index')) is int
                        and candidates[0].get('record_index') == declaration['record_index']
                        and declaration.get('status') == 'typed_declarations')
        evidence.append(dict(entity_key=node['entity_key'],field='native_seismic_declarations',
            raw_value=declaration,locator=locator,source_model_sha256=model_digest,
            resolution='exact_native_record' if source_bound else 'source_recorded',
            scope='Literal source declarations; no physical-axis, completeness, virtual-state or receiving qualification'))
        if not source_bound:
            gaps.append(dict(entity_key=node['entity_key'],field='native_seismic_declarations',
                code='NATIVE_SEISMIC_DECLARATIONS_UNBOUND',
                reason='Exact supported source profile, unique typed subject and Model-byte identity are required',searched_scope=locator))
            continue
        field_gaps = declaration.get('gaps', [])
        if not isinstance(field_gaps, list) or len(field_gaps) > 64:
            raise ValueError('Native seismic declaration gaps exceed schema bound')
        for gap in field_gaps:
            if isinstance(gap, dict):
                gaps.append(dict(entity_key=node['entity_key'],field=gap.get('field'),
                    code='NATIVE_SEISMIC_FIELD_UNRESOLVED',reason=gap.get('reason'),searched_scope=locator))
        fields = declaration.get('fields', [])
        if not isinstance(fields, list) or len(fields) > 64:
            raise ValueError('Native seismic declaration fields exceed schema bound')
        field_counts = {}
        for field in fields:
            if isinstance(field, dict):
                key = field.get('field')
                field_counts[key] = field_counts.get(key, 0) + 1
        for ordinal, field in enumerate(fields):
            if not isinstance(field, dict) or field.get('field') not in reference_kinds:
                continue
            name = field['field']; target_id = field.get('value')
            if target_id in ('', 0, None, '00000000-0000-0000-0000-000000000000'):
                continue
            where = locator+f'/fields/{ordinal}'
            if (field_counts[name] != 1 or field.get('value_kind') != 'reference'
                    or not isinstance(target_id, str) or not re.fullmatch(
                        r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', target_id)):
                gaps.append(dict(entity_key=node['entity_key'],field=name,code='NATIVE_SEISMIC_REFERENCE_AMBIGUOUS',
                    reason='Duplicate or malformed native reference; no target selected',searched_scope=where))
                continue
            relation, allowed_types = reference_kinds[name]
            targets = native_by_id.get(target_id, [])
            target = entity(target_id, {'status':'referenced'}, where)
            exact = (target_id != tag and len(targets) == 1 and targets[0].get('subject_type') in allowed_types)
            relationships.append(dict(source_key=node['entity_key'],target_key=target['entity_key'],
                relation=relation,status='exact_id_reference' if exact else 'unresolved_reference',
                locator=where,native_path=field.get('native_path'),source_model_sha256=model_digest,
                dependency_scope='Provenance/declaration only, not a required payload dependency or physical calibration'))
            if not exact:
                gaps.append(dict(entity_key=node['entity_key'],field=name,code='NATIVE_SEISMIC_REFERENCE_UNRESOLVED',
                    reason='Referenced target missing, ambiguous, self-referential or outside the validated target-type scope',searched_scope=where))
    for number, item in enumerate((inventory or {}).get("objects", [])):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        transport_id = str(item['id'])
        if transport_id in seismic_transports:
            raise ValueError('Duplicate seismic transport identity')
        locator = f"inventory#/objects/{number}"
        # Native identity comes only from the discovery receipt's exact binding.
        # A UUID-looking filename, shared name or reported original path is insufficient.
        exact = item.get('association') == 'exact_native_UUID_in_selected_store'
        owners = []
        for member in item.get('native_objects', []):
            if isinstance(member, dict) and member.get('object_id'):
                owner = entity(str(member['object_id']), {**member, 'category': 'seismic', 'status': 'metadata_only'},
                               locator + '/native_objects')
                owners.append(owner['entity_key'])
        owners = sorted(set(owners))
        identity = 'native_binding:' + digest(canonical(owners))[:24] if exact and owners else 'unresolved_transport:' + transport_id
        node = entity(identity, {"name": item.get("name"), "category": "seismic",
                                "status": item.get("status") or "inventory_only"}, locator, 'seismic_transport')
        node.update(transport_id=transport_id, association=item.get('association'),
                    identity_scope='exact_native_owner_set_within_source_revision' if exact and owners else 'transport_only_unresolved_persistent_identity',
                    source_reference_recorded_only=item.get('source'), preserved_copy=item.get('preserved_copy'))
        seismic_transports[transport_id] = node
        seismic_owners[transport_id] = owners if exact else []
        for owner in owners:
            relationships.append(dict(source_key=owner, target_key=node['entity_key'], relation='materialized_payload',
                                      status='exact_id_reference' if exact else 'unresolved_reference', locator=locator + '/native_objects'))

    rows = [("files", i, row) for i, row in enumerate(file_index["files"])]
    rows += [("partial_files", i, row) for i, row in enumerate(file_index.get("partial_files", []))]
    for source_list, row_number, row in contract_rows(rows, 'metadata_assembly', source='file_index'):
        if not isinstance(row, dict):
            raise ValueError("Index row must be an object")
        identity = str(row.get("object_id") or "")
        lane = "companion" if (row.get("source_lane") == "companion" or
                              str(row.get("category") or "").startswith("companion_") or
                              identity.startswith("companion_")) else "native"
        if lane == 'native' and identity in legacy_tracks and (
                _category(row) == 'trajectory' or row.get('category') in ('trajectory', 'trajectories')):
            lane = 'native_trajectory'
        node = seismic_transports.get(identity) if _category(row) == 'seismic' else None
        if node is None:
            node = entity(identity, row, f"FILE_INDEX.json#/{source_list}/{row_number}", lane)
        unit = row.get("units")
        if unit and unit != "unknown":
            evidence.append(dict(entity_key=node["entity_key"], field="index_declared_units", raw_value=unit,
                                 locator=f"FILE_INDEX.json#/{source_list}/{row_number}/units",
                                 resolution="index_declaration_not_independent_validation"))
        base, relative = _base_and_path(package, destination, run, row)
        inferred_role = _role(row)
        role = "property_values" if node["category"] in ("surface_property", "qa_scalar") and inferred_role in (
            "geometry_values", "data", "unclassified_data") else (
            "geometry_values" if node["category"] == "surface_geometry" and inferred_role == "unclassified_data" else inferred_role)
        # Identity describes the owned representation, not its position in an
        # index/generation or its changing acceptance state. Historical ordinal
        # keys remain readable; only newly built catalogues use this scheme.
        representation = dict(path_base=base, relative_path=relative, role=role,
                              format=row.get("format"))
        key = f"{node['entity_key']}:artifact:{digest(canonical(representation))}"
        if key in artifact_keys:
            raise ValueError("Duplicate owned artifact representation in FILE_INDEX")
        artifact_keys.add(key)
        owners = [node['entity_key'], *seismic_owners.get(identity, [])] if _category(row) == 'seismic' else [node['entity_key']]
        artifacts.append(dict(artifact_key=key, identity_scheme='source-revision-owner-portable-representation/1',
                              entity_key=node["entity_key"], owner_keys=owners, role=role,
                              format=row.get("format"), path_base=base, relative_path=relative,
                              expected_bytes=row.get("bytes"), recorded_sha256=row.get("sha256") or None,
                              integrity_scope=row.get("integrity") or "unknown", numerical_qc=row.get("numerical_qc") or "unknown",
                              original_status=row.get("status") or "unknown", index_section=source_list,
                              storage=row.get('storage') or 'unknown',
                              deletion_ownership='generated_output_alias; hardlinks may share underlying bytes; independent copy required before destructive receiving operations',
                              source_locator=f"FILE_INDEX.json#/{source_list}/{row_number}", record_count=row.get("record_count")))
        for owner in owners:
            entities[owner]["artifact_keys"].append(key)

    # Read only bounded, receipt-bound metadata through its actual indexed path.
    # Never follow receipt_path/source absolute paths, and never read the seismic payload.
    roots = dict(exports=destination, package=package, run=run)
    for number, item in enumerate(contract_rows((inventory or {}).get('objects', []), "metadata_assembly", source="companion_inventory")):
        if not isinstance(item, dict) or str(item.get('id')) not in seismic_transports:
            continue
        node = seismic_transports[str(item['id'])]
        owner_keys = [node['entity_key'], *seismic_owners.get(str(item['id']), [])]
        result = item.get('result') or {}
        locator = f'inventory#/objects/{number}/result'
        accepted = item.get('status') == 'converted' and result.get('status') == 'passed'
        source_matches = (isinstance(item.get('source'), str) and isinstance(result.get('source'), str)
                          and item['source'].replace('\\', '/').casefold() == result['source'].replace('\\', '/').casefold())
        if accepted and not source_matches:
            accepted = False
            node['source_status'] = 'source_binding_conflict'
            for owner in owner_keys:
                gaps.append(dict(entity_key=owner, field='seismic_source', code='SEISMIC_SOURCE_BINDING_CONFLICT',
                                 reason='Conversion receipt source differs from this inventory transport source', searched_scope=locator + '/source'))
        for owner in owner_keys:
            entities[owner]['materialization_status'] = 'converted' if accepted else 'unaccepted_or_unbound'
        if not accepted:
            continue
        summary = result.get('summary') or {}
        for owner in owner_keys:
            evidence.append(dict(entity_key=owner, field='conversion_qc', raw_value=summary,
                                 locator=locator + '/summary', resolution='recorded_numerical_readback_not_receiving_import'))
            evidence.append(dict(entity_key=owner, field='seismic_source_integrity',
                                 raw_value={k: result.get(k) for k in ('full_seismic_hash', 'source_unchanged',
                                     'source_stat_unchanged', 'source_hashes_before', 'source_hashes_after', 'integrity_scope')},
                                 locator=locator, resolution='recorded_source_preservation_scope'))
        for filename, field in (('conversion_metadata.json', 'seismic_conversion'), ('source_metadata.json', 'seismic_source')):
            declarations = [a for a in result.get('artifacts', []) if a.get('path') == filename]
            group = [a for a in artifacts if a['entity_key'] == node['entity_key'] and a['format'] == 'JSON'
                     and a['index_section'] == 'files' and a['original_status'] == 'exported']
            candidates = []
            if len(declarations) == 1 and _SHA.fullmatch(str(declarations[0].get('sha256') or '')):
                spec = declarations[0]
                candidates = [a for a in group if a['recorded_sha256'] == spec['sha256'] and a['expected_bytes'] == spec.get('size_bytes')]
            reason = 'Missing or ambiguous receipt-to-index metadata binding'
            if len(candidates) == 1:
                artifact = candidates[0]
                path = _resolve(roots[artifact['path_base']], artifact['relative_path'])
                try:
                    with path.open('rb') as handle:
                        raw = read_up_to(handle, MAX_SIDECAR_BYTES + 1)
                    consumed_metadata.append(dict(path_base=artifact['path_base'],
                        relative_path=artifact['relative_path'], state='bounded' if len(raw) <= MAX_SIDECAR_BYTES else 'oversize',
                        raw_sha256=digest(raw) if len(raw) <= MAX_SIDECAR_BYTES else None))
                    if len(raw) > MAX_SIDECAR_BYTES:
                        raise ValueError('Metadata exceeds 1 MiB contract normalization bound')
                    if len(raw) != artifact['expected_bytes'] or digest(raw) != artifact['recorded_sha256']:
                        raise ValueError('Metadata differs from receipt/index digest or size')
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError('Metadata must be an object')
                    metadata_bindings.append(dict(artifact_key=artifact['artifact_key'], path_base=artifact['path_base'],
                                                  relative_path=artifact['relative_path'], sha256=digest(raw)))
                    for owner in owner_keys:
                        evidence.append(dict(entity_key=owner, field=field, raw_value=value,
                                             artifact_key=artifact['artifact_key'], locator=artifact['relative_path'] + '#',
                                             resolution='receipt_bound_sidecar_fresh_sha256'))
                    continue
                except (OSError, ValueError) as exc:
                    if not any(x['path_base'] == artifact['path_base'] and
                               x['relative_path'] == artifact['relative_path'] for x in consumed_metadata):
                        consumed_metadata.append(dict(path_base=artifact['path_base'],
                            relative_path=artifact['relative_path'],
                            state='missing' if not path.exists() else 'unreadable', raw_sha256=None))
                    reason = str(exc)
            for owner in owner_keys:
                gaps.append(dict(entity_key=owner, field=field, code='SEISMIC_METADATA_UNBOUND', reason=reason,
                                 searched_scope=locator + '/artifacts and FILE_INDEX accepted JSON rows'))
    # Older head tables lack bound UUIDs and retain offset-scoped members.
    # New typed identities reuse the exact well entity with explicit row selectors.
    if any(a["role"] == "data" and entities[a["entity_key"]]["category"] == "well" for a in artifacts):
        heads_path = package / "02_wells/well_headers/native_well_heads.csv"
        if bound_heads is not None or (not bound_heads_known and heads_path.is_file()):
            _unlinked(heads_path)
            if bound_heads is not None:
                head_raw = bound_heads
            else:
                with heads_path.open('rb') as source_handle:
                    head_raw = read_up_to(source_handle, MAX_HEAD_BYTES + 1)
            if len(head_raw) > MAX_HEAD_BYTES:
                raise ValueError('Native head table exceeds bounded 32 MiB metadata read')
            head_hash = digest(head_raw)
            bound_head_artifacts = set()
            with io.StringIO(head_raw.decode('utf-8-sig'), newline='') as handle:
                for row_number, row in enumerate(csv.DictReader(handle), start=2):
                    offset = row.get("model_subject_offset")
                    if not offset:
                        continue
                    native_well = row.get('native_well_object_id')
                    subject = well_subject_map.get(native_well, {})
                    try:
                        xy = [float(row['x']), float(row['y'])]
                        typed_identity = (row.get('native_well_identity_status') == 'exact_typed_Model_WellTraceSubject_provider_name_XY'
                            and bool(subject) and all(math.isfinite(v) for v in xy) and xy == subject.get('head_xy')
                            and row.get('well_name') == subject.get('name')
                            and row.get('trajectory_provider_object_id') == subject.get('definite_provider_id')
                            and row.get('trajectory_provider_link_status') == 'native_Model_ptd_definite_survey_provider_exact')
                    except (KeyError, TypeError, ValueError, OverflowError):
                        typed_identity = False
                    member = entity(native_well if typed_identity else "model_subject_offset:" + offset,
                                    {"name": row.get("well_name"), "category": "well", "status": "decoded"},
                                    f"02_wells/well_headers/native_well_heads.csv#row={row_number}", "native" if typed_identity else "derived_member")
                    if not typed_identity:
                        member["native_uuid"] = None
                    member["membership_selector"] = {"column": "model_subject_offset", "value": offset,
                                                       "row_number": row_number}
                    evidence.append(dict(entity_key=member['entity_key'],field='native_head_record',raw_value=row,
                        locator=f'02_wells/well_headers/native_well_heads.csv#row={row_number}',
                        resolution='recorded_native_head_XY_and_separate_trajectory_start_Z_provenance'))
                    provider = row.get("trajectory_provider_object_id")
                    status = row.get("trajectory_provider_link_status") or "unknown"
                    if provider:
                        provider_node = entity(provider, {"category": "trajectory", "status": "referenced"},
                                               f"02_wells/well_headers/native_well_heads.csv#row={row_number}",
                                               'native_trajectory' if provider in legacy_tracks else 'native')
                        relationships.append(dict(source_key=member["entity_key"], target_key=provider_node["entity_key"],
                                                  relation="declared_definite_provider" if typed_identity else "trajectory_provider",
                                                  status="exact_id_reference" if status == 'native_Model_ptd_definite_survey_provider_exact' else "candidate",
                                                  locator=f"02_wells/well_headers/native_well_heads.csv#row={row_number}/trajectory_provider_object_id"))
                    for artifact in artifacts:
                        if entities[artifact["entity_key"]]["source_id"] == "well_headers":
                            if (typed_identity and artifact['recorded_sha256'] == head_hash
                                    and artifact['expected_bytes'] == len(head_raw)):
                                artifact['owner_keys'].append(member['entity_key'])
                                artifact.setdefault('owner_selectors', []).append(dict(owner_key=member['entity_key'],selector=member['membership_selector']))
                                member['artifact_keys'].append(artifact['artifact_key'])
                                member['source_status'] = 'decoded'
                                if artifact['artifact_key'] not in bound_head_artifacts:
                                    metadata_bindings.append(dict(artifact_key=artifact['artifact_key'],
                                        path_base=artifact['path_base'],relative_path=artifact['relative_path'],
                                        sha256=head_hash,kind='native_head_rows'))
                                    bound_head_artifacts.add(artifact['artifact_key'])
                            relationships.append(dict(source_key=member["entity_key"], target_key=artifact["entity_key"],
                                                      relation="aggregate_table_member", status="exact_row_selector",
                                                      selector=member["membership_selector"],
                                                      locator=f"02_wells/well_headers/native_well_heads.csv#row={row_number}"))
    for node in entities.values():
        if not node["artifact_keys"]:
            gaps.append(dict(entity_key=node["entity_key"], field="payload", code="NO_ACCEPTED_ARTIFACT",
                             reason="Inventory or recovery only; no accepted index row", searched_scope="FILE_INDEX.json"))
    relationships = list({canonical(row): row for row in relationships}.values())
    evidence = annotate_rows(evidence)
    tables = {"entities": sorted(entities.values(), key=lambda x: x["entity_key"]),
              "artifacts": artifacts,
              "relationships": sorted(relationships, key=lambda x: (x["source_key"], x["relation"], x["target_key"])),
              "metadata_evidence": sorted(evidence, key=lambda x: (x["entity_key"], x["field"], x["locator"])),
              "gaps": sorted(gaps, key=lambda x: (x["entity_key"], x["field"], x["code"]))}
    return dict(tables=tables, package=package, destination=destination, run=run,
                project_key=project_key, revision_scope=revision_scope,
                source_hashes=source_hashes, index_hash=index_hash,
                context_hash=context_hash, native_rel=native_rel, native_hash=native_hash,
                spatial_rel=spatial_rel, spatial_hash=spatial_hash,
                metadata_bindings=metadata_bindings, consumed_metadata=consumed_metadata)


def _write_table(path: Path, rows) -> dict:
    """Emit one canonical row at a time; never buffer a whole serialized table.

    The exclusive generation directory belongs to this attempt. A failed file
    remains uncommitted diagnostic evidence; callers never promote its pointer.
    """
    hasher = hashlib.sha256()
    total = count = 0
    with ContractProgress('catalogue_table_write', total=len(rows) if hasattr(rows, '__len__') else None,
                          table=path.stem) as progress, path.open('xb') as handle:
        for row in rows:
            if count >= MAX_TABLE_ROWS:
                raise ValueError('Contract table row count bound exceeded')
            payload = canonical(row)
            if len(payload) > MAX_ROW_BYTES:
                raise ValueError('Contract row byte bound exceeded')
            if total + len(payload) > MAX_TABLE_BYTES:
                raise ValueError('Contract table byte bound exceeded')
            if handle.write(payload) != len(payload):
                raise OSError('Incomplete contract table write')
            hasher.update(payload)
            total += len(payload)
            count += 1
            progress.advance()
        handle.flush()
        os.fsync(handle.fileno())
    return dict(path=path.name, sha256=hasher.hexdigest(), rows=count)


def _metadata_requires_shards(rows) -> bool:
    if len(rows) > MAX_TABLE_ROWS:
        return True
    total = 0
    for row in rows:
        size = len(canonical(row))
        if size > MAX_ROW_BYTES:
            raise ValueError('Contract row byte bound exceeded')
        total += size
        if total > MAX_TABLE_BYTES:
            return True
    return False


def _write_sharded_metadata(root: Path, rows) -> dict:
    """Split only the oversized evidence table; every shard keeps old bounds."""
    if len(rows) > MAX_SHARDED_TABLE_ROWS:
        raise ValueError('Sharded metadata aggregate row bound exceeded')
    shards = []
    aggregate = hashlib.sha256()
    aggregate_bytes = aggregate_rows = 0
    handle = None
    hasher = None
    part_bytes = part_rows = 0

    def close_part():
        nonlocal handle, hasher, part_bytes, part_rows
        if handle is None:
            return
        handle.flush(); os.fsync(handle.fileno()); handle.close()
        shards.append(dict(index=len(shards) + 1, path=path.name,
                           rows=part_rows, bytes=part_bytes, sha256=hasher.hexdigest()))
        handle = None

    try:
        with ContractProgress('catalogue_table_write', total=len(rows), table='metadata_evidence') as progress:
            for row in rows:
                payload = canonical(row)
                size = len(payload)
                if size > MAX_ROW_BYTES:
                    raise ValueError('Contract row byte bound exceeded')
                if aggregate_rows >= MAX_SHARDED_TABLE_ROWS or aggregate_bytes + size > MAX_SHARDED_TABLE_BYTES:
                    raise ValueError('Sharded metadata aggregate byte/row bound exceeded')
                if handle is None or part_rows >= MAX_TABLE_ROWS or part_bytes + size > MAX_TABLE_BYTES:
                    close_part()
                    if len(shards) >= MAX_TABLE_SHARDS:
                        raise ValueError('Sharded metadata shard count bound exceeded')
                    path = root / f'metadata_evidence.{len(shards) + 1:04d}.jsonl'
                    handle = path.open('xb')
                    hasher = hashlib.sha256()
                    part_bytes = part_rows = 0
                if handle.write(payload) != size:
                    raise OSError('Incomplete sharded contract table write')
                hasher.update(payload); aggregate.update(payload)
                part_bytes += size; part_rows += 1
                aggregate_bytes += size; aggregate_rows += 1
                progress.advance()
            close_part()
    finally:
        if handle is not None:
            handle.close()
    if len(shards) < 2:
        raise ValueError('Sharded metadata requires at least two nonempty shards')
    return dict(encoding=SHARDED_ENCODING, shards=shards, rows=aggregate_rows,
                bytes=aggregate_bytes, sha256=aggregate.hexdigest())


def publish_contract(package: Path, destination: Path, file_index: dict,
                     inventory: dict | None = None) -> dict:
    """Commit an immutable catalogue generation after all tables and manifest exist."""
    normalized = build_catalogue(package, destination, file_index, inventory)
    tables = normalized['tables']
    package, destination, run = (normalized[k] for k in ('package', 'destination', 'run'))
    project_key, revision_scope, source_hashes = (normalized[k] for k in
        ('project_key', 'revision_scope', 'source_hashes'))
    index_hash, context_hash, native_rel, native_hash, spatial_rel, spatial_hash = (
        normalized[k] for k in ('index_hash', 'context_hash', 'native_rel',
                                 'native_hash', 'spatial_rel', 'spatial_hash'))
    metadata_bindings = normalized['metadata_bindings']
    generation = uuid.uuid4().hex
    root = destination / "INTEROPERABILITY" / "generations" / generation
    _unlinked(root)
    root.mkdir(parents=True, exist_ok=False)
    table_hashes = {}
    for name in TABLES:
        path = root / (name + ".jsonl")
        table_hashes[name] = (_write_sharded_metadata(root, tables[name])
                              if name == 'metadata_evidence' and _metadata_requires_shards(tables[name])
                              else _write_table(path, tables[name]))
    script_dir = Path(__file__).resolve().parent
    engine_hashes = {}
    for name in ("geoviewer_io.py", "geoviewer_contract.py", "geoviewer_native_receipt.py", "geoviewer_surface_registration.py", "geoviewer_metadata_evidence.py", "validate_geoviewer_contract.py", "geoviewer_receiving_profiles.py",
                 "geoviewer_contract_retention.py", "geoviewer_contract_publication.py", "geoviewer_preparation_report.py", "standalone_petrel_extract.py",
                 "geoviewer_delivery.py", "geoviewer_incremental.py", "geoviewer_seismic_delivery.py", "petrel_native_recovery.py", "petrel_native_log_evidence.py", "petrel_native_well_evidence.py",
                 "petrel_file_convert.py", "petrel_native_binary.py", "petrel_native_geometry_attributes.py", "petrel_native_polygon_bindings.py", "geoviewer_metadata.py", "export_petrel_native_spatial_zero_gui.py"):
        source = script_dir / name
        if source.is_file():
            engine_hashes[name] = digest(source.read_bytes())
    schema = SHARDED_SCHEMA if 'encoding' in table_hashes['metadata_evidence'] else SCHEMA
    manifest = dict(schema=schema, generation=generation, project_revision=project_key,
                    project_identity_scope=revision_scope + "; persistent project identity unresolved",
                    source_fingerprint=source_hashes, engine_script_sha256=engine_hashes,
                    metadata_bindings=metadata_bindings,
                    roots={"package": os.path.relpath(package, destination).replace("\\", "/"),
                           "run": os.path.relpath(run, destination).replace("\\", "/")},
                    file_index=dict(path="FILE_INDEX.json", canonical_sha256=index_hash,
                                    accepted_count=len(file_index["files"]), partial_count=len(file_index.get("partial_files", []))),
                    receipts={"project_context": {"path": "01_project_metadata/project_context.json", "sha256": context_hash},
                              "native_recovery": {"path": native_rel, "sha256": native_hash},
                              "native_spatial": {"path": spatial_rel, "sha256": spatial_hash}}, tables=table_hashes,
                    integrity_policy="table_sha256; index_canonical_sha256; payload size/state by validator; no automatic bulk rehash")
    manifest_bytes = canonical(manifest)
    if len(manifest_bytes) > MAX_TABLE_BYTES:
        raise ValueError('Contract manifest byte bound exceeded')
    (root / "manifest.json").write_bytes(manifest_bytes)
    pointer = dict(schema=schema, generation=generation,
                   manifest=f"INTEROPERABILITY/generations/{generation}/manifest.json",
                   manifest_sha256=digest(manifest_bytes))
    pending = destination / (POINTER + "." + generation + ".partial")
    pending.write_bytes(canonical(pointer))
    os.replace(pending, destination / POINTER)
    return pointer
