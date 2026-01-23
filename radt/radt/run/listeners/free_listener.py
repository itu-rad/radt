import io
import mlflow
import subprocess
import time

from multiprocessing import Process


class FreeThread(Process):
    def __init__(self, run_id, mlflow_buffer=None, experiment_id=88):
        super(FreeThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.mlflow_buffer = mlflow_buffer

    def _enqueue_metrics(self, metrics, timestamp_ms=None):
        if self.mlflow_buffer:
            ts = int(timestamp_ms) if timestamp_ms is not None else int(time.time() * 1000)
            entries = [{"key": k, "value": v, "timestamp": ts, "step": 0} for k, v in metrics.items()]
            try:
                for e in entries:
                    self.mlflow_buffer.put(e)
            except Exception:
                mlflow.log_metrics(metrics)
        else:
            mlflow.log_metrics(metrics)

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        self.free = subprocess.Popen(
            f"free --mega --total -s 1".split(),
            stdout=subprocess.PIPE,
        )
        m = {}
        for line in io.TextIOWrapper(self.free.stdout, encoding="utf-8"):
            line = line.strip().split()

            if not line:
                continue

            if "Mem:" in line[0]:
                m["system/Free - Mem Total GB"] = float(line[1]) / 1024
                m["system/Free - Mem Used GB"] = float(line[2]) / 1024
                m["system/Free - Mem Free GB"] = float(line[3]) / 1024
                m["system/Free - Mem Shared GB"] = float(line[4]) / 1024
                m["system/Free - Mem Buff/Cache GB"] = float(line[5]) / 1024
                m["system/Free - Mem Available GB"] = float(line[6]) / 1024

            elif "Swap:" in line[0]:
                m["system/Free - Swap Total GB"] = float(line[1]) / 1024
                m["system/Free - Swap Used GB"] = float(line[2]) / 1024
                m["system/Free - Swap Free GB"] = float(line[3]) / 1024

            elif "Total:" in line[0]:
                m["system/Free - Total Total GB"] = float(line[1]) / 1024
                m["system/Free - Total Used GB"] = float(line[2]) / 1024
                m["system/Free - Total Free GB"] = float(line[3]) / 1024
                self._enqueue_metrics(m)
                m = {}
