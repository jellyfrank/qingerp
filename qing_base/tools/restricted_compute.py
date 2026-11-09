"""Observe read-time compute failures without changing ORM access or cache data.

Callers own transaction isolation and must let failures escape their savepoint
before retrying. Only a private Python scope enables this observer.

Originally written by the same rights holder for the Solutions ``mommy_base``
addon and relicensed under LGPL-3 for QingERP. The context variables use their
own names, so this observer stays independent when ``mommy_base`` is installed.
"""


from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple

from odoo.exceptions import AccessError


class ComputeNode(NamedTuple):
    model_name: str
    record_ids: tuple[int, ...]
    field_names: tuple[str, ...]


class RestrictedComputeError(Exception):
    """A recoverable read compute failure; deliberately not an AccessError."""

    def __init__(self, nodes: tuple[ComputeNode, ...], original_error: AccessError):
        super().__init__("Access denied while computing a field.")
        self.nodes = tuple(nodes)
        self.original_error = original_error


class _ComputeFrame(NamedTuple):
    node: ComputeNode | None
    recoverable: bool


_observation_scope = ContextVar("qing_compute_observation_scope", default=False)
_compute_stack = ContextVar("qing_compute_observation_stack", default=())
_suspended = ContextVar("qing_compute_observation_suspended", default=False)
_strict = ContextVar("qing_compute_observation_strict", default=False)
_native_errors = ContextVar("qing_compute_observation_native_errors", default=())


def is_observing_computes():
    return _observation_scope.get() and not _suspended.get() and not _strict.get()


def are_computes_strict():
    return _strict.get()


def _remember_native_error(error):
    if _observation_scope.get():
        errors = _native_errors.get()
        if not any(error is previous for previous in errors):
            _native_errors.set(errors + (error,))


@contextmanager
def observe_computes():
    """Enable observation, including suspended callbacks, but never lift strictness."""
    if _observation_scope.get():
        token = _suspended.set(False)
        try:
            yield
        finally:
            _suspended.reset(token)
        return

    scope_token = _observation_scope.set(True)
    suspension_token = _suspended.set(False)
    stack_token = _compute_stack.set(())
    errors_token = _native_errors.set(())
    try:
        yield
    finally:
        _native_errors.reset(errors_token)
        _compute_stack.reset(stack_token)
        _suspended.reset(suspension_token)
        _observation_scope.reset(scope_token)


@contextmanager
def suspend_computes():
    """Temporarily disable observation; an explicit callback scope may enable it."""
    token = _suspended.set(True)
    try:
        yield
    except AccessError as error:
        _remember_native_error(error)
        raise
    finally:
        _suspended.reset(token)


@contextmanager
def strict_computes():
    """Keep operations strict, including their nested observation callbacks."""
    token = _strict.set(True)
    try:
        yield
    except AccessError as error:
        _remember_native_error(error)
        raise
    finally:
        _strict.reset(token)


@contextmanager
def observe_compute(records, field):
    """Capture all siblings and active ancestors, without reading record values."""
    if not is_observing_computes():
        yield
        return

    siblings = records.pool.field_computed.get(field, (field,))
    record_ids = tuple(records._ids)
    persisted = all(isinstance(record_id, int) and record_id > 0 for record_id in record_ids)
    node = ComputeNode(
        records._name, record_ids, tuple(sibling.name for sibling in siblings),
    ) if persisted else None
    frame = _ComputeFrame(node, persisted and not any(sibling.store for sibling in siblings))
    stack = _compute_stack.get() + (frame,)
    token = _compute_stack.set(stack)
    strict_token = _strict.set(True) if not frame.recoverable else None
    try:
        yield
    except AccessError as error:
        # An ineligible descendant has already unwound by the time we see it.
        native = any(error is previous for previous in _native_errors.get())
        if native or not all(item.recoverable for item in stack):
            _remember_native_error(error)
            raise
        raise RestrictedComputeError(tuple(item.node for item in stack), error) from error
    finally:
        if strict_token is not None:
            _strict.reset(strict_token)
        _compute_stack.reset(token)
