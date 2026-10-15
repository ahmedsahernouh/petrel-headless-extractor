# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Incrementally publish accepted object receipts while an extraction is running.

This module never reads Petrel sources and never copies large results.  It uses
the small, terminal receipts written by the native and seismic converters and
publishes hard links with the same names as ``geoviewer_delivery``.
"""
from __future__ import annotations

import csv
import hashlib
from html import escape
import io
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid
from urllib.parse import quote

from geoviewer_delivery import slug
from geoviewer_diagnostics import event
from geoviewer_io import atomic_json, atomic_text, contained, systemic
from geoviewer_metadata import PRODUCT, VERSION
from geoviewer_seismic_delivery import validate_seismic_rows


USEFUL_NATIVE = {'.csv', '.las', '.xyz', '.dat', '.zmap', '.txt', '.json'}
USEFUL_SEISMIC = {'.segy', '.json'}
FIELDS = ['object_id', 'name', 'category', 'format', 'label', 'path', 'bytes',
          'status', 'numerical_qc', 'units', 'sha256', 'integrity', 'storage',
          'record_count', 'receipt_signature', 'recovery_status', 'metadata_warning']
START = '<!-- GEOVIEWER_INCREMENTAL_CHECKPOINT_START -->'
END = '<!-- GEOVIEWER_INCREMENTAL_CHECKPOINT_END -->'
NATIVE_TYPES = {'FloatWellLog', 'IntWellLog', 'RegValGrid2', 'ValGrid2'}
SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


class _Cancelled(RuntimeError):
    pass


def _linked(path, stop):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            return True
        if item == stop:
            break
    return False


def _raw_unlinked(path, label):
    """Reject aliases before resolve(), which would otherwise hide the link."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError(label+' has linked ancestry: '+str(item))
    return path


def _relative(path, base):
    return os.path.relpath(path, base).replace('\\', '/')


class IncrementalDelivery:
    """Maintain a shallow accepted-export index during one parent pipeline."""

    def __init__(self, run, report, destination, context, log_path=None,
                 event_path=None, enabled=True, interval=5, stop_timeout=30):
        self.run = _raw_unlinked(run, 'Run path').resolve()
        self.report = Path(report)
        self.destination = Path(destination)
        self.context = context or {}
        self.log_path = Path(log_path) if log_path else None
        self.event_path = Path(event_path) if event_path else None
        self.enabled = bool(enabled)
        self.interval = max(.05, float(interval))
        self.stop_timeout = max(0.0, float(stop_timeout))
        self.findings = []
        self.worker_errors = []
        self._records = {}
        self._commit_cache = {}
        self._accepted_commits = {}
        self._accepted_seismic = {}
        self._seismic_inventory = None
        self.current_index = self._index([])
        self._stop = threading.Event()
        self._thread = None
        self._refresh_lock = threading.Lock()
        self._stopping = False
        self._stop_deadline = None
        self._last_payload = None
        self._last_checkpoint = None

    def _index(self, records):
        return dict(product=PRODUCT, version=VERSION, partial=True,
                    status='extraction_in_progress', files=list(records),
                    invalid_seismic=[dict(object_id=identity, receipt_signature=state['signature'])
                                     for identity, state in sorted(self._accepted_seismic.items()) if state['withdrawn']],
                    findings=list(self.findings), worker_errors=list(self.worker_errors),
                    scope='Accepted decoded/converted objects observed so far.',
                    storage_note='Digests are receipt hashes. Source-project integrity and final project QC remain pending.')

    def _note(self, message, error=False):
        message = str(message)
        target = self.worker_errors if error else self.findings
        if message not in target:
            target.append(message)
        event('incremental_delivery_error' if error else 'incremental_delivery_finding',
              severity='error' if error else 'warning', message=message,
              expected_log_path=str(self.log_path) if self.log_path else None,
              expected_event_path=str(self.event_path) if self.event_path else None)

    def _discover_package(self):
        root = self.run/'extraction/package'
        if not root.is_dir():
            return None
        choices = [p for p in root.iterdir() if p.is_dir() and not p.name.endswith('.partial')]
        if len(choices) > 1:
            raise ValueError('Ambiguous extraction package: expected exactly one package below '+str(root))
        return _raw_unlinked(choices[0], 'Package path').resolve(strict=True) if choices else None

    def _safe_artifact(self, root, relative):
        if (not isinstance(relative, str) or not relative
                or any(part.lower().endswith(('.partial', '.tmp')) for part in Path(relative).parts)):
            raise ValueError('Pending or missing artifact path is not accepted')
        root = Path(root).resolve(strict=True)
        path = contained(root, root/relative)
        if _linked(path, root):
            raise ValueError('Linked artifact path is not accepted: '+str(path))
        if not path.is_file():
            raise ValueError('Receipt artifact is missing: '+str(path))
        return path

    def _digest_syntax(self, value, *, optional=False):
        if optional and value in (None, ''):
            return value
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ValueError('invalid receipt SHA-256 syntax')
        return value.lower()

    def _prior_integrity(self, state):
        for artifact in state['artifacts']:
            source = artifact['source']
            try:
                if (not source.is_file() or _linked(source, state['root'])
                        or source.stat().st_size != artifact['bytes']):
                    return False
                with source.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != artifact['sha256']:
                        return False
            except OSError:
                return False
        return True

    def _changed_commit(self, commit, signature):
        state = self._accepted_commits.get(commit)
        if not state or state['signature'] == signature:
            return False
        if self._prior_integrity(state):
            for key, artifact in zip(state['keys'], state['artifacts']):
                state['rows'][key] = self._bind_native_row(key, artifact['source'], state['rows'][key])
            state['withdrawn'] = False
            self._note('Changed native commit ignored; frozen accepted receipt retained after exceptional artifact revalidation: '+str(commit))
        else:
            self._withdraw_commit(commit)
            self._note('Changed native commit rejected and prior delivery withdrawn because prior artifact integrity no longer holds: '+str(commit))
        return True

    def _withdraw_commit(self, commit):
        state = self._accepted_commits.get(commit)
        if state:
            state['withdrawn'] = True
            for key in state['keys']:
                self._records.pop(key, None)

    def _bind_native_row(self, key, source, row):
        target = self.destination/row['path']
        try:
            valid = (target.is_file() and os.path.samefile(source, target)
                     and (row['storage'] != 'single_physical_copy_hardlink'
                          or not _linked(target, self.destination)))
        except OSError:
            valid = False
        if not valid:
            row = self._publish(source, row['category'], row['object_id'], row['name'],
                                row['format'], row['numerical_qc'], row['units'],
                                row['sha256'], row['receipt_signature'],
                                recovery_status=row['recovery_status'],
                                metadata_warning=row['metadata_warning'])
        self._records[key] = row
        return row

    def _publish(self, source, category, identity, name, label, qc, unit,
                 digest, receipt_signature, recovery_status='', metadata_warning=''):
        source = Path(source)
        stem = slug(name)+'_'+slug(identity)[:36]+'_'+slug(source.stem)
        target = self.destination/category/(stem+source.suffix.lower())
        layout = 'single_physical_copy_hardlink'
        try:
            self.destination.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = contained(self.destination.resolve(strict=True), target)
            if _linked(target, self.destination):
                raise ValueError('Linked delivery path is not accepted: '+str(target))
            if target.exists():
                identical = os.path.samefile(source, target)
            else:
                os.link(source, target)
                identical = os.path.samefile(source, target)
            if not identical:
                raise FileExistsError('Export name collision or hard-link identity check failed: '+str(target))
        except ValueError:
            raise
        except OSError as exc:
            if systemic(exc):
                raise
            target = source
            layout = 'original_path_filesystem_cannot_hardlink'
            self._note('Shallow link unavailable; original validated file retained: '+str(exc))
        return dict(object_id=identity, name=name, category=category,
                    format=label, label=source.stem.replace('_', ' ')+' ('+label+')',
                    path=_relative(target, self.destination), bytes=source.stat().st_size,
                    status='exported', numerical_qc=qc, units=unit or 'unknown',
                    recovery_status=recovery_status, metadata_warning=metadata_warning,
                    sha256=digest or '', integrity='receipt_sha256' if digest else 'receipt_file_size_and_prior_numerical_QC',
                    storage=layout, record_count='', receipt_signature=receipt_signature)

    def _native_candidates(self, package):
        self._check_cancel()
        native = package/'native_data'
        if not native.is_dir():
            for commit in tuple(self._accepted_commits):
                self._withdraw_commit(commit)
            return
        observed = set()
        for directory in sorted(native.iterdir()):
            self._check_cancel()
            if (not directory.is_dir() or directory.name.endswith('.partial')
                    or _linked(directory, native)):
                continue
            commit = directory/'_commit.json'
            if not commit.is_file() or commit.is_symlink():
                continue
            observed.add(commit)
            self._check_cancel()
            try:
                stat = commit.stat(); cache_key = (stat.st_mtime_ns, stat.st_size)
                cached = self._commit_cache.get(commit)
                if cached and cached[0] == cache_key:
                    receipt, signature = cached[1], cached[2]
                else:
                    raw = commit.read_bytes()
                    receipt = json.loads(raw.decode('utf-8-sig'))
                    signature = hashlib.sha256(raw).hexdigest()
                    self._commit_cache[commit] = (cache_key, receipt, signature)
                if self._changed_commit(commit, signature):
                    continue
                prior = self._accepted_commits.get(commit)
                if prior and prior['withdrawn'] and not self._prior_integrity(prior):
                    self._note('Native commit remains withdrawn until frozen artifact integrity is restored: '+str(commit))
                    continue
                if receipt.get('schema') != 'geoviewer.object/1' or receipt.get('tool_version') != '1.0.0':
                    raise ValueError('invalid receipt schema/tool version')
                record = receipt.get('record')
                if not isinstance(record, dict):
                    raise ValueError('missing receipt record')
                identity = record.get('object_id'); name = record.get('object_name') or record.get('name')
                if not identity or not name:
                    raise ValueError('missing object identity')
                identity = str(uuid.UUID(str(identity)))
                blob_type = record.get('blob_type')
                if blob_type not in NATIVE_TYPES:
                    raise ValueError('unknown native blob type')
                if record.get('numeric_round_trip') != 'exact':
                    raise ValueError('numeric round trip is not exact')
                if record.get('status') not in ('decoded', 'missing_metadata'):
                    raise ValueError('object is not decoded or missing_metadata')
                artifacts = record.get('artifacts')
                if not isinstance(artifacts, list) or not artifacts:
                    raise ValueError('receipt has no accepted artifacts')
                accepted = []
                for artifact in artifacts:
                    self._check_cancel()
                    source = self._safe_artifact(native, artifact.get('path'))
                    self._check_cancel()
                    if source.parent != directory or source.suffix.lower() not in USEFUL_NATIVE:
                        if source.suffix.lower() in USEFUL_NATIVE:
                            raise ValueError('artifact is outside its committed object directory')
                        continue
                    expected = artifact.get('bytes')
                    if type(expected) is not int or source.stat().st_size != expected:
                        raise ValueError('artifact size does not match receipt: '+str(source))
                    digest = self._digest_syntax(artifact.get('sha256'))
                    accepted.append((artifact, source, expected, digest))
                # Receipt validation is all-or-nothing: publication starts only after
                # every useful artifact has passed confinement and metadata checks.
                keys = []
                for artifact, source, expected, digest in accepted:
                    self._check_cancel()
                    category = 'logs' if blob_type.endswith('WellLog') else 'grids'
                    display_name = (str(record['well'])+'_'+str(name)) if record.get('well') else str(name)
                    key = ('native', str(identity), artifact['path'], signature)
                    if key in self._records:
                        self._bind_native_row(key, source, self._records[key])
                    else:
                        self._records[key] = self._publish(source, category, str(identity), display_name,
                                                           source.suffix[1:].upper(), 'exact_readback',
                                                           record.get('unit'), digest, signature,
                                                           recovery_status=record.get('status') or '',
                                                           metadata_warning=record.get('reason') or '')
                    keys.append(key)
                if accepted:
                    self._accepted_commits[commit] = dict(signature=signature, keys=keys, root=native,
                        rows={key: self._records[key] for key in keys}, withdrawn=False,
                        artifacts=[dict(source=source, bytes=expected, sha256=digest)
                                   for _, source, expected, digest in accepted])
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
                self._withdraw_commit(commit)
                self._note('Rejected native commit '+str(commit)+': '+str(exc))
        for commit in tuple(self._accepted_commits):
            if commit not in observed:
                self._withdraw_commit(commit)
                self._note('Missing native commit; prior delivery withdrawn: '+str(commit))

    def _seismic_candidates(self, inventory):
        if inventory is not None:
            self._seismic_inventory = inventory
        if self._seismic_inventory is None:
            return
        candidates, findings = validate_seismic_rows(self._seismic_inventory, self.run)
        for finding in findings:
            self._note(finding)
        observed = set()
        for candidate in candidates:
            self._check_cancel()
            identity = candidate['identity']
            if identity in observed:
                self._note('Duplicate seismic identity withheld: '+identity)
                continue
            observed.add(identity)
            prior = self._accepted_seismic.get(identity)
            if prior:
                if prior['signature'] != candidate['signature']:
                    # Do not mix a changed receipt with yesterday's accepted rows.
                    self._withdraw_seismic(identity)
                    self._note('Changed seismic receipt withheld pending a new verified delivery: '+identity)
                    continue
                if prior['withdrawn']:
                    if not self._prior_seismic_integrity(prior):
                        self._note('Seismic delivery remains withdrawn until frozen artifact integrity is restored: '+identity)
                        continue
                    prior['withdrawn'] = False
                try:
                    for key, artifact in zip(prior['keys'], prior['artifacts']):
                        self._bind_native_row(key, artifact['source'], prior['rows'][key])
                except (OSError, ValueError) as exc:
                    self._withdraw_seismic(identity)
                    self._note('Seismic delivery binding rejected '+identity+': '+str(exc))
                continue
            keys = []
            try:
                for artifact in candidate['artifacts']:
                    self._check_cancel()
                    source = artifact['source']
                    key = ('seismic', identity, artifact['relative'], candidate['signature'])
                    unit = candidate['receipt']['summary'].get('profile', {}).get('source_vertical_unit', 'unknown')
                    self._records[key] = self._publish(source, 'seismic', identity,
                        str(candidate['row']['name']), source.suffix[1:].upper(),
                        'every_amplitude_and_trace_geometry', unit, artifact['sha256'], candidate['signature'])
                    keys.append(key)
            except (OSError, ValueError) as exc:
                for key in keys:
                    self._records.pop(key, None)
                self._note('Rejected seismic delivery '+identity+': '+str(exc))
                continue
            self._accepted_seismic[identity] = dict(signature=candidate['signature'], keys=keys,
                rows={key: self._records[key] for key in keys}, artifacts=candidate['artifacts'], withdrawn=False)
        for identity in tuple(self._accepted_seismic):
            if identity not in observed:
                self._withdraw_seismic(identity)

    def _withdraw_seismic(self, identity):
        state = self._accepted_seismic.get(identity)
        if state:
            state['withdrawn'] = True
            for key in state['keys']:
                self._records.pop(key, None)

    def _prior_seismic_integrity(self, state):
        for artifact in state['artifacts']:
            source, digest = artifact['source'], artifact['sha256']
            if not digest:
                return False  # No-full-hash SEG-Y cannot regain byte identity from size alone.
            try:
                if not source.is_file() or _linked(source, self.run) or source.stat().st_size != artifact['bytes']:
                    return False
                with source.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        return False
            except OSError:
                return False
        return True

    def _html(self, index):
        title = 'Accepted exports observed so far' if index.get('partial') else 'Accepted exported files'
        note = ('Partial checkpoint. Final source integrity and project QC are not established by this index.'
                if index.get('partial') else 'Extraction has ended. Read the main report for final integrity, category gaps and validation scope.')
        rows = []
        errors = index.get('worker_errors', [])
        error_section = ('<section aria-label="Delivery issues"><h2>Delivery issues</h2><ul>'
                         + ''.join('<li>'+escape(str(message))+'</li>' for message in errors)
                         + '</ul></section>') if errors else ''
        for item in index['files']:
            link = quote(item['path'], safe='/..')
            # Final spatial splits predate the incremental index's display label.
            # Keep their verified path usable without requiring a schema rewrite.
            label = item.get('label') or Path(item['path']).name
            rows.append('<tr><td>'+escape(item['category'])+'</td><td>'+escape(item['name'])+
                        '<br><small>'+escape(item['object_id'])+'</small></td><td><a href="'+link+'">'+
                        escape(label)+'</a></td><td>'+escape(item['units'])+'</td><td>'+escape(item['numerical_qc'])+'</td><td>'+
                        escape(str(item.get('recovery_status') or ''))+'</td><td>'+
                        escape(str(item.get('metadata_warning') or ''))+'</td></tr>')
        return ('<!doctype html><html><head><meta charset="utf-8"><title>Incremental accepted exports</title>'
                '<style>body{font:16px system-ui;max-width:1200px;margin:30px auto;padding:20px}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ccc}</style>'
                '</head><body><main><h1>'+title+'</h1><p>'+note+'</p>'+error_section+
                '<input aria-label="Search exports" placeholder="Filter objects or formats" oninput="document.querySelectorAll(\'tbody tr\').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase()))">'
                '<table><thead><tr><th>Category</th><th>Object</th><th>File</th><th>Units</th><th>QC</th><th>Recovery status</th><th>Metadata / computation limitations</th></tr></thead><tbody>'+''.join(rows)+
                '</tbody></table></main></body></html>')

    def _checkpoint(self, count, has_index):
        if not self.report.is_file():
            return
        report_stat = self.report.stat()
        state = (count, has_index, report_stat.st_mtime_ns, report_stat.st_size)
        if state == self._last_checkpoint:
            return
        text = self.report.read_text(encoding='utf-8')
        target = self.destination/'INCREMENTAL_INDEX.html'
        link = quote(_relative(target, self.report.parent), safe='/..')
        body = (START+'<section id="incremental-delivery-checkpoint"><h2>Extraction checkpoint</h2><p><b>'+str(count)+
                ' accepted files observed so far.</b> This is a partial status; final source integrity and project QC remain pending.'+
                ((' <a href="'+link+'">Open the live accepted-export index</a>.') if has_index else '')+'</p></section>'+END)
        if START in text and END in text:
            text = text[:text.index(START)] + body + text[text.index(END)+len(END):]
        elif '<main>' in text:
            text = text.replace('<main>', '<main>'+body, 1)
        elif '<body>' in text:
            text = text.replace('<body>', '<body>'+body, 1)
        else:
            return
        atomic_text(self.report, text)
        report_stat = self.report.stat()
        self._last_checkpoint = (count, has_index, report_stat.st_mtime_ns, report_stat.st_size)

    def _write_snapshot(self, index):
        if self._stopping:
            raise _Cancelled('Incremental delivery is stopping')
        self.destination.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(index['files'])
        atomic_text(self.destination/'FILE_INDEX.csv', '\ufeff'+stream.getvalue())
        if self._stopping:
            raise _Cancelled('Incremental delivery stopped between snapshot files')
        atomic_json(self.destination/'FILE_INDEX.json', index)
        self.current_index = index
        if self._stopping:
            raise _Cancelled('Incremental delivery stopped after the JSON checkpoint')
        atomic_text(self.destination/'INCREMENTAL_INDEX.html', self._html(index))

    def _withdraw_qc_rejected_native(self, package):
        """Remove known QC failures from accepted indexes, preserving raw files.

        Only invoked after the background publisher stops. Historical indexes
        without this new QC evidence retain their explicitly provisional scope.
        """
        path = self.run/'qc/result.json'
        if not path.is_file():
            return False
        path = self._safe_artifact(self.run, 'qc/result.json')
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        quality = result.get('native_artifact_qc')
        if not isinstance(quality, dict) or quality.get('status') != 'failed':
            return False
        if Path(result.get('package', '')).resolve(strict=True) != package:
            self._note('Native QC receipt belongs to a different package; it cannot classify these exports', error=True)
            return False
        failures = quality.get('failures')
        if not isinstance(failures, list) or not failures:
            # The package is known to have failed, but affected identities are
            # unavailable. Do not leave yesterday's accepted index in place.
            self._note('Failed native QC receipt has no usable failure details; '
                       'all native entries are withheld from this checkpoint', error=True)
            failures = [dict(scope='global', object_id=None)]
        global_failure = any(not isinstance(row, dict) or row.get('scope') == 'global'
                             or not row.get('object_id') for row in failures)
        rejected = {str(row['object_id']).casefold() for row in failures
                    if isinstance(row, dict) and row.get('object_id')}
        before = len(self._records)
        self._records = {key: row for key, row in self._records.items()
                         if key[0] != 'native' or (not global_failure
                             and str(row.get('object_id', '')).casefold() not in rejected)}
        self._note('Native package QC rejected '+str(len(failures))+' artifact/receipt checks; '
                   +str(before-len(self._records))+' indexed native files withdrawn. '
                   'Raw files and receipts are preserved. See '+str(path), error=True)
        # True forces an empty replacement index when the failed pipeline creates
        # a fresh checkpoint reader over an existing nonempty index.
        return True

    def refresh(self, package=None, inventory=None, *, stopped_pipeline=False):
        if stopped_pipeline and self._thread and self._thread.is_alive():
            raise RuntimeError('Spatial checkpoint requires a stopped background publisher')
        with self._refresh_lock:
            if self._stopping:
                return self.current_index
            if not self.enabled:
                try:
                    self._checkpoint(0, False)
                except (OSError, ValueError, UnicodeError) as exc:
                    self._note('Checkpoint update failed: '+str(exc), error=True)
                return self.current_index
            try:
                selected = _raw_unlinked(package, 'Package path').resolve(strict=True) if package else self._discover_package()
                qc_rejected = False
                if selected:
                    expected_root = (self.run/'extraction/package').resolve()
                    if not selected.is_relative_to(expected_root) or _linked(selected, expected_root):
                        raise ValueError('Package escaped the run or is linked')
                    self._native_candidates(selected)
                    if stopped_pipeline:
                        from geoviewer_delivery import publish_exports
                        self._records = {key: row for key, row in self._records.items() if key[0] != 'spatial'}
                        try:
                            spatial = publish_exports(selected, {}, self.destination, spatial_checkpoint=True)
                        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
                            if systemic(exc): raise
                            self._note('Spatial checkpoint unavailable: '+str(exc))
                            spatial = {'files': [], 'findings': []}
                        for finding in spatial['findings']:
                            self._note(finding)
                        for row in spatial['files']:
                            self._records[('spatial', row['object_id'], row['path'])] = row
                        qc_rejected = self._withdraw_qc_rejected_native(selected)
                self._seismic_candidates(inventory)
                records = sorted(self._records.values(), key=lambda x: (x['category'], x['name'], x['object_id'], x['path'], x['receipt_signature']))
                index = self._index(records)
                if stopped_pipeline:
                    index['status'] = 'stopped_partial'
                    index['scope'] = 'Retained open files after extraction stopped; final package QC is not established.'
                payload = json.dumps(index, sort_keys=True, ensure_ascii=True)
                changed = payload != self._last_payload
                if changed and (records or self._last_payload is not None or qc_rejected) and not self._stopping:
                    self._write_snapshot(index)
                    self._last_payload = payload
                if not self._stopping:
                    try:
                        self._checkpoint(len(records), bool(records))
                    except (OSError, ValueError, UnicodeError) as exc:
                        self._note('Checkpoint update failed: '+str(exc), error=True)
                if not records:
                    self.current_index = index
            except _Cancelled:
                pass
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
                if systemic(exc):
                    raise
                self._note('Incremental refresh failed: '+str(exc), error=True)
            return self.current_index

    def _work(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except BaseException as exc:
                self._note('Background worker stopped: '+repr(exc), error=True)
                break
            self._stop.wait(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stopping = False; self._stop.clear(); self._stop_deadline = None
        self._thread = threading.Thread(target=self._work, name='geoviewer-incremental-delivery', daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stopping = True
        self._stop.set()
        if self._thread:
            if self._stop_deadline is None:
                self._stop_deadline = time.monotonic() + self.stop_timeout
            self._thread.join(timeout=max(0.0, self._stop_deadline-time.monotonic()))
            if self._thread.is_alive():
                frame = sys._current_frames().get(self._thread.ident)
                stack = ''.join(traceback.format_stack(frame)) if frame else 'Worker stack unavailable'
                self._note('Background worker did not stop within '+str(self.stop_timeout)+
                           ' seconds; final publishing must wait. Worker stack:\n'+stack, error=True)
                raise TimeoutError('Incremental delivery worker is still active; final publishing is unsafe')
        return self.current_index

    def _check_cancel(self):
        if self._stop.is_set():
            raise _Cancelled('Incremental delivery is stopping')
