import os
import shlex
import sys
import time
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from queue import Empty, Queue
from string import ascii_uppercase
from subprocess import PIPE, STDOUT, Popen
from threading import Thread
from enum import Enum

import migedit
import mlflow
import numpy as np
import pandas as pd

from mlflow import log_params
from mlflow.tracking import MlflowClient

from .. import constants

class ExecutionType(Enum):
    DIRECT = "direct"
    MLFLOW = "mlflow"


def coloured(colour: int, string: str):
    """Add colour tags to a string

    Args:
        colour (int): Colour id
        string (str): String to colour

    Returns:
        str: Coloured string
    """
    return f"\033[{colour}m{string}\033[0m"


def sysprint(string: str):
    """Print in colour 33

    Args:
        string (str): String to print
    """
    print(coloured(33, string))


def runformat(colour: int, letter: str, string: str):
    """Format string styled to a specific run

    Args:
        colour (int): Colour id
        letter (str): Run letter
        string (str): String to format

    Returns:
        str: Formatted string
    """
    prefix = f"[RUN {letter}]:".ljust(20)
    if colour is None:
        return f"{prefix} {string}"
    return f"{coloured(colour, prefix)} {string}"


def execute_command(cmd: str, shell: bool = False, vars: dict = {}):
    """Execute a (non-run) command

    Args:
        cmd (str or list): Command to run
        shell (bool, optional): Whether to enable shell. Defaults to False.
        vars (dict, optional): Environment vars for the command. Defaults to {}.

    Returns:
        str: stdout output of the command
    """

    if isinstance(cmd, str):
        cmd = cmd.split()

    if vars:
        sysprint(f"Executing command - {cmd} - {vars}")
    else:
        sysprint(f"Executing command - {cmd}")

    env = os.environ.copy()
    for k, v in vars.items():
        env[k] = str(v)

    result = []
    with Popen(
        cmd, stdout=PIPE, bufsize=1, universal_newlines=True, shell=shell, env=env
    ) as p:
        result.extend(p.stdout)

        if p.returncode != 0:
            pass

    return result


def enqueue_output(out: PIPE, queue: Queue):
    """Enqueue any output from pipe into queue

    Args:
        out (PIPE): Pipe to read from
        queue (Queue): Queue to write to
    """
    try:
        for line in iter(out.readline, b""):
            queue.put(line)
    except ValueError:
        pass
    out.close()


def process_output(popens, log_runs, log, run_ids):
    for colour, letter, _, q, _ in popens:
        while True:
            try:
                l = q.get_nowait()
                if not l.strip() and not "\n" in l:
                    continue
            except Empty:
                break
            else:
                log_runs[letter].append(l)
                log.append(runformat(None, letter, l))
                print(runformat(colour, letter, l), end="")

                if run_ids[letter]:
                    continue

                if "in run with ID '" in l:
                    run_ids[letter] = (
                        l.split("in run with ID '")[-1].split("'")[0].strip()
                    )
                    print(runformat(colour, letter, f"MAPPED TO {run_ids[letter]}"))


def execute_workload(defs: list, group_run_id: str | None = None, execution_type: ExecutionType = ExecutionType.DIRECT, poll_interval=1.0):
    """Executes a workload. Handles run halting and collecting of run status.

    Args:
        defs (list): Workload definitions to run

    Returns:
        list: Run results to write back to df
    """
    run_ids = {}

    terminate = False

    log = []
    log_runs = {}
    popens = []
    returncodes = {}

    start_time = time.time()

    # Remove MLprojects
    for _, _, _, _, _, _, _, filepath, _ in defs:
        while (Path(filepath) / "MLproject").is_file():
            (Path(filepath) / "MLproject").unlink()
            time.sleep(2)

    with ExitStack() as stack:
        try:
            for id, colour, letter, run_name, vars, cmd, param_def, filepath, _ in defs:
                print(
                    runformat(
                        colour,
                        letter,
                        f"context: {id}-{colour}-{letter}-{run_name}-{vars}-{cmd}-{filepath}",
                    )
                )

                env = os.environ.copy()
                for k, v in vars.items():
                    env[k] = str(v)

                # Write run blocker
                with open(Path(filepath) / "radtlock", "w") as lock:
                    lock.write("")

                # Write mlflow mlproject
                # This is used even when not using MLflow managed runs (as a blocker)
                with open(Path(filepath) / "MLproject", "w") as project_file:
                    if execution_type == ExecutionType.MLFLOW:
                        project_file.write(param_def)
                    else:
                        project_file.write("")

                stack.enter_context(
                    p := Popen(
                        cmd,
                        stdout=PIPE,
                        stderr=STDOUT,
                        bufsize=1,
                        env=env,
                        universal_newlines=True,
                        # shell=True,  # TODO: remove shell
                    )
                )

                q = Queue()
                t = Thread(target=enqueue_output, args=(p.stdout, q))
                t.daemon = True
                t.start()

                popens.append((colour, letter, p, q, t))
                log_runs[letter] = []
                run_ids[letter] = False

                time.sleep(3)

                # Wait for MLproject to be cleared
                while (Path(filepath) / "MLproject").is_file() or run_ids[
                    letter
                ] == False:
                    process_output(popens, log_runs, log, run_ids)
                    time.sleep(2)

            # Group runs into workload children
            # And add experiment/workload to name
            parent_id = ""
            for _, _, letter, run_name, _, _, _, filepath, _ in defs:
                if run_id := run_ids[letter]:
                    client = MlflowClient()
                    if run := client.get_run(run_id):
                        
                        # MLFlow: params are set, we grab workload name from there
                        if execution_type == ExecutionType.MLFLOW:
                            workload_name = run.data.params["workload"]
                        else:
                            # Direct: set the params in param_def
                            log_params(param_def, run_id=run_id)
                            workload_name = param_def["workload"]

                        run_name = (
                            run_name
                            if (str(run_name).strip() not in ("", "nan"))
                            else run.info.run_name
                        )
                        client.set_tag(
                            run_id,
                            "mlflow.runName",
                            f"({workload_name} {letter}) {run_name}",  # todo: rename this to be less ambigious with the other name
                        )

                        if not parent_id:
                            parent_id = run_id

                            # If using a group, parents should fall under that
                            if group_run_id:
                                client.set_tag(
                                    run_id, "mlflow.parentRunId", group_run_id
                                )

                        elif parent_id != run_id:
                            client.set_tag(run_id, "mlflow.parentRunId", parent_id)

            # Remove run blockers to start synchronised runs
            for _, _, _, _, _, _, _, filepath, _ in defs:
                if (Path(filepath) / "radtlock").is_file():
                    (Path(filepath) / "radtlock").unlink()

            while True:

                # Stop once all processes have finished
                for _, _, p, _, _ in popens:
                    # Check if process is alive
                    if p.poll() is None:
                        break
                else:
                    break

                process_output(popens, log_runs, log, run_ids)
                time.sleep(poll_interval)

        except KeyboardInterrupt:
            try:
                sysprint("Interrupting runs... Please wait")
                terminate = True

                time.sleep(1)
                for colour, letter, p, q, t in popens:
                    last_update = time.time()
                    while time.time() - last_update < 1.0 or p.poll() is None:
                        try:
                            l = q.get_nowait()
                            if not l.strip() and not "\n" in l:
                                continue
                        except Empty:
                            pass
                        else:
                            last_update = time.time()
                            log_runs[letter].append(l)
                            log.append(runformat(None, letter, l))
                            print(runformat(colour, letter, l), end="")
                        time.sleep(poll_interval)
            except KeyboardInterrupt:
                pass

        for _, letter, p, _, _ in popens:
            returncodes[letter] = p.returncode

    sysprint("Sending logs to server.")
    results = []

    for id, _, letter, _, _, _, _, filepath, row in defs:
        if run_id := run_ids[letter]:
            client = MlflowClient()
            if run := client.get_run(run_id):
                results.append(
                    (
                        id,
                        letter,
                        returncodes[letter],
                        run_id,
                        run.info.run_name,
                        run.info.status,
                    )
                )
                client.log_text(run_id, "".join(log_runs[letter]), f"log_{run_id}.txt")
                client.log_text(run_id, "".join(log), f"log_workload.txt")

                if row["WorkloadListener"]:
                    try:
                        for file in Path(filepath).glob(
                            f"{row['WorkloadListener'].split('-o ')[1].split()[0]}*.*-rep"
                        ):
                            client.log_artifact(run_id, str(file))
                            file.unlink()
                    except IndexError:
                        pass

    if terminate:
        sys.exit()

    return results


def get_gpu_ids():
    """Get UUIDs of all gpus

    Returns:
        dict: GPU indices and UUIDs or empty if nvidia-smi not found
    """
    gpus = {}
    try:
        for line in execute_command("nvidia-smi -L"):
            if "UUID: GPU" in line:
                gpu = line.split("GPU")[1].split(":")[0].strip()
                gpus[gpu] = line.split("UUID:")[1].split(")")[0].strip()
    except FileNotFoundError as e:
        sysprint(f"SMI not found or unreachable. Continuing without SMI. ({e})")
    return gpus


def make_dcgm_groups(dev_table: pd.DataFrame):
    """Removes old DCGM groups and make a DCGM group with the required devices.

    Args:
        dev_table (pd.DataFrame): Run to device mapping table.

    Raises:
        ValueError: Group could not be created

    Returns:
        bool: Whether DCGMI is available.
        pd.DataFrame: Run to id mapping table.
    """

    try:
        # Grab existing groups
        result = [l for l in execute_command("dcgmi group -l") if "Group ID" in l]
        group_ids = [int(l.split("|")[-2]) for l in result]

        # Delete groups. First two are protected and shouldn't be deleted
        if len(group_ids) > 1:
            for i in group_ids:
                if i in (0, 1):
                    continue

                result = "".join(execute_command(f"dcgmi group -d {i}")).lower()
                if "error" in result:
                    raise ValueError(
                        "DCGMI group index not found. Could not be deleted"
                    )

        set_ids = {}
        for i, s in enumerate(dev_table.sort_values().unique()):
            # Create a new group
            result = "".join(execute_command(f"dcgmi group -c mldnn_{i}")).lower()
            if "error" in result:
                raise ValueError("DCGMI group could not be created.")
            group_id = int(result.split("group id of ")[1].split()[0])

            gpu_ids = ",".join(str(int(float(x))) for x in s)

            # Add the gpu ids to the new group
            result = "".join(
                execute_command(f"dcgmi group -g {group_id} -a {gpu_ids}")
            ).lower()
            if "error" in result:
                raise ValueError("DCGMI group could not be set up with required GPUs.")

            set_ids[s] = group_id

        dcgmi_table = {}
        for i, v in dev_table.items():
            dcgmi_table[i] = set_ids[v]
        return True, dcgmi_table
    except (FileNotFoundError, ValueError) as e:
        sysprint(f"DCGMI not found or unreachable. Continuing without DCGMI. ({e})")
        dcgmi_table = {}
        for i, v in dev_table.items():
            dcgmi_table[i] = ""
        return False, dcgmi_table


def make_mps(df_workload: pd.DataFrame, gpu_uuids: dict):
    """Initialise MPS mode if MPS flag is present

    Args:
        df_workload (pd.DataFrame): Workload to run
        gpu_uuids (dict): GPU UUIDs to run MPS on

    Raises:
        Exception: Attempting to run MPS on multiple devices
    """

    gpu_ids = (
        df_workload[df_workload["Collocation"].str.strip().str.lower() == "mps"][
            "Devices"
        ]
        .sort_values()
        .unique()
    )

    for gpu in gpu_ids:
        result = "".join(
            execute_command(
                f"nvidia-cuda-mps-control -d",
                vars={"CUDA_VISIBLE_DEVICES": str(gpu_uuids[str(gpu)])},
            )
        ).lower()
        if "is already running" in result:
            raise Exception(
                f"MPS Error (did you try to run MPS on multiple groups?): {result}"
            )


def remove_mps():
    """Remove MPS"""
    execute_command(["echo quit | nvidia-cuda-mps-control"], shell=True)


def clear_page_cache():
    """Clears OS page cache"""
    return  # TODO: re-enable
    execute_command(['sudo sh -c "/bin/echo 3 > /proc/sys/vm/drop_caches"'], shell=True)


def determine_operating_mode(
    parsed_args: Namespace, file: Path, args_passthrough: list
):
    """Determine and initialise whether running a .csv or .py file

    Args:
        parsed_args (Namespace): Schedule arguments
        file (Path): Path to file
        args_passthrough (list): Run arguments

    Returns:
        pd.DataFrame, pd.Dataframe: Dataframe to run, copy
    """
    if isinstance(file, pd.DataFrame):
        df_raw = None
        df = file
    elif file.suffix == ".py":
        df_raw = None
        df = pd.DataFrame(np.empty(0, dtype=constants.CSV_FORMAT))

        if len(args_passthrough):
            params = " ".join(args_passthrough)
        else:
            params = ""

        df.loc[0] = pd.Series(
            {
                "Experiment": parsed_args.experiment,
                "Workload": parsed_args.workload,
                "Name": parsed_args.name,
                "Status": "",
                "Run": "",
                "Devices": parsed_args.devices,
                "Collocation": parsed_args.collocation,
                "Listeners": parsed_args.listeners,
                "File": str(file),
                "Params": params,
            }
        )

    elif file.suffix == ".csv":
        df_raw = pd.read_csv(file, delimiter=",", header=0, skipinitialspace=True)
        df_raw["Collocation"] = df_raw["Collocation"].astype(str)
        # Ensure a Name column exists immediately after Workload
        cols = list(df_raw.columns)
        if "Name" not in df_raw.columns:
            try:
                widx = cols.index("Workload")
                df_raw.insert(widx + 1, "Name", "")
            except ValueError:
                raise ValueError("CSV file must contain a 'Workload' column")
        df = df_raw.copy()

    return df, df_raw


def start_schedule(
    parsed_args: Namespace,
    file: Path,
    args_passthrough: list,
    group_name: str | None = None,
):
    """Schedule (execute) a .py or .csv file via RADT

    Args:
        parsed_args (Namespace): Schedule arguments
        file (Path): Path to file
        args_passthrough (list): Run arguments
        group_name (str | None): Group name
    """
    df, df_raw = determine_operating_mode(parsed_args, file, args_passthrough)

    df["Workload_Unique"] = (
        df["Experiment"].astype(str) + "+" + df["Workload"].astype(str)
    )

    for workload in df["Workload_Unique"].unique():
        df_workload = df[df["Workload_Unique"] == workload].copy()

        df_workload["Letter"] = "-"
        df_workload["Number"] = -1

        for i, (id, row) in enumerate(df_workload.iterrows()):
            # Skip workloads that have been finished already
            # Reruns FAILED workloads when --rerun is specified
            if not (
                "FINISHED" in str(row["Status"]).strip()
                or ("FAILED" in str(row["Status"]).strip() and (not parsed_args.rerun))
            ):
                break
        else:
            sysprint(f"SKIPPING Workload: {workload}")
            continue

        assigned = []
        for i, row in df_workload.iterrows():
            if row["Devices"] not in assigned:
                assigned.append(row["Devices"])
            letter = row["Devices"]
            df_workload.loc[i, "Letter"] = letter
            df_workload.loc[i, "Number"] = ascii_uppercase[
                (df_workload["Letter"].value_counts()[letter] - 1)
            ]

        letter_quants = df_workload["Letter"].value_counts()
        for i, row in df_workload.iterrows():
            if letter_quants[row["Letter"]] > 1:
                df_workload.loc[i, "Letter"] = f'{row["Letter"]}_{row["Number"]}'
            if str(row["Collocation"]).strip() not in ("-", "", "nan"):
                df_workload.loc[i, "Letter"] = (
                    f'{df_workload.loc[i, "Letter"]}_{df_workload.loc[i, "Collocation"]}'
                )

        # Set devices string and DCGMI group
        try:
            migedit.remove_mig_devices()
        except FileNotFoundError:
            # SMI not found, continue
            pass

        dev_table = df_workload["Devices"].astype(str).str.split("+").apply(frozenset)
        mig_table, entity_table = dev_table.copy(), dev_table.copy()

        for i, row in df_workload.iterrows():
            remove_mps()
            clear_page_cache()

            if "g" in str(row["Collocation"]):  # TODO: fix
                result = migedit.make_mig_devices(
                    row["Devices"], [row["Collocation"]], remove_old=False
                )
                mig_table.loc[i] = frozenset([y for x in result for y in x[4]])
                entity_table.loc[i] = frozenset([x[3] for x in result])

        gpu_uuids = get_gpu_ids()
        for i, v in mig_table.items():
            s = set()
            for device in v:
                device = device.strip()
                if device in gpu_uuids:
                    device = gpu_uuids[device]
                s.add(device)
            mig_table[i] = frozenset(s)

        dcgmi_enabled, dcgmi_table = make_dcgm_groups(entity_table)

        make_mps(df_workload, gpu_uuids)

        workload_definitions = []

        # Check if python or python3 is the correct command -- only when not using conda
        if parsed_args.useconda:
            python_command = "python"
        else:
            py_check = execute_command(
                "command -v python || command -v python3", shell=True
            )

            # if ends on python3, use that
            if py_check and py_check[-1].strip()[-7:] == "python3":
                python_command = "python3"
            else:
                python_command = "python"

        for i, (id, row) in enumerate(df_workload.iterrows()):
            row = row.copy()
            row["Filepath"] = str(Path(row["File"]).parent.absolute())
            row["File"] = str(Path(row["File"]).name)
            row["Envmanager"] = "conda" if parsed_args.useconda else "local"

            # Workload Listener
            listeners = row["Listeners"].split("+")
            row["WorkloadListener"] = ""
            listener_env_vars = {k: "False" for k in constants.RUN_LISTENERS}

            for listener in listeners:
                if (k := listener.strip()) in constants.WORKLOAD_LISTENERS:
                    row["WorkloadListener"] = constants.WORKLOAD_LISTENERS[k].format(
                        **row
                    )
                    listeners.remove(listener)
                else:
                    if k.upper() == "DCGMI" and not dcgmi_enabled:
                        continue
                    listener_env_vars[f"RADT_LISTENER_{k.upper()}"] = "True"

            listeners = "+".join(listeners)

            # Determine the actual command to run
            # This differs for CONDA mode (using mlflow wrapping) vs using a direct python command
            if parsed_args.useconda:
                # CONDA mode (using mlflow wrapping)
                command = (constants.COMMAND.format(**row).split()
                    + ["-P", f"workload_listener={row['WorkloadListener']}"])
                param_def = (constants.MLPROJECT_CONTENTS.replace(
                        "<REPLACE_COMMAND>",
                        constants.MLFLOW_COMMAND.format(
                            WorkloadListener=row["WorkloadListener"],
                            Listeners=listeners,
                            File=row["File"],
                            Params=row["Params"] or '""',
                            PythonCommand=python_command,
                        ),
                    ).replace(
                        "<REPLACE_ENV>",
                        "conda_env: conda.yaml" if parsed_args.useconda else "",
                    ))
            else:
                # Direct mode (using direct python command)
                command = shlex.split(constants.DIRECT_COMMAND.format(
                        Listeners=listeners,
                        File=row["File"],
                        Params=row["Params"] or '""',
                        PythonCommand=python_command,
                    ))
                param_def = {"letter": row["Letter"],
                             "workload": row["Workload"],
                             "listeners": listeners,
                             "params": row["Params"] or "",
                             "file": row["File"],
                             "workload_listener": row["WorkloadListener"]
                             }

            workload_definitions.append(
                (
                    id,
                    constants.COLOURS[i % 6],
                    row["Letter"],
                    row["Name"],
                    {
                        "MLFLOW_EXPERIMENT_ID": str(row["Experiment"]).strip(),
                        "CUDA_VISIBLE_DEVICES": ",".join(map(str, mig_table[id])),
                        "RADT_DCGMI_GROUP": str(dcgmi_table[id]),
                        "SMI_GPU_ID": str(row["Devices"]),
                        "RADT_PRESENT": "True",
                        "RADT_MANUAL_MODE": "True" if parsed_args.manual else "False",
                        "PYTHONUNBUFFERED": "1" if not parsed_args.buffered else "",
                    }
                    | listener_env_vars,
                    command,
                    param_def,
                    row["Filepath"],
                    row,
                )
            )

        execution_type = ExecutionType.MLFLOW if parsed_args.useconda else ExecutionType.DIRECT
        
        # If group name is set, start a run to group all workloads into
        if group_name is not None:
            with mlflow.start_run(
                run_name=group_name,
                experiment_id=str(df_workload.loc[:, "Experiment"].iloc[0]).strip(),
            ) as parent_run:
                sysprint(
                    f"RUNNING WORKLOAD: {workload} with group run '{group_name}' with ID {parent_run.info.run_id} in {execution_type.value} mode"
                )
                results = execute_workload(
                    workload_definitions, group_run_id=parent_run.info.run_id, execution_type=execution_type, poll_interval=parsed_args.poll_interval
                )
        else:
            # Format and run the row
            sysprint(f"RUNNING WORKLOAD: {workload} in {execution_type.value} mode")
            results = execute_workload(workload_definitions, execution_type=execution_type, poll_interval=parsed_args.poll_interval)

        remove_mps()

        # Write if .csv
        if isinstance(df_raw, pd.DataFrame):
            for id, letter, returncode, run_id, run_name, status in results:
                df_raw.loc[id, "Run"] = run_id
                df_raw.loc[id, "Status"] = f"{status} {run_name} ({letter})"

            # Write the result of the run to the csv
            target = Path("result.csv")
            df_raw.to_csv(target, index=False)
            file.unlink()
            target.rename(file)
