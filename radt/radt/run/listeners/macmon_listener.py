import ast
import io
import mlflow
import subprocess

from multiprocessing import Process


class MacmonThread(Process):
    def __init__(self, run_id, experiment_id=88):
        super(MacmonThread, self).__init__()
        self.run_id = run_id
        self.experiment_id = experiment_id

    def run(self):
        mlflow.start_run(run_id=self.run_id).__enter__()  # attach to run

        self.macmon = subprocess.Popen(
            f"macmon pipe".split(),
            stdout=subprocess.PIPE,
        )
        for line in io.TextIOWrapper(self.macmon.stdout, encoding="utf-8"):
            if line:
                json = ast.literal_eval(line)

                m = {}
                for k, v in json.items():
                    if k == "timestamp":
                        continue
                    
                    if isinstance(v, list):
                        for i, sub_v in enumerate(v):
                            m[f"system/macmon - {k.replace('_',' ').title()}:{i}"] = float(sub_v)
                    elif isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            m[f"system/macmon - {k.replace('_',' ').title()}:{sub_k.replace('_',' ').title()}"] = float(sub_v)
                    else:
                        m[f"system/macmon - {k.replace('_',' ').title()}"] = float(v)

                mlflow.log_metrics(m)
