import os
import sys
import types
from time import time
from subprocess import PIPE, Popen
import mlflow

from .listeners import (
    dcgmi_listener,
    ps_listener,
    smi_listener,
    top_listener,
    iostat_listener,
    free_listener,
)


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

        # Capture (package) versions for pip, conda, smi
        try:
            self.log_text("".join(execute_command("pip freeze")), "pip.txt")
        except FileNotFoundError as e:
            pass

        try:
            self.log_text("".join(execute_command("conda list")), "conda.txt")
        except FileNotFoundError as e:
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

        # Spawn threads for listeners
        if os.getenv("RADT_LISTENER_PS") == "True":
            os.environ["RADT_LISTENER_PS"] = "False"
            self.threads.append(ps_listener.PSThread(self.run_id))
        if os.getenv("RADT_LISTENER_SMI") == "True":
            os.environ["RADT_LISTENER_SMI"] = "False"
            self.threads.append(smi_listener.SMIThread(self.run_id))
        if os.getenv("RADT_LISTENER_DCGMI") == "True":
            os.environ["RADT_LISTENER_DCGMI"] = "False"
            self.threads.append(dcgmi_listener.DCGMIThread(self.run_id))
        if os.getenv("RADT_LISTENER_TOP") == "True":
            os.environ["RADT_LISTENER_TOP"] = "False"
            self.threads.append(top_listener.TOPThread(self.run_id))
        if os.getenv("RADT_LISTENER_IOSTAT") == "True":
            os.environ["RADT_LISTENER_IOSTAT"] = "False"
            self.threads.append(iostat_listener.IOstatThread(self.run_id))
        if os.getenv("RADT_LISTENER_FREE") == "True":
            os.environ["RADT_LISTENER_FREE"] = "False"
            self.threads.append(free_listener.FreeThread(self.run_id))

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
        mlflow.log_metric(name, value, epoch)
        if epoch >= self.max_epoch or time() > self.max_time:
            print("Maximum epoch reached")
            sys.exit()

    def log_metrics(self, metrics, epoch=0):
        """
        Log multiple metrics. Terminates the run if epoch/time limit has been reached.

        :param name: Dict of metrics (string: float). Key-value pairs of metrics to be logged.
        :param epoch: Integer training step (epoch) at which was the metric calculated.
                     Defaults to 0.
        """
        if "RADT_MAX_EPOCH" not in os.environ:
            return
        mlflow.log_metrics(metrics, epoch)
        if epoch >= self.max_epoch or time() > self.max_time:
            print("Maximum epoch reached")
            sys.exit()
