import atexit
import logging
import os
import queue
import sys
import threading
import time

from mlflow.tracing.export.async_export_queue import Task

_logger = logging.getLogger(__name__)


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


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

    def __init__(self, flush_interval=0.2, max_batch_size=100, shutdown_timeout=30.0,
                 max_queue_size=100_000):
        # Bounded queue: on a slow link the upload can't keep up with the span
        # rate on a long (multi-hour) run, so an unbounded queue grows without
        # limit and OOMs. When full we drop new tasks (and count them) rather
        # than block the workload thread. 0/negative env value = unbounded.
        self._max_queue_size = int(_env_float("RADT_TRACE_MAX_QUEUE_SIZE", max_queue_size))
        self._queue = queue.Queue(
            maxsize=self._max_queue_size if self._max_queue_size > 0 else 0
        )
        self._dropped = 0
        self._flush_interval = _env_float("RADT_TRACE_FLUSH_INTERVAL", flush_interval)
        self._max_batch_size = int(_env_float("RADT_TRACE_MAX_BATCH_SIZE", max_batch_size))
        # Hard upper bound on how long shutdown will wait for the final drain, so
        # the process always auto-stops even if the network has stalled.
        self._shutdown_timeout = _env_float("RADT_TRACE_SHUTDOWN_TIMEOUT", shutdown_timeout)
        # Concurrency for the per-flush uploads (trace blobs are latency-bound and
        # independent, so overlapping them is the main throughput lever).
        self._upload_workers = max(1, int(_env_float("RADT_TRACE_UPLOAD_WORKERS", 8)))
        # Set RADT_TRACE_DEBUG=1 for a per-flush breakdown of where time goes.
        self._debug = os.environ.get("RADT_TRACE_DEBUG") == "1"
        if self._debug:
            # Surface mlflow's own swallowed export failures - notably _log_spans,
            # which logs failures at DEBUG (invisible by default), so a server that
            # silently drops spans looks like a clean run with empty traces. Scope
            # this to the export logger only; enabling DEBUG on all of mlflow floods
            # the output with unrelated chatter (e.g. mlflow.tracking.fluent).
            logging.getLogger("mlflow.tracing.export").setLevel(logging.DEBUG)

        self._stats_lock = threading.Lock()
        # Cumulative diagnostics.
        self._stat_tasks = 0
        self._stat_seconds = 0.0
        self._stat_errors = 0

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

        try:
            # Non-blocking: never stall the workload thread. Drop on overflow.
            self._queue.put_nowait(task)
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                print(
                    f"radt[trace]: queue full ({self._max_queue_size}); dropped "
                    f"{self._dropped} trace task(s) — uploads can't keep up with the "
                    f"span rate on this network. Raise RADT_TRACE_MAX_QUEUE_SIZE / "
                    f"RADT_TRACE_UPLOAD_WORKERS or reduce span volume.",
                    file=sys.stderr, flush=True,
                )
            return

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

        # One-line shutdown summary so the bottleneck is visible without debug mode.
        leftover = self._queue.qsize()
        print(
            f"radt[trace]: exported {self._stat_tasks} task(s) "
            f"[{self._stat_seconds:.1f}s work across {self._upload_workers} "
            f"worker(s)]; errors={self._stat_errors}; dropped={self._dropped}; "
            f"not-flushed={leftover}",
            file=sys.stderr, flush=True,
        )

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
    def _handle_inline(task: Task) -> None:
        """Export a single task synchronously (used for late shutdown spans)."""
        try:
            task.handler(*task.args)
        except BaseException as e:
            _logger.warning(f"Inline trace export failed during shutdown: {e}")

    def _timed_call(self, task: Task):
        """Run one task exactly as native mlflow would, recording stats."""
        t0 = time.monotonic()
        err = 0
        try:
            task.handler(*task.args)
        except BaseException as e:
            err = 1
            _logger.warning(f"trace export call failed: {e}")
        dt = time.monotonic() - t0
        with self._stats_lock:
            self._stat_tasks += 1
            self._stat_seconds += dt
            self._stat_errors += err

    def _run_parallel(self, tasks):
        """Execute tasks concurrently on a pool of daemon threads.

        Each task is run verbatim (``task.handler(*task.args)``) - we do NOT
        batch, reorder, or rewrite args. Merging spans across traces into one
        ``_log_spans`` call diverged from native mlflow and made the 3.5.x server
        fall back to artifact storage (empty SQL ``spans`` table). The throughput
        win comes purely from overlapping these latency-bound uploads.

        Daemon threads (not a ThreadPoolExecutor, whose non-daemon threads
        register an atexit join) so a stalled upload can never block shutdown.
        """
        n_workers = min(self._upload_workers, len(tasks))
        if n_workers <= 1:
            for task in tasks:
                self._timed_call(task)
            return

        local_q = queue.Queue()
        for task in tasks:
            local_q.put(task)

        def worker():
            while True:
                try:
                    task = local_q.get_nowait()
                except queue.Empty:
                    return
                self._timed_call(task)

        threads = [
            threading.Thread(target=worker, name="radt-trace-upload", daemon=True)
            for _ in range(n_workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _flush_once(self):
        """Export everything currently queued.

        Every queued task is run exactly as native mlflow would
        (``task.handler(*task.args)``); the only change from native is that the
        uploads for this flush are overlapped across a daemon-thread pool. See
        ``_run_parallel`` for why we no longer batch/reorder.

        Guarded by ``_flush_lock`` so a synchronous ``flush()`` from another
        thread never exports concurrently with the worker thread.
        """
        with self._flush_lock:
            tasks = self._drain_queue()
            if not tasks:
                return

            self._run_parallel(tasks)

            if self._debug:
                print(
                    f"radt[trace]: flushed {len(tasks)} task(s) across "
                    f"{min(self._upload_workers, len(tasks))} worker(s); "
                    f"cum errors={self._stat_errors}",
                    file=sys.stderr, flush=True,
                )

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
