import io
import mlflow
import subprocess

from datetime import datetime
from multiprocessing import Process

import os


class SMIThread(Process):
    def __init__(self, run_id, experiment_id=88):
        super(SMIThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id

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
                    m["SMI - Power Draw"] = float(line[0])
                    m["SMI - Timestamp"] = datetime.strptime(
                        line[1] + "000", r"%Y/%m/%d %H:%M:%S.%f"
                    ).timestamp()
                    try:
                        m["SMI - GPU Util"] = float(line[2]) / 100
                    except ValueError:
                        m["SMI - GPU Util"] = float(-1)
                    try:
                        m["SMI - Mem Util"] = float(line[3]) / 100
                    except ValueError:
                        m["SMI - Mem Util"] = float(-1)
                    m["SMI - Mem Used"] = float(line[4])
                    m["SMI - Performance State"] = int(line[5][1:])
                    mlflow.log_metrics(m)
                except ValueError as e:
                    print("SMI Listener failed to report metrics")
                    break
