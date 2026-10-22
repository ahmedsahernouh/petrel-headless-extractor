# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only UUID links for Petrel reservoir property metadata; no value semantics."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_native_binary as binary
from petrel_native_log_evidence import category_legend, property_category_legend, supported_table_profile
import petrel_property_history as history_evidence

SCHEMA = "fieldviewer.reservoir-links/1"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DECODERS = (Path(binary.__file__), Path(sys.modules[category_legend.__module__].__file__), Path(history_evidence.__file__), Path(__file__))


def exact_uuid(value):
    if not isinstance(value, str) or not UUID_RE.fullmatch(value) or str(uuid.UUID(value)) != value:
        raise ValueError(f"Expected canonical lowercase UUID: {value!r}")
    return value


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _rows(property_ids):
    result = []
    for row in property_ids:
        if isinstance(row, str):
            result.append({"object_id": exact_uuid(row), "index_label": None})
        elif isinstance(row, dict):
            label = row.get("source_label", row.get("source_name", row.get("index_label", row.get("name"))))
            result.append({"object_id": exact_uuid(row.get("object_id")), "index_label": label})
        else:
            raise ValueError("Property index rows must be objects or UUID strings")
    if not result or len(result) > 100_000:
        raise ValueError("Property index must contain 1..100000 rows")
    return result


def _stub_map(stubs):
    found = defaultdict(list)
    for stub in stubs:
        tag = stub.get("unique_tag")
        if isinstance(tag, str) and UUID_RE.fullmatch(tag):
            found[tag].append(stub)
    return found


def _model_map(documents):
    found = defaultdict(list)
    kinds = Counter()
    for index, node in enumerate(documents):
        kinds[node.name] += 1
        tag = node.get("unique_tag")
        if isinstance(tag, str) and UUID_RE.fullmatch(tag):
            found[tag].append((index, node))
    return found, dict(sorted(kinds.items()))


def _identity(index_node):
    index, node = index_node
    return {"document_index": index, "kind": node.name,
            "version": node.attrs.get("Version"), "unique_tag": node.get("unique_tag")}


def _template_fields(node):
    names = ("standard_measurement", "initial_unit", "unit_is_customized",
             "unit_measurement_is_customized", "is_predefined", "property_type",
             "use_local_color_table", "color_table_subject", "global_color_table_tag")
    return {name: node.get(name, None) for name in names}


def _property_palettes(pid, declarations, model_map, stub_map):
    refs = []
    for field in ('color_table_subject', 'global_color_table_tag'):
        raw = declarations[field]
        ref = dict(reference_field=field, uuid=raw, status='unresolved',
                   source_locator=f'Model.ptd/PropertySubject[{pid}]/{field}',
                   code_mapping_status='unvalidated', effective_palette_qualified=False)
        refs.append(ref)
        if raw in ('', None):
            ref['status'] = 'not_declared'; continue
        try:
            cid = exact_uuid(raw)
        except ValueError:
            ref['status'] = 'invalid_reference'; continue
        records = model_map[cid]; stubs = stub_map[cid]
        ref.update(record_candidates=len(records), stub_candidates=len(stubs))
        if len(records) != 1 or len(stubs) != 1:
            ref['status'] = 'ambiguous_identity' if len(records) > 1 or len(stubs) > 1 else 'missing_identity'
            continue
        ref.update(record=_identity(records[0]), project_stub_name=stubs[0].get('name'))
        table = records[0][1]
        if table.name != 'DiscColorTableSubject' or stubs[0].get('kind') != 'DiscColorTableSubject':
            ref['status'] = 'class_mismatch'; continue
        legend = property_category_legend([table], pid, cid, field)
        ref.update(status='linked_record', legend=legend,
                   legend_status=legend['status'] if supported_table_profile(table.attrs.get('Version'), allow_reservoir_profile=True) else 'unsupported_record_profile')
    return refs


def link_properties(stubs, model_documents, property_ids):
    """Link exact property/template/table identities. Inputs are dictionaries and NBFX Nodes.

    `stubs` have `kind`, `unique_tag`, `name`; `model_documents` is an iterable of
    parsed Model.ptd Node records in source order; property_ids is an iterable
    of canonical UUID strings or index rows with object_id and source_label.
    The result never qualifies sample units, category codes, nulls, or a model.
    """
    requested = _rows(property_ids)
    index_counts = Counter(row["object_id"] for row in requested)
    stub_map = _stub_map(stubs)
    model_map, kinds = _model_map(model_documents)
    output = []
    for request in requested:
        pid = request["object_id"]
        row = {"property_uuid": pid, "index_label": request["index_label"],
               "native_property_name": None, "project_stub_name": None, "status": "unresolved",
               "property_stub_candidates": len(stub_map[pid]),
               "property_record_candidates": len(model_map[pid]),
               "template": None, "default_color_table": None,
               "stored_units_qualified": False, "category_code_mapping_qualified": False,
               "native_null_semantics_qualified": False, "complete_model_qualified": False}
        if index_counts[pid] != 1:
            row.update(status="ambiguous_index_uuid", index_occurrences=index_counts[pid])
            output.append(row); continue
        pstubs = [s for s in stub_map[pid] if s.get("kind") == "PropertySubject"]
        pnodes = [pair for pair in model_map[pid] if pair[1].name == "PropertySubject"]
        if len(stub_map[pid]) != 1 or len(model_map[pid]) != 1 or len(pstubs) != 1 or len(pnodes) != 1:
            row["status"] = ("ambiguous_property_identity" if len(stub_map[pid]) > 1 or len(model_map[pid]) > 1
                             else "missing_or_wrong_property_identity")
            output.append(row); continue
        row["project_stub_name"] = pstubs[0].get("name")
        property_doc = pnodes[0][1]
        row["native_property_name"] = property_doc.get("name", None)
        row["property_record"] = _identity(pnodes[0])
        row['native_property_evidence'] = history_evidence.property_history_evidence(property_doc, pid)
        row['property_color_table_declarations'] = {name: property_doc.get(name, None) for name in
            ('use_local_color_table', 'color_table_subject', 'global_color_table_tag')}
        raw = property_doc.get("template", None)
        style = property_doc.child("make_property_style", required=False)
        style_raw = style.get("template_tag", None) if style else None
        row["root_template_value"] = raw
        row["style_template_value"] = style_raw
        if raw != style_raw:
            row["status"] = "property_template_fields_disagree"
            output.append(row); continue
        if type(raw) is int and raw == 0:
            zero_stubs = [s for s in stubs if s.get("unique_tag") == 0 and s.get("kind") == "ContTemplateSubject"]
            row["template"] = {"status": "unresolved_zero_template", "zero_stub_count": len(zero_stubs),
                               "zero_stub_names": sorted({str(s.get("name")) for s in zero_stubs})}
            row["status"] = "unresolved_zero_template"
            output.append(row); continue
        try:
            tid = exact_uuid(raw)
        except ValueError:
            row["status"] = "invalid_template_reference"
            output.append(row); continue
        tnodes = model_map[tid]
        tstubs = stub_map[tid]
        row["template"] = {"uuid": tid, "record_candidates": len(tnodes),
                           "stub_candidates": len(tstubs), "status": "unresolved"}
        if len(tnodes) != 1 or len(tstubs) != 1:
            row["template"]["status"] = "ambiguous_identity" if len(tnodes) > 1 or len(tstubs) > 1 else "missing_identity"
            row["status"] = "ambiguous_template" if row["template"]["status"] == "ambiguous_identity" else "missing_template"
            output.append(row); continue
        tpair = tnodes[0]
        tnode = tpair[1]
        if tnode.name not in ("ContTemplateSubject", "DiscTemplateSubject") or tstubs[0].get("kind") != tnode.name:
            row["status"] = "template_class_mismatch"
            row["template"]["status"] = "class_mismatch"
            output.append(row); continue
        row["template"].update(status="linked", record=_identity(tpair),
                               native_template_name=tstubs[0].get("name"),
                               declarations=_template_fields(tnode),
                               declaration_locator=f"Model.ptd/{tnode.name}[{tid}]")
        row["status"] = "linked_metadata_only"
        if tnode.name == "DiscTemplateSubject":
            row['property_palette_references'] = _property_palettes(pid, row['property_color_table_declarations'], model_map, stub_map)
            color_id = tnode.get("default_color_table_tag", "")
            table = {"uuid": color_id, "source_locator": f"Model.ptd/DiscTemplateSubject[{tid}]/default_color_table_tag",
                     "status": "unresolved", "code_mapping_status": "unvalidated"}
            row["default_color_table"] = table
            try:
                color_id = exact_uuid(color_id)
            except ValueError:
                table["status"] = "missing_or_invalid_reference"
                output.append(row); continue
            candidates = model_map[color_id]
            table_stubs = stub_map[color_id]
            table["record_candidates"] = len(candidates)
            table["stub_candidates"] = len(table_stubs)
            if len(candidates) != 1 or len(table_stubs) != 1:
                table["status"] = ("ambiguous_identity" if len(candidates) > 1 or len(table_stubs) > 1
                                   else "missing_identity")
                output.append(row); continue
            table["record"] = _identity(candidates[0])
            table["project_stub_name"] = table_stubs[0].get("name")
            if candidates[0][1].name != "DiscColorTableSubject" or table_stubs[0].get("kind") != "DiscColorTableSubject":
                table["status"] = "class_mismatch"
                output.append(row); continue
            table["status"] = "linked_record"
            legend = category_legend([candidates[0][1]], tid, color_id, allow_reservoir_profile=True)
            table["legend"] = legend
            if legend["status"] != "recorded_native_legend" and not supported_table_profile(candidates[0][1].attrs.get("Version"), allow_reservoir_profile=True):
                table["legend_status"] = "unsupported_record_profile"
            else:
                table["legend_status"] = legend["status"]
        output.append(row)
    return {"schema": SCHEMA, "scope": "metadata_links_only", "property_count": len(output),
            "model_document_count": sum(kinds.values()), "model_document_kinds": kinds,
            "properties": output, "stored_units_qualified": False,
            "category_code_mapping_qualified": False, "native_null_semantics_qualified": False,
            "complete_model_qualified": False}


def project_stubs(project_node):
    """Read class stubs from one parsed .pet project document."""
    found = []
    for entry in project_node.child("class_infos").child("map").children:
        if not isinstance(entry, binary.Node):
            raise ValueError("Non-node project class entry")
        kind = entry.get("key")
        stack = [entry.child("value")]
        while stack:
            node = stack.pop()
            if node.attrs.get("Type") == "SubjectStub":
                found.append({"kind": kind, "unique_tag": node.get("uniqueTag"),
                              "name": node.get("visualName"), "parent": node.get("parentTag")})
            stack.extend(c for c in node.children if isinstance(c, binary.Node))
    return found


def _safe_paths(project, output, index):
    project = Path(project).absolute()
    output = Path(output).absolute()
    index = Path(index).absolute()
    if project.suffix.lower() != ".pet" or not project.is_file() or not index.is_file():
        raise ValueError("Expected exact .pet file and property index file")
    model = project.with_suffix(".ptd") / "Model.ptd"
    if not model.is_file():
        raise ValueError("Matching Model.ptd missing")
    for path in (project, model, index, output):
        for part in (path, *path.parents):
            if part.is_symlink() or (part.exists() and getattr(part, "is_junction", lambda: False)()):
                raise ValueError(f"Reparse point refused: {part}")
    # Resolve only after checking each spelling for reparse points. The resolved
    # paths normalize '..' before containment checks; Path.absolute() does not.
    project = project.resolve(strict=True)
    model = model.resolve(strict=True)
    index = index.resolve(strict=True)
    output = output.resolve(strict=False)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Output must be a new directory under an existing parent")
    if output == project.parent or project.parent in output.parents:
        raise ValueError("Output is inside native source directory")
    return project, model, index, output


def _html(report):
    rows = []
    legends = []
    histories = []
    for p in report["properties"]:
        t = p.get("template") or {}
        d = t.get("declarations") or {}
        cells = (p["property_uuid"], p.get("native_property_name"), p.get("project_stub_name"), p.get("index_label"),
                 p["status"], t.get("uuid"), t.get("status"), d.get("standard_measurement"),
                 d.get("initial_unit"), (p.get("default_color_table") or {}).get("status"))
        rows.append("<tr>" + "".join("<td>" + escape(str(v if v is not None else "")) + "</td>" for v in cells) + "</tr>")
        evidence = p.get('native_property_evidence') or {}
        cached = evidence.get('cached_statistics') or {}
        history = evidence.get('history') or {}
        entry_rows = ''.join('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in
            (entry.get('entry_ordinal'), entry.get('operation'), entry.get('arguments'), entry.get('source_locator'))) + '</tr>'
            for entry in history.get('entries', []))
        histories.append('<details><summary>' + escape(str(p.get('native_property_name') or p['property_uuid']))
            + ' — native cached statistics and calculation history</summary><p>Cached statistics: '
            + escape(json.dumps(cached, ensure_ascii=False)) + '</p><p>History status: '
            + escape(str(history.get('status', 'not_recorded')))
            + '. Entries retain serialized order and source arguments; their presence does not establish the calculation behind current values. Expressions are never executed.</p>'
            + '<table><tr><th>Serialized ordinal</th><th>Operation</th><th>Native arguments</th><th>Source locator</th></tr>'
            + entry_rows + '</table></details>')
        refs = []
        if p.get('default_color_table'):
            refs.append(('Declared template default', p['default_color_table']))
        refs.extend((r['reference_field'], r) for r in p.get('property_palette_references', []))
        if refs:
            sections = []
            for role, ref in refs:
                legend = ref.get('legend') or {}
                items = ''.join('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in
                    (item['native_ordinal'], item['label'], item['raw_level'], item['raw_parent_item'])) + '</tr>'
                    for item in legend.get('items', []))
                sections.append('<h3>' + escape(role) + '</h3><p>Table ' + escape(str(ref.get('uuid', '')))
                    + ' · ' + escape(str(ref.get('legend_status', ref.get('status', ''))))
                    + ' · raw minimum discrete code: ' + escape(str(legend.get('raw_min_discrete_code', 'unavailable')))
                    + '</p><table><tr><th>Native ordinal</th><th>Native label</th><th>Raw level</th><th>Raw parent item</th></tr>' + items + '</table>')
            local = (p.get('property_color_table_declarations') or {}).get('use_local_color_table')
            legends.append('<details><summary>' + escape(str(p.get('native_property_name') or p['property_uuid']))
                + ' — native legend references</summary><p>Declared use_local_color_table: ' + escape(str(local))
                + '. References remain separate; effective display precedence, code-to-label mapping and null meaning are unvalidated.</p>'
                + ''.join(sections) + '</details>')
    header = ("UUID", "Model name", "Project stub name", "Index label", "Link status", "Template UUID",
              "Template status", "Declared measurement", "Declared initial unit", "Default table")
    return ("<!doctype html><meta charset='utf-8'><title>Reservoir metadata links</title>"
            "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.4rem;text-align:left}tr:nth-child(even){background:#f6f6f6}</style>"
            "<h1>Reservoir metadata links</h1><p>Read-only metadata scope. Template units are declarations; stored units, category code mapping, native nulls, and complete model qualification remain unresolved.</p>"
            "<table><thead><tr>" + "".join("<th>" + escape(h) + "</th>" for h in header)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table><h2>Recorded native legend metadata</h2>"
            + "<p>Blank and duplicate labels are preserved. Ordinals are serialized positions, not assigned sample codes.</p>"
            + ''.join(legends) + '<h2>Native cached statistics and recorded history</h2>'
            + '<p>Cached ranges are source declarations and are insufficient to infer nulls. Template labels do not establish stored units. No automatic null or unit conversion is applied.</p>'
            + ''.join(histories))


def audit_project(project, output, property_ids):
    """Inspect exact .pet + matching Model.ptd and create a new JSON/HTML receipt."""
    project, model, index, output = _safe_paths(project, output, property_ids)
    inputs = {"pet": project, "model": model, "property_index": index}
    decoders = {p.name: p for p in DECODERS}
    before = {key: sha(path) for key, path in inputs.items()}
    decoder_before = {key: sha(path) for key, path in decoders.items()}
    rows = _rows(json.loads(index.read_text(encoding="utf-8")))
    output.mkdir(exist_ok=False)
    try:
        projects = list(binary.read_documents(binary.project_payload(binary.read_bounded(project))))
        if len(projects) != 1:
            raise ValueError("Expected exactly one .pet project document")
        stubs = project_stubs(projects[0])
        documents = list(binary.read_documents(binary.decompress(binary.read_bounded(model))))
        result = link_properties(stubs, documents, rows)
        after = {key: sha(path) for key, path in inputs.items()}
        decoder_after = {key: sha(path) for key, path in decoders.items()}
        if before != after or decoder_before != decoder_after:
            raise ValueError("Source or decoder hash changed during audit")
        result.update(status="verified_metadata_audit", verified_utc=datetime.now(timezone.utc).isoformat(),
                      inputs={key: str(path) for key, path in inputs.items()},
                      sha256_before=before, sha256_after=after,
                      decoder_sha256_before=decoder_before, decoder_sha256_after=decoder_after,
                      project_document_count=len(projects), project_stub_count=len(stubs))
        (output / "REPORT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (output / "REPORT.html").write_text(_html(result), encoding="utf-8")
        return result
    except Exception as exc:
        (output / "FAILURE.json").write_text(json.dumps({"schema": SCHEMA, "status": "failed",
            "error": str(exc), "inputs": {key: str(path) for key, path in inputs.items()},
            "sha256_before": before, "decoder_sha256_before": decoder_before}, indent=2), encoding="utf-8")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--property-index", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = audit_project(args.project, args.output, args.property_index)
    except Exception as exc:
        print(json.dumps({"schema": SCHEMA, "status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"schema": SCHEMA, "status": result["status"], "property_count": result["property_count"],
                      "linked_metadata_count": sum(p["status"] == "linked_metadata_only" for p in result["properties"]),
                      "output": str(Path(args.output).absolute()), "complete_model_qualified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
