# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only evidence from the observed PropertySubject metadata tree.

This module accepts an already parsed Node. It does not read native project files,
interpret expressions, decode NBFX time values, or qualify property semantics.
"""
from __future__ import annotations

import math
import re

from petrel_native_binary import Node


SCHEMA = "petrel_property_history_evidence_v1"
ROOT_VERSION = [23, 3, 6, 14, 1, 2, 21, 0, 0, 1]
XMLNS = "http://www.slb.com/Petrel/2011/03/Serialization"
HISTORY_FIELDS = frozenset(("user_name", "operation", "arguments", "begin_time",
                            "end_time", "app_version", "build_date", "is_editable"))
MAX_ENTRIES = 4096
MAX_HISTORY_UTF8 = 1024 * 1024
MAX_ARGUMENT_UTF8 = 65536
MAX_OTHER_UTF8 = 4096
_HEX16 = re.compile(r"[0-9a-fA-F]{16}\Z")


class _Unresolved(ValueError):
    pass


def _one(parent: Node, name: str) -> Node | None:
    found = [item for item in parent.children if isinstance(item, Node) and item.name == name]
    if len(found) > 1:
        raise _Unresolved(f"duplicate_{name}")
    return found[0] if found else None


def _scalar(node: Node, name: str, kind: str, *, limit: int = 0):
    if (node.name != name or node.attrs or node.children or len(node.content) != 1):
        raise _Unresolved(f"invalid_{name}")
    value = node.content[0]
    if kind == "string":
        if type(value) is not str or len(value.encode("utf-8")) > limit:
            raise _Unresolved(f"invalid_{name}")
    elif kind == "integer":
        if type(value) is not int:
            raise _Unresolved(f"invalid_{name}")
    elif kind == "boolean":
        if type(value) is not bool:
            raise _Unresolved(f"invalid_{name}")
    elif kind == "number":
        if type(value) not in (int, float) or not math.isfinite(value):
            raise _Unresolved(f"invalid_{name}")
    elif kind == "raw_time":
        if (type(value) is not dict or set(value) != {"nbfx_type", "raw_hex"}
                or type(value["nbfx_type"]) is not int or value["nbfx_type"] != 150
                or type(value["raw_hex"]) is not str
                or _HEX16.fullmatch(value["raw_hex"]) is None):
            raise _Unresolved(f"invalid_{name}")
        return dict(value)
    else:
        raise AssertionError(kind)
    return value


def _cached_statistics(root: Node, pid: str) -> dict:
    locator = f"Model.ptd/PropertySubject[{pid}]"
    try:
        fields = {}
        for name in ("min", "max", "min_gt0"):
            item = _one(root, name)
            if item is None:
                return {"status": "not_recorded", "reason": f"missing_{name}",
                        "source_locator": locator}
            fields[name] = _scalar(item, name, "number")
        return {"status": "recorded_cached_statistics", "source_locator": locator,
                **fields}
    except _Unresolved as exc:
        return {"status": "unresolved", "reason": str(exc), "source_locator": locator}


def _history(root: Node, pid: str) -> dict:
    locator = f"Model.ptd/PropertySubject[{pid}]/history"
    try:
        history = _one(root, "history")
        if history is None:
            return {"status": "not_recorded", "reason": "missing_history",
                    "source_locator": locator}
        size = history.attrs.get("Size")
        if (set(history.attrs) != {"Size"} or type(size) is not int
                or not 0 <= size <= MAX_ENTRIES or size != len(history.children)
                or history.content):
            raise _Unresolved("invalid_history_size_or_framing")
        entries = []
        # Include a conservative per-record allowance for locators, numeric fields,
        # typed times, and JSON keys as well as the bounded serialized strings.
        total_bytes = 0
        for ordinal, entry in enumerate(history.children):
            total_bytes += 256
            if total_bytes > MAX_HISTORY_UTF8:
                raise _Unresolved("history_total_exceeds_limit")
            if (not isinstance(entry, Node) or entry.name != "entry"
                    or entry.attrs != {"Version": 3} or type(entry.attrs.get("Version")) is not int
                    or entry.content or len(entry.children) != len(HISTORY_FIELDS)):
                raise _Unresolved("unsupported_history_entry_profile")
            by_name = {}
            for child in entry.children:
                if not isinstance(child, Node) or child.name not in HISTORY_FIELDS or child.name in by_name:
                    raise _Unresolved("invalid_history_entry_fields")
                by_name[child.name] = child
            if set(by_name) != HISTORY_FIELDS:
                raise _Unresolved("invalid_history_entry_fields")
            values = {}
            for name in ("user_name", "operation", "arguments", "build_date"):
                limit = MAX_ARGUMENT_UTF8 if name == "arguments" else MAX_OTHER_UTF8
                values[name] = _scalar(by_name[name], name, "string", limit=limit)
                total_bytes += len(values[name].encode("utf-8"))
                if total_bytes > MAX_HISTORY_UTF8:
                    raise _Unresolved("history_total_exceeds_limit")
            for name in ("begin_time", "end_time"):
                values[name] = _scalar(by_name[name], name, "raw_time")
            version = by_name["app_version"]
            if (version.attrs or version.content or len(version.children) != 2):
                raise _Unresolved("invalid_app_version")
            app_fields = {}
            for child in version.children:
                if (not isinstance(child, Node) or child.name not in ("version", "flags")
                        or child.name in app_fields):
                    raise _Unresolved("invalid_app_version")
                app_fields[child.name] = _scalar(child, child.name, "integer")
            if set(app_fields) != {"version", "flags"}:
                raise _Unresolved("invalid_app_version")
            values["app_version"] = app_fields
            values["is_editable"] = _scalar(by_name["is_editable"], "is_editable", "boolean")
            values["entry_ordinal"] = ordinal
            values["source_locator"] = f"{locator}/entry[{ordinal}]"
            entries.append(values)
        return {"status": "recorded_native_history", "source_locator": locator,
                "entry_count": size, "entries": entries}
    except _Unresolved as exc:
        return {"status": "unresolved", "reason": str(exc), "source_locator": locator}


def property_history_evidence(node: Node, pid: str) -> dict:
    """Return bounded source metadata only; never admit generic property semantics."""
    base = {"schema": SCHEMA, "source_metadata_only": True,
            "qualified_generic_property_semantics": False,
            "qualified_null_semantics": False,
            "qualified_model_semantics": False}
    if (not isinstance(node, Node) or node.name != "PropertySubject"
            or set(node.attrs) != {"xmlns", "Version"}
            or node.attrs.get("xmlns") != XMLNS
            or type(node.attrs.get("Version")) is not list
            or len(node.attrs["Version"]) != len(ROOT_VERSION)
            or any(type(x) is not int for x in node.attrs["Version"])
            or node.attrs["Version"] != ROOT_VERSION or node.content
            or type(pid) is not str or not pid):
        unresolved = {"status": "unresolved", "reason": "unsupported_property_subject_profile"}
        return {**base, "cached_statistics": dict(unresolved), "history": dict(unresolved)}
    return {**base, "cached_statistics": _cached_statistics(node, pid),
            "history": _history(node, pid)}
