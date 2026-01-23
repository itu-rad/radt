import os
import sys
import types
from time import time
from subprocess import PIPE, Popen
import mlflow
from mlflow.tracking import MlflowClient
from mlflow.entities import Metric as MlflowMetric
from collections import deque
import multiprocessing
import queue

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
    with Popen(cmd, stdout=PIPE, bufsize=1, universal_newlines=True, env=env, shell=True) as p:
        result.extend(p.stdout)

        if p.returncode != 0:
            pass

    return result


class _MLFlowLogger(multiprocessing.Process):
    """
    Background process that periodically flushes metrics from a queue to MLflow
    """
    def __init__(self, run_id, buffers, lock=None, flush_interval=5.0, max_batch_size=1000):
        super().__init__(daemon=True)
        self.run_id = run_id
        self._buffers = buffers
        self._lock = lock

        self._flush_interval = float(flush_interval)
        self._stop_event = multiprocessing.Event()
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
            self._stop_event.wait(self._flush_interval)

        # On stop: repeatedly flush until no remaining metrics
        while True:
            try:
                flushed_any = self._flush_once(final=True)
            except Exception as e:
                print(f"MLFlowLogger final flush error: {e}")
                flushed_any = False
            if not flushed_any:
                break

    def _drain_queue(self):
        # Drain all currently queued items into a list without blocking.
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

        # Send in chunks if needed because mlflow has a max batch size
        try:
            if not metric_dicts:
                return True
            # convert to Mlflow Metric entities and send in chunks
            for i in range(0, len(metric_dicts), self._max_batch_size):
                batch_dicts = metric_dicts[i:i + self._max_batch_size]
                batch_entities = [
                    MlflowMetric(d["key"], d["value"], d["timestamp"], d["step"]) for d in batch_dicts
                ]
                self._client._tracking_client.store.log_batch(run_id=self.run_id, metrics=batch_entities, params=[], tags=[])
            return True
        except Exception:
            # On failure, requeue the metrics at the front of the current write buffer
            for original in to_flush:
                try:
                    self._buffers.put(original)
                except Exception:
                    # if put fails, drop the metric
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
        if "RADT_PRESENT" not in os.environ:
            return

        try:
            run = mlflow.start_run(run_id=os.getenv("RADT_RUN_ID"))
        except Exception as e:
            run = mlflow.active_run()
        self.run_id = run.info.run_id

        # Queue for main process and listener logging
        self._buffer_main = multiprocessing.Queue()
        self._buffer_listeners = multiprocessing.Queue()

        # Capture (package) versions for pip, conda, smi
        try:
            self.log_text("".join(execute_command("pip freeze")), "pip.txt")
        except FileNotFoundError as e:
            pass

        try:
            self.log_text("".join(execute_command("conda list")), "conda.txt")
        except (
            Exception
        ) as e:  # Either a FileNotFoundError or DirectoryNotACondaEnvironmentError
            print(
                f"Conda not found or unreachable. Continuing without conda list. ({e})"
            )
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

        if "RADT_PRESENT" not in os.environ:
            if isinstance(att, types.MethodType) or isinstance(att, types.FunctionType):
                return dummy

        return att

    def __enter__(self):
        if "RADT_PRESENT" not in os.environ:
            return self

        self.processes = []

        # main logger handles user-invoked log_metric/log_metrics
        main_logger = _MLFlowLogger(self.run_id, self._buffer_main)
        self.processes.append(main_logger)

        # listener logger accepts metrics from listeners
        listener_logger = _MLFlowLogger(self.run_id, self._buffer_listeners)
        self.processes.append(listener_logger)

        # Spawn processes for enabled listeners
        for listener_name, listener_class in listeners.items():
            listener_env_key = f"RADT_LISTENER_{listener_name.upper()}"
            if os.getenv(listener_env_key) == "True":
                os.environ[listener_env_key] = "False"
                inst = listener_class(self.run_id, self._buffer_listeners)
                self.processes.append(inst)    

        for process in self.processes:
            process.start()

        return self

    def __exit__(self, type, value, traceback):
        """
        Terminate listeners and run
        """
        if "RADT_PRESENT" not in os.environ:
            return
        
        # Terminate listeners before loggers so the logger can flush remaining items.
        for process in reversed(self.processes):
            process.terminate()
        
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
        if "RADT_PRESENT" not in os.environ:
            return

        entry = {"key": name, "value": value, "timestamp": int(time() * 1000), "step": int(epoch)}
        self._buffer_main.put(entry)

    def log_metrics(self, metrics, epoch=0):
        """
        Log multiple metrics. Terminates the run if epoch/time limit has been reached.

        :param name: Dict of metrics (string: float). Key-value pairs of metrics to be logged.
        :param epoch: Integer training step (epoch) at which was the metric calculated.
                     Defaults to 0.
        """
        if "RADT_PRESENT" not in os.environ:
            return

        entries = [{"key": k, "value": v, "timestamp": int(time() * 1000), "step": int(epoch)} for k, v in metrics.items()]
        for entry in entries:
            self._buffer_main.put(entry)
