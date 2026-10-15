# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Typed metadata observations; no unit conversion, inheritance or scientific calibration."""
from __future__ import annotations

import copy
import json
import math

SCHEMA = 'geoviewer.metadata-evidence/1'
UNKNOWN = frozenset(('', 'unknown', 'unresolved', 'not_decoded', 'not_resolved',
                     'not_available', 'not_established', 'not established', 'unspecified'))
UNKNOWN_PREFIXES = ('unresolved;', 'unresolved:', 'not_resolved_', 'not_available_', 'not_decoded_')


def typed_key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def known(value):
    if value is None:
        return False
    if isinstance(value, str):
        token = value.strip().casefold()
        return token not in UNKNOWN and not token.startswith(UNKNOWN_PREFIXES)
    if type(value) in (int, float):
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if isinstance(value, (list, dict)):
        if not value:
            return False
        try:
            typed_key(value)
        except (ValueError, TypeError):
            return False
    return isinstance(value, (bool, list, dict))


def value_type(value):
    return {type(None): 'null', bool: 'boolean', int: 'integer', float: 'number',
            str: 'string', list: 'array', dict: 'object'}.get(type(value), 'unsupported')


def resolve_values(values):
    unique = {typed_key(v): v for v in values if known(v)}
    status = 'conflict' if len(unique) > 1 else 'recorded' if unique else 'unresolved'
    return dict(status=status, value=copy.deepcopy(next(iter(unique.values()))) if len(unique) == 1 else None,
                alternatives=copy.deepcopy(list(unique.values())) if len(unique) > 1 else [],
                unresolved_observations=sum(not known(v) for v in values),
                reason_code='CONFLICTING_METADATA_VALUES' if status == 'conflict' else
                            'METADATA_VALUE_UNRESOLVED' if status == 'unresolved' else None)


def single(values):
    return resolve_values(values)['value']


def status_is(fields, name, expected):
    """Agreeing repeated declarations are okay; a conflicting/unknown status is not."""
    values = fields.get(name, [])
    return bool(values) and all(type(v) is str and v == expected for v in values)


def annotate_rows(rows):
    """Add a parallel typed view, preserving raw values, locators and old resolution labels."""
    fields = {}
    for row in rows:
        fields.setdefault(row['entity_key'], {}).setdefault(row['field'], []).append(row['raw_value'])
    result = []
    for original in rows:
        if original.get('evidence_schema', SCHEMA) != SCHEMA:
            raise ValueError('Unsupported metadata evidence schema: ' + str(original.get('evidence_schema')))
        row = copy.deepcopy(original)
        raw, method = row['raw_value'], row.get('resolution', 'unspecified')
        available = known(raw)
        status, scope = _classification(row, fields, available)
        row.update(evidence_schema=SCHEMA, value_type=value_type(raw),
                   normalized_value=copy.deepcopy(raw) if available else None,
                   evidence_status=status, resolution_method=method,
                   scope=original.get('scope', scope),
                   normalization_scope='typed representation only; no semantic calibration, inheritance or conversion',
                   unresolved_reason=None if available else 'MISSING_PLACEHOLDER_OR_INVALID_METADATA_VALUE')
        result.append(row)
    return result


def _classification(row, fields, available):
    """Shared status rule for full annotations and low-copy summaries."""
    method = row.get('resolution', 'unspecified')
    status, scope = 'recorded_source_value', 'source_entity_observation'
    if not available:
        status = 'unresolved'
    elif method == 'index_declaration_not_independent_validation':
        status, scope = 'candidate', 'artifact_declaration_for_entity'
    elif method in ('inherited_project_setting', 'user_override', 'exact_correlated_companion',
                    'exact_native_record', 'validated_native_template_link', 'candidate'):
        status = method
    elif method.startswith('derived_'):
        status = 'derived_value'
    elif 'not_calibrated' in method:
        status = 'native_declaration'
    elif method == 'source_recorded' and row['field'] == 'unit':
        status = ('validated_native_profile' if status_is(fields[row['entity_key']],
                   'unit_status', 'validated_metric_profile') else 'native_declaration')
    elif method == 'source_recorded' and row['field'] == 'crs':
        status = ('validated_source_reference' if status_is(fields[row['entity_key']],
                   'crs_status', 'verified') else 'candidate')
    return status, scope


def summarize_rows(rows):
    """Resolve agreement/conflict within each exact entity+field, including legacy rows.

    Recorded agreement is not calibrated meaning. Unresolved observations remain
    counted. No object/project precedence or cross-field inheritance is inferred.
    """
    raw_fields = {}
    groups = {}
    for row in rows:
        if row.get('evidence_schema', SCHEMA) != SCHEMA:
            raise ValueError('Unsupported metadata evidence schema: ' + str(row.get('evidence_schema')))
        key, field = row['entity_key'], row['field']
        raw_fields.setdefault(key, {}).setdefault(field, []).append(row['raw_value'])
        groups.setdefault(key, {}).setdefault(field, []).append(row)
    result = {}
    for key, entity_groups in groups.items():
        result[key] = {}
        for field, observations in entity_groups.items():
            summary = resolve_values([r['raw_value'] for r in observations])
            summaries = [_classification(r, raw_fields, known(r['raw_value'])) for r in observations]
            summary.update(evidence_statuses=sorted({status for status, _ in summaries}),
                           locators=list(dict.fromkeys(r['locator'] for r in observations)),
                           scopes=list(dict.fromkeys(r.get('scope', scope) for r, (_, scope) in zip(observations, summaries))),
                           resolution_method='exact_entity_field_typed_agreement_no_precedence')
            result[key][field] = summary
    return result
