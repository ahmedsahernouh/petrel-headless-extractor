# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded native legend evidence, without inferred category or null mappings."""
import math

from petrel_native_binary import Node

NAMESPACE = 'http://www.slb.com/Petrel/2011/03/Serialization'
TABLE_PROFILE = [0, 2, 1, 2, 20, 0, 0, 1]
STYLE_PROFILE = [7, 0, 1]
MAX_ITEMS = 4096
MAX_LABEL_BYTES = 65536


def _label(item):
    if not isinstance(item, Node) or item.attrs.get('Version') != 3:
        raise ValueError('Unsupported discrete legend item profile')
    name = item.get('name')
    level = item.get('level')
    parent = item.get('parent_item')
    if not isinstance(name, str) or len(name) > 1024:
        raise ValueError('Invalid discrete legend label')
    if type(level) not in (int, float) or not math.isfinite(level):
        raise ValueError('Invalid discrete legend level')
    if type(parent) is not int:
        raise ValueError('Invalid discrete legend parent item')
    return dict(label=name, raw_level=level, raw_parent_item=parent)


def category_legend(candidates, template_id, color_table_id):
    """Record an exact default-template legend; do not assign codes to labels.

    min_discrete_code and item order are preserved separately. Neither implies
    null handling, effective local-display overrides, interval direction or a
    calibrated code lookup. Unsupported optional legend metadata cannot discard
    an otherwise independently supported numeric object.
    """
    result = dict(status='unresolved', template_id=template_id,
        color_table_id=color_table_id, link_scope='declared_template_default',
        code_mapping_status='unvalidated', source_locator=(
            'Model.ptd/DiscTemplateSubject['+str(template_id)+']/default_color_table_tag'))
    if not color_table_id:
        return dict(result, reason='No default colour-table identity declared')
    if len(candidates) != 1:
        return dict(result, reason='Exact colour-table identity missing or ambiguous',
                    candidate_count=len(candidates))
    node = candidates[0]
    try:
        if (node.name != 'DiscColorTableSubject' or node.get('unique_tag') != color_table_id
                or node.attrs.get('xmlns') != NAMESPACE or node.attrs.get('Version') != TABLE_PROFILE):
            raise ValueError('Unsupported exact discrete colour-table profile')
        style = node.child('color_table_style')
        if style.attrs.get('Type') != 'ColorTableStyle' or style.attrs.get('Version') != STYLE_PROFILE:
            raise ValueError('Unsupported colour-table style profile')
        items = style.child('color_items')
        size = items.attrs.get('Size')
        if type(size) is not int or not 0 <= size <= MAX_ITEMS or size != len(items.children):
            raise ValueError('Discrete legend count outside validated bound or mismatched')
        base = style.get('min_discrete_code')
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
