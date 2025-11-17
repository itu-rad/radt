import numpy as np

COLOURS = [31, 32, 34, 35, 36, 33]

CSV_FORMAT = np.dtype(
    [
        ("Experiment", int),
        ("Workload", int),
        ("Name", str),
        ("Status", str),
        ("Run", str),
        ("Devices", str),
        ("Collocation", str),
        ("Listeners", str),
        ("File", str),
        ("Params", str),
    ]
)

COMMAND = (
    "mlflow run {Filepath} --env-manager={Envmanager} "
    "-P letter={Letter} "
    "-P workload={Workload} "
    "-P listeners={Listeners} "
    "-P file={File} "
    '-P params="-" '
)

RUN_LISTENERS = ["ps", "smi", "dcgmi", "top", "iostat", "free"]

WORKLOAD_LISTENERS = {
    "nsys": "nsys profile --capture-range nvtx --nvtx-capture profile --cuda-memory-usage=true --capture-range-end repeat -o nsys_{Experiment}_{Workload}_{Letter} -f true -w true -x true -t cuda,nvtx ",
    "nsysraw": "nsys profile --cuda-memory-usage true -o nsys_{Experiment}_{Workload}_{Letter} -f true -w true -x true -t cuda,nvtx ",
    "ncu": "ncu -o ncu_{Experiment}_{Workload}_{Letter} -f --nvtx --nvtx-include profile ",
    "ncuraw": "ncu -o ncu_{Experiment}_{Workload}_{Letter} -f -c 100 ",
    "ncuattach": "ncu --mode=launch ",
}


MLPROJECT_CONTENTS = """name: radt

<REPLACE_ENV>

entry_points:
  main:
    parameters:
      letter: { type: string, default: ""}
      workload: { type: string, default: ""}
      listeners: { type: string, default: "smi+top+dcgmi+iostat+free" }
      params: { type: string, default: "-"}
      file: { type: string, default: "cifar10.py"}
      workload_listener: { type: string, default: ""}
    command: |
      <REPLACE_COMMAND>
"""

MLFLOW_COMMAND = (
    '''{WorkloadListener}{PythonCommand} -m radt run -l {Listeners} -c {File} -p "{Params}"'''
)
