import io
import mlflow
import subprocess

from multiprocessing import Process


class FreeThread(Process):
    def __init__(self, run_id, experiment_id=88):
        super(FreeThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id

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
                m["Free - Mem Total GB"] = float(line[1]) / 1024
                m["Free - Mem Used GB"] = float(line[2]) / 1024
                m["Free - Mem Free GB"] = float(line[3]) / 1024
                m["Free - Mem Shared GB"] = float(line[4]) / 1024
                m["Free - Mem Buff/Cache GB"] = float(line[5]) / 1024
                m["Free - Mem Available GB"] = float(line[6]) / 1024

            elif "Swap:" in line[0]:
                m["Free - Swap Total GB"] = float(line[1]) / 1024
                m["Free - Swap Used GB"] = float(line[2]) / 1024
                m["Free - Swap Free GB"] = float(line[3]) / 1024

            elif "Total:" in line[0]:
                m["Free - Total Total GB"] = float(line[1]) / 1024
                m["Free - Total Used GB"] = float(line[2]) / 1024
                m["Free - Total Free GB"] = float(line[3]) / 1024
                mlflow.log_metrics(m)
                m = {}
