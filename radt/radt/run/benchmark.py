import os
import sys
import types
from time import time
from subprocess import PIPE, Popen
import mlflow
import threading
from mlflow.tracking import MlflowClient
from collections import deque

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
    def __init__(self, run_id, buffers, lock, flush_interval=5.0, max_batch_size=1000):
        super().__init__(daemon=True)
        self.run_id = run_id
        # buffers is a dict-like object with 'write' and 'flush' deque objects
        self._buffers = buffers
        self._lock = lock
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
                print(f"MLFlowLogger error during flush: {e}")
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

    def _swap_buffers(self):
        # swap write deque with a fresh deque under lock and return the deque to flush
        with self._lock:
            current_write = self._buffers["write"]
            if not current_write:
                # empty deque object -> nothing to flush
                return None
            self._buffers["write"] = deque()
        return current_write

    def _flush_once(self, final=False):
        # Swap out the write buffer quickly and process outside the lock
        to_flush = self._swap_buffers()
        if not to_flush:
            return False

        # Convert deque items to metric dicts
        metric_dicts = []
        for m in to_flush:
            metric_dicts.append({
                "key": m["name"],
                "value": float(m["value"]),
                "timestamp": int(m["timestamp"]),
                "step": int(m["step"]),
            })

        # Send in chunks if needed
        try:
            if metric_dicts:
                # chunking to avoid overly large batches
                for i in range(0, len(metric_dicts), self._max_batch_size):
                    batch = metric_dicts[i:i + self._max_batch_size]
                    self._client.log_batch(run_id=self.run_id, metrics=batch)
            return True
        except Exception:
            # On failure, requeue the metrics at the front of the current write buffer
            # Requeue under lock to avoid races
            with self._lock:
                # prepend failed metrics back into the current write deque preserving order
                # convert dicts back to original entry format
                for d in reversed(metric_dicts):
                    self._buffers["write"].appendleft({
                        "name": d["key"],
                        "value": d["value"],
                        "timestamp": d["timestamp"],
                        "step": d["step"],
                    })
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

        # Shared in-memory swap buffers and synchronization primitives
        # Producers append to buffers['write'] without acquiring lock (deque.append is thread-safe).
        # Logger swaps buffers['write'] for an empty deque under lock and processes the swapped deque.
        self._buffers = {"write": deque(), "flush": deque()}
        self._buffers_lock = threading.Lock()
        # fallback buffer used when batch logger is not active; kept in memory and
        # merged into the write deque on the next logging call.
        self._fallback_buffer = []
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
            batch_logger = MLFlowLogger(self.run_id, self._buffers, self._buffers_lock, flush_interval=self._batch_flush_interval)
            self.threads.append(batch_logger)

        # Spawn threads for enabled listeners
        for listener_name, listener_class in listeners.items():
            listener_env_key = f"RADT_LISTENER_{listener_name.upper()}"
            if os.getenv(listener_env_key) == "True":
                os.environ[listener_env_key] = "False"
                self.threads.append(listener_class(self.run_id))    

        for thread in self.threads:
            thread.start()

        return self

    def __exit__(self, type, value, traceback):
        # Terminate listeners and run
        if "RADT_MAX_EPOCH" not in os.environ:
            return
        for thread in self.threads:
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

        entry = {"name": name, "value": value, "timestamp": int(time() * 1000), "step": int(epoch)}

        if getattr(self, "_batch_logger_enabled", False):
            # If any entries were saved to the fallback buffer previously, move them first.
            if self._fallback_buffer:
                for e in self._fallback_buffer:
                    self._buffers["write"].append(e)
                self._fallback_buffer.clear()
            # append the new entry to the active write deque (non-blocking)
            self._buffers["write"].append(entry)
            return

        # Batch logger not enabled: store into fallback list so the next log call can
        # append both fallback + new entries into the write deque.
        self._fallback_buffer.append(entry)
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
        entries = [{"name": k, "value": v, "timestamp": timestamp, "step": int(epoch)} for k, v in metrics.items()]

        if getattr(self, "_batch_logger_enabled", False):
            # move any fallback entries first
            if self._fallback_buffer:
                for e in self._fallback_buffer:
                    self._buffers["write"].append(e)
                self._fallback_buffer.clear()
            # append all new entries
            for entry in entries:
                self._buffers["write"].append(entry)
            return

        # Batch logger not enabled: extend fallback buffer
        self._fallback_buffer.extend(entries)
        return
