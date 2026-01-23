import argparse
import sys
from pathlib import Path

from . import constants
from .run import start_run
from .schedule import start_schedule


def schedule_split_arguments():
    """Split arguments for `radt` into parsed arguments and passthrough arguments

    Returns:
        list, Path, list: Split arguments
    """
    sysargs = sys.argv[1:]

    for i, arg in enumerate(sysargs):
        if arg.strip()[-3:] == ".py" or arg.strip()[-4:] == ".csv":
            return sysargs[:i], Path(arg), sysargs[i + 1 :]
    else:
        print("Please supply a python or csv file.")
        exit()


def schedule_parse_arguments(args: list):
    """Argparse for `radt`

    Args:
        args (list): List of raw arguments

    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="RADt Automatic Tracking and Benchmarking"
    )
    parser.add_argument(
        "-e",
        "--experiment",
        type=int,
        dest="experiment",
        default=0,
        help="Experiment ID",
    )
    parser.add_argument(
        "-w", "--workload", type=int, dest="workload", default=0, help="Workload ID"
    )
    parser.add_argument(
        "-d",
        "--devices",
        type=str,
        dest="devices",
        default="0",
        help="Devices to run on separated by +, e.g. 0, 1+2",
    )
    parser.add_argument(
        "-n",
        "--name",
        type=str,
        dest="name",
        default="",
        help="Name of the run",
    )
    parser.add_argument(
        "-c",
        "--collocation",
        type=str,
        dest="collocation",
        default="-",
        help="Method of collocation, either empty, mps, or a MIG profile string",
    )
    parser.add_argument(
        "-l",
        "--listeners",
        type=str,
        dest="listeners",
        default="smi+top+dcgmi+iostat+free",
        help=f"Metric collectors separated by +, available: {' '.join(constants.RUN_LISTENERS + list(constants.WORKLOAD_LISTENERS.keys()))}",
    )
    parser.add_argument(
        "-r",
        "--rerun",
        action="store_true",
        dest="rerun",
        default=False,
        help="Whether to force rerun runs that have previously failed",
    )
    parser.add_argument(
        "-i",
        "--epoch",
        type=int,
        dest="max_epoch",
        default=5,
        help="Maximum number of epochs to train for",
    )
    parser.add_argument(
        "-t",
        "--time",
        type=int,
        dest="max_time",
        default=2 * 24 * 60,
        help="Maximum amount of time to train for (minutes)",
    )
    parser.add_argument(
        "--conda",
        action="store_true",
        dest="useconda",
        default=False,
        help="Use conda.yaml to create a conda environment",
    )
    parser.add_argument(
        "--poll_interval",
        type=float,
        dest="poll_interval",
        default=1.0,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--buffered",
        action="store_true",
        dest="buffered",
        default=False,
        help="Whether to use buffered output (PYTHONUNBUFFERED is true if not set)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        dest="manual",
        default=False,
        help="Only start tracking run when context is initialised",
    )

    return parser.parse_args(args)


def run_parse_arguments(args: list):
    """Argparse for `radt run`

    Args:
        args (list): List of raw arguments

    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(description="RADt runner")

    parser.add_argument(
        "-l",
        "--listeners",
        metavar="LISTENERS",
        required=True,
        help=f"listeners, available: {' '.join(constants.RUN_LISTENERS)}",
    )
    parser.add_argument(
        "-c",
        "--command",
        type=str,
        metavar="COMMAND",
        required=True,
    )
    parser.add_argument("-p", "--params", type=str, metavar="PARAMS")

    return parser.parse_args(args)


def check_run_listeners(l):
    """Check whether all run listeners are registered

    Args:
        l (list): Listeners

    Raises:
        Exception: Listener unavailable
    """
    if len(l) == 1 and l[0] == "none":
        return
    for entry in l:
        if entry not in constants.RUN_LISTENERS:
            raise Exception(f"Unavailable listener: {entry}")


def cli_schedule():
    args, file, args_passthrough = schedule_split_arguments()
    parsed_args = schedule_parse_arguments(args)

    start_schedule(parsed_args, file, args_passthrough)


def cli_run():
    args = run_parse_arguments(sys.argv[2:])
    listeners = args.listeners.lower().split("+")
    check_run_listeners(listeners)
    start_run(args, listeners)


def cli():
    """Entrypoint for `radt` and `radt run`"""
    if sys.argv[1].strip() == "run":
        cli_run()
    else:
        cli_schedule()
