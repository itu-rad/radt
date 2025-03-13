import mlflow
import os
import subprocess
import time

from multiprocessing import Process


# This listener writes *a lot* of metrics and may affect performance!
class PSThread(Process):
    def __init__(self, run_id, experiment_id=88):
        super(PSThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.parent_pid = os.getpid()

    def run(self):
        with mlflow.start_run(run_id=self.run_id):
            while True:
                output = (
                    subprocess.run(
                        f"ps -p {self.parent_pid} -L -o pid,tid,psr,pcpu,%mem".split(),
                        capture_output=True,
                    )
                    .stdout.decode()
                    .splitlines()
                )

                for line in output[1:]:
                    line = line.strip().split(" ")
                    line = [x for x in line if x.strip() != ""]

                    pid = line[0]
                    tid = line[1]
                    psr = line[2]
                    cpu = line[3]
                    mem = line[4]

                    mlflow.log_metric(f"PS - CPU {psr}", float(cpu))
                    mlflow.log_metric(f"PS - MEM {psr}", float(mem))
                time.sleep(5)
