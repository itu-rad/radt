"""Radt's multiprocessing span tracing: a child process reconstructs and exports

The workload process never touches mlflow/OpenTelemetry for tracing. It
calls :func:`span`, which only attaches timestamps, assigns correlation
ids, and drops a small tuple onto a `multiprocessing.Queue`. A radt-owned child
process (:class:`_TraceExporter`, built exactly like `_MLFlowLogger`)
reconstructs real mlflow spans from those events and exports them. All
mlflow span components(SimpleSpanProcessor conversion, the async upload threads,
GC over span objects) live in the child, off the workload's critical path.

Public API:
    start(experiment_id)   create the queue + exporter process (MAIN THREAD, once,
                           before any workload thread / CUDA init).
    span(name, attributes) context manager wrapping a unit of work.
    shutdown(timeout)      flush + join the exporter.
"""

import contextlib
import contextvars
import logging
import multiprocessing
import os
import queue
import threading
import time

from .benchmark import _arm_parent_death_signal

_logger = logging.getLogger(__name__)

# Per-thread stack of (trace_id, span_id) for the currently-open spans. A fresh
# thread gets the empty default (threads do NOT inherit contextvar state), so its
# spans start new root traces — exactly OpenTelemetry's per-thread context
# behavior (cross-thread spans are roots correlated by their flow_id attributes).
_span_stack: "contextvars.ContextVar[tuple]" = contextvars.ContextVar(
    "radt_trace_span_stack", default=()
)

_STOP = "__radt_trace_stop__"
_MAX_QUEUE = int(os.getenv("RADT_TRACE_PROC_QUEUE_SIZE", "200000"))

_queue = None  # multiprocessing.Queue to the exporter
_proc = None  # _TraceExporter
_experiment_id = None
_enabled = False
_dropped = 0

_id_lock = threading.Lock()
_id_counter = 0


def _next_id():
    """A process-unique correlation id (match a parent event to its children in the event stream)."""
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter


def _emit(item):
    global _dropped
    q = _queue
    if q is None:
        return
    try:
        q.put_nowait(item)
    except queue.Full:
        _dropped += 1


@contextlib.contextmanager
def span(name, attributes=None):
    """Wrap a unit of work as a span. ~µs of workload-thread cost (two clock reads
    + a queue append); no mlflow/OTel object is created here."""
    if not _enabled or _queue is None:
        yield None
        return
    stack = _span_stack.get()
    sid = _next_id()
    if stack:
        trace_id, parent_id = stack[-1][0], stack[-1][1]
    else:
        trace_id, parent_id = sid, None  # new root: trace id := own id
    _emit(("s", sid, parent_id, trace_id, name, attributes, time.time_ns()))
    token = _span_stack.set(stack + ((trace_id, sid),))
    try:
        yield sid
    finally:
        _span_stack.reset(token)
        _emit(("e", sid, None, None, None, None, time.time_ns()))


def start(experiment_id=None):
    """Create the queue + exporter process. Call ONCE from the main thread, before
    any pipeline thread or CUDA init. Idempotent."""
    global _queue, _proc, _enabled, _experiment_id
    if _proc is not None:
        return
    _experiment_id = (
        str(experiment_id) if experiment_id not in (None, "", 0, "0") else None
    )
    try:
        _queue = multiprocessing.Queue(maxsize=_MAX_QUEUE)
    except OSError:
        # macOS caps a BoundedSemaphore at SEM_VALUE_MAX (~32767); Linux allows the
        # large bound. Fall back to a platform-safe cap.
        _queue = multiprocessing.Queue(maxsize=32000)
        _logger.warning(
            "radt[proc-trace]: multiprocessing.Queue(maxsize=%d) failed, "
            "falling back to 32000 (macOS semaphore limit)",
            _MAX_QUEUE,
        )
    _proc = _TraceExporter(_queue, _experiment_id)
    _arm_parent_death_signal(_proc)  # SIGKILL child if workload dies (fork/Linux)
    _proc.start()
    _enabled = True


def set_run(run_id):
    """Associate subsequently-created traces with an mlflow run so they nest under
    it in the UI (sets the trace's ``mlflow.sourceRun`` metadata, exactly as the
    in-process path does when a run is active). Emitted as a FIFO event so the child
    activates the run before any span arrives — call it after the run exists and
    before the workload starts. No-op if tracing is disabled or run_id is falsy."""
    if not _enabled or _queue is None or not run_id:
        return
    _emit(("set_run", run_id, None, None, None, None, None))


def shutdown(timeout=None):
    """Signal end-of-stream and wait for the exporter to drain fully. The child
    self-terminates via its progress/stall guard, so the join is just a generous
    backstop (RADT_TRACE_JOIN_TIMEOUT, default 1800s) — it does not itself cap the
    drain. The workload is already done here, so this wait is post-run wall time."""
    global _enabled
    _enabled = False
    if _proc is None:
        return
    if timeout is None:
        timeout = float(os.environ.get("RADT_TRACE_JOIN_TIMEOUT", "1800"))
    try:
        _queue.put_nowait(
            (_STOP, None, None, None, None, None, None)
        )  # FIFO end marker
        _queue.close()
        _queue.join_thread()  # flush the feeder so all events land
    except Exception:
        _logger.exception("radt[proc-trace]: queue close/join failed")
    _proc.terminate(timeout=timeout)  # stop-Event backstop + generous join
    if _dropped:
        _logger.warning(
            "radt[proc-trace]: dropped %d span event(s) on overflow "
            "(raise RADT_TRACE_PROC_QUEUE_SIZE)",
            _dropped,
        )


def _stringify(attrs):
    if not attrs:
        return None
    return {str(k): str(v) for k, v in attrs.items()}


def _find_v2_queue():
    """Best-effort reach into mlflow's active MlflowV3SpanExporter._async_queue —
    the radt V2 uploader instance — so the drain can watch its progress."""
    try:
        from opentelemetry import trace as _otel

        tp = _otel.get_tracer_provider()
        proc = getattr(tp, "_active_span_processor", None)
        procs = getattr(proc, "_span_processors", None) or ([proc] if proc else [])
        for p in procs:
            exp = getattr(p, "span_exporter", None) or getattr(
                p, "_span_exporter", None
            )
            q = getattr(exp, "_async_queue", None)
            if q is not None:
                return q
    except Exception:
        _logger.exception("radt[proc-trace]: failed to find V2 queue")
    return None


def _drain_until_idle(q):
    """Drain the uploader to the remote automatically: keep going as long as the
    backlog is shrinking OR uploads keep completing. No fixed total timeout — only
    a STALL guard: bail if there's been no progress for RADT_TRACE_DRAIN_STALL_S
    (default 30s), so a slow-but-live remote still gets every span while an
    unresponsive one can't hang shutdown forever."""
    guard = float(os.environ.get("RADT_TRACE_DRAIN_STALL_S", "30"))
    check = 1.0
    stalled = 0.0
    prev_pending, prev_done = -1, -1
    while True:
        try:
            pending = q._queue.qsize()
            done = q._stat_tasks
        except Exception:  # noqa: BLE001
            _logger.exception("radt[proc-trace]: failed to get queue stats")
            return None
        _logger.debug(
            "radt[proc-trace][drain] pending=%s uploaded=%s stalled=%.0fs",
            pending,
            done,
            stalled,
        )
        # Progress = the queue shrank OR an upload completed. NOTE pending can sit
        # constant while uploads complete (the worker drains the whole queue into
        # one blocking _run_parallel batch), so `done` increasing is the true
        # liveness signal — don't exit merely because pending hit 0.
        if pending < prev_pending or done > prev_done:
            stalled = 0.0
        else:
            stalled += check
            # Empty queue + settled uploads → done quickly; a wedged remote →
            # only give up after the full guard window.
            window = min(guard, 5.0) if pending == 0 else guard
            if stalled >= window:
                if pending > 0:
                    _logger.warning(
                        "radt[proc-trace]: drain STALLED — no progress for %.0fs, %s still pending; giving up",
                        guard,
                        pending,
                    )
                return done
        prev_pending, prev_done = pending, done
        time.sleep(check)


class _TraceExporter(multiprocessing.Process):
    """Radt-owned process that reconstructs + exports mlflow spans from events.

    Mirrors ``benchmark._MLFlowLogger``: daemonic, drains an mp.Queue, stop-Event
    + bounded-join shutdown. All mlflow/OTel span machinery runs HERE.
    """

    def __init__(self, event_queue, experiment_id):
        super().__init__(daemon=True, name="radt-proc-trace")
        self._q = event_queue
        self._experiment_id = experiment_id
        self._stop_event = multiprocessing.Event()

    def run(self):
        import radt  # noqa: F401  — applies the V2 async-upload patch in THIS process

        # Capture the actual V2 uploader instance mlflow constructs (via radt's
        # patch), so the drain can watch its real progress. mlflow keeps its own
        # tracer provider — not the global OTel one — so reaching the exporter by
        # traversal is unreliable; hooking the class __init__ is robust.
        captured = {}
        try:
            from radt.run import async_export_queue as _aeq

            _V2 = _aeq.AsyncTraceExportQueueV2
            _orig_v2_init = _V2.__init__

            def _capturing_init(self, *a, **k):
                _orig_v2_init(self, *a, **k)
                captured["q"] = self

            _V2.__init__ = _capturing_init
        except Exception:  # noqa: BLE001
            _logger.exception(
                "radt[proc-trace]: failed to patch AsyncTraceExportQueueV2"
            )

        import mlflow
        from mlflow import start_span_no_context

        # Defensive: a forked child could inherit a disabled tracer (e.g. if the
        # parent ran the CHOREO_DISABLE_TRACING path); we DO want spans here.
        try:
            mlflow.tracing.enable()
        except Exception:  # noqa: BLE001
            _logger.exception("radt[proc-trace]: failed to enable mlflow tracing")

        if self._experiment_id:
            os.environ["MLFLOW_EXPERIMENT_ID"] = self._experiment_id
            try:
                mlflow.set_experiment(experiment_id=self._experiment_id)
            except Exception:
                _logger.exception(
                    "radt[proc-trace]: set_experiment(%s) failed",
                    self._experiment_id,
                )

        live = {}  # sid -> LiveSpan
        started = ended = errors = 0
        while True:
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            phase = item[0]
            if phase == _STOP:
                self._stop_event.set()
                continue
            if phase == "set_run":
                # Activate the parent's run in THIS process so each trace's on_start
                # records mlflow.sourceRun = run_id via the normal path -> traces nest
                # under the run, matching the in-process arm. Don't end_run (the parent
                # owns the run lifecycle).
                try:
                    mlflow.start_run(run_id=item[1])
                except Exception:
                    _logger.exception(
                        "radt[proc-trace]: start_run(%s) failed",
                        item[1],
                    )
                continue
            try:
                if phase == "s":
                    _, sid, parent_id, _trace_id, name, attrs, ts = item
                    parent = live.get(parent_id) if parent_id is not None else None
                    is_root = parent_id is None
                    live[sid] = start_span_no_context(
                        name=name,
                        parent_span=parent,
                        experiment_id=self._experiment_id if is_root else None,
                        attributes=_stringify(attrs),
                        start_time_ns=ts,
                    )
                    started += 1
                elif phase == "e":
                    _, sid, _, _, _, _, ts = item
                    sp = live.pop(sid, None)
                    if sp is not None:
                        sp.end(end_time_ns=ts)
                        ended += 1
            except Exception:  # noqa: BLE001
                errors += 1
                if errors <= 5:
                    _logger.exception("radt[proc-trace]: span replay error")

        # End any spans left open (workload torn down mid-span).
        for sp in live.values():
            try:
                sp.end(end_time_ns=time.time_ns())
                ended += 1
            except Exception:  # noqa: BLE001
                _logger.exception("radt[proc-trace]: span end error on shutdown")
        # Automatic progress-guarded drain of the upload backlog to the remote.
        drain_t0 = time.perf_counter()
        uploaded = _drain_until_idle(captured.get("q") or _find_v2_queue())
        try:
            mlflow.flush_trace_async_logging(terminate=True)  # finalize the worker
        except Exception:
            _logger.exception("radt[proc-trace]: flush_trace_async_logging failed")
        if uploaded is None:
            _logger.info(
                "radt[proc-trace]: exporter done — started=%s ended=%s errors=%s drain=%.1fs",
                started,
                ended,
                errors,
                time.perf_counter() - drain_t0,
            )
        else:
            _logger.info(
                "radt[proc-trace]: exporter done — started=%s ended=%s errors=%s uploaded=%s drain=%.1fs",
                started,
                ended,
                errors,
                uploaded,
                time.perf_counter() - drain_t0,
            )

    def terminate(self, timeout=30.0):
        # Bounded join; daemonic, so an overrun is abandoned at interpreter exit.
        self._stop_event.set()
        self.join(timeout=timeout)
