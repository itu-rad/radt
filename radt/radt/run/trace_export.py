"""Client-side Perfetto export for radt batch traces.

Converts the ``radt-trace/`` artifacts written by :mod:`radt.run.trace` into a
``.pftrace`` file, using nothing but a stock mlflow client. This is the path for
users on a vanilla mlflow server: the custom radT mlflow image offers the same
conversion behind an "open trace" button, but nothing here depends on it.

The two implementations are deliberately independent -- the mlflow server must
not import radt, whose package import patches mlflow's span exporter. They agree
via the on-disk contract instead: ``SCHEMA_VERSION`` and the ``record_formats``
map in the manifest. Change the record layout on one side and the other must
bump the version to match.

Requires the ``perfetto`` extra::

    pip install 'radt[export]'

Usage::

    radt export-trace <run_id> [-o out.pftrace] [--upload]

or::

    from radt.run.trace_export import export_trace
    export_trace("<run_id>")
"""

import gzip
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trace import ARTIFACT_DIR, MANIFEST_NAME, SCHEMA_VERSION

_logger = logging.getLogger(__name__)

#: Must match the name the radT mlflow server uses, so a trace exported here is
#: recognised as already-converted by its "open trace" button.
TRACE_NAME = "trace.pftrace"

SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

_PROCESS_PID = 100
_SEQUENCE_ID = 42


class TraceExportError(Exception):
    """Raised for conditions worth reporting to the user verbatim."""


@dataclass
class _Span:
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def track_key(self):
        """Spans are laid out on a track per workload thread.

        ``thread_id`` comes from the workload, not from radt, so it is often
        absent; falling back to the trace id keeps those spans on the timeline
        rather than dropping them.
        """
        thread_id = self.attributes.get("thread_id")
        if thread_id is not None:
            return ("thread", thread_id)
        return ("trace", self.attributes.get("__trace_id"))

    @property
    def track_label(self):
        kind, value = self.track_key
        return f"Thread {value}" if kind == "thread" else f"Trace {value}"


def _flatten(value, parent_key="", sep="."):
    items = {}
    for key, item in value.items():
        name = f"{parent_key}{sep}{key}" if parent_key else str(key)
        if isinstance(item, dict):
            items.update(_flatten(item, name, sep))
        else:
            items[name] = item
    return items


def _flow_id(value):
    """Perfetto flow ids are 64-bit; radt correlates spans with UUID strings."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value)).int & ((1 << 63) - 1)
    except ValueError:
        return None


def _batch_names(directory):
    """Batches to read, and whether the trace is known to be complete.

    Prefers the manifest's list. Without one -- a run killed before it was
    uploaded -- falls back to the batches on disk: radt names them with a
    zero-padded sequence, so sorting restores upload order and each is
    independently readable. Partial beats nothing.
    """
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = manifest.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise TraceExportError(
                f"radT trace schema version {version} is not supported "
                f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}). Upgrade radt."
            )
        return manifest.get("batches", []), True

    salvaged = sorted(
        p.name
        for p in directory.iterdir()
        if p.name.startswith("spans-") and p.name.endswith(".jsonl.gz")
    )
    if not salvaged:
        raise TraceExportError(
            f"No span batches in this run's {ARTIFACT_DIR}/ artifacts -- it did not "
            "use radT batch tracing."
        )
    _logger.warning(
        "radt[export]: no %s; salvaging %d batch(es) from an unfinished upload",
        MANIFEST_NAME,
        len(salvaged),
    )
    return salvaged, False


def _read_spans(directory):
    batch_names, _complete = _batch_names(directory)

    starts = {}
    ends = {}
    trace_ids = {}
    for name in batch_names:
        batch = directory / name
        if not batch.exists():
            _logger.warning("radt[export]: batch %s listed in the manifest is missing", name)
            continue
        try:
            with gzip.open(batch, "rt", encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    if record[0] == "s":
                        _, span_id, _parent, trace_id, span_name, attrs, ts = record
                        starts[span_id] = (span_name, attrs or {}, ts)
                        trace_ids[span_id] = trace_id
                    elif record[0] == "e":
                        ends[record[1]] = record[2]
        except Exception:
            # Truncated by the kill that lost the manifest, most likely.
            _logger.warning("radt[export]: batch %s is unreadable, skipping", name)

    spans = []
    for span_id, (name, attrs, start_ns) in starts.items():
        # A workload killed mid-span leaves a start with no end; a zero-length
        # slice is more honest than inventing a duration.
        end_ns = ends.get(span_id, start_ns)
        attributes = dict(attrs)
        attributes["__trace_id"] = trace_ids.get(span_id)
        spans.append(_Span(name, start_ns, end_ns, attributes))
    return spans


def _read_metrics(client, run_id):
    """Metric history per key, for Perfetto counter tracks."""
    run = client.get_run(run_id)
    series = {}
    for key in run.data.metrics:
        try:
            history = client.get_metric_history(run_id, key)
        except Exception:
            _logger.exception("radt[export]: failed to read metric history for %s", key)
            continue
        if points := [(m.timestamp, m.value) for m in history if m.timestamp is not None]:
            series[key] = sorted(points)
    return series


def build_pftrace(spans, metrics):
    """Serialise spans + metric series into a Perfetto trace."""
    try:
        from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import TrackEvent
        from perfetto.trace_builder.proto_builder import TraceProtoBuilder
    except ImportError as exc:
        raise TraceExportError(
            "Perfetto export requires the 'perfetto' package. Install it with "
            "`pip install 'radt[export]'`."
        ) from exc

    builder = TraceProtoBuilder()

    # Tracks are laid out in the order their first span starts, so the timeline
    # reads top-to-bottom in the order work actually began.
    first_start = {}
    labels = {}
    for span in spans:
        key = span.track_key
        labels.setdefault(key, span.track_label)
        if key not in first_start or span.start_ns < first_start[key]:
            first_start[key] = span.start_ns
    ordered_tracks = sorted(first_start, key=first_start.__getitem__)

    if ordered_tracks:
        packet = builder.add_packet()
        packet.track_descriptor.uuid = uuid.uuid4().int & ((1 << 63) - 1)
        packet.track_descriptor.process.pid = _PROCESS_PID
        packet.track_descriptor.process.process_name = "radT"

    track_uuids = {}
    for rank, key in enumerate(ordered_tracks):
        track_uuid = uuid.uuid4().int & ((1 << 63) - 1)
        track_uuids[key] = track_uuid
        packet = builder.add_packet()
        packet.track_descriptor.uuid = track_uuid
        packet.track_descriptor.name = labels[key]
        packet.track_descriptor.thread.pid = _PROCESS_PID
        packet.track_descriptor.thread.tid = rank
        # Without this Perfetto orders tracks by uuid, which is random here.
        packet.track_descriptor.sibling_order_rank = rank

    for span in sorted(spans, key=lambda s: s.start_ns):
        track_uuid = track_uuids[span.track_key]
        packet = builder.add_packet()
        packet.timestamp = int(span.start_ns)
        packet.trusted_packet_sequence_id = _SEQUENCE_ID
        packet.track_event.type = TrackEvent.TYPE_SLICE_BEGIN
        packet.track_event.track_uuid = track_uuid
        packet.track_event.name = span.name

        flows = [
            flow
            for flow in (
                _flow_id(span.attributes.get("in_flow_id")),
                _flow_id(span.attributes.get("out_flow_id")),
            )
            if flow
        ]
        if flows:
            packet.track_event.flow_ids.extend(flows)

        for name, value in _flatten(span.attributes).items():
            if name.startswith("__"):  # internal plumbing, not user data
                continue
            annotation = packet.track_event.debug_annotations.add()
            annotation.name = name
            if isinstance(value, bool):
                annotation.bool_value = value
            elif isinstance(value, int):
                annotation.int_value = value
            elif isinstance(value, float):
                annotation.double_value = value
            else:
                annotation.string_value = str(value)

        packet = builder.add_packet()
        packet.timestamp = int(span.end_ns)
        packet.trusted_packet_sequence_id = _SEQUENCE_ID
        packet.track_event.type = TrackEvent.TYPE_SLICE_END
        packet.track_event.track_uuid = track_uuid

    for key, points in metrics.items():
        track_uuid = uuid.uuid4().int & ((1 << 63) - 1)
        packet = builder.add_packet()
        packet.track_descriptor.uuid = track_uuid
        packet.track_descriptor.name = key
        packet.track_descriptor.counter.unit_name = "value"
        for timestamp_ms, value in points:
            packet = builder.add_packet()
            packet.timestamp = int(timestamp_ms) * 1_000_000  # mlflow logs ms
            packet.trusted_packet_sequence_id = _SEQUENCE_ID
            packet.track_event.type = TrackEvent.TYPE_COUNTER
            packet.track_event.track_uuid = track_uuid
            packet.track_event.double_counter_value = float(value)

    return builder.serialize()


def verify_trace(run_id, tracking_uri=None):
    """Check that a run's span batches uploaded completely.

    Returns a report dict, with ``ok`` False and a populated ``problems`` list
    if anything is missing or inconsistent.
    """
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    report = {"run_id": run_id, "problems": []}

    with tempfile.TemporaryDirectory() as tmp:
        try:
            local = Path(client.download_artifacts(run_id, ARTIFACT_DIR, tmp))
        except Exception as exc:
            report["problems"].append(f"no {ARTIFACT_DIR}/ artifacts: {exc}")
            report["ok"] = False
            return report

        manifest_path = local / MANIFEST_NAME
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report["schema_version"] = manifest.get("schema_version")
            report["declared_events"] = manifest.get("event_count")
            listed = manifest.get("batches", [])
            report["complete"] = True
        else:
            # Keep going: the batches are independently readable, so report how
            # much survived rather than only that the upload was cut short.
            report["problems"].append(
                f"{MANIFEST_NAME} missing -- radt writes it last, so the upload did not finish"
            )
            report["complete"] = False
            listed = sorted(
                p.name
                for p in local.iterdir()
                if p.name.startswith("spans-") and p.name.endswith(".jsonl.gz")
            )
        report["batches"] = len(listed)

        present = {p.name for p in local.iterdir()}
        if missing := [name for name in listed if name not in present]:
            report["problems"].append(f"{len(missing)} batch file(s) missing: {missing[:3]}")

        starts, ends, unreadable = {}, {}, []
        for name in listed:
            path = local / name
            if not path.exists():
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        record = json.loads(line)
                        if record[0] == "s":
                            starts[record[1]] = record
                        elif record[0] == "e":
                            ends[record[1]] = record
            except Exception as exc:
                unreadable.append(f"{name}: {exc}")

    if unreadable:
        report["problems"].append(f"unreadable batch(es): {unreadable[:3]}")

    report["spans"] = len(starts)
    report["events"] = len(starts) + len(ends)
    declared = report.get("declared_events")
    if declared is not None and declared != report["events"]:
        report["problems"].append(
            f"manifest declares {declared} events, found {report['events']}"
        )

    unclosed = set(starts) - set(ends)
    orphans = set(ends) - set(starts)
    report["unclosed_spans"] = len(unclosed)
    report["orphan_ends"] = len(orphans)
    # Expected in a salvaged trace -- the run died mid-span -- so only a problem
    # when the upload claimed to be complete.
    if unclosed and report.get("complete"):
        report["problems"].append(f"{len(unclosed)} span(s) never closed")
    if orphans:
        report["problems"].append(f"{len(orphans)} end record(s) with no matching start")

    if starts:
        begin = min(r[6] for r in starts.values())
        finish = max(ends[k][2] for k in ends) if ends else begin
        report["duration_s"] = round((finish - begin) / 1e9, 3)
        names = {}
        for record in starts.values():
            names[record[4]] = names.get(record[4], 0) + 1
        report["top_span_names"] = sorted(names.items(), key=lambda kv: -kv[1])[:5]

    report["ok"] = not report["problems"]
    return report


def export_trace(run_id, output_path=None, tracking_uri=None, include_metrics=True, upload=False):
    """Convert a run's radT batch trace into a Perfetto ``.pftrace``.

    Works against any mlflow server, including a stock one.

    Args:
        run_id: mlflow run holding the ``radt-trace/`` artifacts.
        output_path: where to write; defaults to ``<run_id>.pftrace`` in the cwd.
        tracking_uri: mlflow tracking URI; defaults to the ambient configuration.
        include_metrics: also emit the run's metrics as Perfetto counter tracks.
        upload: additionally store the result as a run artifact, which makes the
            radT mlflow UI treat the trace as already converted.

    Returns:
        ``pathlib.Path`` of the written file.
    """
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    output = Path(output_path) if output_path else Path(f"{run_id}.pftrace")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            local = client.download_artifacts(run_id, ARTIFACT_DIR, tmp)
        except Exception as exc:
            raise TraceExportError(
                f"Could not download {ARTIFACT_DIR}/ artifacts for run {run_id}: {exc}"
            ) from exc

        spans = _read_spans(Path(local))
        if not spans:
            raise TraceExportError(f"Run {run_id} has no spans to export.")

        metrics = _read_metrics(client, run_id) if include_metrics else {}
        payload = build_pftrace(spans, metrics)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    _logger.info("radt[export]: wrote %s (%d spans)", output, len(spans))

    if upload:
        # Staged under TRACE_NAME rather than uploading `output` directly: the
        # server recognises the trace by that exact name, and `output` is
        # whatever the caller asked for.
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / TRACE_NAME
            staged.write_bytes(payload)
            client.log_artifact(run_id, os.fspath(staged), artifact_path=ARTIFACT_DIR)
        _logger.info("radt[export]: uploaded to %s/%s", ARTIFACT_DIR, TRACE_NAME)

    return output
