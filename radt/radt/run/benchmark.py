import os
import sys
import types
from time import time
from subprocess import PIPE, Popen
import mlflow
import threading
from mlflow.tracking import MlflowClient
from mlflow.entities import Metric as MlflowMetric
from collections import deque
import queue
import multiprocessing

from .listeners import listeners


def dummy(*args, **kwargs):
    return


def execute_command(cmd: str):
    """Execute a command

    Args:
        cmd (str or list): Command to run

    Returns:
        str: stdout output of the command
    """

    if isinstance(cmd, str):
        cmd = cmd.split()

    env = os.environ.copy()

    result = []
    with Popen(cmd, stdout=PIPE, bufsize=1, universal_newlines=True, env=env) as p:
        result.extend(p.stdout)

        if p.returncode != 0:
            pass

    return result


# new: background logger thread that flushes metrics via MlflowClient.log_batch
class MLFlowLogger(threading.Thread):
    def __init__(self, run_id, buffers, lock=None, flush_interval=5.0, max_batch_size=1000):
        super().__init__(daemon=True)
        self.run_id = run_id
        # buffers is now a queue.Queue instance
        self._buffers = buffers
        # locks are obsolete with queue-based design
        self._lock = lock
        # enforce flush_interval (kept as-is)
        self._flush_interval = float(flush_interval)
        self._stop_event = threading.Event()
        self._client = MlflowClient()
        self._max_batch_size = int(max_batch_size)

    def run(self):
        # Periodically flush buffer until stopped
        while not self._stop_event.is_set():
            try:
                self._flush_once()
            except Exception as e:
                # keep running on errors
                print(f"MLFlowLogger error during flush: {e}")
            # try:
            # except Exception as e:
            #     print(f"MLFlowLogger error during flush: {e}")
            self._stop_event.wait(self._flush_interval)

        # On stop: repeatedly swap+flush until no remaining metrics
        while True:
            try:
                flushed_any = self._flush_once(final=True)
            except Exception as e:
                print(f"MLFlowLogger final flush error: {e}")
                flushed_any = False
            if not flushed_any:
                break

    def _drain_queue(self):
        # Drain all currently queued items into a list without blocking producers.
        drained = []
        try:
            while True:
                item = self._buffers.get_nowait()
                drained.append(item)
        except queue.Empty:
            pass
        return drained

    def _flush_once(self, final=False):
        # Drain the queue into a local list and process outside the queue
        to_flush = self._drain_queue()
        if not to_flush:
            return False

        # Normalize items to dicts for conversion / requeue on failure
        metric_dicts = []
        for m in to_flush:
            metric_dicts.append({
                "key": m.get("key") or m.get("name"),
                "value": float(m.get("value")),
                "timestamp": int(m.get("timestamp")),
                "step": int(m.get("step", 0)),
            })

        # Send in chunks if needed
        try:
            if not metric_dicts:
                return True
            # convert to Mlflow Metric entities and send in chunks
            for i in range(0, len(metric_dicts), self._max_batch_size):
                batch_dicts = metric_dicts[i:i + self._max_batch_size]
                batch_entities = [
                    MlflowMetric(d["key"], d["value"], d["timestamp"], d["step"]) for d in batch_dicts
                ]
                # use underlying store API to log a batch of metrics
                self._client._tracking_client.store.log_batch(run_id=self.run_id, metrics=batch_entities, params=[], tags=[])
            return True
        except Exception:
            # On failure, requeue the metrics at the front of the current write buffer
            # Requeue failed items back into the queue (order will be preserved relative to this batch)
            for original in to_flush:
                try:
                    self._buffers.put(original)
                except Exception:
                    # best-effort; if put fails, drop the metric
                    pass
            raise

    def terminate(self):
        self._stop_event.set()
        self.join()


class RADTBenchmark:
    def __init__(self):
        """
        Context manager for a run.
        Will track ML operations while active.
        """
        if "RADT_MAX_EPOCH" not in os.environ:
            return

        try:
            run = mlflow.start_run(run_id=os.getenv("RADT_RUN_ID"))
        except Exception as e:
            run = mlflow.active_run()
        self.run_id = run.info.run_id

        # Shared in-memory swap buffers and synchronization primitives for main metrics
        # main buffer is a thread-safe queue
        self._buffers_main = queue.Queue()
        # fallback buffer used when main batch logger is not active; merged on next log call
        self._fallback_buffer_main = []

        # Separate queue for listeners so listener traffic doesn't interfere with main metrics
        # Use multiprocessing.Queue so listener Process instances can put() into it across processes.
        self._buffers_listeners = multiprocessing.Queue()
        self._fallback_buffer_listeners = []

        # enable batch logger (always enabled when RADT is active; can be made conditional)
        self._batch_logger_enabled = True
        self._batch_flush_interval = float(os.getenv("RADT_BATCH_FLUSH_INTERVAL", "5.0"))

        # Capture (package) versions for pip, conda, smi
        try:
            self.log_text("".join(execute_command("pip freeze")), "pip.txt")
        except FileNotFoundError as e:
            pass

        try:
            self.log_text("".join(execute_command("conda list")), "conda.txt")
        except Exception as e: # Either a FileNotFoundError or DirectoryNotACondaEnvironmentError
            print(f"Conda not found or unreachable. Continuing without conda list. ({e})")
            pass

        try:
            self.log_text("".join(execute_command("nvidia-smi")), "smi.txt")
        except FileNotFoundError as e:
            pass

    def __dir__(self):
        return dir(super()) + dir(mlflow)

    def __getattribute__(self, name):
        """Get attribute, overwrites methods and functions
        if RADT has not been loaded"""
        try:
            att = super().__getattribute__(name)
        except AttributeError:
            att = getattr(mlflow, name)

        if "RADT_MAX_EPOCH" not in os.environ:
            if isinstance(att, types.MethodType) or isinstance(att, types.FunctionType):
                return dummy
        return att

    def __enter__(self):
        if "RADT_MAX_EPOCH" not in os.environ:
            return self

        self.threads = []
        self.max_epoch = int(os.getenv("RADT_MAX_EPOCH"))
        self.max_time = time() + int(os.getenv("RADT_MAX_TIME"))

        # spawn the batch logger thread and include in threads list
        if getattr(self, "_batch_logger_enabled", False):
            # main logger handles user-invoked log_metric/log_metrics
            main_logger = MLFlowLogger(self.run_id, self._buffers_main, flush_interval=self._batch_flush_interval)
            self.threads.append(main_logger)
            # listener logger accepts metrics from listeners
            listener_logger = MLFlowLogger(self.run_id, self._buffers_listeners, flush_interval=self._batch_flush_interval)
            self.threads.append(listener_logger)
        else:
            listener_logger = None

        # Spawn threads for enabled listeners
        for listener_name, listener_class in listeners.items():
            listener_env_key = f"RADT_LISTENER_{listener_name.upper()}"
            if os.getenv(listener_env_key) == "True":
                os.environ[listener_env_key] = "False"
                # pass the listener queue (buffer) as second arg; listeners will put() into it
                inst = listener_class(self.run_id, self._buffers_listeners)
                self.threads.append(inst)    

        for thread in self.threads:
            thread.start()

        return self

    def __exit__(self, type, value, traceback):
        # Terminate listeners and run
        if "RADT_MAX_EPOCH" not in os.environ:
            return
        # Terminate listeners before loggers so the logger can flush remaining items.
        for thread in reversed(self.threads):
            thread.terminate()
        mlflow.end_run()

    def log_metric(self, name, value, epoch=0):
        """
        Log a metric. Terminates the run if epoch/time limit has been reached.

        :param name: Metric name (string). This string may only contain alphanumerics, underscores
                    (_), dashes (-), periods (.), spaces ( ), and slashes (/).
                    All backend stores will support keys up to length 250, but some may
                    support larger keys.
        :param value: Metric value (float).
        :param epoch: Integer training step (epoch) at which was the metric calculated.
                     Defaults to 0.
        """
        if "RADT_MAX_EPOCH" not in os.environ:
            return
        # termination check first
        if epoch >= self.max_epoch or time() > self.max_time:
            print("Maximum epoch reached")
            sys.exit()

        entry = {"key": name, "value": value, "timestamp": int(time() * 1000), "step": int(epoch)}

        if getattr(self, "_batch_logger_enabled", False):
            # move any main fallback entries first
            if self._fallback_buffer_main:
                for e in self._fallback_buffer_main:
                    self._buffers_main.put(e)
                self._fallback_buffer_main.clear()
            # append the new entry to the main queue (non-blocking)
            self._buffers_main.put(entry)
            return

        # Batch logger not enabled: store into main fallback list
        self._fallback_buffer_main.append(entry)
        return

    def log_metrics(self, metrics, epoch=0):
        """
        Log multiple metrics. Terminates the run if epoch/time limit has been reached.

        :param name: Dict of metrics (string: float). Key-value pairs of metrics to be logged.
        :param epoch: Integer training step (epoch) at which was the metric calculated.
                     Defaults to 0.
        """
        if "RADT_MAX_EPOCH" not in os.environ:
            return
        # termination check first
        if epoch >= self.max_epoch or time() > self.max_time:
            print("Maximum epoch reached")
            sys.exit()

        timestamp = int(time() * 1000)
        entries = [{"key": k, "value": v, "timestamp": timestamp, "step": int(epoch)} for k, v in metrics.items()]

        if getattr(self, "_batch_logger_enabled", False):
            # move any main fallback entries first
            if self._fallback_buffer_main:
                for e in self._fallback_buffer_main:
                    self._buffers_main.put(e)
                self._fallback_buffer_main.clear()
            # append all new entries to main queue
            for entry in entries:
                self._buffers_main.put(entry)
            return

        # Batch logger not enabled: extend main fallback buffer
        self._fallback_buffer_main.extend(entries)
        return
