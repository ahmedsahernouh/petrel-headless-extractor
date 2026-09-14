"""Zero-GUI Petrel project audit report.

Reads an existing export package (manifest, report JSONs, derived CSVs) and
writes a self-contained HTML audit plus a JSON summary. Never launches Petrel
and needs no Petrel license: every input is a file the export chain already
produced. Every section is optional; missing inputs render as "not available"
so the report still works on packages where only part of the chain has run.

Usage:
    python scripts/report_petrel_project_audit.py --export-package <path>
        [--output-dir <path>] [--title <text>]

Prints a single "SummaryJson:{...}" line for the MCP chain-tool runner.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import math
import mimetypes
import os
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

TOOL_VERSION = "2.0-interactive-dashboard"
NOT_AVAILABLE = "not available in this export package"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def read_csv_rows(path: Path) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def latest_report(directory: Path, prefix: str) -> Path | None:
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(prefix + "*.json"))
    return matches[-1] if matches else None


def to_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def gather_project_summary(package: Path) -> dict:
    summary = load_json_file(package / "01_project_metadata" / "project_summary.json") or {}
    return {
        "project_name": summary.get("project_name", ""),
        "petrel_version": summary.get("petrel_version", ""),
        "project_path": summary.get("project_path", ""),
        "export_id": summary.get("export_id", package.name),
        "coordinate_reference_system": summary.get("coordinate_reference_system", ""),
        "coordinate_reference_system_status": summary.get("coordinate_reference_system_status", ""),
        "coordinate_reference_system_evidence": summary.get("coordinate_reference_system_evidence", ""),
        "coordinate_reference_system_warning": summary.get("coordinate_reference_system_warning", ""),
        "xy_units": summary.get("xy_units", ""),
        "depth_units": summary.get("depth_units", ""),
        "time_units": summary.get("time_units", ""),
        "velocity_units": summary.get("velocity_units", ""),
        "native_petrel_version_candidates": summary.get("native_petrel_version_candidates", []),
        "available": bool(summary),
    }


def gather_manifest(package: Path) -> dict:
    manifest_path = package / "00_manifest" / "export_manifest.csv"
    rows = read_csv_rows(manifest_path)
    if not rows:
        return {"available": False, "row_count": 0}
    by_type: dict[str, int] = {}
    by_format: dict[str, int] = {}
    by_validation: dict[str, int] = {}
    crs_values: dict[str, int] = {}
    dates: list[str] = []
    checksum_rows = 0
    for row in rows:
        by_type[row.get("source_object_type", "")] = by_type.get(row.get("source_object_type", ""), 0) + 1
        by_format[row.get("export_format", "")] = by_format.get(row.get("export_format", ""), 0) + 1
        by_validation[row.get("validation_status", "")] = by_validation.get(row.get("validation_status", ""), 0) + 1
        crs = (row.get("coordinate_reference_system") or "").strip()
        if crs and crs.lower() != "unknown":
            crs_values[crs] = crs_values.get(crs, 0) + 1
        if row.get("sha256"):
            checksum_rows += 1
        date = (row.get("export_date_utc") or "").strip()
        if date:
            dates.append(date)
    return {
        "available": True,
        "path": str(manifest_path),
        "row_count": len(rows),
        "by_source_object_type": dict(sorted(by_type.items(), key=lambda item: -item[1])),
        "by_export_format": dict(sorted(by_format.items(), key=lambda item: -item[1])),
        "by_validation_status": by_validation,
        "checksummed_rows": checksum_rows,
        "crs_values": crs_values,
        "export_date_range": [min(dates), max(dates)] if dates else [],
    }


def gather_wells(package: Path) -> dict:
    headers = read_csv_rows(package / "02_wells" / "well_headers" / "las_well_headers.csv")
    curves = read_csv_rows(package / "02_wells" / "well_headers" / "las_curve_inventory.csv")
    native_head_rows = read_csv_rows(package / "02_wells" / "well_headers" / "native_well_heads.csv")
    wells = []
    for row in headers:
        wells.append(
            {
                "well_name": row.get("well_name", ""),
                "curve_count": row.get("curve_count", ""),
                "data_row_count": row.get("data_row_count", ""),
                "start_depth": to_float(row.get("start_depth")),
                "stop_depth": to_float(row.get("stop_depth")),
                "depth_unit": row.get("depth_unit", ""),
                "null_value": row.get("null_value", ""),
            }
        )
    mnemonics = sorted({row.get("mnemonic", "") for row in curves if row.get("mnemonic")})
    native_heads = [
        {
            "well_name": row.get("well_name", ""),
            "x": to_float(row.get("x")),
            "y": to_float(row.get("y")),
            "z": to_float(row.get("z")),
            "native_name_uniqueness": row.get("native_name_uniqueness", ""),
            "xy_crosscheck_status": row.get("xy_trajectory_crosscheck_status", ""),
            "crs_status": row.get("crs_status", ""),
        }
        for row in native_head_rows
        if row.get("well_name") and row.get("x") and row.get("y")
    ]
    return {
        "available": bool(wells or native_heads),
        "well_count": len(wells) if wells else len(native_heads),
        "las_well_count": len(wells),
        "native_well_head_count": len(native_heads),
        "wells": wells,
        "native_well_heads": native_heads,
        "curve_rows": len(curves),
        "distinct_mnemonics": mnemonics,
    }


def gather_well_tops(package: Path) -> dict:
    well_top_root = package / "02_wells" / "well_tops"
    rows: list[dict[str, str]] = []
    if well_top_root.exists():
        for path in sorted(well_top_root.rglob("*.csv")):
            rows.extend(read_csv_rows(path))
    picks: list[dict[str, str]] = []
    seen_picks: set[tuple[str, ...]] = set()
    for row in rows:
        if (row.get("is_actual_pick_record") or "").lower() != "yes":
            continue
        key = tuple(
            row.get(field, "")
            for field in ("source_file", "source_row", "well_name", "surface", "measured_depth", "depth", "x", "y")
        )
        if key in seen_picks:
            continue
        seen_picks.add(key)
        picks.append(row)
    if not picks:
        return {"available": False, "pick_count": 0}
    per_surface: dict[str, int] = {}
    per_well: dict[str, int] = {}
    z_values: list[float] = []
    md_values: list[float] = []
    for row in picks:
        per_surface[row.get("surface", "")] = per_surface.get(row.get("surface", ""), 0) + 1
        per_well[row.get("well_name", "")] = per_well.get(row.get("well_name", ""), 0) + 1
        z = to_float(row.get("depth"))
        md = to_float(row.get("measured_depth"))
        if z is not None:
            z_values.append(z)
        if md is not None:
            md_values.append(md)
    return {
        "available": True,
        "pick_count": len(picks),
        "well_count": len(per_well),
        "surface_count": len(per_surface),
        "per_surface": dict(sorted(per_surface.items(), key=lambda item: -item[1])),
        "per_well": dict(sorted(per_well.items())),
        "z_range": [min(z_values), max(z_values)] if z_values else [],
        "md_range": [min(md_values), max(md_values)] if md_values else [],
        "petrel_export_confirmed": all((row.get("petrel_export_confirmed") or "").lower() == "yes" for row in picks),
    }


def gather_surfaces(package: Path) -> dict:
    report_path = latest_report(package / "07_workflows_reports" / "surfaces_export", "surfaces_zero_gui_export_")
    report = load_json_file(report_path) if report_path else None
    if not report:
        return {"available": False}
    surfaces = []
    for entry in report.get("surfaces", []):
        surfaces.append(
            {
                "guid": entry.get("guid", ""),
                "dims": entry.get("dims", []),
                "status": entry.get("status", ""),
                "live_nodes": entry.get("live_nodes"),
                "z_min": entry.get("z_min"),
                "z_max": entry.get("z_max"),
                "mask_agreement": entry.get("mask_agreement"),
            }
        )
    transform = report.get("survey_transform") or {}
    return {
        "available": True,
        "report_path": str(report_path),
        "summary": report.get("summary", {}),
        "surfaces": surfaces,
        "survey_origin": transform.get("origin_trace", {}),
    }


def gather_seismic(package: Path) -> dict:
    report_path = latest_report(package / "07_workflows_reports" / "seismic_zgy_export", "seismic_zgy_export_")
    report = load_json_file(report_path) if report_path else None
    if not report:
        return {"available": False}
    cubes = []
    for entry in report.get("cubes", []):
        stats = entry.get("amplitude_stats_decimated") or {}
        cubes.append(
            {
                "guid": entry.get("guid", ""),
                "status": entry.get("status", ""),
                "inline_count": entry.get("inline_count"),
                "xline_count": entry.get("xline_count"),
                "sample_count": entry.get("sample_count"),
                "inline_range": entry.get("inline_range", []),
                "xline_range": entry.get("xline_range", []),
                "sample_range": entry.get("sample_range", []),
                "zgy_bytes": entry.get("zgy_bytes"),
                "amplitude_min": stats.get("min"),
                "amplitude_max": stats.get("max"),
                "amplitude_rms": stats.get("rms"),
            }
        )
    return {
        "available": True,
        "report_path": str(report_path),
        "cube_count": len(cubes),
        "cubes": cubes,
        "summary": report.get("summary", {}),
    }


def gather_native_semantic(package: Path) -> dict:
    report_path = latest_report(
        package / "07_workflows_reports" / "native_semantic_export", "zero_gui_native_semantic_export_"
    )
    report = load_json_file(report_path) if report_path else None
    if not report:
        return {"available": False}
    return {
        "available": True,
        "report_path": str(report_path),
        "counts": report.get("counts", {}),
        "validation_status": report.get("validation_status", ""),
    }


def gather_domain_files(package: Path) -> dict:
    counts: dict[str, int] = {}
    for child in sorted(package.iterdir()):
        if child.is_dir() and child.name[:2].isdigit():
            counts[child.name] = sum(1 for item in child.rglob("*") if item.is_file())
    return counts


def gather_native_inventory(package: Path) -> dict:
    type_path = package / "01_project_metadata" / "native_data_object_type_counts.csv"
    type_rows = []
    for row in read_csv_rows(type_path):
        type_rows.append(
            {
                "object_type": row.get("object_type", ""),
                "unique_object_ids": to_int(row.get("unique_object_ids")),
                "version_records": to_int(row.get("version_records")),
                "occurrences": to_int(row.get("occurrences")),
                "registry_status": row.get("decode_status", ""),
            }
        )
    type_rows.sort(key=lambda row: (-row["unique_object_ids"], row["object_type"]))
    by_type = {row["object_type"]: row for row in type_rows}

    spatial_path = package / "07_workflows_reports" / "native_spatial_zero_gui" / "native_spatial_decode_report.json"
    spatial = load_json_file(spatial_path) or {}
    return {
        "available": bool(type_rows or spatial),
        "registry_path": str(type_path) if type_path.is_file() else "",
        "registry_types": type_rows,
        "registry_by_type": by_type,
        "registry_unique_objects": sum(row["unique_object_ids"] for row in type_rows),
        "spatial_report_path": str(spatial_path) if spatial else "",
        "spatial_tool_version": spatial.get("tool_version", ""),
        "decoded_object_type_counts": spatial.get("object_type_counts", {}),
        "object_status_counts": spatial.get("object_status_counts", {}),
        "polygon_vertex_rows": to_int(spatial.get("polygon_vertex_rows")),
        "point_vertex_rows": to_int(spatial.get("point_vertex_rows")),
        "trajectory_rows": to_int(spatial.get("trajectory_rows")),
        "native_well_head_rows": to_int(spatial.get("native_well_head_rows")),
        "validated_native_well_top_rows": to_int(spatial.get("validated_native_well_top_rows")),
        "crs_boundary": spatial.get("crs_boundary", ""),
    }


def gather_file_inventory(package: Path) -> dict:
    manifest_rows = read_csv_rows(package / "00_manifest" / "export_manifest.csv")
    manifest_by_path: dict[str, dict] = {}
    for row in manifest_rows:
        relative = (row.get("export_file") or "").replace("\\", "/").strip("/").casefold()
        if relative:
            manifest_by_path[relative] = row

    records = []
    by_extension: dict[str, int] = {}
    total_bytes = 0
    for path in sorted((item for item in package.rglob("*") if item.is_file()), key=lambda item: str(item).casefold()):
        relative = path.relative_to(package).as_posix()
        size = path.stat().st_size
        suffix = path.suffix.lower() or "[no extension]"
        manifest_row = manifest_by_path.get(relative.casefold(), {})
        total_bytes += size
        by_extension[suffix] = by_extension.get(suffix, 0) + 1
        records.append(
            {
                "path": relative,
                "name": path.name,
                "size_bytes": size,
                "extension": suffix,
                "modified_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "validation_status": manifest_row.get("validation_status", "not_registered"),
                "source_object_type": manifest_row.get("source_object_type", ""),
                "export_status": manifest_row.get("export_status", ""),
                "sha256": manifest_row.get("sha256", ""),
            }
        )
    return {
        "available": bool(records),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "by_extension": dict(sorted(by_extension.items(), key=lambda item: (-item[1], item[0]))),
        "files": records,
    }


def png_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        header = path.read_bytes()[:24]
        if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
            return struct.unpack(">II", header[16:24])
    except OSError:
        pass
    return None, None


def gather_media(package: Path, max_embed_bytes: int = 12 * 1024 * 1024) -> dict:
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    items = []
    for path in sorted((item for item in package.rglob("*") if item.is_file()), key=lambda item: str(item).casefold()):
        if path.suffix.lower() not in image_extensions:
            continue
        relative = path.relative_to(package).as_posix()
        size = path.stat().st_size
        width, height = png_dimensions(path)
        payload = ""
        status = "linked_not_embedded_size_limit"
        if size <= max_embed_bytes:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                payload = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                status = "embedded"
            except OSError:
                status = "unreadable"
        lower = relative.casefold()
        role = (
            "Petrel saved project image"
            if lower.endswith("/image.png") and "08_native_project" in lower
            else "Saved screenshot"
            if "screenshot" in lower or "capture" in lower
            else "Extracted image"
        )
        items.append(
            {
                "path": relative,
                "name": path.name,
                "role": role,
                "size_bytes": size,
                "width": width,
                "height": height,
                "embed_status": status,
                "data_uri": payload,
            }
        )
    return {"available": bool(items), "image_count": len(items), "images": items}


def decimate_xy(points: list[tuple[float, float]], maximum: int) -> list[tuple[float, float]]:
    if len(points) <= maximum:
        return points
    stride = max(1, math.ceil(len(points) / maximum))
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def gather_spatial_overview(package: Path, wells: dict) -> dict:
    polygon_path = package / "05_spatial" / "polygons" / "native_polygons_vertices.csv"
    point_path = package / "05_spatial" / "points" / "native_points_vertices.csv"
    polygon_groups: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in read_csv_rows(polygon_path):
        x, y = to_float(row.get("x")), to_float(row.get("y"))
        if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
            continue
        key = (row.get("object_id", ""), row.get("part_index", "0"))
        polygon_groups.setdefault(key, []).append((x, y))
    source_polygon_vertices = sum(len(vertices) for vertices in polygon_groups.values())
    polylines = [
        {
            "object_id": object_id,
            "part_index": part_index,
            "points": decimate_xy(points, 220),
            "source_vertex_count": len(points),
        }
        for (object_id, part_index), points in polygon_groups.items()
        if len(points) >= 2
    ]
    if sum(len(item["points"]) for item in polylines) > 12_000:
        polylines = polylines[: max(1, math.floor(len(polylines) * 12_000 / sum(len(item["points"]) for item in polylines)))]

    raw_points = []
    for row in read_csv_rows(point_path):
        x, y = to_float(row.get("x")), to_float(row.get("y"))
        if x is not None and y is not None and math.isfinite(x) and math.isfinite(y):
            raw_points.append((x, y))
    points = decimate_xy(raw_points, 1200)
    head_rows = [
        {"well_name": row["well_name"], "x": row["x"], "y": row["y"]}
        for row in wells.get("native_well_heads", [])
        if row.get("x") is not None and row.get("y") is not None
    ]

    all_xy = [point for item in polylines for point in item["points"]] + points + [
        (float(row["x"]), float(row["y"])) for row in head_rows
    ]
    source_bbox = {}
    if all_xy:
        source_xs = [point[0] for point in all_xy]
        source_ys = [point[1] for point in all_xy]
        source_bbox = {
            "min_x": min(source_xs), "max_x": max(source_xs),
            "min_y": min(source_ys), "max_y": max(source_ys),
        }

    # Validated point objects and well heads are the most useful anchors for a default
    # project view. Some decoded polygon payloads contain extreme numeric values; retain
    # them in CSV, but do not let a few such vertices collapse the browser preview.
    anchor_xy = raw_points + [(float(row["x"]), float(row["y"])) for row in head_rows]
    bbox = {}
    display_extent_basis = "all decoded display coordinates"
    if len(anchor_xy) >= 2:
        xs = [point[0] for point in anchor_xy]
        ys = [point[1] for point in anchor_xy]
        dx = max(max(xs) - min(xs), 1.0)
        dy = max(max(ys) - min(ys), 1.0)
        bbox = {
            "min_x": min(xs) - dx * 0.08, "max_x": max(xs) + dx * 0.08,
            "min_y": min(ys) - dy * 0.08, "max_y": max(ys) + dy * 0.08,
        }
        display_extent_basis = "decoded point objects and native well heads with 8% padding"
    elif source_bbox:
        bbox = dict(source_bbox)

    omitted_polygon_vertices = 0
    display_polylines = []
    if bbox:
        for item in polylines:
            retained = [
                point for point in item["points"]
                if bbox["min_x"] <= point[0] <= bbox["max_x"] and bbox["min_y"] <= point[1] <= bbox["max_y"]
            ]
            omitted_polygon_vertices += len(item["points"]) - len(retained)
            if len(retained) >= 2:
                display_polylines.append({**item, "points": retained})
    else:
        display_polylines = polylines

    return {
        "available": bool(all_xy),
        "bbox": bbox,
        "source_bbox": source_bbox,
        "display_extent_basis": display_extent_basis,
        "well_heads": head_rows,
        "polylines": display_polylines,
        "points": points,
        "source_polygon_vertices": source_polygon_vertices,
        "display_polygon_vertices": sum(len(item["points"]) for item in display_polylines),
        "omitted_preview_polygon_vertices": omitted_polygon_vertices,
        "source_point_vertices": len(raw_points),
        "display_point_vertices": len(points),
        "coordinate_boundary": "native numeric XY overview; not a georeferenced web basemap and CRS must be independently confirmed",
    }


def build_qc_flags(audit: dict) -> list[dict]:
    flags: list[dict] = []

    def add(severity: str, message: str) -> None:
        flags.append({"severity": severity, "message": message})

    manifest = audit["manifest"]
    if not manifest["available"]:
        add("warning", "No export manifest found; run the export pipeline and register_and_validate first.")
    else:
        bad = {k: v for k, v in manifest["by_validation_status"].items() if k != "validated"}
        if bad:
            add("warning", f"Manifest rows not in 'validated' state: {bad}.")
        if not manifest["crs_values"]:
            add("warning", "No manifest row carries an explicit coordinate reference system; CRS is only in sidecar files.")
    project = audit["project"]
    if project["available"] and (project["coordinate_reference_system"] or "unknown").lower() == "unknown":
        add("info", "project_summary.json CRS is 'unknown'; fill it from Petrel project settings when available.")
    surfaces = audit["surfaces"]
    if surfaces["available"]:
        unresolved = [s["guid"][:8] for s in surfaces["surfaces"] if s["status"] != "exported"]
        if unresolved:
            add("info", f"{len(unresolved)} surface grid(s) refused as layout-unresolved (fail-closed): {', '.join(unresolved)}.")
    wells = audit["wells"]
    if wells["wells"]:
        sparse = [w["well_name"] for w in wells["wells"] if (to_float(w["data_row_count"]) or 0) < 10]
        if sparse:
            add("info", f"{len(sparse)} LAS export(s) have fewer than 10 data rows: {', '.join(sparse)}.")
    tops = audit["well_tops"]
    if tops["available"] and not tops.get("petrel_export_confirmed", False):
        add("warning", "Well top rows are not all confirmed against a Petrel-authored export.")
    for name, section in (("wells", wells), ("well tops", tops), ("surfaces", surfaces), ("seismic", audit["seismic"])):
        if not section["available"]:
            add("info", f"No {name} evidence in this package; the corresponding export tool has not run here.")
    return flags


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def fmt_num(value: object, digits: int = 2) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return esc(value)


def html_table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def kpi_tile(label: str, value: str) -> str:
    return f'<div class="tile"><div class="tile-value">{esc(value)}</div><div class="tile-label">{esc(label)}</div></div>'


def render_html_legacy(audit: dict, title: str) -> str:
    project = audit["project"]
    manifest = audit["manifest"]
    wells = audit["wells"]
    tops = audit["well_tops"]
    surfaces = audit["surfaces"]
    seismic = audit["seismic"]
    semantic = audit["native_semantic"]

    tiles = [
        kpi_tile("Wells / native heads", str(wells["well_count"]) if wells["available"] else "-"),
        kpi_tile("Log curve rows", str(wells["curve_rows"]) if wells["available"] else "-"),
        kpi_tile("Well top picks", str(tops["pick_count"]) if tops["available"] else "-"),
        kpi_tile(
            "Surfaces exported",
            f"{surfaces['summary'].get('exported', 0)}/{surfaces['summary'].get('total', 0)}" if surfaces["available"] else "-",
        ),
        kpi_tile("Seismic cubes", str(seismic["cube_count"]) if seismic["available"] else "-"),
        kpi_tile("Manifest rows", f"{manifest['row_count']:,}" if manifest["available"] else "-"),
    ]

    sections: list[str] = []

    qc_items = "".join(
        f'<li class="{esc(flag["severity"])}"><strong>{esc(flag["severity"].upper())}</strong> {esc(flag["message"])}</li>'
        for flag in audit["qc_flags"]
    ) or "<li>No findings.</li>"
    sections.append(f"<section><h2>QC findings</h2><ul class='qc'>{qc_items}</ul></section>")

    if wells["wells"]:
        rows = [
            [
                esc(w["well_name"]),
                esc(w["curve_count"]),
                esc(w["data_row_count"]),
                fmt_num(w["start_depth"]),
                fmt_num(w["stop_depth"]),
                esc(w["depth_unit"]),
            ]
            for w in wells["wells"]
        ]
        mnemonics = ", ".join(wells["distinct_mnemonics"])
        sections.append(
            "<section><h2>Wells and logs</h2>"
            + html_table(["Well", "Curves", "Data rows", "Start depth", "Stop depth", "Unit"], rows)
            + f"<p class='note'>Distinct curve mnemonics ({len(wells['distinct_mnemonics'])}): {esc(mnemonics)}</p></section>"
        )
    else:
        sections.append(f"<section><h2>Wells and logs</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if wells["native_well_heads"]:
        head_rows = [
            [
                esc(w["well_name"]),
                fmt_num(w["x"], 3),
                fmt_num(w["y"], 3),
                fmt_num(w["z"], 3),
                esc(w["native_name_uniqueness"]),
                esc(w["xy_crosscheck_status"]),
                esc(w["crs_status"]),
            ]
            for w in wells["native_well_heads"]
        ]
        sections.append(
            "<section><h2>Native well-head coordinates</h2>"
            + html_table(["Well", "X", "Y", "Trajectory-start Z", "Name status", "XY cross-check", "CRS status"], head_rows)
            + "<p class='note'>X/Y are native Model.ptd WellTraceSubject well_head_ values. "
            + "Trajectory-start Z appears only after an X/Y match and is not asserted to be datum elevation.</p></section>"
        )
    else:
        sections.append(f"<section><h2>Native well-head coordinates</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if tops["available"]:
        surface_rows = [[esc(name), str(count)] for name, count in tops["per_surface"].items()]
        z_range = tops["z_range"]
        md_range = tops["md_range"]
        confirmed = "yes" if tops.get("petrel_export_confirmed") else "no"
        sections.append(
            "<section><h2>Well tops</h2>"
            + f"<p>{tops['pick_count']} marker picks across {tops['well_count']} wells and {tops['surface_count']} surfaces. "
            + (f"Z (elevation) range {fmt_num(z_range[0])} to {fmt_num(z_range[1])}. " if z_range else "")
            + (f"MD range {fmt_num(md_range[0])} to {fmt_num(md_range[1])}. " if md_range else "")
            + f"Confirmed against Petrel-authored ASCII export: {confirmed}.</p>"
            + html_table(["Surface / marker", "Picks"], surface_rows)
            + "</section>"
        )
    else:
        sections.append(f"<section><h2>Well tops</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if surfaces["available"]:
        rows = [
            [
                esc(s["guid"][:8]),
                esc("x".join(str(d) for d in s["dims"])),
                esc(s["status"]),
                esc(s["live_nodes"] if s["live_nodes"] is not None else "-"),
                fmt_num(s["z_min"]),
                fmt_num(s["z_max"]),
                fmt_num((s["mask_agreement"] or 0) * 100, 2) + "%" if s["mask_agreement"] is not None else "-",
            ]
            for s in surfaces["surfaces"]
        ]
        origin = surfaces["survey_origin"]
        origin_note = (
            f"Survey origin trace: inline {esc(origin.get('inline'))}, xline {esc(origin.get('xline'))}, "
            f"X {fmt_num(origin.get('x'))}, Y {fmt_num(origin.get('y'))}."
            if origin
            else ""
        )
        sections.append(
            "<section><h2>Surfaces (native decode)</h2>"
            + html_table(["GUID", "Grid", "Status", "Live nodes", "Z min", "Z max", "Mask agreement"], rows)
            + f"<p class='note'>{origin_note} Non-exported grids are fail-closed refusals, not data loss.</p></section>"
        )
    else:
        sections.append(f"<section><h2>Surfaces</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if seismic["available"]:
        rows = [
            [
                esc(c["guid"][:8]),
                esc(f"{c['inline_count']} x {c['xline_count']} x {c['sample_count']}"),
                esc(f"{c['inline_range'][0]}-{c['inline_range'][1]}" if c["inline_range"] else "-"),
                esc(f"{c['xline_range'][0]}-{c['xline_range'][1]}" if c["xline_range"] else "-"),
                esc(
                    f"{fmt_num(c['sample_range'][0], 1)} to {fmt_num(c['sample_range'][1], 1)}"
                    if c["sample_range"]
                    else "-"
                ),
                fmt_num(c["amplitude_rms"]),
                fmt_num((c["zgy_bytes"] or 0) / (1024 * 1024), 1) + " MB",
                esc(c["status"]),
            ]
            for c in seismic["cubes"]
        ]
        sections.append(
            "<section><h2>Seismic cubes (ZGY)</h2>"
            + html_table(
                ["GUID", "Dimensions", "Inlines", "Xlines", "Sample range", "RMS amplitude", "Size", "Status"], rows
            )
            + "</section>"
        )
    else:
        sections.append(f"<section><h2>Seismic cubes</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if semantic["available"]:
        rows = [[esc(name.replace("_", " ")), str(count)] for name, count in semantic["counts"].items()]
        sections.append(
            "<section><h2>Native project store (semantic decode)</h2>"
            + html_table(["Object class", "Count"], rows)
            + f"<p class='note'>Validation status: {esc(semantic['validation_status'])}</p></section>"
        )
    else:
        sections.append(f"<section><h2>Native project store</h2><p class='note'>{NOT_AVAILABLE}</p></section>")

    if manifest["available"]:
        type_rows = [[esc(name), str(count)] for name, count in manifest["by_source_object_type"].items()]
        format_rows = [[esc(name), str(count)] for name, count in manifest["by_export_format"].items()]
        crs_text = ", ".join(f"{name} ({count} rows)" for name, count in manifest["crs_values"].items()) or "none recorded on rows"
        date_range = manifest["export_date_range"]
        sections.append(
            "<section><h2>Export manifest</h2>"
            + f"<p>{manifest['row_count']:,} rows, {manifest['checksummed_rows']:,} with SHA-256 checksums. "
            + f"Validation: {esc(manifest['by_validation_status'])}. CRS on rows: {esc(crs_text)}."
            + (f" Export dates {esc(date_range[0][:10])} to {esc(date_range[1][:10])}." if date_range else "")
            + "</p><div class='cols'><div><h3>By object type</h3>"
            + html_table(["Object type", "Rows"], type_rows)
            + "</div><div><h3>By format</h3>"
            + html_table(["Format", "Rows"], format_rows)
            + "</div></div></section>"
        )

    domain_rows = [[esc(name), str(count)] for name, count in audit["domain_file_counts"].items()]
    sections.append(
        "<section><h2>Package file inventory</h2>" + html_table(["Domain folder", "Files"], domain_rows) + "</section>"
    )

    evidence = [
        [esc(name), esc(path)]
        for name, path in (
            ("Export manifest", manifest.get("path", "")),
            ("Surfaces report", surfaces.get("report_path", "")),
            ("Seismic report", seismic.get("report_path", "")),
            ("Native semantic report", semantic.get("report_path", "")),
        )
        if path
    ]
    sections.append("<section><h2>Evidence</h2>" + html_table(["Source", "Path"], evidence) + "</section>")

    style = """
    body { font-family: 'Segoe UI', Arial, sans-serif; margin: 0; color: #1c2733; background: #f4f6f8; }
    header { background: #12303f; color: #fff; padding: 24px 32px; }
    header h1 { margin: 0 0 6px 0; font-size: 24px; }
    header p { margin: 2px 0; color: #b8ccd6; font-size: 13px; }
    main { max-width: 1080px; margin: 0 auto; padding: 20px 32px 48px; }
    .tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }
    .tile { background: #fff; border: 1px solid #dbe3e8; border-radius: 8px; padding: 14px 20px; min-width: 130px; }
    .tile-value { font-size: 26px; font-weight: 600; color: #12303f; }
    .tile-label { font-size: 12px; color: #5b7282; margin-top: 2px; }
    section { background: #fff; border: 1px solid #dbe3e8; border-radius: 8px; padding: 18px 22px; margin: 16px 0; }
    h2 { margin: 0 0 10px 0; font-size: 17px; color: #12303f; }
    h3 { margin: 8px 0; font-size: 14px; color: #12303f; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th { text-align: left; background: #eef2f5; padding: 6px 10px; border-bottom: 2px solid #dbe3e8; white-space: nowrap; }
    td { padding: 5px 10px; border-bottom: 1px solid #edf1f4; }
    .scroll { overflow-x: auto; }
    .cols { display: flex; flex-wrap: wrap; gap: 20px; }
    .cols > div { flex: 1; min-width: 260px; }
    .note { font-size: 12px; color: #5b7282; }
    ul.qc { margin: 0; padding-left: 18px; font-size: 13px; }
    ul.qc li { margin: 4px 0; }
    ul.qc li.warning strong { color: #b3541e; }
    ul.qc li.info strong { color: #2a6f8f; }
    footer { text-align: center; color: #5b7282; font-size: 12px; padding: 12px; }
    """
    header_lines = [
        f"Project: {esc(project['project_name'] or 'unknown')} | Petrel version: {esc(project['petrel_version'] or 'unknown')}",
        f"Export package: {esc(audit['export_package'])}",
        f"Generated {esc(audit['created_at_utc'])} by report_petrel_project_audit.py v{TOOL_VERSION} - zero-GUI, Petrel was not launched.",
    ]
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)}</title><style>{style}</style></head><body>"
        f"<header><h1>{esc(title)}</h1>" + "".join(f"<p>{line}</p>" for line in header_lines) + "</header>"
        f"<main><div class='tiles'>{''.join(tiles)}</div>{''.join(sections)}</main>"
        "<footer>Produced from exported evidence files only. No Petrel process or license was used.</footer>"
        "</body></html>"
    )


def human_size(value: int | float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def file_href(audit: dict, relative_path: str) -> str:
    prefix = str(audit.get("file_link_prefix", "")).replace("\\", "/").rstrip("/")
    relative = str(relative_path).replace("\\", "/").lstrip("/")
    combined = f"{prefix}/{relative}" if prefix else relative
    return quote(combined, safe="/:._-~")


def render_file_tree(audit: dict) -> str:
    root: dict = {"dirs": {}, "files": []}
    for record in audit.get("file_inventory", {}).get("files", []):
        node = root
        parts = record["path"].split("/")
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append(record)

    def totals(node: dict) -> tuple[int, int]:
        count = len(node["files"])
        size = sum(to_int(record.get("size_bytes")) for record in node["files"])
        for child in node["dirs"].values():
            child_count, child_size = totals(child)
            count += child_count
            size += child_size
        node["file_count"] = count
        node["size_bytes"] = size
        return count, size

    totals(root)

    def render_node(node: dict, current_path: str, depth: int) -> str:
        chunks = []
        for directory_name in sorted(node["dirs"], key=str.casefold):
            child = node["dirs"][directory_name]
            directory_path = f"{current_path}/{directory_name}".strip("/")
            open_attr = " open" if depth == 0 else ""
            chunks.append(
                f'<details class="tree-dir" data-path="{esc(directory_path.casefold())}"{open_attr}>'
                f'<summary><span class="folder-icon">▸</span>{esc(directory_name)}'
                f'<span class="tree-meta">{child["file_count"]} files · {esc(human_size(child["size_bytes"]))}</span></summary>'
                f'<div class="tree-children">{render_node(child, directory_path, depth + 1)}</div></details>'
            )
        for record in sorted(node["files"], key=lambda item: item["name"].casefold()):
            status = record.get("validation_status") or "not_registered"
            status_class = "good" if status == "validated" else "muted" if status == "not_registered" else "warn"
            searchable = f'{record["path"]} {record.get("source_object_type", "")} {status}'.casefold()
            chunks.append(
                f'<div class="file-node" data-path="{esc(searchable)}">'
                f'<span class="file-icon">•</span><a href="{file_href(audit, record["path"])}">{esc(record["name"])}</a>'
                f'<span class="tree-meta">{esc(human_size(record["size_bytes"]))} · '
                f'<span class="status {status_class}">{esc(status)}</span></span></div>'
            )
        return "".join(chunks)

    return render_node(root, "", 0)


def render_spatial_svg(overview: dict) -> str:
    if not overview.get("available") or not overview.get("bbox"):
        return f'<p class="note">{NOT_AVAILABLE}</p>'
    bbox = overview["bbox"]
    min_x, max_x = float(bbox["min_x"]), float(bbox["max_x"])
    min_y, max_y = float(bbox["min_y"]), float(bbox["max_y"])
    dx = max(max_x - min_x, 1.0)
    dy = max(max_y - min_y, 1.0)
    width, height = 1000.0, 620.0
    left, right, top, bottom = 72.0, 28.0, 28.0, 62.0
    scale = min((width - left - right) / dx, (height - top - bottom) / dy)
    used_width, used_height = dx * scale, dy * scale
    x0 = left + ((width - left - right) - used_width) / 2
    y0 = top + ((height - top - bottom) - used_height) / 2

    def project(x: float, y: float) -> tuple[float, float]:
        return x0 + (x - min_x) * scale, y0 + used_height - (y - min_y) * scale

    grid = []
    for index in range(6):
        fraction = index / 5
        x = x0 + used_width * fraction
        y = y0 + used_height * fraction
        native_x = min_x + dx * fraction
        native_y = max_y - dy * fraction
        grid.append(f'<line x1="{x:.2f}" y1="{y0:.2f}" x2="{x:.2f}" y2="{y0 + used_height:.2f}"/>')
        grid.append(f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x0 + used_width:.2f}" y2="{y:.2f}"/>')
        grid.append(f'<text x="{x:.2f}" y="{y0 + used_height + 24:.2f}" text-anchor="middle">{native_x:,.0f}</text>')
        grid.append(f'<text x="{x0 - 12:.2f}" y="{y + 4:.2f}" text-anchor="end">{native_y:,.0f}</text>')

    polygons = []
    for item in overview.get("polylines", []):
        coords = " ".join(f"{px:.2f},{py:.2f}" for px, py in (project(float(x), float(y)) for x, y in item["points"]))
        polygons.append(
            f'<polyline points="{coords}"><title>Polygon {esc(item["object_id"][:8])}, part {esc(item["part_index"])}, '
            f'{item["source_vertex_count"]} source vertices</title></polyline>'
        )
    points = []
    for x, y in overview.get("points", []):
        px, py = project(float(x), float(y))
        points.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1.65"/>')
    well_nodes = []
    label_wells = len(overview.get("well_heads", [])) <= 30
    for row in overview.get("well_heads", []):
        px, py = project(float(row["x"]), float(row["y"]))
        well_nodes.append(
            f'<circle class="well-dot" cx="{px:.2f}" cy="{py:.2f}" r="4.2"><title>{esc(row["well_name"])} — '
            f'X {float(row["x"]):.3f}, Y {float(row["y"]):.3f}</title></circle>'
        )
        if label_wells:
            well_nodes.append(f'<text class="well-label" x="{px + 6:.2f}" y="{py - 6:.2f}">{esc(row["well_name"])}</text>')

    return (
        '<div class="map-shell"><svg id="spatial-map" viewBox="0 0 1000 620" data-original-viewbox="0 0 1000 620" '
        'role="img" aria-label="Native XY spatial overview">'
        '<rect class="map-bg" x="0" y="0" width="1000" height="620"/>'
        f'<g class="map-grid">{"".join(grid)}</g>'
        f'<g id="layer-polygons" class="map-polygons">{"".join(polygons)}</g>'
        f'<g id="layer-points" class="map-points">{"".join(points)}</g>'
        f'<g id="layer-wells" class="map-wells">{"".join(well_nodes)}</g>'
        '<text class="axis-label" x="520" y="612" text-anchor="middle">Native X</text>'
        '<text class="axis-label" x="15" y="310" text-anchor="middle" transform="rotate(-90 15 310)">Native Y</text>'
        '</svg></div>'
    )


def render_html(audit: dict, title: str) -> str:
    project = audit["project"]
    manifest = audit["manifest"]
    wells = audit["wells"]
    tops = audit["well_tops"]
    surfaces = audit["surfaces"]
    seismic = audit["seismic"]
    semantic = audit["native_semantic"]
    native = audit["native_inventory"]
    overview = audit["spatial_overview"]
    media = audit["media"]
    files = audit["file_inventory"]
    registry = native.get("registry_by_type", {})
    decoded = native.get("decoded_object_type_counts", {})

    def registered(object_type: str) -> int:
        return to_int((registry.get(object_type) or {}).get("unique_object_ids"))

    trajectory_registered = sum(
        registered(name)
        for name in ("ExplicitTrajectoryProviderData", "MdInclAzimTrajectoryProviderData", "XYZTrajectoryProviderData")
    )
    trajectory_decoded_objects = sum(
        to_int(decoded.get(name))
        for name in ("ExplicitTrajectoryProviderData", "MdInclAzimTrajectoryProviderData", "XYZTrajectoryProviderData")
    )
    seismic_registered = registered("SeismicBaseAccessGrid")
    faults_registered = registered("FaultInterpretation")
    polygons_registered = registered("Polygons3")
    points_registered = registered("Points3")
    grids_registered = registered("RegValGrid2")
    logs_registered = registered("FloatWellLog") + registered("IntWellLog")
    polygons_decoded = to_int(decoded.get("Polygons3"))
    points_decoded = to_int(decoded.get("Points3"))
    manifest_validated = to_int(manifest.get("by_validation_status", {}).get("validated"))

    tiles = [
        ("Native well heads", wells.get("native_well_head_count", 0), "decoded XY rows"),
        ("Polygons", polygons_decoded, f"decoded live objects · {polygons_registered} registry IDs"),
        ("Polygon vertices", native.get("polygon_vertex_rows", 0), "native XYZ rows"),
        ("Seismic", seismic_registered, f"registry IDs · {seismic.get('cube_count', 0) if seismic.get('available') else 0} converted"),
        ("Faults", faults_registered, "registry IDs · geometry not decoded"),
        ("Package files", files.get("file_count", 0), human_size(files.get("total_bytes", 0))),
        ("Manifest validation", f"{manifest_validated}/{manifest.get('row_count', 0)}", "validated rows"),
        ("Saved images", media.get("image_count", 0), "embedded in this report"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-value">{esc(value)}</div><div class="tile-label">{esc(label)}</div>'
        f'<div class="tile-detail">{esc(detail)}</div></div>'
        for label, value, detail in tiles
    )

    qc_items = "".join(
        f'<li class="{esc(flag["severity"])}"><strong>{esc(flag["severity"].upper())}</strong> {esc(flag["message"])}</li>'
        for flag in audit["qc_flags"]
    ) or "<li class='good'><strong>PASS</strong> No QC findings were generated.</li>"

    crs_value = project.get("coordinate_reference_system") or "unknown"
    crs_status = project.get("coordinate_reference_system_status") or "not recorded"
    crs_warning = project.get("coordinate_reference_system_warning") or (
        "No confirmed project CRS was found. Numeric coordinates are preserved without inference."
    )
    versions = ", ".join(project.get("native_petrel_version_candidates") or []) or "none recorded"
    project_rows = [
        ["Project", esc(project.get("project_name") or "unknown")],
        ["Reported Petrel version", esc(project.get("petrel_version") or "unknown")],
        ["Native version candidates", esc(versions)],
        ["Source project path", esc(project.get("project_path") or "unknown")],
        ["Export package", esc(audit["export_package"])],
        ["Export ID", esc(project.get("export_id") or "unknown")],
    ]

    coverage_rows = [
        ["Well heads", registered("DummyWellTraceSubjectShape"), wells.get("native_well_head_count", 0), "CSV with native X/Y", "Exact Model.ptd WellTraceSubject fields; CRS/units unresolved"],
        ["Trajectories", trajectory_registered, trajectory_decoded_objects, f'{native.get("trajectory_rows", 0):,} CSV records', "Only validated provider layouts"],
        ["Polygons", polygons_registered, polygons_decoded, f'{native.get("polygon_vertex_rows", 0):,} XYZ vertices', "Decoded live supported objects; registry can include other versions"],
        ["Point sets", points_registered, points_decoded, f'{native.get("point_vertex_rows", 0):,} XYZ vertices', "Object identity/attributes may remain unresolved"],
        ["Seismic", seismic_registered, seismic.get("cube_count", 0) if seismic.get("available") else 0, "ZGY when available", "Registry evidence is not converted seismic"],
        ["Fault interpretations", faults_registered, 0, "Metadata only", "Native fault geometry decoder not validated"],
        ["Regular-value grids", grids_registered, surfaces.get("summary", {}).get("exported", 0) if surfaces.get("available") else 0, "Surface arrays when validated", "Grid values/masks remain fail-closed without proof"],
        ["Well logs", logs_registered, wells.get("las_well_count", 0), "LAS/CSV companions", "Native binary log arrays not decoded here"],
        ["Well tops", 0, tops.get("pick_count", 0) if tops.get("available") else native.get("validated_native_well_top_rows", 0), "Validated CSV only", "Native labels require independent calibration"],
    ]
    coverage_html = html_table(
        ["Domain", "Registry IDs", "Decoded/exported", "Open result", "Evidence boundary"],
        [[esc(cell) for cell in row] for row in coverage_rows],
    )

    image_cards = []
    for item in media.get("images", []):
        dimensions = f'{item["width"]} × {item["height"]}' if item.get("width") and item.get("height") else "dimensions unavailable"
        if item.get("data_uri"):
            visual = f'<img loading="lazy" src="{item["data_uri"]}" alt="{esc(item["role"])}: {esc(item["path"])}">'
        else:
            visual = f'<a class="image-placeholder" href="{file_href(audit, item["path"])}">Open image</a>'
        image_cards.append(
            f'<figure>{visual}<figcaption><strong>{esc(item["role"])}</strong><br>{esc(item["path"])}<br>'
            f'{esc(dimensions)} · {esc(human_size(item["size_bytes"]))}</figcaption></figure>'
        )
    gallery_html = "".join(image_cards) if image_cards else f'<p class="note">{NOT_AVAILABLE}</p>'

    map_controls = (
        '<div class="map-controls">'
        '<label><input type="checkbox" data-layer="layer-polygons" checked> Polygons</label>'
        '<label><input type="checkbox" data-layer="layer-points" checked> Points</label>'
        '<label><input type="checkbox" data-layer="layer-wells" checked> Well heads</label>'
        '<button type="button" data-map-action="zoom-in">＋</button><button type="button" data-map-action="zoom-out">−</button>'
        '<button type="button" data-map-action="reset">Reset view</button></div>'
    )
    bbox = overview.get("bbox", {})
    map_note = (
        f'Display extent ({overview.get("display_extent_basis", "native coordinates")}) X {fmt_num(bbox.get("min_x"), 1)} to {fmt_num(bbox.get("max_x"), 1)}; '
        f'Y {fmt_num(bbox.get("min_y"), 1)} to {fmt_num(bbox.get("max_y"), 1)}. '
        f'Display uses {overview.get("display_polygon_vertices", 0):,} decimated polygon vertices from '
        f'{overview.get("source_polygon_vertices", 0):,} source rows and {overview.get("display_point_vertices", 0):,} '
        f'points from {overview.get("source_point_vertices", 0):,}. '
        f'{overview.get("omitted_preview_polygon_vertices", 0):,} decimated polygon vertices outside the display extent are omitted from the preview only and remain in CSV. '
        f'This is a native-coordinate overview, not a georeferenced web basemap.'
        if bbox else overview.get("coordinate_boundary", "")
    )

    head_rows = [
        [
            esc(row["well_name"]), fmt_num(row["x"], 3), fmt_num(row["y"], 3), fmt_num(row["z"], 3),
            esc(row["native_name_uniqueness"]), esc(row["xy_crosscheck_status"]), esc(row["crs_status"]),
        ]
        for row in wells.get("native_well_heads", [])
    ]
    head_table = html_table(["Well", "X", "Y", "Trajectory-start Z", "Name status", "XY cross-check", "CRS status"], head_rows) if head_rows else f'<p class="note">{NOT_AVAILABLE}</p>'

    native_rows = [
        [esc(row["object_type"]), f'{row["unique_object_ids"]:,}', f'{row["version_records"]:,}', f'{row["occurrences"]:,}', esc(row["registry_status"])]
        for row in native.get("registry_types", [])
    ]
    native_table = html_table(["Native object type", "Unique IDs", "Version rows", "Occurrences", "Registry status"], native_rows) if native_rows else f'<p class="note">{NOT_AVAILABLE}</p>'

    manifest_type_rows = [[esc(name or "[blank]"), str(count)] for name, count in manifest.get("by_source_object_type", {}).items()]
    manifest_format_rows = [[esc(name or "[blank]"), str(count)] for name, count in manifest.get("by_export_format", {}).items()]
    manifest_html = (
        f'<p><strong>{manifest.get("row_count", 0):,}</strong> manifest rows; '
        f'<strong>{manifest.get("checksummed_rows", 0):,}</strong> checksummed; validation {esc(manifest.get("by_validation_status", {}))}.</p>'
        '<div class="cols"><div><h3>By object type</h3>'
        + html_table(["Object type", "Rows"], manifest_type_rows)
        + '</div><div><h3>By format</h3>'
        + html_table(["Format", "Rows"], manifest_format_rows)
        + '</div></div>'
        if manifest.get("available") else f'<p class="note">{NOT_AVAILABLE}</p>'
    )

    evidence_items = []
    for label, path in (
        ("Export manifest", manifest.get("path", "")),
        ("Native object registry", native.get("registry_path", "")),
        ("Native spatial decoder", native.get("spatial_report_path", "")),
        ("Native semantic report", semantic.get("report_path", "")),
        ("Surfaces report", surfaces.get("report_path", "")),
        ("Seismic report", seismic.get("report_path", "")),
    ):
        if not path:
            continue
        try:
            relative = Path(path).resolve().relative_to(Path(audit["export_package"]).resolve()).as_posix()
            link = f'<a href="{file_href(audit, relative)}">{esc(relative)}</a>'
        except ValueError:
            link = esc(path)
        evidence_items.append([esc(label), link])
    evidence_html = html_table(["Evidence", "File"], evidence_items)

    file_tree = render_file_tree(audit)
    domain_rows = [[esc(name), str(count)] for name, count in audit["domain_file_counts"].items()]

    style = r"""
    :root { --ink:#17242c; --muted:#617681; --navy:#102f3b; --teal:#0e7c7b; --gold:#d9921e; --paper:#ffffff; --line:#d8e2e6; --bg:#eef3f5; --good:#18794e; --warn:#b75d12; }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; font-family:'Segoe UI',Arial,sans-serif; color:var(--ink); background:var(--bg); }
    a { color:#086b82; text-decoration:none; } a:hover { text-decoration:underline; }
    header { background:linear-gradient(125deg,#0b2833,#174a57 65%,#0e7c7b); color:white; padding:30px max(28px,calc((100vw - 1320px)/2)); }
    header h1 { margin:0 0 8px; font-size:30px; font-weight:650; }
    header p { margin:4px 0; color:#c9dce2; font-size:13px; }
    nav { position:sticky; top:0; z-index:20; background:#fff; border-bottom:1px solid var(--line); padding:0 max(20px,calc((100vw - 1320px)/2)); display:flex; gap:4px; overflow-x:auto; box-shadow:0 2px 8px #16313c16; }
    nav a { padding:12px 11px; white-space:nowrap; font-size:12px; font-weight:600; color:#31515d; }
    nav a:hover { background:#e8f3f4; text-decoration:none; }
    main { max-width:1320px; margin:0 auto; padding:22px 26px 60px; }
    section { scroll-margin-top:60px; background:var(--paper); border:1px solid var(--line); border-radius:12px; padding:21px 24px; margin:17px 0; box-shadow:0 2px 11px #18323c0a; }
    h2 { margin:0 0 12px; color:var(--navy); font-size:20px; } h3 { color:var(--navy); font-size:14px; }
    .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:0 0 18px; }
    .tile { background:#fff; border:1px solid var(--line); border-top:4px solid var(--teal); border-radius:10px; padding:14px 16px; min-height:112px; }
    .tile:nth-child(3n+2) { border-top-color:var(--gold); } .tile-value { font-size:28px; font-weight:700; color:var(--navy); }
    .tile-label { font-size:12px; font-weight:650; margin-top:2px; } .tile-detail { font-size:11px; color:var(--muted); margin-top:7px; line-height:1.35; }
    .brief-grid { display:grid; grid-template-columns:minmax(300px,1fr) minmax(300px,1fr); gap:20px; }
    .crs-card { background:#f3f8f8; border-left:4px solid var(--teal); border-radius:7px; padding:16px; }
    .crs-value { font-size:20px; font-weight:700; color:var(--navy); } .boundary { margin-top:10px; padding:10px 12px; background:#fff8e8; border-left:3px solid var(--gold); color:#654b1f; font-size:12px; }
    table { width:100%; border-collapse:collapse; font-size:12.5px; } th { position:sticky; top:0; background:#edf3f5; text-align:left; padding:8px 9px; border-bottom:2px solid var(--line); white-space:nowrap; } td { padding:7px 9px; border-bottom:1px solid #e8eef0; vertical-align:top; }
    tbody tr:hover { background:#f7fafb; } .scroll { overflow:auto; max-height:520px; border:1px solid #e4ebee; border-radius:7px; }
    .cols { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; } .note { color:var(--muted); font-size:12px; line-height:1.5; }
    .gallery { display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr)); gap:16px; } figure { margin:0; border:1px solid var(--line); border-radius:9px; overflow:hidden; background:#10191e; } figure img { width:100%; max-height:560px; object-fit:contain; display:block; } figcaption { padding:10px 12px; background:#fff; font-size:11.5px; color:var(--muted); line-height:1.45; }
    .map-shell { border:1px solid #b8c8ce; border-radius:8px; overflow:hidden; background:#0d1d24; } #spatial-map { display:block; width:100%; max-height:680px; touch-action:none; cursor:grab; } #spatial-map:active { cursor:grabbing; }
    .map-bg { fill:#0d1d24; } .map-grid line { stroke:#31505c; stroke-width:.75; } .map-grid text,.axis-label { fill:#9ab1ba; font-size:11px; } .axis-label { font-size:13px; font-weight:600; }
    .map-polygons polyline { fill:none; stroke:#e1a43a; stroke-width:1.25; opacity:.72; vector-effect:non-scaling-stroke; } .map-points circle { fill:#40c4d8; opacity:.55; } .well-dot { fill:#ff665a; stroke:white; stroke-width:1.2; vector-effect:non-scaling-stroke; } .well-label { fill:#f3f8fa; font-size:10px; paint-order:stroke; stroke:#0d1d24; stroke-width:3px; }
    .map-controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:8px 0 12px; font-size:12px; } button { border:1px solid #b8c8ce; background:#fff; color:#254652; border-radius:6px; padding:6px 10px; cursor:pointer; } button:hover { background:#eaf3f5; }
    .tree-toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; } .tree-toolbar input { flex:1; min-width:260px; padding:8px 10px; border:1px solid #b8c8ce; border-radius:6px; }
    .file-tree { font-size:12px; border:1px solid var(--line); border-radius:8px; padding:9px 12px; max-height:650px; overflow:auto; background:#fbfcfd; } details.tree-dir { margin-left:8px; } details.tree-dir > summary { cursor:pointer; padding:5px 4px; font-weight:650; color:#2a4a56; } details.tree-dir[open] > summary .folder-icon { display:inline-block; transform:rotate(90deg); } .tree-children { margin-left:13px; border-left:1px solid #dce6e9; padding-left:8px; }
    .file-node { display:flex; align-items:center; gap:6px; padding:4px 5px 4px 9px; margin-left:8px; } .file-node:hover { background:#edf5f6; } .file-node a { overflow-wrap:anywhere; } .tree-meta { margin-left:auto; color:#7a8e97; font-size:10.5px; white-space:nowrap; padding-left:12px; }
    .status { border-radius:9px; padding:1px 6px; } .status.good { background:#e6f5ec; color:var(--good); } .status.warn { background:#fff1df; color:var(--warn); } .status.muted { background:#edf1f3; color:#657982; }
    ul.qc { margin:0; padding-left:20px; font-size:13px; } ul.qc li { margin:7px 0; } ul.qc li.warning strong { color:var(--warn); } ul.qc li.info strong { color:#16718b; } ul.qc li.good strong { color:var(--good); }
    footer { color:#657982; text-align:center; font-size:11.5px; padding:18px; }
    @media(max-width:760px) { main { padding:16px 12px 40px; } section { padding:17px 14px; } .brief-grid,.cols { grid-template-columns:1fr; } header { padding:24px 18px; } header h1 { font-size:24px; } }
    @media print { nav,.map-controls,.tree-toolbar { display:none; } body { background:#fff; } section { box-shadow:none; break-inside:avoid; } .scroll,.file-tree { max-height:none; overflow:visible; } }
    """

    script = r"""
    (() => {
      document.querySelectorAll('[data-layer]').forEach(input => input.addEventListener('change', () => {
        const layer = document.getElementById(input.dataset.layer); if (layer) layer.style.display = input.checked ? '' : 'none';
      }));
      const tree = document.getElementById('file-tree');
      const search = document.getElementById('tree-search');
      const filterTree = () => {
        if (!tree || !search) return; const q = search.value.trim().toLowerCase();
        tree.querySelectorAll('.file-node').forEach(node => node.hidden = !!q && !node.dataset.path.includes(q));
        [...tree.querySelectorAll('details.tree-dir')].reverse().forEach(dir => {
          const visible = [...dir.querySelectorAll('.file-node')].some(node => !node.hidden);
          const ownMatch = !q || dir.dataset.path.includes(q); dir.hidden = !!q && !visible && !ownMatch;
          if (q && (visible || ownMatch)) dir.open = true;
        });
      };
      if (search) search.addEventListener('input', filterTree);
      document.querySelectorAll('[data-tree-action]').forEach(button => button.addEventListener('click', () => {
        if (!tree) return; const open = button.dataset.treeAction === 'expand'; tree.querySelectorAll('details').forEach(item => item.open = open);
      }));
      const svg = document.getElementById('spatial-map');
      if (svg) {
        let box = [0,0,1000,620], drag = null;
        const apply = () => svg.setAttribute('viewBox', box.join(' '));
        const zoom = factor => { const nw=box[2]*factor, nh=box[3]*factor; box=[box[0]+(box[2]-nw)/2,box[1]+(box[3]-nh)/2,nw,nh]; apply(); };
        document.querySelectorAll('[data-map-action]').forEach(button => button.addEventListener('click', () => {
          const action=button.dataset.mapAction; if(action==='zoom-in') zoom(.8); else if(action==='zoom-out') zoom(1.25); else {box=[0,0,1000,620];apply();}
        }));
        svg.addEventListener('wheel', event => { event.preventDefault(); zoom(event.deltaY < 0 ? .88 : 1.14); }, {passive:false});
        svg.addEventListener('pointerdown', event => { drag={x:event.clientX,y:event.clientY,box:[...box]}; svg.setPointerCapture(event.pointerId); });
        svg.addEventListener('pointermove', event => { if(!drag) return; const rect=svg.getBoundingClientRect(); box[0]=drag.box[0]-(event.clientX-drag.x)*drag.box[2]/rect.width; box[1]=drag.box[1]-(event.clientY-drag.y)*drag.box[3]/rect.height; apply(); });
        svg.addEventListener('pointerup', () => drag=null); svg.addEventListener('pointercancel', () => drag=null);
      }
    })();
    """

    nav = "".join(
        f'<a href="#{section_id}">{label}</a>'
        for section_id, label in (
            ("overview", "Overview"), ("crs", "CRS"), ("maps", "Maps & images"), ("coverage", "Coverage"),
            ("wells", "Wells"), ("native", "Native inventory"), ("files", "File tree"), ("qc", "QC & evidence"),
        )
    )
    generated = esc(audit["created_at_utc"])
    header = (
        f'<header><h1>{esc(title)}</h1><p>Project: {esc(project.get("project_name") or "unknown")} · '
        f'Petrel version: {esc(project.get("petrel_version") or "unknown")} · Export: {esc(project.get("export_id") or "unknown")}</p>'
        f'<p>Generated {generated} by report_petrel_project_audit.py v{TOOL_VERSION}. Zero-GUI; Petrel and Ocean were not used.</p></header>'
    )

    body = f"""
    <section id="overview"><h2>Project overview</h2><div class="tiles">{tile_html}</div>
      <div class="brief-grid"><div>{html_table(["Project field","Value"], project_rows)}</div>
      <div><h3>Extraction state</h3><p><strong>{native.get('registry_unique_objects',0):,}</strong> native registry object IDs were inventoried. The dashboard distinguishes registry references, decoded live objects, and open-format rows.</p>
      <p><strong>{files.get('file_count',0):,}</strong> package files occupy {esc(human_size(files.get('total_bytes',0)))}. The dynamic tree below links to every available artifact.</p>
      <div class="boundary">Counts in the native registry are evidence of stored object references. They are not automatically converted geometry, seismic, logs, grids, or interpreted results.</div></div></div></section>
    <section id="crs"><h2>Coordinate reference system and units</h2><div class="crs-card"><div class="crs-value">{esc(crs_value)}</div>
      <p><strong>Status:</strong> {esc(crs_status)}</p><p>XY units: <strong>{esc(project.get('xy_units') or 'unknown')}</strong> · Depth: <strong>{esc(project.get('depth_units') or 'unknown')}</strong> · Time: <strong>{esc(project.get('time_units') or 'unknown')}</strong> · Velocity: <strong>{esc(project.get('velocity_units') or 'unknown')}</strong></p>
      <div class="boundary">{esc(crs_warning)}</div></div></section>
    <section id="maps"><h2>Spatial overview, base maps, and saved project images</h2>{map_controls}{render_spatial_svg(overview)}<p class="note">{esc(map_note)}</p>
      <h3>Saved images and screenshots</h3><div class="gallery">{gallery_html}</div>
      <p class="note">Images are embedded exactly as found in the copied project/package. They are visual evidence and are not used to infer coordinates, interpretation, or approval.</p></section>
    <section id="coverage"><h2>Extraction coverage and remaining boundaries</h2>{coverage_html}
      <p class="note">“Registry IDs” and “decoded/exported” are intentionally separate. Zero means the object class was not converted by a validated decoder in this package; it does not mean the source project lacks the data.</p></section>
    <section id="wells"><h2>Native well-head coordinates</h2>{head_table}<p class="note">Native Model.ptd X/Y values are retained. Trajectory-start Z is included only after X/Y matching and is not asserted to be a datum elevation.</p></section>
    <section id="native"><h2>Native project object inventory</h2>{native_table}
      <div class="cols"><div><h3>Package domains</h3>{html_table(["Domain folder","Files"], domain_rows)}</div><div><h3>Manifest summary</h3>{manifest_html}</div></div></section>
    <section id="files"><h2>Extracted data structure</h2><div class="tree-toolbar"><input id="tree-search" type="search" placeholder="Filter files, paths, object types, or validation status…">
      <button type="button" data-tree-action="expand">Expand all</button><button type="button" data-tree-action="collapse">Collapse all</button></div>
      <div id="file-tree" class="file-tree">{file_tree}</div><p class="note">Links are package-relative so the report remains portable when the complete extraction folder is moved.</p></section>
    <section id="qc"><h2>QC findings and evidence</h2><ul class="qc">{qc_items}</ul><h3>Principal evidence files</h3>{evidence_html}</section>
    """
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title><style>{style}</style></head><body>{header}<nav>{nav}</nav><main>{body}</main>'
        '<footer>Produced from copied/exported evidence only. Native-source files remained read-only; unsupported payloads remain explicit.</footer>'
        f'<script>{script}</script></body></html>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-GUI Petrel project audit report")
    parser.add_argument("--export-package", required=True, help="Export package root directory")
    parser.add_argument("--output-dir", default="", help="Output directory (default: <package>/07_workflows_reports/project_audit)")
    parser.add_argument("--title", default="", help="Report title (default: 'Petrel Project Audit - <project>')")
    args = parser.parse_args()

    package = Path(args.export_package)
    if not package.is_dir():
        print("SummaryJson:" + json.dumps({"status": "failed", "error": f"export package not found: {package}"}))
        return 1

    audit: dict = {
        "operation": "project_audit_report",
        "tool_version": TOOL_VERSION,
        "created_at_utc": utc_iso(),
        "export_package": str(package),
        "runtime_gui_used": False,
        "petrel_process_launched": False,
        "project": gather_project_summary(package),
        "manifest": gather_manifest(package),
        "wells": gather_wells(package),
        "well_tops": gather_well_tops(package),
        "surfaces": gather_surfaces(package),
        "seismic": gather_seismic(package),
        "native_semantic": gather_native_semantic(package),
        "domain_file_counts": gather_domain_files(package),
    }
    audit["native_inventory"] = gather_native_inventory(package)
    audit["file_inventory"] = gather_file_inventory(package)
    audit["media"] = gather_media(package)
    audit["spatial_overview"] = gather_spatial_overview(package, audit["wells"])
    audit["qc_flags"] = build_qc_flags(audit)

    title = args.title or f"Petrel Project Audit - {audit['project']['project_name'] or package.name}"
    output_dir = Path(args.output_dir) if args.output_dir else package / "07_workflows_reports" / "project_audit"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    html_path = output_dir / f"petrel_project_audit_{stamp}.html"
    json_path = output_dir / f"petrel_project_audit_{stamp}.json"

    try:
        audit["file_link_prefix"] = os.path.relpath(package, output_dir).replace("\\", "/")
    except ValueError:
        audit["file_link_prefix"] = package.as_uri()
    html_path.write_text(render_html(audit, title), encoding="utf-8")

    # Keep one stable, obvious entry point beside the extracted package folders.
    # Its file links are package-relative, so moving the complete folder preserves them.
    dashboard_path = package / "PROJECT_REPORT.html"
    audit["file_link_prefix"] = ""
    dashboard_path.write_text(render_html(audit, title), encoding="utf-8")

    # The machine-readable report retains inventories and summaries but does not duplicate
    # embedded image bytes or the browser-only decimated geometry payload.
    json_audit = dict(audit)
    json_audit["media"] = dict(audit["media"])
    json_audit["media"]["images"] = [
        {key: value for key, value in image.items() if key != "data_uri"}
        for image in audit["media"].get("images", [])
    ]
    json_audit["spatial_overview"] = {
        key: value
        for key, value in audit["spatial_overview"].items()
        if key not in {"polylines", "points", "well_heads"}
    }
    json_path.write_text(json.dumps(json_audit, indent=2), encoding="utf-8")

    warnings = sum(1 for flag in audit["qc_flags"] if flag["severity"] == "warning")
    summary = {
        "status": "passed",
        "dashboard_html": str(dashboard_path),
        "html_report": str(html_path),
        "json_report": str(json_path),
        "project_name": audit["project"]["project_name"],
        "petrel_version": audit["project"]["petrel_version"],
        "manifest_rows": audit["manifest"]["row_count"],
        "wells": audit["wells"]["well_count"] if audit["wells"]["available"] else 0,
        "well_top_picks": audit["well_tops"]["pick_count"] if audit["well_tops"]["available"] else 0,
        "surfaces_exported": audit["surfaces"]["summary"].get("exported", 0) if audit["surfaces"]["available"] else 0,
        "seismic_cubes": audit["seismic"]["cube_count"] if audit["seismic"]["available"] else 0,
        "qc_warnings": warnings,
        "qc_flags": len(audit["qc_flags"]),
        "runtime_gui_used": False,
        "petrel_process_launched": False,
    }
    print("Dashboard:" + str(dashboard_path))
    print("HTML:" + str(html_path))
    print("Report:" + str(json_path))
    print("SummaryJson:" + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
