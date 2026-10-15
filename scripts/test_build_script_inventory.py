# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Validate declared runtime inputs without assembling or publishing a package."""
import ast
from pathlib import Path
import re
import unittest

import build_standalone_petrel_toolkit as build

SCRIPTS = Path(__file__).resolve().parent


class BuildScriptInventoryTests(unittest.TestCase):
    def test_explicit_script_list_contains_transitive_local_imports(self):
        listed = set(build.SCRIPTS)
        self.assertEqual(len(listed), len(build.SCRIPTS), 'Duplicate build input')
        missing = []
        for name in build.SCRIPTS:
            path = SCRIPTS/name
            self.assertTrue(path.is_file(), name)
            if path.suffix != '.py':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                imports = ([a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import)
                           else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module
                           else [])
                for module in imports:
                    dependency = module + '.py'
                    if (SCRIPTS/dependency).is_file() and dependency not in listed:
                        missing.append((name, dependency))
        self.assertEqual(missing, [], 'Listed scripts cannot import omitted local modules')

    def test_documented_contract_entrypoints_and_doctor_requirements_are_included(self):
        for entrypoint in ('geoviewer_contract.py', 'validate_geoviewer_contract.py',
                           'geoviewer_legacy_contract.py', 'geoviewer_contract_retention.py'):
            self.assertIn(entrypoint, build.SCRIPTS)
        doctor = (SCRIPTS/'doctor_portable_petrel_toolkit.ps1').read_text(encoding='utf-8-sig')
        required = re.search(r'\$required\s*=\s*@\((.*?)\)', doctor, re.S)
        self.assertIsNotNone(required)
        for name in re.findall(r'"([^"\n]+)"', required.group(1)):
            self.assertIn(name, build.SCRIPTS, 'Doctor requires a file omitted by the builder')


if __name__ == '__main__':
    unittest.main()
