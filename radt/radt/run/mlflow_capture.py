"""Divert ``mlflow.start_span`` spans into radT's artifact batches.

Workloads instrumented with plain ``mlflow.start_span`` -- rather than
:func:`radt.run.trace.span` -- otherwise stream every span to the tracking
server over OTLP. Against a radT server that traffic is pure overhead: the
server can read the batched artifacts and convert them to Perfetto itself.

This patches ``MlflowV3SpanExporter.export``, the single point every completed
span passes through, and writes the same on-disk format
:mod:`radt.run.trace` produces. The workload needs no changes.

Flushing is the subtle part. Callers commonly finish with ``os._exit(0)`` to
skip a slow interpreter teardown, which also skips ``atexit`` and background
threads -- so anything still spooled would vanish silently. ``mlflow``'s own
``flush_trace_async_logging`` is patched to drain and upload first, because
that is what such callers already invoke on the way out.
"""

import logging
import os
import threading

from .trace import ARTIFACT_DIR, _BatchSpool, _jsonable

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_spool = None
_patched = False
_capturing = None  # tri-state: None = not yet decided
_experiment_id = None
_original_export = None
_original_flush = None
#: Spans arrive with an experiment but no run until mlflow resolves one, so the
#: run id is looked up per trace and remembered for the manifest.
_run_id = None


def _ensure_spool():
    """Created on first capture, so a process that never traces spools nothing."""
    global _spool

    if _spool is None:
        _spool = _BatchSpool(_experiment_id)
    return _spool


def _span_records(span, trace_id, run_id):
    """One completed OTel span -> radT's start/end record pair."""
    attributes = {}
    for key, value in dict(span.attributes or {}).items():
        attributes[str(key)] = _jsonable(value)
    if run_id:
        attributes.setdefault("mlflow.run_id", run_id)

    span_id = span.context.span_id
    parent_id = span.parent.span_id if span.parent else None
    start_ns = span.start_time
    end_ns = span.end_time if span.end_time is not None else start_ns
    return (
        ["s", span_id, parent_id, trace_id, span.name, attributes or None, start_ns],
        ["e", span_id, end_ns],
    )


def _resolve_run_id(otel_trace_id):
    """The run a trace belongs to, as stamped into trace metadata at span start."""
    try:
        from mlflow.tracing.constant import TraceMetadataKey
        from mlflow.tracing.trace_manager import InMemoryTraceManager

        manager = InMemoryTraceManager.get_instance()
        mlflow_trace_id = manager.get_mlflow_trace_id_from_otel_id(otel_trace_id)
        if mlflow_trace_id is None:
            return None
        with manager.get_trace(mlflow_trace_id) as trace:
            if trace is None:
                return None
            return trace.info.trace_metadata.get(TraceMetadataKey.SOURCE_RUN)
    except Exception:  # noqa: BLE001 - attribution is best effort
        _logger.debug("radt[mlflow-capture]: could not resolve run id", exc_info=True)
        return None


def _capture(spans):
    global _run_id

    for span in spans:
        try:
            otel_trace_id = span.context.trace_id
            if _run_id is None:
                if resolved := _resolve_run_id(otel_trace_id):
                    _run_id = resolved
                    _spool.set_run(resolved)
            start, end = _span_records(span, otel_trace_id, _run_id)
            _spool.add(start)
            _spool.add(end)
        except Exception:  # noqa: BLE001 - never break the workload over tracing
            _logger.debug("radt[mlflow-capture]: failed to capture a span", exc_info=True)
    _spool.maybe_roll()


def _should_capture():
    """Whether to divert spans, decided once on the first exported span.

    Deferred rather than decided at import: the answer needs a probe of the
    tracking server, and paying for that on ``import radt`` would slow every
    process whether or not it traces anything.
    """
    global _capturing

    if _capturing is None:
        from . import trace as _trace

        if _trace._backend is not None:
            # radt.run.trace.start() already resolved a backend; an operator who
            # asked for mlflow tracing must not have their spans diverted.
            _capturing = _trace._backend == "radt"
        else:
            _capturing = (
                os.getenv("RADT_TRACE_BACKEND") or _trace._detect_backend()
            ).lower() == "radt"
        _logger.info(
            "radt[mlflow-capture]: %s",
            f"batching mlflow spans into {ARTIFACT_DIR}/ artifacts"
            if _capturing
            else "no radT server; leaving mlflow tracing untouched",
        )
    return _capturing


def enable(experiment_id=None):
    """Route ``mlflow.start_span`` spans into artifact batches instead of OTLP.

    Installs the hooks only; whether spans are actually diverted is decided on
    the first export (see :func:`_should_capture`), so a stock server keeps its
    normal tracing. Idempotent, and safe before mlflow tracing is initialised.
    """
    global _spool, _patched, _original_export, _original_flush, _experiment_id

    with _lock:
        if _patched:
            return
        import mlflow
        from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter

        _experiment_id = experiment_id
        _original_export = MlflowV3SpanExporter.export
        _original_flush = mlflow.flush_trace_async_logging

        def export(self, spans):
            if not _should_capture():
                return _original_export(self, spans)
            _ensure_spool()
            _capture(spans)  # deliberately not calling through: no OTLP traffic

        def flush_trace_async_logging(terminate=False):
            # Callers invoke this immediately before os._exit(0), so this is the
            # last chance to get spans off the machine.
            try:
                _original_flush(terminate=terminate)
            except Exception:  # noqa: BLE001
                _logger.debug("radt[mlflow-capture]: upstream flush failed", exc_info=True)
            if terminate:
                shutdown()

        MlflowV3SpanExporter.export = export
        mlflow.flush_trace_async_logging = flush_trace_async_logging
        _patched = True
        _logger.info(
            "radt[mlflow-capture]: mlflow spans will be batched into %s/ artifacts",
            ARTIFACT_DIR,
        )


def set_run(run_id):
    """Pin the run to upload to, rather than inferring it from trace metadata."""
    global _run_id
    if _spool is not None and run_id:
        _run_id = run_id
        _spool.set_run(run_id)


def shutdown():
    """Flush and upload everything spooled. Idempotent.

    Drains mlflow's own span queue first: spans it is still holding would
    otherwise arrive after the spool is closed and be dropped.
    """
    if _original_flush is not None:
        try:
            _original_flush(terminate=True)
        except Exception:  # noqa: BLE001
            _logger.debug("radt[mlflow-capture]: upstream flush failed", exc_info=True)
    with _lock:
        if _spool is None:
            return
        _spool.close()
        _logger.info("radt[mlflow-capture]: uploaded %s span event(s)", _spool.total)


def disable():
    """Leave mlflow tracing alone in this process, whatever a probe would say.

    Used by the mlflow-backend exporter process, whose entire purpose is to push
    spans through mlflow tracing -- a fresh interpreter (spawn) would otherwise
    re-decide on its own and divert them.
    """
    global _capturing

    _capturing = False


def is_enabled():
    return _patched


def _restore():
    """Undo the patches. For tests; production processes simply exit."""
    global _spool, _patched, _run_id, _capturing, _experiment_id

    with _lock:
        if not _patched:
            return
        import mlflow
        from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter

        MlflowV3SpanExporter.export = _original_export
        mlflow.flush_trace_async_logging = _original_flush
        _spool = None
        _patched = False
        _run_id = None
        _capturing = None
        _experiment_id = None
