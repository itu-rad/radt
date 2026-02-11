import io
import mlflow
import subprocess
import time
from multiprocessing import Process


class TOPThread(Process):
    def __init__(
        self,
        run_id,
        mlflow_buffer=None,
        process_names=[
            "python",
            "pt_data_worker",
        ],
        experiment_id=88,
    ):
        super(TOPThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.mlflow_buffer = mlflow_buffer

        self.process_names = process_names

    def _enqueue_metrics(self, metrics, timestamp_ms=None):
        if self.mlflow_buffer:
            ts = int(timestamp_ms) if timestamp_ms is not None else int(time.time() * 1000)
            entries = [{"key": k, "value": v, "timestamp": ts, "step": 0} for k, v in metrics.items()]
            for e in entries:
                self.mlflow_buffer.put(e)
        else:
            mlflow.log_metrics(metrics)

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        self.top = subprocess.Popen(
            "top -i -b -n 999999999 -d 1".split(),
            stdout=subprocess.PIPE,
        )

        #  ======= flags and accumulative cpu% and mem% ========
        Flag = False
        pervFlag = True
        CPU_util = 0
        Mem_util = 0
        # ============================================b==========

        # =========== Going through each line ==================
        for line in io.TextIOWrapper(self.top.stdout, encoding="utf-8"):
            m = {}
            line = line.lstrip()

            if (
                line.startswith("top")
                or line.startswith("Tasks")
                or line.startswith("%")
                or line.startswith("PID")
                or line.startswith(" ")
            ):
                pass
            else:
                word_vector = line.strip().split()
                if (
                    line.startswith("KiB")
                    or line.startswith("MiB")
                    or line.startswith("GiB")
                ) and len(word_vector) != 0:
                    if word_vector[1] == "Mem":
                        Flag = not (Flag)

                        if Flag == pervFlag:
                            m["system/TOP - CPU Utilization"] = CPU_util
                            m["system/TOP - Memory Utilization"] = Mem_util
                            pervFlag = not (Flag)
                            CPU_util = 0
                            Mem_util = 0

                        if word_vector[8] == "used,":
                            m["system/TOP - Memory Usage GB"] = (
                                float(word_vector[7]) / 1000
                            )
                    elif word_vector[1] == "Swap:":
                        m["system/TOP - Swap Memory GB"] = float(word_vector[6]) / 1000

                elif len(word_vector) != 0:
                    if word_vector[11].strip() in self.process_names:
                        if Flag != pervFlag:
                            CPU_util += float(word_vector[8])
                            Mem_util += float(word_vector[9])
            if len(m):
                self._enqueue_metrics(m)

        m = {}
        m["system/system/TOP - CPU Utilization"] = CPU_util
        m["system/TOP - Memory Utilization"] = Mem_util
        self._enqueue_metrics(m)
