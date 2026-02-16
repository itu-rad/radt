import argparse
import sys
from pathlib import Path

from . import constants
from .run import start_run
from .schedule import start_schedule


def schedule_split_arguments(parser):
    """Split arguments for `radt` into parsed arguments and passthrough arguments

    Returns:
        list, Path, list: Split arguments
    """
    sysargs = sys.argv[1:]

    for i, arg in enumerate(sysargs):
        if (
            arg.strip()[-3:] == ".py"
            or arg.strip()[-4:] == ".csv"
            or arg.strip()[-4:] == ".yml"
            or arg.strip()[-5:] == ".yaml"
        ):
            return sysargs[:i], Path(arg), sysargs[i + 1 :]
    else:
        parser.print_help()
        print("\nPlease supply a python, csv, or yaml file.")
        exit()


def schedule_parser():
    """Argparser for `radt`

    Returns:
        argparse.Parser: arg parser
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

    return parser


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
    parser = schedule_parser()
    args, file, args_passthrough = schedule_split_arguments(parser)
    parsed_args = parser.parse_args(args)

    start_schedule(parsed_args, file, args_passthrough)


def cli_run():
    args = run_parse_arguments(sys.argv[2:])
    listeners = args.listeners.lower().split("+")
    check_run_listeners(listeners)
    start_run(args, listeners)


def cli():
    """Entrypoint for `radt` and `radt run`"""
    if len(sys.argv) > 1 and sys.argv[1].strip() == "run":
        cli_run()
    else:
        cli_schedule()


def schedule_external(args, df, group_name=None):
    """Schedule a dataframe

    Args:
        entrypoint (str): Path to the entrypoint
        run_definitions (list): List of run definitions
    """

    parsed_args = schedule_parser(args)
    args_passthrough = []
    start_schedule(parsed_args, df, args_passthrough, group_name=group_name)
