"""Runner for Resource-Aware Data systems Tracker (radT) for automatically tracking and training machine learning software"""

import os
import runpy
import sys
from pathlib import Path
from time import sleep

import mlflow

from .benchmark import RADTBenchmark


def update_params_listing(command, params):
    # Log the parameters individually so they are filterable in MLFlow
    statements = {}

    statements["model"] = command
    statements["params"] = params

    # Identify arguments to upload as parameters
    key = ""
    value = []
    for param in params.split():
        if param.startswith("-"):
            if key != "":
                statements[key.lstrip("-")] = " ".join(value)
            key = param
            value = []
        else:
            value.append(param)

    if key != "":
        statements[key.lstrip("-")] = " ".join(value)

    run = mlflow.active_run()

    # Log parameter
    for k, v in statements.items():
        try:
            mlflow.log_param(k, v)
        except mlflow.exceptions.MlflowException as e:
            print("Failed to log parameter:", k, v)

        if "data" in k.lower():
            try:
                mlflow.log_param("data", v)
            except mlflow.exceptions.MlflowException as e:
                print("Failed to log parameter:", "data", v)


def start_run(args, listeners):
    try:
        RUN_ID = mlflow.start_run().info.run_id
    except Exception:
        RUN_ID = mlflow.active_run().info.run_id
    os.environ["RADT_RUN_ID"] = RUN_ID

    passthrough = args.params
    update_params_listing(args.command, args.params)

    sys.argv = [sys.argv[0]] + passthrough.split()

    # Clear MLproject file so next run may start
    with open(Path("MLproject")) as file:
        mlflow.log_text(file.read() or "Direct mode - no contents", "MLproject")
    Path("MLproject").unlink()

    code = "run_path(progname, run_name='__main__')"
    globs = {"run_path": runpy.run_path, "progname": args.command}

    mlflow.log_param("manual", os.getenv("RADT_MANUAL_MODE") == "True")

    # Print RUN_ID to initiate lock release
    print(f"RADT active in run with ID '{RUN_ID}'")

    # Wait for lock
    while Path("radtlock").is_file():
        sleep(0.1)

    if os.getenv("RADT_MANUAL_MODE") == "True":
        try:
            exec(code, globs, None)
        except (SystemExit, KeyboardInterrupt):
            pass
    else:
        with RADTBenchmark() as run:
            try:
                exec(code, globs, None)
            except (SystemExit, KeyboardInterrupt):
                pass
