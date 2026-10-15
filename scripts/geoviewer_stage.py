# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Record isolated category outcomes; stop only for demonstrated shared failures.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import argparse
from collections import Counter
import errno
import json
from pathlib import Path
from geoviewer_diagnostics import event
from geoviewer_io import atomic_json, output_probe


_TERMINAL_OBJECT_STATUSES = {'decoded', 'empty_supported_object', 'missing_metadata',
                             'conversion_failed', 'unsupported_layout', 'write_failed',
                             'finalization_failed'}


def _count_evidence(value, expected):
    if not isinstance(value, dict) or not value:
        return False
    counts = list(value.values())
    return (all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in counts)
            and sum(counts) == expected)


def _terminal_partial_receipt(report):
    """Return true only when a native partial receipt proves terminal object outcomes."""
    if (not isinstance(report, dict) or report.get('status') != 'partial' or
            report.get('has_gaps') is not True or report.get('source_unchanged') is not True):
        return False
    objects = report.get('objects')
    if not isinstance(objects, list) or not objects:
        return False
    statuses = []
    object_types = []
    object_ids = []
    for item in objects:
        if (not isinstance(item, dict) or not isinstance(item.get('status'), str) or
                item['status'] not in _TERMINAL_OBJECT_STATUSES or
                not isinstance(item.get('blob_type'), str) or not item['blob_type'] or
                not isinstance(item.get('object_id'), str) or not item['object_id']):
            return False
        statuses.append(item['status'])
        object_types.append(item['blob_type'])
        object_ids.append(item['object_id'])
    if len(set(object_ids)) != len(object_ids):
        return False
    observed = report.get('object_status_counts')
    selected = report.get('object_type_counts')
    if not _count_evidence(observed, len(objects)) or not _count_evidence(selected, len(objects)):
        return False
    return (Counter(observed) == Counter(statuses) and Counter(selected) == Counter(object_types))


def _gap_reasons(report, object_status_counts):
    """Keep semantic gaps distinct from failed conversions and preserve good exports."""
    reasons = []
    for status, count in object_status_counts.items():
        if count and status not in ('decoded', 'empty_supported_object'):
            reasons.append(f'{count} object(s): {status}')
    heads = report.get('model_well_head_decode')
    if isinstance(heads, dict) and heads.get('status') in ('failed_closed', 'decoded_with_failed_closed_subjects'):
        reasons.append('Native well-head metadata ' + heads['status'] + ': ' +
                       str(heads.get('reason') or heads.get('error') or 'unsupported Model.ptd layout'))
    coverage = report.get('coverage')
    if isinstance(coverage, dict) and coverage.get('status') in ('partial', 'failed_closed'):
        reasons.append('Spatial coverage: ' + str(coverage.get('reason') or coverage['status']))
    if not reasons and (report.get('has_gaps') or report.get('status') == 'partial'):
        reasons.append('Category reports unresolved object or export gaps; see object diagnostics')
    return reasons


def record(package, category, code, report_path=None):
    package = Path(package)
    path = package/'01_project_metadata/pipeline_stages.json'
    stages = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
    report = {}
    if report_path and Path(report_path).is_file():
        try: report = json.loads(Path(report_path).read_text(encoding='utf-8-sig'))
        except (ValueError,OSError) as exc: report = {'reason':'Unreadable category receipt: '+str(exc)}
    if not isinstance(report, dict):
        report = {'reason':'Malformed category receipt: expected a JSON object'}
    error = report.get('error') or {}
    if not isinstance(error,dict): error = {}
    object_status_counts = report.get('object_status_counts', {})
    if not isinstance(object_status_counts, dict): object_status_counts = {}
    fatal = (report.get('source_unchanged') is False or error.get('errno') in (errno.ENOSPC,errno.EROFS,errno.ENOMEM)
             or error.get('winerror') in (8,14,21,39,112))
    gap_reasons = _gap_reasons(report, object_status_counts)
    gaps = bool(code or gap_reasons or report.get('has_gaps') or
                report.get('status') in ('failed','unsupported_layout','not_available','partial'))
    if code:
        try: output_probe(package)
        except OSError as exc:
            fatal=True; error.update(message=str(exc),operation='output_health_probe')
    terminal_partial = bool(code == 10 and not fatal and _terminal_partial_receipt(report))
    row=dict(category=category,exit_code=code,status='failed' if fatal else 'partial' if gaps else 'completed',
             fatal=fatal,reason=report.get('reason') or error.get('message') or
             ('Shared integrity or output failure; category stopped' if fatal else
              'Category completed with object-level gaps; ' + '; '.join(gap_reasons) if terminal_partial else
              'Category completion could not be verified; independent categories will continue' if code else
              '; '.join(gap_reasons) if gap_reasons else None),
             report_path=str(report_path) if report_path else None,error=error,
             object_status_counts=object_status_counts, gap_reasons=gap_reasons)
    stages.append(row); atomic_json(path,stages)
    event('category_outcome',severity='error' if fatal else 'warning' if gaps else 'info',**row)
    if gaps: print(category+': '+str(row['reason'] or 'Some objects were not recovered; see object diagnostics'),flush=True)
    return 1 if fatal else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',required=True,type=Path)
    parser.add_argument('--category',required=True)
    parser.add_argument('--code',required=True,type=int)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    raise SystemExit(record(args.package,args.category,args.code,args.report))
