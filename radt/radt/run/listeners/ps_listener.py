import mlflow
import os
import subprocess
import time

from multiprocessing import Process


# This listener writes *a lot* of metrics and may affect performance!
class PSThread(Process):
    def __init__(self, run_id, mlflow_buffer=None, experiment_id=88):
        super(PSThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.mlflow_buffer = mlflow_buffer
        self.parent_pid = os.getpid()

    def _enqueue_metrics(self, metrics, timestamp_ms=None):
        if self.mlflow_buffer:
            ts = int(timestamp_ms) if timestamp_ms is not None else int(time.time() * 1000)
            entries = [{"key": k, "value": v, "timestamp": ts, "step": 0} for k, v in metrics.items()]
            try:
                for e in entries:
                    self.mlflow_buffer.put(e)
            except Exception:
                # fallback
                for k, v in metrics.items():
                    mlflow.log_metric(k, float(v))
        else:
            for k, v in metrics.items():
                mlflow.log_metric(k, float(v))

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

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

                m = {f"system/PS - CPU {psr}": float(cpu), f"system/PS - MEM {psr}": float(mem)}
                self._enqueue_metrics(m)
            time.sleep(5)
