#!/usr/bin/env python3
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Extract safe zero-GUI semantic metadata from copied Petrel native stores.

This script reads the exported native store package created by
export_petrel_native_project_zero_gui.ps1. It does not launch Petrel, does not
use Ocean, and does not modify .pet/.ptd project files. Outputs are metadata
CSV/JSON files with explicit boundaries: native geometry arrays and proprietary
payloads are not decoded here.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


PROJECT_NAME_DEFAULT = "Petrel2010 demo project"
PETREL_VERSION_DEFAULT = "2018.2.0.5333"
DOTNET_EPOCH_TICKS = 621355968000000000


def win_rel(base: Path, path: Path) -> str:
    return os.path.relpath(path, base).replace("/", "\\")


def slug(value: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return out or "native_semantic"


def short_hash(value: str, length: int = 10) -> str:
    return hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:length]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def child_text(element: ET.Element, name: str) -> str:
    child = element.find(name)
    if child is None:
        return ""
    return clean(child.text)


def dotnet_ticks_to_iso(value: str) -> str:
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        seconds = (ticks - DOTNET_EPOCH_TICKS) / 10_000_000
        return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def parse_droid_id(droid: str) -> str:
    match = re.search(r"(?:&|[?])Id=([^&\s]+)", droid)
    if match:
        return match.group(1)
    match = re.search(r"/([0-9a-fA-F-]{16,})$", droid)
    if match:
        return match.group(1)
    return ""


def parse_droid_type(droid: str) -> str:
    match = re.search(r"(?:&|[?])Type=([^&\s]+)", droid)
    return match.group(1) if match else ""


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_parent(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field, "")) for field in fieldnames})


def write_csv_if_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> bool:
    """Write a CSV only when it carries at least one data row."""
    if not rows:
        if path.exists():
            path.unlink()
        return False
    write_csv(path, rows, fieldnames)
    return True


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def load_native_inventory(export_package: Path) -> dict[str, dict[str, str]]:
    inventory = export_package / "00_manifest" / "native_store_inventory.csv"
    if not inventory.exists():
        return {}
    _, rows = read_csv_rows(inventory)
    return {row.get("source_relative_path", ""): row for row in rows}


def manifest_blank_row(headers: list[str]) -> dict[str, str]:
    return {header: "" for header in headers}


def upsert_manifest(export_package: Path, rows: list[dict[str, str]]) -> dict[str, int]:
    manifest_path = export_package / "00_manifest" / "export_manifest.csv"
    headers, existing_rows = read_csv_rows(manifest_path)
    if not headers:
        raise RuntimeError(f"Manifest has no headers: {manifest_path}")

    by_file = {row["export_file"]: row for row in rows}
    used: set[str] = set()
    output: list[dict[str, str]] = []
    updated = 0
    for existing in existing_rows:
        export_file = existing.get("export_file", "")
        if export_file in by_file:
            output.append(by_file[export_file])
            used.add(export_file)
            updated += 1
        else:
            output.append(existing)

    appended = 0
    for row in rows:
        export_file = row["export_file"]
        if export_file not in used:
            output.append(row)
            used.add(export_file)
            appended += 1

    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in output:
            writer.writerow({header: row.get(header, "") for header in headers})

    return {"updated": updated, "appended": appended}


def new_manifest_row(
    headers: list[str],
    export_package: Path,
    path: Path,
    project_name: str,
    petrel_version: str,
    export_date_utc: str,
    source_type: str,
    export_format: str,
    notes: str,
) -> dict[str, str]:
    row = manifest_blank_row(headers)
    rel = win_rel(export_package, path)
    row["export_id"] = f"zero_gui_semantic_{slug(rel)}_{short_hash(rel)}"
    row["project_name"] = project_name
    row["petrel_version"] = petrel_version
    row["export_date_utc"] = export_date_utc
    row["source_object_path"] = f"Petrel native semantic extraction/{rel}"
    row["source_object_type"] = source_type
    row["export_format"] = export_format
    row["export_file"] = rel
    row["export_status"] = "exported_zero_gui_semantic"
    row["validation_status"] = "unchecked"
    row["sha256"] = sha256(path)
    row["notes"] = notes
    return row


def parse_modeling_data(modeling_xml: Path) -> dict[str, list[dict[str, str]]]:
    result = {
        "frameworks": [],
        "faults": [],
        "horizons": [],
        "zones": [],
        "object_counts": [],
    }
    if not modeling_xml.exists():
        return result

    root = ET.parse(modeling_xml).getroot()
    source_rel = "SMD\\ModelingData.xml"
    counts = Counter(child.tag for child in list(root))
    for tag, count in sorted(counts.items()):
        result["object_counts"].append(
            {
                "source_relative_path": source_rel,
                "object_class": tag,
                "count": str(count),
            }
        )

    def base_row(element: ET.Element, object_class: str) -> dict[str, str]:
        droid = child_text(element, "DroidString")
        last_ticks = child_text(element, "LastModificationTime")
        return {
            "object_class": object_class,
            "object_id": parse_droid_id(droid),
            "object_droid_type": parse_droid_type(droid),
            "name": child_text(element, "Name"),
            "volcan_name": child_text(element, "VolcanName"),
            "domain_name": child_text(element, "DomainName"),
            "droid_string": droid,
            "last_modification_time_raw": last_ticks,
            "last_modification_time_utc": dotnet_ticks_to_iso(last_ticks),
            "last_modification_user": child_text(element, "LastModificationUser"),
            "source_relative_path": source_rel,
            "decode_status": "xml_metadata_only_no_geometry_arrays_decoded",
        }

    for element in root.findall("StructuralFrameworkImpl"):
        row = base_row(element, "StructuralFrameworkImpl")
        row.update(
            {
                "fault_model_collection_droid": child_text(element, "FaultModelCollectionDroid"),
                "horizon_model_collection_droid": child_text(element, "HorizonModelCollectionDroid"),
                "structural_framework_file": child_text(element, "StructuralFrameworkFile"),
                "fault_horizon_parameter_file": child_text(element, "FaultHorizonParameterFile"),
            }
        )
        result["frameworks"].append(row)

    for element in root.findall("FaultModelImpl"):
        row = base_row(element, "FaultModelImpl")
        row.update(
            {
                "initial_fault_droid": child_text(element, "InitialFaultDroid"),
                "prototype_droid": child_text(element, "PrototypeDroid"),
                "fault_associations_file": child_text(element, "FaultAssociationsFile"),
            }
        )
        result["faults"].append(row)

    for element in root.findall("HorizonModelImpl"):
        row = base_row(element, "HorizonModelImpl")
        row.update(
            {
                "prototype_droid": child_text(element, "PrototypeDroid"),
                "horizon_file": child_text(element, "HorizonFile"),
                "top_zone_droid": child_text(element, "TopZoneDroid"),
                "base_zone_droid": child_text(element, "BaseZoneDroid"),
            }
        )
        result["horizons"].append(row)

    for element in root.findall("ZoneModelImpl"):
        row = base_row(element, "ZoneModelImpl")
        row.update(
            {
                "top_horizon_droid": child_text(element, "TopHorizonDroid"),
                "base_horizon_droid": child_text(element, "BaseHorizonDroid"),
                "prototype_droid": child_text(element, "PrototypeDroid"),
            }
        )
        result["zones"].append(row)

    return result


def parse_prop_file(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in text.splitlines()]
    start = 1 if lines and re.fullmatch(r"\d+", lines[0]) else 0
    props: dict[str, str] = {}
    i = start
    while i + 1 < len(lines):
        key = lines[i].strip()
        value = lines[i + 1].strip()
        if key:
            props[key] = value
        i += 2
    return props


def infer_prop_kind(keys: list[str]) -> str:
    joined = "\n".join(keys)
    if "Surface.Fault." in joined:
        return "fault_surface_property_metadata"
    if "Horizon" in joined:
        return "horizon_property_metadata"
    if "GridLattice" in joined or "Pillar" in joined:
        return "grid_property_metadata"
    return "native_property_metadata"


def parse_gms_properties(gms_dir: Path, export_package: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not gms_dir.exists():
        return rows

    selected_keys = [
        "Surface.Fault.Type",
        "Surface.Fault.GridInterval",
        "Surface.Fault.Smoothing",
        "Surface.Fault.Size",
        "Surface.Fault.NumListOfFaultRelationships",
        "Surface.Fault.NumDisplacementData",
        "Surface.Fault.FaultBestFitPlaneGrid.IsDefined",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.X.Range.Min",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.X.Range.Max",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.X.Range.Num",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Y.Range.Min",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Y.Range.Max",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Y.Range.Num",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Origin.Point3D.X",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Origin.Point3D.Y",
        "Surface.Fault.FaultBestFitPlaneGrid.SurfaceSetGrid.GridLattice.Origin.Point3D.Z",
    ]

    for prop in sorted(gms_dir.glob("*.prop.ptd")):
        props = parse_prop_file(prop)
        keys = sorted(props)
        bulk = prop.with_name(prop.name.replace(".prop.ptd", ".bulk.ptd"))
        prefix_counts = Counter(key.split(".")[0] for key in keys)
        truncating_faults = sorted(
            {
                value
                for key, value in props.items()
                if key.endswith("TruncatingFault.UniqueName") and value
            }
        )
        row = {
            "property_file_id": prop.name.replace(".prop.ptd", ""),
            "property_file": win_rel(export_package, prop),
            "bulk_file": win_rel(export_package, bulk) if bulk.exists() else "",
            "bulk_size_bytes": str(bulk.stat().st_size) if bulk.exists() else "",
            "property_count": str(len(props)),
            "property_kind": infer_prop_kind(keys),
            "top_key_prefixes": ";".join(f"{key}:{count}" for key, count in prefix_counts.most_common(8)),
            "referenced_truncating_fault_unique_names": ";".join(truncating_faults[:20]),
            "decode_status": "key_value_metadata_only_no_bulk_geometry_decoded",
        }
        for key in selected_keys:
            row[slug(key)] = props.get(key, "")
        rows.append(row)
    return rows


def sqlite_schema_rows(qr_dir: Path, export_package: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    schema_rows: list[dict[str, str]] = []
    value_rows: list[dict[str, str]] = []
    if not qr_dir.exists():
        return schema_rows, value_rows

    for db in sorted(qr_dir.glob("*.db")):
        rel = win_rel(export_package, db)
        con = sqlite3.connect(str(db))
        try:
            tables = con.execute(
                "select name, type from sqlite_master where type in ('table','view') order by type,name"
            ).fetchall()
            for table_name, table_type in tables:
                cols = con.execute(f'pragma table_info("{table_name}")').fetchall()
                col_desc = ";".join(f"{col[1]}:{col[2]}" for col in cols)
                try:
                    row_count = con.execute(f'select count(*) from "{table_name}"').fetchone()[0]
                except sqlite3.DatabaseError:
                    row_count = -1
                schema_rows.append(
                    {
                        "database_file": rel,
                        "sqlite_object_type": table_type,
                        "table_name": table_name,
                        "row_count": str(row_count),
                        "column_count": str(len(cols)),
                        "columns": col_desc,
                    }
                )

                if row_count > 0 and row_count <= 250 and len(cols) <= 4:
                    column_names = [col[1] for col in cols]
                    for values in con.execute(f'select * from "{table_name}" limit 250').fetchall():
                        value_rows.append(
                            {
                                "database_file": rel,
                                "table_name": table_name,
                                "columns": ";".join(column_names),
                                "values": ";".join(clean(value) for value in values),
                            }
                        )
        finally:
            con.close()
    return schema_rows, value_rows


def extract_xml_metadata(ptd_root: Path, export_package: Path, max_names: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    files = sorted(list(ptd_root.rglob("*.xml")) + list(ptd_root.rglob("*.bxml")))
    name_pattern = re.compile(r"<(?:Name|DisplayName|ObjectName|TemplateName|WindowName)>([^<]{1,220})</", re.I)
    droid_pattern = re.compile(r"://[^<\s]+(?:Type=|/)[^<\s]+", re.I)
    terms = ["Well", "WellLog", "Surface", "Horizon", "Fault", "Seismic", "Grid", "Workflow", "Report", "Window"]

    for path in files:
        rel = win_rel(export_package, path)
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        parse_status = "not_xml_parsed"
        root_tag = ""
        direct_child_count = ""
        first_lt = text.find("<")
        if first_lt >= 0:
            xml_candidate = text[first_lt:]
            try:
                root = ET.fromstring(xml_candidate)
                parse_status = "xml_parsed"
                root_tag = root.tag.split("}")[-1]
                direct_child_count = str(len(list(root)))
            except ET.ParseError:
                parse_status = "xml_text_scanned_parse_failed"

        names = [clean(match.group(1)) for match in name_pattern.finditer(text)]
        droids = [clean(match.group(0)) for match in droid_pattern.finditer(text)]
        term_flags = [term for term in terms if re.search(term, text, re.I)]
        rows.append(
            {
                "source_relative_path": win_rel(ptd_root, path),
                "package_relative_path": rel,
                "size_bytes": str(path.stat().st_size),
                "parse_status": parse_status,
                "root_tag": root_tag,
                "direct_child_count": direct_child_count,
                "name_count": str(len(names)),
                "first_names": ";".join(names[:max_names]),
                "droid_reference_count": str(len(droids)),
                "first_droid_references": ";".join(droids[:max_names]),
                "term_flags": ";".join(term_flags),
            }
        )
    return rows


def extract_borehole_references(ptd_root: Path) -> list[dict[str, str]]:
    """Extract named borehole references exposed by copied Toucan/I2DW XML.

    The I2DW depth bounds are display-window limits, not surveyed well depths.
    Keeping that semantic boundary in every row prevents the metadata from being
    mistaken for a well-header or trajectory export.
    """
    rows: list[dict[str, str]] = []

    def child_text(node: ET.Element, child_name: str) -> str:
        for child in list(node):
            if child.tag.split("}")[-1] == child_name:
                return clean(child.text)
        return ""

    toucan_root = ptd_root / "Toucan"
    for path in sorted(toucan_root.glob("PetrelBoreholeWrapper_*.xml")) if toucan_root.exists() else []:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        rows.append(
            {
                "reference_class": "toucan_borehole_wrapper",
                "name": child_text(root, "Name"),
                "petrel_object_reference": child_text(root, "PetrelDO"),
                "toucan_droid": child_text(root, "Droid"),
                "fence_element_droid": "",
                "visible": "",
                "visible_depth_min": "",
                "visible_depth_max": "",
                "depth_range_semantics": "",
                "modify_date_raw": child_text(root, "ModifyDate"),
                "source_relative_path": win_rel(ptd_root, path),
                "decode_status": "named_native_borehole_reference_no_header_or_trajectory_decoded",
            }
        )

    windows_path = ptd_root / "Ocean" / "I2DW-Windows.xml"
    if windows_path.exists():
        try:
            root = ET.parse(windows_path).getroot()
        except (ET.ParseError, OSError):
            root = None
        if root is not None:
            for node in root.iter():
                if node.tag.split("}")[-1] != "BoreholeFenceElement":
                    continue
                rows.append(
                    {
                        "reference_class": "i2dw_borehole_fence_element",
                        "name": child_text(node, "Name"),
                        "petrel_object_reference": child_text(node, "Borehole"),
                        "toucan_droid": "",
                        "fence_element_droid": child_text(node, "Droid"),
                        "visible": child_text(node, "Visible"),
                        "visible_depth_min": child_text(node, "VisibleDepthMin"),
                        "visible_depth_max": child_text(node, "VisibleDepthMax"),
                        "depth_range_semantics": "saved_well_section_display_bounds_not_surveyed_depths",
                        "modify_date_raw": "",
                        "source_relative_path": win_rel(ptd_root, windows_path),
                        "decode_status": "named_display_reference_no_header_trajectory_or_log_samples_decoded",
                    }
                )

    return rows


def extract_native_object_registry(
    ptd_root: Path, export_package: Path
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Index text-like Petrel object registry references in copied PTD stores.

    These records expose an object identifier, runtime type, and saved timestamp,
    but not the proprietary object payload.  The extraction is deliberately an
    inventory surface: it must not be presented as decoded log samples, surveys,
    grids, or geometry.
    """
    pattern = re.compile(
        rb"%?3://Petrel/"
        rb"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        rb"([A-Za-z][A-Za-z0-9_.+`]{0,100}?)"
        rb"(20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})"
    )
    grouped: Counter[tuple[str, str, str, str, str]] = Counter()
    first_offsets: dict[tuple[str, str, str, str, str], int] = {}

    for path in sorted(ptd_root.rglob("*.ptd")):
        source_rel = win_rel(ptd_root, path)
        package_rel = win_rel(export_package, path)
        payload = path.read_bytes()
        for match in pattern.finditer(payload):
            object_id = match.group(1).decode("ascii").lower()
            object_type = match.group(2).decode("ascii")
            timestamp_raw = match.group(3).decode("ascii")
            key = (source_rel, package_rel, object_id, object_type, timestamp_raw)
            grouped[key] += 1
            first_offsets.setdefault(key, match.start())

    rows: list[dict[str, str]] = []
    for key in sorted(grouped, key=lambda value: (value[3], value[2], value[4], value[0])):
        source_rel, package_rel, object_id, object_type, timestamp_raw = key
        rows.append(
            {
                "source_relative_path": source_rel,
                "package_relative_path": package_rel,
                "object_id": object_id,
                "object_type": object_type,
                "saved_timestamp_raw": timestamp_raw,
                "first_byte_offset": str(first_offsets[key]),
                "occurrence_count": str(grouped[key]),
                "decode_status": "native_registry_reference_only_payload_not_decoded",
            }
        )

    type_counts: list[dict[str, str]] = []
    for object_type in sorted({row["object_type"] for row in rows}):
        selected = [row for row in rows if row["object_type"] == object_type]
        type_counts.append(
            {
                "object_type": object_type,
                "unique_object_ids": str(len({row["object_id"] for row in selected})),
                "version_records": str(len(selected)),
                "occurrences": str(sum(int(row["occurrence_count"]) for row in selected)),
                "decode_status": "native_registry_reference_only_payload_not_decoded",
            }
        )
    return rows, type_counts


def native_spatial_capability_rows(type_counts: list[dict[str, str]]) -> list[dict[str, str]]:
    """Describe spatial products evidenced by registry types without decoding payloads."""
    capabilities = [
        (re.compile(r"^Polygons3$", re.I), "polygons", "polygon geometry and attributes"),
        (re.compile(r"^Points3$", re.I), "points", "point geometry and attributes"),
        (re.compile(r"TrajectoryProvider", re.I), "well_locations_and_trajectories", "well-head location and deviation survey"),
        (re.compile(r"WellTop|WellMarker|MarkerCollection", re.I), "well_tops", "well-top or marker table"),
        (re.compile(r"FaultInterpretation", re.I), "fault_interpretation", "fault interpretation geometry"),
        (re.compile(r"HorizonInterpretation", re.I), "horizon_interpretation", "horizon interpretation geometry"),
    ]
    rows: list[dict[str, str]] = []
    for count_row in type_counts:
        object_type = count_row["object_type"]
        for pattern, product, requested_output in capabilities:
            if not pattern.search(object_type):
                continue
            rows.append(
                {
                    "object_type": object_type,
                    "unique_object_ids": count_row["unique_object_ids"],
                    "version_records": count_row["version_records"],
                    "occurrences": count_row["occurrences"],
                    "spatial_product": product,
                    "requested_output": requested_output,
                    "extraction_status": "detected_in_native_registry_payload_not_decoded",
                    "current_output": "inventory_evidence_only",
                    "next_safe_tier": "standard companion export or validated Petrel-authored export",
                    "validation_required": "CRS, units, object identity, geometry count, and Petrel comparison",
                }
            )
            break
    return rows


def _printable_context(payload: bytes, start: int, end: int) -> str:
    fragment = payload[max(0, start) : min(len(payload), end)]
    return clean("".join(chr(byte) if 32 <= byte <= 126 else " " for byte in fragment))[:600]


def extract_native_crs_evidence(
    ptd_root: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Extract conservative CRS/version evidence from Model.ptd.

    Selection is intentionally limited to name/EPSG pairs with an unambiguous
    published mapping. Other strings remain candidates and never become an
    asserted project CRS automatically.
    """
    model_path = ptd_root / "Model.ptd"
    created = dt.datetime.now(dt.timezone.utc).isoformat()
    if not model_path.exists():
        return [], {
            "created_at_utc": created,
            "source_relative_path": "Model.ptd",
            "status": "model_store_not_found",
            "selected_crs": "unknown",
            "petrel_version_candidates": [],
            "boundary": "No CRS was inferred.",
        }

    payload = model_path.read_bytes()
    text = "".join(chr(byte) if 32 <= byte <= 126 else " " for byte in payload)
    source_rel = "Model.ptd"
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, int]] = set()
    name_pattern = re.compile(
        r"PowerPlan:[A-Za-z0-9_.-]+|WGS[_ ]1984[_ ]UTM[_ ]Zone[_ ]\d{1,2}[NS]|Ain[_ ]el[_ ]Abd[_ ]UTM[_ ]Zone[_ ]\d{1,2}[NS]?",
        re.I,
    )
    epsg_pattern = re.compile(r"EPSG\s*[,=: ]\s*(\d{4,6})", re.I)

    for kind, pattern in (("crs_name", name_pattern), ("authority_code", epsg_pattern)):
        for match in pattern.finditer(text):
            value = clean(match.group(0))
            if kind == "authority_code":
                value = f"EPSG:{match.group(1)}"
            key = (kind, value.lower(), match.start())
            if key in seen:
                continue
            seen.add(key)
            context = _printable_context(payload, match.start() - 260, match.end() + 260)
            role = "native_project_store_candidate"
            if re.search(r"LateBoundCoordinateReferenceSystem", context, re.I):
                role = "late_bound_crs_candidate"
            elif re.search(r"EarlyBoundCoordinateReferenceSystem", context, re.I):
                role = "early_bound_crs_candidate"
            elif re.search(r"changed.{0,80}(coordinate|CRS)|coordinate.{0,80}changed", context, re.I):
                role = "project_crs_change_history_candidate"
            rows.append(
                {
                    "evidence_kind": kind,
                    "value": value,
                    "normalized_value": re.sub(r"[_ ]+", "_", value).lower(),
                    "authority": "EPSG" if kind == "authority_code" else "",
                    "code": match.group(1) if kind == "authority_code" else "",
                    "byte_offset": str(match.start()),
                    "selection_role": role,
                    "source_relative_path": source_rel,
                    "context": context,
                    "status": "native_text_evidence_not_petrel_export_validated",
                }
            )

    version_candidates = sorted(
        set(re.findall(r"(?<!\d)(20\d{2}\.[0-9]+(?:\.[0-9]+){0,2})(?!\d)", text))
    )
    all_codes = {row["code"] for row in rows if row["code"]}
    name_rows = [row for row in rows if row["evidence_kind"] == "crs_name"]
    mappings = [
        (re.compile(r"WGS[_ ]1984[_ ]UTM[_ ]Zone[_ ]39N", re.I), "32639", "WGS 84 / UTM zone 39N (EPSG:32639)"),
        (re.compile(r"Ain[_ ]el[_ ]Abd[_ ]UTM[_ ]Zone[_ ]39N?", re.I), "20439", "Ain el Abd / UTM zone 39N (EPSG:20439)"),
    ]
    selectable: list[tuple[int, dict[str, str], str, str]] = []
    for row in name_rows:
        for pattern, code, display in mappings:
            if pattern.fullmatch(row["value"]) and code in all_codes:
                selectable.append((int(row["byte_offset"]), row, code, display))
                break

    selected_crs = "unknown"
    selected_code = ""
    selected_name = ""
    selected_status = "candidates_found_no_safe_automatic_selection" if rows else "no_crs_evidence_found"
    selected_evidence = ""
    if selectable:
        _offset, selected_row, selected_code, selected_crs = max(selectable, key=lambda item: item[0])
        selected_name = selected_row["value"]
        selected_status = "strong_native_name_and_epsg_candidate_not_petrel_export_validated"
        selected_row["selection_role"] = "selected_project_crs_candidate"
        selected_evidence = f"{selected_row['source_relative_path']} byte {selected_row['byte_offset']} plus EPSG:{selected_code} evidence"

    summary = {
        "created_at_utc": created,
        "source_relative_path": source_rel,
        "status": selected_status,
        "selected_crs": selected_crs,
        "selected_native_name": selected_name,
        "selected_authority": "EPSG" if selected_code else "",
        "selected_code": selected_code,
        "selected_evidence": selected_evidence,
        "candidate_count": len(rows),
        "petrel_version_candidates": version_candidates,
        "boundary": "The selected value is conservative native-store evidence, not a Petrel-authored export or CRS transformation validation. Confirm axis order, units, datum transformation, and project settings before operational use.",
    }
    return rows, summary


def extract_external_spatial_references(ptd_root: Path) -> list[dict[str, str]]:
    """Find referenced standard GIS filenames; do not claim the files are present."""
    pattern = re.compile(rb"([A-Za-z0-9][A-Za-z0-9_ .()\-]{0,160}\.(?:shp|shx|dbf|prj))", re.I)
    grouped: Counter[tuple[str, str]] = Counter()
    first_offsets: dict[tuple[str, str], int] = {}
    for path in sorted(ptd_root.rglob("*.ptd")):
        if path.stat().st_size > 256 * 1024 * 1024:
            continue
        payload = path.read_bytes()
        for match in pattern.finditer(payload):
            reference = clean(match.group(1).decode("latin-1", errors="ignore"))
            key = (win_rel(ptd_root, path), reference)
            grouped[key] += 1
            first_offsets.setdefault(key, match.start())
    return [
        {
            "source_relative_path": source_rel,
            "referenced_file_name": reference,
            "extension": Path(reference).suffix.lower(),
            "first_byte_offset": str(first_offsets[(source_rel, reference)]),
            "occurrence_count": str(grouped[(source_rel, reference)]),
            "reference_status": "historical_or_native_reference_source_file_not_proven_present",
        }
        for source_rel, reference in sorted(grouped)
    ]


def update_project_crs_metadata(export_package: Path, crs_summary: dict[str, Any], crs_path: Path) -> None:
    """Apply only a strong selected candidate to package summaries."""
    project_summary_path = export_package / "01_project_metadata" / "project_summary.json"
    if project_summary_path.exists():
        project_summary = json.loads(project_summary_path.read_text(encoding="utf-8-sig"))
        existing = clean(project_summary.get("coordinate_reference_system", ""))
        selected = clean(crs_summary.get("selected_crs", "unknown"))
        if selected != "unknown" and existing.lower() in {"", "unknown"}:
            project_summary["coordinate_reference_system"] = selected
        project_summary["coordinate_reference_system_status"] = crs_summary.get("status", "unknown")
        project_summary["coordinate_reference_system_evidence"] = win_rel(export_package, crs_path)
        project_summary["coordinate_reference_system_warning"] = crs_summary.get("boundary", "")
        project_summary["native_petrel_version_candidates"] = crs_summary.get("petrel_version_candidates", [])
        write_json(project_summary_path, project_summary)

    json_manifest_path = export_package / "00_manifest" / "export_manifest.json"
    if json_manifest_path.exists():
        manifest = json.loads(json_manifest_path.read_text(encoding="utf-8-sig"))
        project = manifest.setdefault("project", {})
        existing = clean(project.get("coordinate_reference_system", ""))
        selected = clean(crs_summary.get("selected_crs", "unknown"))
        if selected != "unknown" and existing.lower() in {"", "unknown"}:
            project["coordinate_reference_system"] = selected
        project["coordinate_reference_system_status"] = crs_summary.get("status", "unknown")
        project["coordinate_reference_system_evidence"] = win_rel(export_package, crs_path)
        write_json(json_manifest_path, manifest)


def zgy_rows(export_package: Path, native_inventory: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    ptd_root = export_package / "08_native_project" / "ptd_store"
    for path in sorted(ptd_root.glob("*.zgy")):
        source_rel = win_rel(ptd_root, path)
        inv = native_inventory.get(source_rel, {})
        rows.append(
            {
                "source_relative_path": source_rel,
                "package_relative_path": win_rel(export_package, path),
                "file_name": path.name,
                "size_bytes": str(path.stat().st_size),
                "sha256": inv.get("sha256", sha256(path)),
                "format_signature": inv.get("format_signature", "ZGY"),
                "decode_status": "native_zgy_inventory_only_cube_samples_not_decoded",
            }
        )
    return rows


def output_definitions(export_package: Path) -> dict[str, tuple[Path, str, str, list[str]]]:
    return {
        "native_object_registry": (
            export_package / "01_project_metadata" / "native_data_object_registry.csv",
            "native_object_registry_metadata",
            "CSV",
            [
                "source_relative_path",
                "package_relative_path",
                "object_id",
                "object_type",
                "saved_timestamp_raw",
                "first_byte_offset",
                "occurrence_count",
                "decode_status",
            ],
        ),
        "native_object_type_counts": (
            export_package / "01_project_metadata" / "native_data_object_type_counts.csv",
            "native_object_registry_metadata",
            "CSV",
            ["object_type", "unique_object_ids", "version_records", "occurrences", "decode_status"],
        ),
        "native_spatial_capability": (
            export_package / "01_project_metadata" / "native_spatial_capability.csv",
            "native_spatial_capability_inventory",
            "CSV",
            [
                "object_type",
                "unique_object_ids",
                "version_records",
                "occurrences",
                "spatial_product",
                "requested_output",
                "extraction_status",
                "current_output",
                "next_safe_tier",
                "validation_required",
            ],
        ),
        "native_crs_candidates": (
            export_package / "01_project_metadata" / "native_crs_candidates.csv",
            "native_crs_evidence",
            "CSV",
            [
                "evidence_kind",
                "value",
                "normalized_value",
                "authority",
                "code",
                "byte_offset",
                "selection_role",
                "source_relative_path",
                "context",
                "status",
            ],
        ),
        "native_crs_summary": (
            export_package / "01_project_metadata" / "native_crs_summary.json",
            "native_crs_evidence",
            "JSON",
            [],
        ),
        "external_spatial_references": (
            export_package / "01_project_metadata" / "native_external_spatial_references.csv",
            "native_external_spatial_reference_inventory",
            "CSV",
            [
                "source_relative_path",
                "referenced_file_name",
                "extension",
                "first_byte_offset",
                "occurrence_count",
                "reference_status",
            ],
        ),
        "project_object_counts": (
            export_package / "01_project_metadata" / "native_project_object_counts.csv",
            "project_metadata",
            "CSV",
            ["source_relative_path", "object_class", "count"],
        ),
        "project_metadata": (
            export_package / "01_project_metadata" / "native_project_metadata_zero_gui.json",
            "project_metadata",
            "JSON",
            [],
        ),
        "ocean_xml_metadata": (
            export_package / "01_project_metadata" / "native_ocean_xml_metadata.csv",
            "native_xml_metadata",
            "CSV",
            [
                "source_relative_path",
                "package_relative_path",
                "size_bytes",
                "parse_status",
                "root_tag",
                "direct_child_count",
                "name_count",
                "first_names",
                "droid_reference_count",
                "first_droid_references",
                "term_flags",
            ],
        ),
        "sqlite_schema": (
            export_package / "01_project_metadata" / "native_sqlite_schema.csv",
            "sqlite_metadata",
            "CSV",
            ["database_file", "sqlite_object_type", "table_name", "row_count", "column_count", "columns"],
        ),
        "sqlite_values": (
            export_package / "01_project_metadata" / "native_sqlite_reference_values.csv",
            "sqlite_metadata",
            "CSV",
            ["database_file", "table_name", "columns", "values"],
        ),
        "borehole_references": (
            export_package / "02_wells" / "well_headers" / "native_borehole_references.csv",
            "native_borehole_reference_metadata",
            "CSV",
            [
                "reference_class",
                "name",
                "petrel_object_reference",
                "toucan_droid",
                "fence_element_droid",
                "visible",
                "visible_depth_min",
                "visible_depth_max",
                "depth_range_semantics",
                "modify_date_raw",
                "source_relative_path",
                "decode_status",
            ],
        ),
        "faults": (
            export_package / "05_interpretation" / "faults" / "native_fault_models.csv",
            "fault_metadata",
            "CSV",
            [
                "object_class",
                "object_id",
                "object_droid_type",
                "name",
                "volcan_name",
                "domain_name",
                "droid_string",
                "initial_fault_droid",
                "prototype_droid",
                "fault_associations_file",
                "last_modification_time_raw",
                "last_modification_time_utc",
                "last_modification_user",
                "source_relative_path",
                "decode_status",
            ],
        ),
        "horizons": (
            export_package / "05_interpretation" / "horizons" / "native_horizon_models.csv",
            "horizon_metadata",
            "CSV",
            [
                "object_class",
                "object_id",
                "object_droid_type",
                "name",
                "volcan_name",
                "domain_name",
                "droid_string",
                "prototype_droid",
                "horizon_file",
                "top_zone_droid",
                "base_zone_droid",
                "last_modification_time_raw",
                "last_modification_time_utc",
                "last_modification_user",
                "source_relative_path",
                "decode_status",
            ],
        ),
        "zones": (
            export_package / "05_interpretation" / "horizons" / "native_zone_models.csv",
            "zone_metadata",
            "CSV",
            [
                "object_class",
                "object_id",
                "object_droid_type",
                "name",
                "volcan_name",
                "domain_name",
                "droid_string",
                "top_horizon_droid",
                "base_horizon_droid",
                "prototype_droid",
                "last_modification_time_raw",
                "last_modification_time_utc",
                "last_modification_user",
                "source_relative_path",
                "decode_status",
            ],
        ),
        "frameworks": (
            export_package / "06_models_properties" / "structural_models" / "native_structural_frameworks.csv",
            "structural_framework_metadata",
            "CSV",
            [
                "object_class",
                "object_id",
                "object_droid_type",
                "name",
                "volcan_name",
                "domain_name",
                "droid_string",
                "fault_model_collection_droid",
                "horizon_model_collection_droid",
                "structural_framework_file",
                "fault_horizon_parameter_file",
                "last_modification_time_raw",
                "last_modification_time_utc",
                "last_modification_user",
                "source_relative_path",
                "decode_status",
            ],
        ),
        "gms_properties": (
            export_package / "06_models_properties" / "structural_models" / "native_gms_property_files.csv",
            "native_gms_property_metadata",
            "CSV",
            [
                "property_file_id",
                "property_file",
                "bulk_file",
                "bulk_size_bytes",
                "property_count",
                "property_kind",
                "top_key_prefixes",
                "referenced_truncating_fault_unique_names",
                "surface_fault_type",
                "surface_fault_gridinterval",
                "surface_fault_smoothing",
                "surface_fault_size",
                "surface_fault_numlistoffaultrelationships",
                "surface_fault_numdisplacementdata",
                "surface_fault_faultbestfitplanegrid_isdefined",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_x_range_min",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_x_range_max",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_x_range_num",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_y_range_min",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_y_range_max",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_y_range_num",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_origin_point3d_x",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_origin_point3d_y",
                "surface_fault_faultbestfitplanegrid_surfacesetgrid_gridlattice_origin_point3d_z",
                "decode_status",
            ],
        ),
        "zgy": (
            export_package / "03_seismic" / "seismic_metadata" / "native_zgy_inventory.csv",
            "seismic_metadata",
            "CSV",
            ["source_relative_path", "package_relative_path", "file_name", "size_bytes", "sha256", "format_signature", "decode_status"],
        ),
    }


def run_validation(script_dir: Path, export_package: Path) -> tuple[str, str, str]:
    validator = script_dir / "validate_export_package.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(validator),
        "-ExportPackage",
        str(export_package),
        "-UpdateManifest",
        "-WriteChecksums",
    ]
    proc = subprocess.run(command, cwd=str(script_dir.parent), capture_output=True, text=True, timeout=600)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    status = "failed" if proc.returncode else "passed"
    report = ""
    for line in stdout.splitlines():
        if line.startswith("Validation status:"):
            status = line.split(":", 1)[1].strip()
        if line.startswith("Report:"):
            report = line.split(":", 1)[1].strip()
    return status, report, stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=PROJECT_NAME_DEFAULT)
    parser.add_argument("--project-file", default="")
    parser.add_argument("--petrel-version", default=PETREL_VERSION_DEFAULT)
    parser.add_argument("--export-package", required=True)
    parser.add_argument("--inventory-package", default="")
    parser.add_argument("--max-xml-names", type=int, default=20)
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    export_package = Path(args.export_package).resolve()
    ptd_root = export_package / "08_native_project" / "ptd_store"
    if not ptd_root.exists():
        raise SystemExit(f"Native store export not found: {ptd_root}")
    manifest_path = export_package / "00_manifest" / "export_manifest.csv"
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    export_date_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    native_inventory = load_native_inventory(export_package)
    outputs = output_definitions(export_package)

    modeling = parse_modeling_data(ptd_root / "SMD" / "ModelingData.xml")
    gms = parse_gms_properties(ptd_root / "SMD" / "GMS", export_package)
    sqlite_schema, sqlite_values = sqlite_schema_rows(ptd_root / "Ocean" / "QR", export_package)
    xml_metadata = extract_xml_metadata(ptd_root, export_package, args.max_xml_names)
    borehole_references = extract_borehole_references(ptd_root)
    native_object_registry, native_object_type_counts = extract_native_object_registry(ptd_root, export_package)
    native_spatial_capability = native_spatial_capability_rows(native_object_type_counts)
    native_crs_candidates, native_crs_summary = extract_native_crs_evidence(ptd_root)
    external_spatial_references = extract_external_spatial_references(ptd_root)
    zgy = zgy_rows(export_package, native_inventory)

    csv_payloads: dict[str, list[dict[str, Any]]] = {
        "native_object_registry": native_object_registry,
        "native_object_type_counts": native_object_type_counts,
        "native_spatial_capability": native_spatial_capability,
        "native_crs_candidates": native_crs_candidates,
        "external_spatial_references": external_spatial_references,
        "project_object_counts": modeling["object_counts"],
        "faults": modeling["faults"],
        "horizons": modeling["horizons"],
        "zones": modeling["zones"],
        "frameworks": modeling["frameworks"],
        "gms_properties": gms,
        "sqlite_schema": sqlite_schema,
        "sqlite_values": sqlite_values,
        "borehole_references": borehole_references,
        "ocean_xml_metadata": xml_metadata,
        "zgy": zgy,
    }
    written_output_keys: set[str] = set()
    for key, rows in csv_payloads.items():
        path, _source_type, _format, fields = outputs[key]
        if write_csv_if_rows(path, rows, fields):
            written_output_keys.add(key)

    crs_summary_path, _, _, _ = outputs["native_crs_summary"]
    write_json(crs_summary_path, native_crs_summary)
    written_output_keys.add("native_crs_summary")
    update_project_crs_metadata(export_package, native_crs_summary, crs_summary_path)

    project_metadata_path, _, _, _ = outputs["project_metadata"]
    project_metadata = {
        "created_at_utc": export_date_utc,
        "project_name": args.project_name,
        "project_file": args.project_file,
        "petrel_version": args.petrel_version,
        "export_package": str(export_package),
        "runtime_gui_used": False,
        "petrel_process_launched": False,
        "semantic_extract_status": "completed",
        "coordinate_reference_system_evidence": native_crs_summary,
        "decode_boundary": "Metadata decoded from XML, SQLite, text-like property stores, native file inventory, and text-like native object registry references. Proprietary arrays, seismic samples, grid bulk geometry, and full object payloads are not decoded.",
        "counts": {
            "native_registry_version_records": len(native_object_registry),
            "native_registry_unique_object_ids": len({row["object_id"] for row in native_object_registry}),
            "native_registry_object_types": len(native_object_type_counts),
            "native_spatial_capability_rows": len(native_spatial_capability),
            "native_crs_candidate_rows": len(native_crs_candidates),
            "external_spatial_reference_rows": len(external_spatial_references),
            "structural_frameworks": len(modeling["frameworks"]),
            "fault_models": len(modeling["faults"]),
            "horizon_models": len(modeling["horizons"]),
            "zone_models": len(modeling["zones"]),
            "gms_property_files": len(gms),
            "sqlite_schema_rows": len(sqlite_schema),
            "sqlite_reference_rows": len(sqlite_values),
            "xml_metadata_files": len(xml_metadata),
            "borehole_reference_rows": len(borehole_references),
            "unique_named_boreholes": len(
                {
                    row["petrel_object_reference"]
                    for row in borehole_references
                    if row["name"] and row["petrel_object_reference"]
                }
            ),
            "zgy_files": len(zgy),
        },
    }
    write_json(project_metadata_path, project_metadata)
    written_output_keys.add("project_metadata")

    report_root = export_package / "07_workflows_reports" / "native_semantic_export"
    report_json = report_root / f"zero_gui_native_semantic_export_{stamp}.json"
    report_md = report_root / f"zero_gui_native_semantic_export_{stamp}.md"

    headers, _ = read_csv_rows(manifest_path)
    manifest_rows: list[dict[str, str]] = []
    notes = {
        "native_object_registry": "Text-like native Petrel object registry references; object payloads and sample arrays are not decoded.",
        "native_object_type_counts": "Counts of unique IDs and saved versions by native registry object type; payloads are not decoded.",
        "native_spatial_capability": "Detected native spatial object types and requested products; geometry and attribute payloads are not decoded.",
        "native_crs_candidates": "CRS names and authority codes recovered as native-store evidence; not a Petrel-authored CRS export.",
        "native_crs_summary": "Conservative project CRS candidate selection with explicit validation boundary.",
        "external_spatial_references": "Historical or native references to external GIS files; referenced source files are not asserted present.",
        "project_metadata": "Zero-GUI project/native semantic metadata summary.",
        "project_object_counts": "Zero-GUI object-class counts parsed from SMD/ModelingData.xml.",
        "ocean_xml_metadata": "Zero-GUI scan of XML/BXML metadata names and references; binary BXML payloads are not decoded.",
        "sqlite_schema": "Zero-GUI SQLite schema and row-count metadata from copied Ocean QR stores.",
        "sqlite_values": "Zero-GUI SQLite small reference table values from copied Ocean QR stores.",
        "borehole_references": "Named borehole references from copied Toucan/I2DW XML; display limits are not surveyed depths.",
        "faults": "Fault metadata parsed from SMD/ModelingData.xml; geometry arrays are not decoded.",
        "horizons": "Horizon metadata parsed from SMD/ModelingData.xml; surfaces are not converted.",
        "zones": "Zone metadata parsed from SMD/ModelingData.xml.",
        "frameworks": "Structural framework metadata parsed from SMD/ModelingData.xml.",
        "gms_properties": "Structural GMS property-store key/value metadata; bulk geometry stores are not decoded.",
        "zgy": "Native ZGY seismic file inventory; seismic cube samples are not decoded.",
    }
    for key, (path, source_type, export_format, _fields) in outputs.items():
        if key not in written_output_keys or not path.exists():
            continue
        manifest_rows.append(
            new_manifest_row(
                headers,
                export_package,
                path,
                args.project_name,
                args.petrel_version,
                export_date_utc,
                source_type,
                export_format,
                notes.get(key, "Zero-GUI native semantic metadata export."),
            )
        )

    summary = {
        "run_id": f"zero_gui_native_semantic_export_{stamp}",
        "created_at_utc": export_date_utc,
        "project_name": args.project_name,
        "project_file": args.project_file,
        "petrel_version": args.petrel_version,
        "export_package": str(export_package),
        "runtime_gui_used": False,
        "petrel_process_launched": False,
        "export_mode": "zero_gui_native_semantic_metadata_extraction",
        "universal_conversion_status": "metadata_only_no_proprietary_array_decode",
        "outputs": {
            key: win_rel(export_package, path)
            for key, (path, _source_type, _export_format, _fields) in outputs.items()
            if key in written_output_keys and path.exists()
        },
        "counts": project_metadata["counts"],
        "manifest_path": str(manifest_path),
    }
    write_json(report_json, summary)
    report_md.write_text(
        "\n".join(
            [
                "# Zero-GUI Native Semantic Export",
                "",
                f"- Project: {args.project_name}",
                f"- Export package: {export_package}",
                "- Runtime GUI used: false",
                "- Petrel launched: false",
                f"- Native registry version records: {len(native_object_registry)}",
                f"- Native registry unique object IDs: {len({row['object_id'] for row in native_object_registry})}",
                f"- Native registry object types: {len(native_object_type_counts)}",
                f"- Native spatial capability rows: {len(native_spatial_capability)}",
                f"- Native CRS evidence rows: {len(native_crs_candidates)}",
                f"- Selected CRS candidate: {native_crs_summary.get('selected_crs', 'unknown')}",
                f"- External GIS file references: {len(external_spatial_references)}",
                f"- Fault models: {len(modeling['faults'])}",
                f"- Horizon models: {len(modeling['horizons'])}",
                f"- Zone models: {len(modeling['zones'])}",
                f"- Structural frameworks: {len(modeling['frameworks'])}",
                f"- GMS property files: {len(gms)}",
                f"- SQLite schema rows: {len(sqlite_schema)}",
                f"- XML/BXML metadata files scanned: {len(xml_metadata)}",
                f"- Native borehole reference rows: {len(borehole_references)}",
                f"- ZGY native files inventoried: {len(zgy)}",
                "",
                "## Boundary",
                "",
                "This pass decodes safe metadata and text-like object registry references from copied native stores only. It does not convert proprietary Petrel arrays, grid bulk geometry, or seismic cube samples to LAS/SEG-Y/ZMAP/RESQML.",
            ]
        ),
        encoding="utf-8",
    )

    for report_path in (report_json, report_md):
        manifest_rows.append(
            new_manifest_row(
                headers,
                export_package,
                report_path,
                args.project_name,
                args.petrel_version,
                export_date_utc,
                "native_semantic_report",
                "JSON" if report_path.suffix.lower() == ".json" else "MD",
                "Zero-GUI native semantic extraction run report.",
            )
        )

    upsert = upsert_manifest(export_package, manifest_rows)
    summary["manifest_updated"] = upsert["updated"]
    summary["manifest_appended"] = upsert["appended"]

    validation_status = "skipped"
    validation_report = ""
    validation_stderr = ""
    if not args.no_validate:
        validation_status, validation_report, validation_stderr = run_validation(script_dir, export_package)

    summary["validation_status"] = validation_status
    summary["validation_report"] = validation_report
    if validation_stderr:
        summary["validation_stderr"] = validation_stderr
    write_json(report_json, summary)

    print("Zero-GUI native semantic export: completed")
    print(f"Export package: {export_package}")
    print(f"Fault models: {len(modeling['faults'])}")
    print(f"Horizon models: {len(modeling['horizons'])}")
    print(f"Zone models: {len(modeling['zones'])}")
    print(f"Structural frameworks: {len(modeling['frameworks'])}")
    print(f"GMS property files: {len(gms)}")
    print(f"SQLite schema rows: {len(sqlite_schema)}")
    print(f"XML/BXML metadata files: {len(xml_metadata)}")
    print(f"Native borehole reference rows: {len(borehole_references)}")
    print(f"Native registry version records: {len(native_object_registry)}")
    print(f"Native registry unique object IDs: {len({row['object_id'] for row in native_object_registry})}")
    print(f"Native registry object types: {len(native_object_type_counts)}")
    print(f"Native spatial capability rows: {len(native_spatial_capability)}")
    print(f"Native CRS candidate rows: {len(native_crs_candidates)}")
    print(f"Selected CRS candidate: {native_crs_summary.get('selected_crs', 'unknown')}")
    print(f"External GIS file references: {len(external_spatial_references)}")
    print(f"ZGY native files: {len(zgy)}")
    print(f"Summary: {report_json}")
    print(f"Validation: {validation_status}")
    if validation_report:
        print(f"Validation report: {validation_report}")

    return 5 if validation_status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
