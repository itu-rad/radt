import argparse
import os
import sqlalchemy
import pandas as pd
import json
import uuid
import numpy as np
import logging
from typing import Dict, Any, Optional, List, Tuple

from perfetto.trace_builder.proto_builder import TraceProtoBuilder
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    TrackEvent,
    TrackDescriptor,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="Export Perfetto trace from DB")
    parser.add_argument(
        "--run_id",
        type=str,
        required=True,
        help="mlflow run id",
    )
    # parser.add_argument(
    #     "--output",
    #     type=str,
    #     required=True,
    #     help="Path to output .pftrace file",
    # )
    return parser.parse_args()


def retrieve_spans(run_id: str) -> pd.DataFrame:
    """Retrieves spans from the database for the given run_id.

    Args:
        run_id: The MLflow run ID to fetch spans for.

    Returns:
        A pandas DataFrame containing the spans data.
    """
    user = os.getenv("MLFLOW_DB_USER")
    password = os.getenv("MLFLOW_DB_PASS")
    url = os.getenv("MLFLOW_DB_URL")
    connection_string = f"postgresql://{user}:{password}@{url}"
    engine = sqlalchemy.create_engine(connection_string)

    query = """
        SELECT 
            s.*
        FROM 
            spans s
        JOIN 
            trace_request_metadata trm ON s.trace_id = trm.request_id
        WHERE 
            trm.key = 'mlflow.sourceRun' 
            AND trm.value = :run_id
        ORDER BY s.start_time_unix_nano 
    """
    logger.info(f"Fetching spans for run_id: {run_id}")
    return pd.read_sql(sqlalchemy.text(query), engine, params={"run_id": run_id})


def retrieve_metrics(run_id: str) -> pd.DataFrame:
    """Retrieves metrics from the database for the given run_id.

    Args:
        run_id: The MLflow run ID to fetch metrics for.

    Returns:
        A pandas DataFrame containing the metrics data.
    """
    user = os.getenv("MLFLOW_DB_USER")
    password = os.getenv("MLFLOW_DB_PASS")
    url = os.getenv("MLFLOW_DB_URL")
    connection_string = f"postgresql://{user}:{password}@{url}"
    engine = sqlalchemy.create_engine(connection_string)

    query = """
        select * from metrics m 
        where m.run_uuid = :run_id
    """
    logger.info(f"Fetching metrics for run_id: {run_id}")
    return pd.read_sql(sqlalchemy.text(query), engine, params={"run_id": run_id})


def explode_spans_content(spans_df: pd.DataFrame) -> pd.DataFrame:
    """Extracts attributes from the JSON content column of the spans DataFrame.

    Expanding keys like 'attributes.thread_id' and flow IDs into their own columns.

    Args:
        spans_df: The spans DataFrame to process.

    Returns:
        A new DataFrame with exploded content columns.
    """
    if spans_df.empty:
        return spans_df

    logger.info("Exploding span content...")
    content_df = pd.json_normalize(spans_df["content"].apply(json.loads))

    # Select relevant columns from content if they exist, otherwise fill with None
    needed_cols = [
        "attributes.thread_id",
        "attributes.in_flow_id",
        "attributes.out_flow_id",
    ]
    for col in needed_cols:
        if col not in content_df.columns:
            content_df[col] = None

    return pd.concat(
        [
            spans_df.reset_index(drop=True),
            content_df[needed_cols],
        ],
        axis=1,
    )


def _get_perfetto_flow_id(flow_uuid_str: Optional[str]) -> Optional[int]:
    """Converts a UUID string to a Perfetto-compatible 64-bit unsigned flow ID.

    Args:
        flow_uuid_str: The flow UUID string.

    Returns:
        A 64-bit integer representation of the UUID, or None if invalid.
    """
    if pd.isna(flow_uuid_str) or not flow_uuid_str:
        return None
    try:
        # Create a UUID object from the string
        u = uuid.UUID(flow_uuid_str)
        # Convert to 64-bit integer (take lower 64 bits)
        return u.int & ((1 << 63) - 1)
    except ValueError:
        logger.warning(f"Invalid flow UUID: {flow_uuid_str}")
        return None


def _flatten_dict(
    d: Dict[str, Any], parent_key: str = "", sep: str = "."
) -> Dict[str, Any]:
    """Recursively flattens a nested dictionary.

    Args:
        d: The dictionary to flatten.
        parent_key: The prefix for the current keys.
        sep: The separator to use between keys.

    Returns:
        A flattened dictionary.
    """
    items: List[Tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def _generate_process_and_thread_tracks(
    builder: TraceProtoBuilder, spans_df: pd.DataFrame
) -> Dict[int, int]:
    """Generates Process and Thread tracks based on span data.

    Creates a single shared process and individual tracks for each unique thread_id
    found in the spans. Tracks are ordered by their first span's start time.

    Args:
        builder: The TraceProtoBuilder instance.
        spans_df: The spans DataFrame.

    Returns:
        A dictionary mapping thread_id to track_uuid.
    """
    thread_map: Dict[int, int] = {}

    if spans_df.empty or "attributes.thread_id" not in spans_df.columns:
        return thread_map

    # Determine thread order based on minimum start time
    thread_order_df = (
        spans_df.groupby("attributes.thread_id")["start_time_unix_nano"]
        .min()
        .reset_index()
    )
    thread_order_df = thread_order_df.sort_values("start_time_unix_nano")

    # Create a map for order rank
    thread_rank_map = {
        row["attributes.thread_id"]: i
        for i, (_, row) in enumerate(thread_order_df.iterrows())
    }

    # Emit Process Descriptor (once) for the shared process
    PROCESS_PID = 100

    proc_packet = builder.add_packet()
    proc_packet.track_descriptor.uuid = uuid.uuid4().int & ((1 << 63) - 1)
    proc_packet.track_descriptor.process.pid = PROCESS_PID
    proc_packet.track_descriptor.process.process_name = "Colocation Benchmark"

    # Emit TrackDescriptors first in the correct order
    for i, (_, row) in enumerate(thread_order_df.iterrows()):
        thread_id = row["attributes.thread_id"]

        # Generate a random UUID for the track
        track_uuid = uuid.uuid4().int & ((1 << 63) - 1)
        thread_map[thread_id] = track_uuid

        # Emit TrackDescriptor packet
        packet = builder.add_packet()
        packet.track_descriptor.uuid = track_uuid
        packet.track_descriptor.name = f"Thread {thread_id}"

        # Use the rank (index) as the TID
        mapped_tid = thread_rank_map.get(thread_id, 0)

        packet.track_descriptor.thread.pid = PROCESS_PID
        packet.track_descriptor.thread.tid = mapped_tid

        # Use sibling_order_rank to enforce order
        packet.track_descriptor.sibling_order_rank = mapped_tid

    return thread_map


def _add_span_events(
    builder: TraceProtoBuilder, spans_df: pd.DataFrame, thread_map: Dict[int, int]
) -> None:
    """Adds span events (slices) to the trace.

    Includes flow IDs and debug annotations.

    Args:
        builder: The TraceProtoBuilder instance.
        spans_df: The spans DataFrame.
        thread_map: A mapping of thread_ids to track_uuids.
    """
    TRUSTED_PACKET_SEQUENCE_ID = 42

    for index, row in spans_df.iterrows():
        thread_id = row.get("attributes.thread_id")

        if thread_id not in thread_map:
            continue

        track_uuid = thread_map[thread_id]

        # Parse timestamps (nanoseconds)
        start_ts = int(row["start_time_unix_nano"])
        end_ts = int(row["end_time_unix_nano"])
        name = str(row["name"])

        # Parse flow IDs
        flow_ids_start = []
        in_flow = row.get("attributes.in_flow_id")
        out_flow = row.get("attributes.out_flow_id")

        in_flow_id = _get_perfetto_flow_id(in_flow)
        out_flow_id = _get_perfetto_flow_id(out_flow)

        if in_flow_id:
            flow_ids_start.append(in_flow_id)

        if out_flow_id:
            flow_ids_start.append(out_flow_id)

        # Emit SLICE_BEGIN
        packet = builder.add_packet()
        packet.timestamp = start_ts
        packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID
        packet.track_event.type = TrackEvent.TYPE_SLICE_BEGIN
        packet.track_event.track_uuid = track_uuid
        packet.track_event.name = name
        if flow_ids_start:
            packet.track_event.flow_ids.extend(flow_ids_start)

        # Add content 'attributes' as debug annotations
        try:
            content_json = row.get("content")
            if content_json and isinstance(content_json, str):
                content_dict = json.loads(content_json)
                attributes = content_dict.get("attributes")

                if attributes and isinstance(attributes, dict):
                    flat_attributes = _flatten_dict(attributes)

                    for k, v in flat_attributes.items():
                        annotation = packet.track_event.debug_annotations.add()
                        annotation.name = k
                        if isinstance(v, bool):
                            annotation.bool_value = v
                        elif isinstance(v, int):
                            annotation.int_value = v
                        elif isinstance(v, float):
                            annotation.double_value = v
                        else:
                            annotation.string_value = str(v)
        except Exception as e:
            logger.warning(f"Failed to add debug annotations for span {name}: {e}")

        # Emit SLICE_END
        packet = builder.add_packet()
        packet.timestamp = end_ts
        packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID
        packet.track_event.type = TrackEvent.TYPE_SLICE_END
        packet.track_event.track_uuid = track_uuid


def _add_metric_events(builder: TraceProtoBuilder, metrics_df: pd.DataFrame) -> None:
    """Adds metric events (counters) to the trace.

    Args:
        builder: The TraceProtoBuilder instance.
        metrics_df: The metrics DataFrame.
    """
    if metrics_df.empty:
        return

    logger.info(f"Processing {len(metrics_df)} metric rows...")
    TRUSTED_PACKET_SEQUENCE_ID = 42

    # Convert timestamp from ms to ns
    # Avoid SettingWithCopyWarning by creating a copy or assigning directly safely
    metrics_df = metrics_df.copy()
    metrics_df["timestamp_ns"] = (metrics_df["timestamp"] * 1000000).astype(np.int64)

    # Group by key to create tracks
    unique_keys = metrics_df["key"].unique()
    metric_track_map = {}

    # Create Counter Tracks for each metric key
    for key in unique_keys:
        track_uuid = uuid.uuid4().int & ((1 << 63) - 1)
        metric_track_map[key] = track_uuid

        packet = builder.add_packet()
        packet.track_descriptor.uuid = track_uuid
        packet.track_descriptor.name = key
        # Mark it as a counter track. unit_name is optional.
        # We assume double values for metrics.
        packet.track_descriptor.counter.unit_name = "value"

    # Emit Counter Events
    for index, row in metrics_df.iterrows():
        key = row["key"]
        val = float(row["value"])
        ts = int(row["timestamp_ns"])

        track_uuid = metric_track_map.get(key)

        packet = builder.add_packet()
        packet.timestamp = ts
        packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID
        # TYPE_COUNTER implies a counter event
        packet.track_event.type = TrackEvent.TYPE_COUNTER
        packet.track_event.track_uuid = track_uuid
        packet.track_event.double_counter_value = val


def generate_trace(
    spans_df: pd.DataFrame, metrics_df: pd.DataFrame, output_path: str
) -> None:
    """Orchestrates the generation of the Perfetto trace.

    Args:
        spans_df: The spans DataFrame.
        metrics_df: The metrics DataFrame.
        output_path: The file path to write the trace to.
    """
    builder = TraceProtoBuilder()

    if spans_df.empty:
        logger.warning("No spans data to process.")

    # 1. Generate Process and Thread Tracks
    if not spans_df.empty:
        logger.info(f"Processing {len(spans_df)} span rows...")
        thread_map = _generate_process_and_thread_tracks(builder, spans_df)

        # 2. Add Span Events (Slices, Flows, Debug Args)
        _add_span_events(builder, spans_df, thread_map)

    # 3. Add Metric Events (Counters)
    _add_metric_events(builder, metrics_df)

    # 4. Serialize to file
    logger.info(f"Writing trace to {output_path}")
    try:
        with open(output_path, "wb") as f:
            f.write(builder.serialize())
        logger.info("Done.")
    except Exception as e:
        logger.error(f"Error writing output file: {e}")


def main() -> None:
    """Main execution entry point."""
    args = parse_args()
    try:
        spans_df = retrieve_spans(args.run_id)
        print(spans_df.head())
        spans_df = explode_spans_content(spans_df)

        metrics_df = retrieve_metrics(args.run_id)

        generate_trace(spans_df, metrics_df, args.run_id + ".pftrace")
    except Exception as e:
        logger.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
