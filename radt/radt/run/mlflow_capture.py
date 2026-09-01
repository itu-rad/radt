"""Divert ``mlflow.start_span`` spans into radT's artifact batches.

Patches ``MlflowV3SpanExporter.export`` -- the point every completed span
passes through -- so workloads instrumented with plain mlflow tracing produce
the same batches :mod:`radt.run.trace` writes, without streaming each span to
the server. Requires no changes to the workload.

``mlflow.flush_trace_async_logging`` is patched too: callers finish with
``os._exit(0)``, which skips atexit and background threads, so the upload has
to complete inside the flush they already call.
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
    """Locked: the export queue runs several workers, and an unguarded lazy init
    lets the loser of the race write to a spool nobody uploads."""
    global _spool

    with _lock:
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


def _records_for(spans):
    """Runs on the workload's thread, so it stays to dict copies and integer
    reads. Run attribution happens here because it reads mlflow's in-memory
    trace manager, which no longer holds the trace by the time a worker runs."""
    global _run_id

    records = []
    for span in spans:
        try:
            otel_trace_id = span.context.trace_id
            if _run_id is None:
                if resolved := _resolve_run_id(otel_trace_id):
                    _run_id = resolved
            records.extend(_span_records(span, otel_trace_id, _run_id))
        except Exception:  # noqa: BLE001 - never break the workload over tracing
            _logger.debug("radt[mlflow-capture]: failed to convert a span", exc_info=True)
    return records


def _write_records(records, run_id):
    """Spool and upload. Runs on the async queue's worker, never the ML thread."""
    spool = _ensure_spool()
    if run_id:
        spool.set_run(run_id)
    for record in records:
        spool.add(record)
    spool.maybe_roll()  # may upload a rolled batch -- worker thread, not the workload


def _should_capture():
    """Decided once, on the first exported span: the answer needs a server probe,
    which would otherwise slow every ``import radt`` whether it traces or not."""
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

    Installs the hooks only; :func:`_should_capture` decides on the first export
    whether to divert, so a stock server keeps its normal tracing. Idempotent,
    and safe to call before mlflow tracing is initialised.
    """
    global _spool, _patched, _original_export, _original_flush, _experiment_id

    with _lock:
        if _patched:
            return
        import mlflow
        from mlflow.tracing.export.async_export_queue import Task
        from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter

        _experiment_id = experiment_id
        _original_export = MlflowV3SpanExporter.export
        _original_flush = mlflow.flush_trace_async_logging

        def export(self, spans):
            if not _should_capture():
                return _original_export(self, spans)

            # export() runs on the span's own thread, so compression and upload
            # are handed to mlflow's async queue -- already radt's own uploader.
            records = _records_for(spans)
            if not records:
                return
            queue = getattr(self, "_async_queue", None)
            if queue is None:
                _write_records(records, _run_id)  # sync export: nothing to hand off to
                return
            queue.put(
                Task(
                    handler=_write_records,
                    args=(records, _run_id),
                    error_msg="Failed to spool radT span batch.",
                )
            )

        def flush_trace_async_logging(terminate=False):
            # Called immediately before os._exit(0): the last chance to upload.
            if not terminate:
                try:
                    _original_flush(terminate=False)
                except Exception:  # noqa: BLE001
                    _logger.debug("radt[mlflow-capture]: upstream flush failed", exc_info=True)
                return
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


def _drain_upstream():
    """Twice, and the order matters: mlflow flushes its span processors before
    its export queue, so the first pass hands us spans that enqueue new write
    tasks. Terminating on that pass would strand exactly those."""
    if _original_flush is None:
        return
    for terminate in (False, True):
        try:
            _original_flush(terminate=terminate)
        except Exception:  # noqa: BLE001
            _logger.debug("radt[mlflow-capture]: upstream flush failed", exc_info=True)


def _close_spool():
    with _lock:
        if _spool is None:
            return
        _spool.close()
        _logger.info("radt[mlflow-capture]: uploaded %s span event(s)", _spool.total)


def shutdown():
    """Drain mlflow, then flush and upload everything spooled. Idempotent."""
    _drain_upstream()
    _close_spool()


def disable():
    """Leave mlflow tracing alone here, whatever a probe would say.

    The mlflow-backend exporter process needs this: under spawn it is a fresh
    interpreter that would otherwise re-decide and divert the spans it exists
    to export.
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
