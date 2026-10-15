# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Compact operation spans in the existing local diagnostic event stream."""
from contextlib import contextmanager
from functools import wraps
import time
import uuid


@contextmanager
def operation_timing(operation, **counts):
    from geoviewer_diagnostics import event
    started = time.monotonic()
    operation_id = uuid.uuid4().hex
    event('operation_started', operation=operation, operation_id=operation_id, **counts)
    outcome = 'completed'
    error_type = None
    try:
        yield
    except BaseException as exc:
        outcome, error_type = 'failed', type(exc).__name__
        raise
    finally:
        event('operation_finished', operation=operation, operation_id=operation_id, status=outcome,
              severity='error' if outcome == 'failed' else 'info',
              elapsed_seconds=round(time.monotonic() - started, 6),
              error_type=error_type, **counts)


def timed_operation(operation):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with operation_timing(operation):
                return function(*args, **kwargs)
        return measured
    return decorate
