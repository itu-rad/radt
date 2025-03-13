import io
import mlflow
import subprocess
from multiprocessing import Process


class TOPThread(Process):
    def __init__(
        self,
        run_id,
        process_names=[
            "python",
            "pt_data_worker",
        ],
        experiment_id=88,
    ):
        super(TOPThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id

        self.process_names = process_names

    def run(self):
        with mlflow.start_run(run_id=self.run_id):
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
                                m["TOP - CPU Utilization"] = CPU_util
                                m["TOP - Memory Utilization"] = Mem_util
                                pervFlag = not (Flag)
                                CPU_util = 0
                                Mem_util = 0

                            if word_vector[8] == "used,":
                                m["TOP - Memory Usage GB"] = (
                                    float(word_vector[7]) / 1000
                                )
                            # else:
                            #     m["top - Memory Usage (GB)"] = float(word_vector[6])/1000)
                        elif word_vector[1] == "Swap:":
                            m["TOP - Swap Memory GB"] = float(word_vector[6]) / 1000

                    elif len(word_vector) != 0:
                        if word_vector[11].strip() in self.process_names:
                            if Flag != pervFlag:
                                CPU_util += float(word_vector[8])
                                Mem_util += float(word_vector[9])
                if len(m):
                    mlflow.log_metrics(m)

            m = {}
            m["TOP - CPU Utilization"] = CPU_util
            m["TOP - Memory Utilization"] = Mem_util
            mlflow.log_metrics(m)
