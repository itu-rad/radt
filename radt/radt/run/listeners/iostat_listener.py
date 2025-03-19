import mlflow
import subprocess
import io

from multiprocessing import Process


class IOstatThread(Process):
    def __init__(self, run_id, experiment_id=88):
        super(IOstatThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        ps = subprocess.Popen(
            "iostat 1 -m".split(),
            stdout=subprocess.PIPE,
        )
        total_tps, total_mb_read_s, total_mb_written_s = 0, 0, 0
        total_mb_read, total_mb_written = 0, 0
        devices = set()

        for line in io.TextIOWrapper(ps.stdout, encoding="utf-8"):
            m = {}
            line = line.lstrip()

            if line.startswith("nvme") or line.startswith("sd"):
                word_vector = line.strip().split()

                device = word_vector[0]  # storage device

                tps = word_vector[1]  # I/O transfers/s
                mb_read_s = word_vector[2]  # MB read/s
                mb_written_s = word_vector[3]  # MB wrtn/s
                mb_read = word_vector[5]  # MB read since last sample
                mb_written = word_vector[6]  # MB written since last sample

                m[f"iostat - {device} - tps"] = float(tps)
                m[f"iostat - {device} - MB read/s"] = float(mb_read_s)
                m[f"iostat - {device} - MB written/s"] = float(mb_written_s)
                m[f"iostat - {device} - MB read"] = float(mb_read)
                m[f"iostat - {device} - MB written"] = float(mb_written)

                mlflow.log_metrics(m)

                if device in devices:
                    mlflow.log_metrics(
                        {
                            "iostat - Total tps": total_tps,
                            "iostat - Total MB read/s": total_mb_read_s,
                            "iostat - Total MB written/s": total_mb_written_s,
                            "iostat - Total MB read": total_mb_read,
                            "iostat - Total MB written": total_mb_written,
                        }
                    )
                    devices = set()
                    total_tps, total_mb_read_s, total_mb_written_s = 0, 0, 0
                    total_mb_read, total_mb_written = 0, 0
                else:
                    devices.add(device)
                    total_tps += float(tps)
                    total_mb_read_s += float(mb_read_s)
                    total_mb_written_s += float(mb_written_s)
                    total_mb_read += float(mb_read)
                    total_mb_written += float(mb_written)
