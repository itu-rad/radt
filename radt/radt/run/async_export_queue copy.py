import logging
import threading
import queue
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from mlflow.tracing.export.async_export_queue import Task

_logger = logging.getLogger(__name__)


# Monkey-patch mlflow's AsyncTraceExportQueue with our V2 implementation
def _patch_mlflow_async_export_queue():
    """Replace mlflow's AsyncTraceExportQueue with our optimized version."""
    try:
        import mlflow.tracing.export.async_export_queue as mlflow_async_module

        mlflow_async_module.AsyncTraceExportQueue = AsyncTraceExportQueueV2
        print(
            "Patched mlflow.tracing.export.async_export_queue.AsyncTraceExportQueue with AsyncTraceExportQueueV2"
        )
    except Exception as e:
        _logger.warning(
            f"Failed to patch mlflow.tracing.export.async_export_queue: {e}"
        )


class _TraceExportLogger(threading.Thread):
    """
    Background thread that batches and flushes trace export tasks from a queue.
    Batches spans by merging lists from tasks with the same callable.
    """

    def __init__(self, buffer, flush_interval=5.0):
        super().__init__(daemon=True)
        self._buffer = buffer
        self._flush_interval = float(flush_interval)
        self._stop_event = threading.Event()

    def run(self):
        """Periodically flush tasks from the queue until stopped."""
        while not self._stop_event.is_set():
            try:
                self._flush_once()
            except Exception as e:
                _logger.error(f"TraceExportLogger error during flush: {e}")
            self._stop_event.wait(self._flush_interval)

        # On stop: repeatedly flush until no remaining tasks
        while True:
            try:
                flushed_any = self._flush_once(final=True)
            except Exception as e:
                _logger.error(f"TraceExportLogger final flush error: {e}")
                flushed_any = False
            if not flushed_any:
                break

    def _drain_queue(self):
        """Drain all currently queued items into a list without blocking."""
        drained = []
        try:
            while True:
                item = self._buffer.get_nowait()
                drained.append(item)
        except queue.Empty:
            pass
        return drained

    def _flush_once(self, final=False):
        """Drain, batch, and execute tasks from the queue."""
        to_flush = self._drain_queue()
        if not to_flush:
            return False

        # Group tasks by handler, merging span lists from the second argument
        tasks_by_handler = {}
        for task in to_flush:
            handler_key = id(task.handler)
            if handler_key not in tasks_by_handler:
                tasks_by_handler[handler_key] = {
                    "handler": task.handler,
                    "spans": [],
                    "error_msg": task.error_msg,
                }
            # Merge spans from this task's args
            # Assuming args[1] is the list of spans to export
            if len(task.args) > 1 and isinstance(task.args[1], list):
                tasks_by_handler[handler_key]["spans"].extend(task.args[1])

        # Execute batched tasks
        try:
            for task_info in tasks_by_handler.values():
                batched_task = Task(
                    handler=task_info["handler"],
                    args=(task_info["spans"],),
                    error_msg=task_info["error_msg"],
                )
                batched_task.handle()
            return True
        except Exception as e:
            # On failure, requeue the original tasks at the front of the queue
            for task in to_flush:
                try:
                    self._buffer.put(task)
                except Exception:
                    _logger.error(f"Failed to requeue task: {e}")
            raise

    def stop(self):
        """Stop the thread."""
        self._stop_event.set()
        self.join(timeout=5)


class AsyncTraceExportQueueV2:
    """
    A queue-based asynchronous tracing export processor.

    Queues tasks for batch processing. Tasks with the same callable and list-based
    spans arguments are automatically batched together for efficient export.
    """

    def __init__(self, flush_interval=5.0):
        self._buffer = queue.Queue()
        self._flush_interval = float(flush_interval)
        self._thread = None
        self._is_active = False
        self._lock = threading.Lock()

    def put(self, task: Task):
        """Put a new task to the queue for processing."""
        if not self.is_active():
            self.activate()

        try:
            # Non-blocking put to avoid blocking the main application
            self._buffer.put(task, block=False)
        except queue.Full:
            _logger.warning(
                "Trace export queue is full, trace will be discarded. "
                "Consider increasing the queue size or reducing trace volume."
            )

    def activate(self) -> None:
        """Activate the async queue to start processing tasks."""
        with self._lock:
            if self._is_active:
                return

            self._thread = _TraceExportLogger(self._buffer, self._flush_interval)
            self._thread.start()
            self._is_active = True

    def is_active(self) -> bool:
        """Check if the queue is actively processing tasks."""
        return self._is_active

    def flush(self, terminate=False) -> None:
        """
        Flush the async queue.

        Args:
            terminate: If True, shut down the thread after flushing.
        """
        if not self.is_active():
            return

        with self._lock:
            if self._thread is not None:
                self._thread.stop()
                self._is_active = False

            # Restart if not terminating
            if not terminate:
                self.activate()


_patch_mlflow_async_export_queue()
