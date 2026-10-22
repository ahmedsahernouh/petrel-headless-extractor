# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded native legend evidence, without inferred category or null mappings."""
import math

from petrel_native_binary import Node

NAMESPACE = 'http://www.slb.com/Petrel/2011/03/Serialization'
TABLE_PROFILE = [0, 2, 1, 2, 20, 0, 0, 1]
RESERVOIR_TABLE_PROFILE = [0, 2, 1, 2, 21, 0, 0, 1]
STYLE_PROFILE = [7, 0, 1]
MAX_ITEMS = 4096
MAX_LABEL_BYTES = 65536


def _profile(value, expected):
    return type(value) is type(expected) and value == expected and (
        not isinstance(value, list) or all(type(v) is int for v in value))


def supported_table_profile(value, *, allow_reservoir_profile=False):
    if type(allow_reservoir_profile) is not bool:
        raise ValueError('Reservoir legend profile opt-in must be Boolean')
    profiles = (TABLE_PROFILE, RESERVOIR_TABLE_PROFILE) if allow_reservoir_profile else (TABLE_PROFILE,)
    return any(_profile(value, p) for p in profiles)


def _scalar(node, name):
    field = node.child(name)
    if not isinstance(field, Node) or field.attrs or field.children or len(field.content) != 1:
        raise ValueError('Invalid discrete legend scalar: ' + name)
    return field.content[0]


def _label(item):
    if not isinstance(item, Node) or not _profile(item.attrs.get('Version'), 3):
        raise ValueError('Unsupported discrete legend item profile')
    if item.content:
        raise ValueError('Unexpected discrete legend item content')
    name = _scalar(item, 'name')
    level = _scalar(item, 'level')
    parent = _scalar(item, 'parent_item')
    if not isinstance(name, str) or len(name) > 1024:
        raise ValueError('Invalid discrete legend label')
    if type(level) not in (int, float) or not math.isfinite(level):
        raise ValueError('Invalid discrete legend level')
    if type(parent) is not int:
        raise ValueError('Invalid discrete legend parent item')
    return dict(label=name, raw_level=level, raw_parent_item=parent)


def category_legend(candidates, template_id, color_table_id, *, allow_reservoir_profile=False):
    """Record an exact default-template legend; do not assign codes to labels.

    min_discrete_code and item order are preserved separately. Neither implies
    null handling, effective local-display overrides, interval direction or a
    calibrated code lookup. Unsupported optional legend metadata cannot discard
    an otherwise independently supported numeric object.

    Profile21 is an explicit metadata-only opt-in for the observed reservoir
    records. Existing log callers retain the profile20 boundary by default.
    """
    result = dict(status='unresolved', template_id=template_id,
        color_table_id=color_table_id, link_scope='declared_template_default',
        code_mapping_status='unvalidated', source_locator=(
            'Model.ptd/DiscTemplateSubject['+str(template_id)+']/default_color_table_tag'))
    return _read_legend(candidates, color_table_id, result, allow_reservoir_profile)


def property_category_legend(candidates, property_id, color_table_id, reference_field):
    """Reservoir property reference metadata; no effective-palette precedence rule."""
    if reference_field not in ('color_table_subject', 'global_color_table_tag'):
        raise ValueError('Unsupported property colour-table reference field')
    result = dict(status='unresolved', property_id=property_id, color_table_id=color_table_id,
        link_scope='declared_property_reference', reference_field=reference_field,
        code_mapping_status='unvalidated', effective_palette_qualified=False,
        source_locator='Model.ptd/PropertySubject['+str(property_id)+']/'+reference_field)
    return _read_legend(candidates, color_table_id, result, True)


def _read_legend(candidates, color_table_id, result, allow_reservoir_profile):
    if not color_table_id:
        return dict(result, reason='No colour-table identity declared')
    if len(candidates) != 1:
        return dict(result, reason='Exact colour-table identity missing or ambiguous',
                    candidate_count=len(candidates))
    node = candidates[0]
    try:
        if (not isinstance(node, Node) or node.name != 'DiscColorTableSubject' or _scalar(node, 'unique_tag') != color_table_id
                or node.attrs.get('xmlns') != NAMESPACE or not supported_table_profile(node.attrs.get('Version'), allow_reservoir_profile=allow_reservoir_profile)):
            raise ValueError('Unsupported exact discrete colour-table profile')
        style = node.child('color_table_style')
        if (not isinstance(style, Node) or style.content or style.attrs.get('Type') != 'ColorTableStyle'
                or not _profile(style.attrs.get('Version'), STYLE_PROFILE)):
            raise ValueError('Unsupported colour-table style profile')
        items = style.child('color_items')
        if not isinstance(items, Node) or items.content:
            raise ValueError('Invalid discrete legend collection')
        size = items.attrs.get('Size')
        if type(size) is not int or not 0 <= size <= MAX_ITEMS or size != len(items.children):
            raise ValueError('Discrete legend count outside validated bound or mismatched')
        base = _scalar(style, 'min_discrete_code')
        if type(base) is not int:
            raise ValueError('Invalid minimum discrete code')
        labels = []
        for ordinal, item in enumerate(items.children):
            if not isinstance(item, Node) or item.name != 'color_item':
                raise ValueError('Unexpected discrete legend child')
            labels.append(dict(native_ordinal=ordinal, **_label(item)))
        undefined = _label(style.child('undef_item'))
        if sum(len(row['label'].encode('utf-8')) for row in labels + [undefined]) > MAX_LABEL_BYTES:
            raise ValueError('Discrete legend labels exceed bounded evidence size')
        return dict(result, status='recorded_native_legend', items=labels,
                    item_count=size, raw_min_discrete_code=base, undefined_item=undefined,
                    table_profile=node.attrs.get('Version'), style_profile=style.attrs.get('Version'),
                    table_locator='Model.ptd/DiscColorTableSubject['+color_table_id+']/color_table_style',
                    interpretation='Native labels and serialization order only; code mapping, local overrides and null semantics not established')
    except (ValueError, KeyError, TypeError) as exc:
        return dict(result, reason=str(exc))
