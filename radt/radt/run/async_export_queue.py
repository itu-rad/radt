import logging
import multiprocessing
import queue
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from mlflow.tracing.export.async_export_queue import Task
from mlflow.tracing import provider
from mlflow.entities.span import Span
from mlflow.entities.trace import Trace

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


class _TraceExportLogger(multiprocessing.Process):
    """
    Background process that batches and flushes trace export tasks from a queue.
    Batches spans by merging lists from tasks with the same callable.
    """

    def __init__(self, buffer, flush_interval=1.0):
        super().__init__(daemon=True)

        self._exporter = provider._get_trace_exporter()
        self._buffer = buffer
        self._flush_interval = float(flush_interval)
        self._stop_event = multiprocessing.Event()

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

        SEQUENCE_MODE = True  # for debugging
        if SEQUENCE_MODE:
            for task in to_flush:
                if task[0] == 0:
                    self._exporter._log_trace(t := Trace.from_dict(task[1]), [])
                    print("Logged trace:", t)
                else:
                    self._exporter._log_spans(
                        0, sl := [Span.from_dict(span) for span in task[1]]
                    )  # TODO: need support for custom exp ids
                    print("Logged spans:", sl)

            # Group tasks by handler, merging span lists from the second argument
            return True

        trace_list = []
        span_list = []

        for task in to_flush:
            if task[0] == 0:
                trace_list.append(Trace.from_dict(task[1]))
            else:
                span_list.extend(Span.from_dict(span) for span in task[1])

        # Execute batched trace uploads
        failed_traces = []
        for trace in trace_list:
            try:
                self._exporter._log_trace(trace, [])  # TODO: support custom prompts
            except Exception as e:
                _logger.error(f"Failed to log trace {trace.info.trace_id}: {e}")
                failed_traces.append((0, trace.to_dict()))

            # Requeue failed traces
            for failed_task in failed_traces:
                try:
                    self._buffer.put(failed_task)
                except Exception as e:
                    _logger.error(f"Failed to requeue trace: {e}")

        # Execute batched span uploads
        try:
            self._exporter._log_spans(
                0, span_list
            )  # TODO: need support for custom exp ids
            print("Logged:", span_list)
        except Exception as e:
            _logger.error(f"Failed to log spans batch: {e}")
            # On failure, requeue the span tasks
            span_dicts = [span.to_dict() for span in span_list]
            try:
                self._buffer.put((1, span_dicts))
            except Exception as requeue_error:
                _logger.error(f"Failed to requeue spans: {requeue_error}")

        return True

    def terminate(self):
        """Stop the process and wait for it to finish."""
        self._stop_event.set()
        self.join()


class AsyncTraceExportQueueV2:
    """
    A queue-based asynchronous tracing export processor.

    Queues tasks for batch processing. Tasks with the same callable and list-based
    spans arguments are automatically batched together for efficient export.
    """

    def __init__(self, flush_interval=5.0):
        self._buffer = None
        self._manager = None
        self._flush_interval = float(flush_interval)
        self._process = None
        self._is_active = False

    def put(self, task: Task):
        """Put a new task to the queue for processing."""
        if not self.is_active():
            self.activate()

        try:
            # Non-blocking put to avoid blocking the main application
            if "trace" in str(task.handler).lower():
                task = (0, task.args[0].to_dict())
            else:
                # spans
                task = (1, [span.to_dict() for span in task.args[1]])

            # for a in task.args:
            #     print("Putting task arg:", a)
            # print("Putting task arg", task.args[0])  # span.to_dict()
            self._buffer.put(task, block=False)
        except queue.Full:
            _logger.warning(
                "Trace export queue is full, trace will be discarded. "
                "Consider increasing the queue size or reducing trace volume."
            )

    def activate(self) -> None:
        """Activate the async queue to start processing tasks."""
        if self._is_active:
            return

        # Create queue here (lazy) so it's created in the parent process context
        if self._buffer is None:
            self._manager = multiprocessing.Manager()
            self._buffer = self._manager.Queue()

        self._process = _TraceExportLogger(self._buffer, self._flush_interval)
        self._process.start()
        self._is_active = True

    def is_active(self) -> bool:
        """Check if the queue is actively processing tasks."""
        return self._is_active

    def flush(self, terminate=False) -> None:
        """
        Flush the async queue by waiting for all tasks to be processed.

        Args:
            terminate: If True, shut down the process after flushing.
        """
        if not self.is_active():
            return

        if self._process is not None:
            # Signal the process to stop, which will trigger final flush
            self._process.terminate()
            # Wait for the process to finish flushing
            self._process.join(timeout=30)  # Give it up to 30 seconds to finish
            self._is_active = False

        # Restart if not terminating
        if not terminate:
            self.activate()
