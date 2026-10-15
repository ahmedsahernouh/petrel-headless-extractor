# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only traversal that prunes identified extractor installations before descent.
Website: https://saherlabs.dev/
"""
import os
from pathlib import Path


def is_toolkit(path):
    path=Path(path)
    return ((path/'STANDALONE.txt').is_file() and (path/'toolkit.json').is_file()
            and (path/'scripts/repair_standalone_dependencies.ps1').is_file())


def project_files(root, excluded=None, *, onerror=None, exclude_roots=()):
    excluded_roots={Path(p).resolve() for p in exclude_roots}
    for base, directories, files in os.walk(root,followlinks=False,onerror=onerror):
        pruned=[d for d in directories if (Path(base)/d).resolve() in excluded_roots or is_toolkit(Path(base)/d)]
        if excluded is not None:excluded.extend(Path(base)/d for d in pruned)
        directories[:]=[d for d in directories if d not in pruned]
        for name in files:yield Path(base)/name
