import io
import mlflow
import subprocess
import time

from datetime import datetime
from multiprocessing import Process

import os


class SMIThread(Process):
    def __init__(self, run_id, mlflow_logger=None, experiment_id=88):
        super(SMIThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.mlflow_logger = mlflow_logger

    def _enqueue_metrics(self, metrics, timestamp_ms=None):
        if self.mlflow_logger:
            ts = int(timestamp_ms) if timestamp_ms is not None else int(time.time() * 1000)
            entries = [{"key": k, "value": v, "timestamp": ts, "step": 0} for k, v in metrics.items()]
            try:
                for e in entries:
                    self.mlflow_logger._buffers["write"].append(e)
            except Exception:
                mlflow.log_metrics(metrics)
        else:
            mlflow.log_metrics(metrics)

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        SMI_GPU_ID = os.getenv("SMI_GPU_ID")

        print("SMI GPU ID:", SMI_GPU_ID)
        self.smi = subprocess.Popen(
            f"nvidia-smi -i {SMI_GPU_ID} -l 1 --query-gpu=power.draw,timestamp,utilization.gpu,utilization.memory,memory.used,pstate --format=csv,nounits,noheader".split(),
            stdout=subprocess.PIPE,
        )
        for line in io.TextIOWrapper(self.smi.stdout, encoding="utf-8"):
            line = line.split(", ")
            if len(line) > 1 and line[0] != "#":
                try:
                    m = {}
                    m["system/SMI - Power Draw"] = float(line[0])
                    m["system/SMI - Timestamp"] = datetime.strptime(
                        line[1] + "000", r"%Y/%m/%d %H:%M:%S.%f"
                    ).timestamp()

                    try:
                        m["system/SMI - GPU Util"] = float(line[2]) / 100
                    except ValueError:
                        m["system/SMI - GPU Util"] = float(-1)
                    try:
                        m["system/SMI - Mem Util"] = float(line[3]) / 100
                    except ValueError:
                        m["system/SMI - Mem Util"] = float(-1)
                    m["system/SMI - Mem Used"] = float(line[4])
                    m["system/SMI - Performance State"] = int(line[5][1:])
                    # enqueue using parsed timestamp in ms
                    self._enqueue_metrics(m)
                except ValueError as e:
                    print("SMI Listener failed to report metrics")
                    break
