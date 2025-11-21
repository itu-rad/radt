import io
import mlflow
import os
import subprocess
import time

from multiprocessing import Process


DCGMI_GROUP_ID = os.getenv("RADT_DCGMI_GROUP")

METRIC_NAMES = [
    "Power Usage",
    "Total Energy Consumption",
    "PCIE TX Throughput",
    "PCIE RX Throughput",
    "GPU Utilization",
    "Memory Copy Utilization",
    "GR Engine Active",
    "SM Active",
    "SM Occupancy",
    "Tensor Active",
    "DRAM Active",
    "FP64 Active",
    "FP32 Active",
    "FP16 Active",
    "PCIE TX Bytes",
    "PCIE RX Bytes",
    "NVlink TX Bytes",
    "NVlink RX Bytes",
]


class DCGMIThread(Process):
    def __init__(self, run_id, mlflow_buffer=None, experiment_id=88):
        super(DCGMIThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.mlflow_buffer = mlflow_buffer


        # Hierarchy of metrics to monitor. Fall back in ascending order if certain metrics are not available for collection.
        self.dcgm_fields = [
            [
                155,
                156,
                200,
                201,
                203,
                204,
                1001,
                1002,
                1003,
                1004,
                1005,
                1006,
                1007,
                1008,
                1009,
                1010,
                1011,
                1012,
            ],  # A100, H100
            [
                155,
                156,
                200,
                201,
                203,
                204,
                1001,
                1002,
                1003,
                1004,
                1005,
                1007,
                1008,
                1009,
                1010,
                1011,
                1012,
            ],  # A10
            [155, 156, 200, 201, 203, 204],  # Rest
        ]


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

    def _start_dcgm(self, idx):
        fields = ",".join(map(str, self.dcgm_fields[idx]))
        self.dcgm = subprocess.Popen(
            f"dcgmi dmon -e {fields} -g {DCGMI_GROUP_ID}".split(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        for idx, _ in enumerate(self.dcgm_fields):
            self._start_dcgm(idx)

            self.monitor()

            # If advanced metrics are not available, restart the service with limited collection
            if "Error setting watches" not in str(self.dcgm.stderr.read()):
                return

    def monitor(self):
        for line in io.TextIOWrapper(self.dcgm.stdout, encoding="utf-8"):
            if "Error" in line:
                raise Exception("DCGMI handler could not find required group!")
            line = line.split()

            if "POWER" in line:
                continue

            if line[0].lower().strip() not in ["#", "id"]:
                m = {}
                for value, name in zip(
                    line[2:], METRIC_NAMES
                ):  # [2:] to get rid of gpu name
                    if value.strip() == "N/A":
                        value = 0
                    m[f"system/DCGMI - {name}"] = float(value)

                self._enqueue_metrics(m)
