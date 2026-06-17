import atexit
import logging
import queue
import sys
import threading

from mlflow.tracing.export.async_export_queue import Task

_logger = logging.getLogger(__name__)


# Monkey-patch mlflow's AsyncTraceExportQueue with our V2 implementation
def _patch_mlflow_async_export_queue():
    """Replace mlflow's AsyncTraceExportQueue with our optimized version."""
    try:
        import mlflow.tracing.export.async_export_queue as mlflow_async_module

        mlflow_async_module.AsyncTraceExportQueue = AsyncTraceExportQueueV2
        _logger.info(
            "Patched mlflow.tracing.export.async_export_queue.AsyncTraceExportQueue "
            "with AsyncTraceExportQueueV2"
        )
    except Exception as e:
        _logger.warning(
            f"Failed to patch mlflow.tracing.export.async_export_queue: {e}"
        )


class AsyncTraceExportQueueV2:
    """A thread-based asynchronous tracing export processor.

    ``put()`` is non-blocking: it appends the live ``Task`` onto an in-process
    queue and returns immediately. A single daemon thread drains the queue and
    forwards tasks to the mlflow exporter, coalescing every queued span into one
    ``_log_spans`` call per experiment per flush. This is the key win over native
    mlflow on a high-latency link: native fires ~one request per span, while this
    collapses a whole flush window into a single batched request.

    The flusher wakes on whichever comes first:
      * ``flush_interval`` seconds elapsing (latency bound), or
      * the queue reaching ``max_batch_size`` (throughput bound).

    Trace export is network (I/O) bound, so a thread - not a process - is the
    right tool: no per-span serialization, no IPC, and the GIL is released
    during the network call.
    """

    def __init__(self, flush_interval=0.2, max_batch_size=100, shutdown_timeout=30.0):
        self._queue = queue.Queue()
        self._flush_interval = float(flush_interval)
        self._max_batch_size = int(max_batch_size)
        # Hard upper bound on how long shutdown will wait for the final drain, so
        # the process always auto-stops even if the network has stalled.
        self._shutdown_timeout = float(shutdown_timeout)

        self._thread = None
        self._is_active = False

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state_lock = threading.Lock()
        self._flush_lock = threading.Lock()

        # Register at construction (NOT lazily in activate()) so our flush runs in
        # the correct atexit LIFO order: AFTER mlflow's tracer-provider shutdown
        # force-flushes its final buffered spans into us. Registering on first
        # put() reverses that order and silently drops the shutdown spans.
        atexit.register(self._atexit_callback)

    def put(self, task: Task):
        """Queue a task for export. Non-blocking."""
        if not self.is_active():
            # Already terminated (shutdown in progress): don't revive the worker -
            # nothing would ever drain it again. Export inline so late spans (e.g.
            # from the tracer provider's shutdown force-flush) are not lost.
            if self._stop_event.is_set():
                self._handle_inline(task)
                return
            self.activate()

        self._queue.put(task)

        # Wake the flusher early once enough work has accumulated.
        if self._queue.qsize() >= self._max_batch_size:
            self._wake_event.set()

    def activate(self) -> None:
        """Start the background flush thread."""
        with self._state_lock:
            if self._is_active:
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="radt-trace-export", daemon=True
            )
            self._thread.start()
            self._is_active = True

    def is_active(self) -> bool:
        """Check if the queue is actively processing tasks."""
        return self._is_active

    def _run(self):
        """Drain and flush the queue until stopped."""
        while not self._stop_event.is_set():
            # Wait for the interval to elapse or an early wake (batch full).
            self._wake_event.wait(self._flush_interval)
            self._wake_event.clear()
            try:
                self._flush_once()
            except Exception as e:
                _logger.error(f"TraceExportLogger error during flush: {e}")

        # Final drain on stop: loop until the queue stays empty, to catch tasks
        # enqueued while we were stopping.
        while not self._queue.empty():
            try:
                self._flush_once()
            except BaseException as e:
                _logger.error(f"TraceExportLogger final flush error: {e}")
                break

    def _drain_queue(self):
        """Drain all currently queued tasks without blocking."""
        drained = []
        try:
            while True:
                drained.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        return drained

    @staticmethod
    def _is_trace_task(task: Task) -> bool:
        # mlflow enqueues either exporter._log_trace or exporter._log_spans as the handler.
        return getattr(task.handler, "__name__", "") == "_log_trace"

    @staticmethod
    def _handle_inline(task: Task) -> None:
        """Export a single task synchronously (used for late shutdown spans)."""
        try:
            task.handler(*task.args)
        except BaseException as e:
            _logger.warning(f"Inline trace export failed during shutdown: {e}")

    def _flush_once(self):
        """Export everything currently queued, batching spans per experiment.

        Span tasks carry ``args == (experiment_id, spans)``; we merge the spans
        of every queued task that shares an ``experiment_id`` into a single
        ``_log_spans(experiment_id, spans)`` call. Trace-info tasks are infrequent
        and forwarded individually.

        Guarded by ``_flush_lock`` so a synchronous ``flush()`` from another
        thread never exports concurrently with the worker thread. Each task's
        bound handler is invoked directly, so this never has to resolve (and
        possibly mismatch) the global exporter instance.
        """
        with self._flush_lock:
            tasks = self._drain_queue()
            if not tasks:
                return

            trace_tasks = []
            spans_by_experiment = {}
            log_spans_handler = None

            for task in tasks:
                if self._is_trace_task(task):
                    trace_tasks.append(task)
                else:
                    experiment_id, spans = task.args[0], task.args[1]
                    log_spans_handler = task.handler  # bound exporter._log_spans
                    spans_by_experiment.setdefault(experiment_id, []).extend(spans)

            # Trace-info uploads first; mlflow's _log_trace already swallows its
            # own network errors, so a failure here won't sink the batch.
            for task in trace_tasks:
                task.handler(*task.args)

            # One batched request per experiment.
            if log_spans_handler is not None:
                for experiment_id, spans in spans_by_experiment.items():
                    log_spans_handler(experiment_id, spans)

    def flush(self, terminate=False) -> None:
        """Export queued tasks.

        Args:
            terminate: If True, stop the worker thread after the final drain.
        """
        if not self.is_active():
            return

        if not terminate:
            # Best-effort synchronous drain of the current backlog.
            try:
                self._flush_once()
            except BaseException as e:
                _logger.error(f"Error during synchronous flush: {e}")
            return

        # Terminating: hand the final drain to the worker thread and just wait on
        # it. We deliberately do NOT call _flush_once() here too - two threads
        # contending on _flush_lock over a slow/stalled socket can wedge shutdown.
        # The join timeout guarantees the process auto-stops regardless.
        pending = self._queue.qsize()
        if pending:
            print(
                f"radt: flushing {pending} pending trace task(s) to server "
                f"(up to {self._shutdown_timeout:.0f}s; Ctrl+C to skip)...",
                file=sys.stderr,
                flush=True,
            )

        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            try:
                self._thread.join(timeout=self._shutdown_timeout)
            except BaseException:
                pass
            if self._thread.is_alive():
                _logger.warning(
                    "radt trace export did not finish within "
                    f"{self._shutdown_timeout:.0f}s; abandoning remaining traces "
                    "to avoid blocking shutdown."
                )
        self._is_active = False

    def _atexit_callback(self) -> None:
        """Flush remaining traces on program exit.

        Must never raise: atexit callbacks that propagate an exception (notably
        KeyboardInterrupt on Ctrl+C, which is a BaseException) print an ugly
        "Exception ignored in atexit callback" traceback.
        """
        try:
            self.flush(terminate=True)
        except BaseException as e:
            try:
                _logger.error(f"Error flushing trace export queue on exit: {e}")
            except Exception:
                pass
