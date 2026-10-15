# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Validate completed seismic receipts and generated artifacts for delivery.

Live callers check receipt binding, path and size. Final callers additionally
hash declared output digests once per physical file. Source ZGY is never read.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from contextlib import nullcontext


_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')
_USEFUL = {'.segy', '.json'}


def seismic_invalidations(index):
    """Preserve exact quarantined identities; never treat malformed state as empty."""
    rows = index.get('invalid_seismic', [])
    if not isinstance(rows, list):
        raise ValueError('invalid_seismic must be a list')
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'object_id', 'receipt_signature'}:
            raise ValueError('Invalid seismic quarantine record')
        identity, signature = row['object_id'], row['receipt_signature']
        if (not isinstance(identity, str) or not identity.strip() or identity in seen
                or not isinstance(signature, str) or not _SHA256.fullmatch(signature)):
            raise ValueError('Invalid or duplicate seismic quarantine identity/binding')
        seen.add(identity)
        result.append(dict(object_id=identity, receipt_signature=signature))
    return result


def _unlinked(path, stop):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('linked seismic delivery path: '+str(item))
        if item == stop:
            break
    return path


def validate_seismic_rows(inventory, run, *, fresh_hash=False):
    """Return accepted candidate plans and explicit per-object findings.

    A supplied inventory is a complete snapshot. ``None`` is reserved by the
    live publisher for no update and must not be passed here.
    """
    run = Path(run).resolve(strict=True)
    candidates, findings = [], []
    for row in inventory.get('objects', []):
        if row.get('status') != 'converted' or not isinstance(row.get('result'), dict) or row['result'].get('status') != 'passed':
            continue
        identity = str(row.get('id', 'unknown'))
        try:
            result = row['result']
            receipt_path = _unlinked(result['receipt_path'], run).resolve(strict=True)
            if not receipt_path.is_relative_to(run/'seismic') or not receipt_path.is_file():
                raise ValueError('receipt escaped the seismic run')
            raw = receipt_path.read_bytes()
            receipt = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(receipt, dict):
                raise ValueError('seismic receipt is not an object')
            summary = receipt.get('summary')
            if (receipt.get('status') != 'passed' or receipt.get('operation') != 'zgy-to-segy'
                    or not isinstance(summary, dict)
                    or summary.get('all_decoded_samples_exact') is not True
                    or summary.get('headers_verified') is not True):
                raise ValueError('completed seismic numerical QC is not recorded')
            if (result.get('operation') != receipt['operation'] or result.get('summary') != summary
                    or result.get('full_seismic_hash') != receipt.get('full_seismic_hash')):
                raise ValueError('inventory result does not match seismic receipt')
            if (_unlinked(receipt.get('receipt_path'), run).resolve(strict=True) != receipt_path
                    or (row.get('receipt_path') and _unlinked(row['receipt_path'], run).resolve(strict=True) != receipt_path)):
                raise ValueError('receipt path binding differs from inventory')
            if (not isinstance(row.get('source'), str) or not isinstance(receipt.get('source'), str)
                    or not Path(row['source']).is_absolute() or not Path(receipt['source']).is_absolute()):
                raise ValueError('seismic source identity is missing')
            if (_unlinked(row['source'], Path(row['source']).anchor).resolve(strict=True)
                    != _unlinked(receipt['source'], Path(receipt['source']).anchor).resolve(strict=True)
                    or result.get('source') != receipt['source']):
                raise ValueError('seismic source identity differs from receipt')
            if result.get('artifacts') != receipt.get('artifacts'):
                raise ValueError('inventory artifact declarations differ from receipt')
            if not row.get('id') or not row.get('name'):
                raise ValueError('seismic object identity is missing')
            declarations = receipt.get('artifacts')
            if not isinstance(declarations, list) or not declarations:
                raise ValueError('seismic receipt has no artifacts')
            folder = receipt_path.parent
            artifacts, seen = [], set()
            for declaration in declarations:
                relative = declaration.get('path')
                if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or any(
                        part.lower().endswith(('.tmp', '.partial')) for part in Path(relative).parts):
                    raise ValueError('invalid seismic artifact path')
                source = _unlinked(folder/relative, folder).resolve(strict=True)
                if not source.is_relative_to(folder) or not source.is_file():
                    raise ValueError('seismic artifact escaped receipt directory or is missing')
                if source.suffix.lower() not in _USEFUL:
                    continue
                if source in seen:
                    raise ValueError('duplicate seismic artifact declaration')
                seen.add(source)
                size = declaration.get('size_bytes')
                if type(size) is not int or size < 0 or source.stat().st_size != size:
                    raise ValueError('seismic artifact size differs from receipt: '+str(source))
                digest = declaration.get('sha256')
                if digest not in (None, '') and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
                    raise ValueError('invalid seismic artifact SHA-256 declaration')
                if receipt.get('full_seismic_hash') and not digest:
                    raise ValueError('full-hash seismic receipt lacks artifact SHA-256')
                artifacts.append(dict(source=source, relative=relative, bytes=size,
                                      sha256=digest.lower() if digest else '', declaration=declaration))
            if not artifacts:
                raise ValueError('seismic receipt has no useful artifacts')
            candidates.append(dict(identity=identity, row=row, receipt=receipt,
                                   signature=hashlib.sha256(raw).hexdigest(), artifacts=artifacts))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
            findings.append('Rejected seismic result '+identity+': '+str(exc))
    # A repeated object identity has no unambiguous declaration, including
    # when one of the competing receipts fails validation.
    by_identity = {}
    for row in inventory.get('objects', []):
        if row.get('status') == 'converted':
            by_identity.setdefault(str(row.get('id')), 0)
            by_identity[str(row.get('id'))] += 1
    duplicates = {identity for identity, count in by_identity.items() if count > 1}
    for identity in duplicates:
        findings.append('Duplicate seismic identity withheld: '+identity)
    candidates = [candidate for candidate in candidates if candidate['identity'] not in duplicates]
    # Every owner of a shared physical file must agree before either can publish.
    owners = {}
    for candidate in candidates:
        for artifact in candidate['artifacts']:
            stat = artifact['source'].stat()
            physical = (stat.st_dev, stat.st_ino) if stat.st_ino else artifact['source']
            owners.setdefault(physical, []).append((candidate, artifact))
    rejected = set()
    if fresh_hash:
        from petrel_progress import hash_batch, hash_file
        hashed_paths = [declarations[0][1]['source'] for declarations in owners.values()
                        if declarations[0][1]['sha256']]
        hash_context = hash_batch('Final generated seismic artifact SHA-256', hashed_paths)
    else:
        hash_context = nullcontext()
    with hash_context:
        for physical, declarations in owners.items():
            sizes = {artifact['bytes'] for _, artifact in declarations}
            digests = {artifact['sha256'] for _, artifact in declarations}
            if len(sizes) != 1 or len(digests) != 1:
                reason = 'conflicting shared seismic artifact declarations'
            elif fresh_hash and next(iter(digests)):
                source = declarations[0][1]['source']
                try:
                    actual = hash_file(source)
                    reason = '' if actual == next(iter(digests)) else 'seismic artifact SHA-256 differs from receipt'
                except OSError as exc:
                    reason = 'seismic artifact SHA-256 unavailable: '+str(exc)
            else:
                reason = ''
            if reason:
                for candidate, artifact in declarations:
                    rejected.add(id(candidate))
                    findings.append('Seismic export withheld for '+candidate['identity']+' at '+str(artifact['source'])+': '+reason)
    return [candidate for candidate in candidates if id(candidate) not in rejected], findings
