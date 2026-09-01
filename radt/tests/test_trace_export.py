import gzip
import json

import pytest

from radt.run import trace
from radt.run.trace_export import TraceExportError, _read_spans, build_pftrace, export_trace

pytest.importorskip("perfetto", reason="perfetto is the optional 'export' extra")
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import Trace, TrackEvent


def parse(payload):
    parsed = Trace()
    parsed.ParseFromString(payload)
    return parsed


def events_of(parsed, event_type):
    return [
        p.track_event
        for p in parsed.packet
        if p.HasField("track_event") and p.track_event.type == event_type
    ]


def descriptors(parsed, field):
    return [
        p.track_descriptor
        for p in parsed.packet
        if p.HasField("track_descriptor") and p.track_descriptor.HasField(field)
    ]


def write_batch(directory, records, schema_version=trace.SCHEMA_VERSION):
    directory.mkdir(parents=True, exist_ok=True)
    with gzip.open(directory / "spans-000001.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    (directory / trace.MANIFEST_NAME).write_text(
        json.dumps({
            "schema_version": schema_version,
            "event_count": len(records),
            "batches": ["spans-000001.jsonl.gz"],
        })
    )


# --- reading --------------------------------------------------------------


def test_starts_and_ends_pair_into_spans(tmp_path):
    write_batch(tmp_path, [["s", 1, None, 1, "work", {"thread_id": 2}, 100], ["e", 1, 900]])
    spans = _read_spans(tmp_path)

    assert len(spans) == 1
    assert (spans[0].name, spans[0].start_ns, spans[0].end_ns) == ("work", 100, 900)
    assert spans[0].attributes["thread_id"] == 2


# A workload killed mid-span leaves a start with no matching end.
def test_unclosed_span_becomes_zero_length(tmp_path):
    write_batch(tmp_path, [["s", 1, None, 1, "killed", {}, 100]])
    assert _read_spans(tmp_path)[0].end_ns == 100


def test_unknown_schema_version_is_refused(tmp_path):
    write_batch(tmp_path, [["s", 1, None, 1, "a", {}, 1]], schema_version=999)
    with pytest.raises(TraceExportError, match="schema version 999 is not supported"):
        _read_spans(tmp_path)


def test_no_batches_at_all_is_reported_clearly(tmp_path):
    with pytest.raises(TraceExportError, match="did not use radT batch tracing"):
        _read_spans(tmp_path)


# radt writes the manifest last, so a kill leaves readable batches behind.
def test_batches_are_salvaged_without_a_manifest(tmp_path):
    write_batch(tmp_path, [["s", 1, None, 1, "work", {"thread_id": 0}, 100], ["e", 1, 900]])
    (tmp_path / trace.MANIFEST_NAME).unlink()

    spans = _read_spans(tmp_path)
    assert [s.name for s in spans] == ["work"]


def test_salvaged_batches_are_read_in_upload_order(tmp_path):
    """Sorting the zero-padded names restores the order radt wrote them in."""
    for seq, name in ((1, "first"), (2, "second"), (10, "tenth")):
        with gzip.open(tmp_path / f"spans-{seq:06d}.jsonl.gz", "wt") as handle:
            handle.write(json.dumps(["s", seq, None, 1, name, {}, seq]) + "\n")
            handle.write(json.dumps(["e", seq, seq + 1]) + "\n")

    spans = sorted(_read_spans(tmp_path), key=lambda s: s.start_ns)
    assert [s.name for s in spans] == ["first", "second", "tenth"]


def test_an_unreadable_batch_does_not_lose_the_others(tmp_path):
    write_batch(tmp_path, [["s", 1, None, 1, "kept", {}, 100], ["e", 1, 900]])
    (tmp_path / trace.MANIFEST_NAME).unlink()
    (tmp_path / "spans-000002.jsonl.gz").write_bytes(b"not gzip at all")

    assert [s.name for s in _read_spans(tmp_path)] == ["kept"]


def test_batch_missing_from_disk_is_skipped_not_fatal(tmp_path):
    """The manifest is written last, so a listed-but-absent batch means the
    artifact store lost it -- salvage the rest rather than failing outright."""
    write_batch(tmp_path, [["s", 1, None, 1, "kept", {}, 1], ["e", 1, 2]])
    manifest = json.loads((tmp_path / trace.MANIFEST_NAME).read_text())
    manifest["batches"].append("spans-000002.jsonl.gz")
    (tmp_path / trace.MANIFEST_NAME).write_text(json.dumps(manifest))

    assert [s.name for s in _read_spans(tmp_path)] == ["kept"]


# --- conversion -----------------------------------------------------------


def test_spans_become_slices_on_a_track_per_thread():
    from radt.run.trace_export import _Span

    parsed = parse(
        build_pftrace(
            [_Span("a", 1000, 2000, {"thread_id": 0}), _Span("b", 1500, 1800, {"thread_id": 1})],
            {},
        )
    )
    assert [d.name for d in descriptors(parsed, "thread")] == ["Thread 0", "Thread 1"]
    assert len(events_of(parsed, TrackEvent.TYPE_SLICE_BEGIN)) == 2
    assert len(events_of(parsed, TrackEvent.TYPE_SLICE_END)) == 2


def test_spans_without_thread_id_keep_their_own_track():
    from radt.run.trace_export import _Span

    parsed = parse(build_pftrace([_Span("x", 1, 2, {"__trace_id": 9})], {}))
    assert [d.name for d in descriptors(parsed, "thread")] == ["Trace 9"]


@pytest.mark.parametrize(
    ("value", "expected_field"),
    [(True, "bool_value"), (3, "int_value"), (1.5, "double_value"), ("s", "string_value")],
)
def test_attributes_become_typed_annotations(value, expected_field):
    from radt.run.trace_export import _Span

    parsed = parse(build_pftrace([_Span("x", 1, 2, {"k": value})], {}))
    begin = events_of(parsed, TrackEvent.TYPE_SLICE_BEGIN)[0]
    annotation = next(a for a in begin.debug_annotations if a.name == "k")
    assert annotation.WhichOneof("value") == expected_field


def test_internal_attributes_are_not_leaked():
    from radt.run.trace_export import _Span

    parsed = parse(build_pftrace([_Span("x", 1, 2, {"__trace_id": 1, "keep": 2})], {}))
    begin = events_of(parsed, TrackEvent.TYPE_SLICE_BEGIN)[0]
    assert {a.name for a in begin.debug_annotations} == {"keep"}


def test_metrics_become_counters_in_nanoseconds():
    from radt.run.trace_export import _Span

    parsed = parse(build_pftrace([_Span("x", 1, 2, {})], {"gpu": [(5, 1.0)]}))
    assert [d.name for d in descriptors(parsed, "counter")] == ["gpu"]
    timestamps = [
        p.timestamp
        for p in parsed.packet
        if p.HasField("track_event") and p.track_event.type == TrackEvent.TYPE_COUNTER
    ]
    assert timestamps == [5_000_000]


# --- end to end against a real (stock) mlflow -----------------------------


def record_run(trace_module, mlflow, experiment_id, spans=5):
    run = mlflow.start_run(experiment_id=experiment_id)
    trace_module.start(experiment_id=experiment_id, backend="radt")
    trace_module.set_run(run.info.run_id)
    for i in range(spans):
        with trace_module.span(f"step-{i}", {"thread_id": 0, "i": i}):
            pass
    mlflow.log_metric("gpu", 1.0, step=0)
    trace_module.shutdown()
    mlflow.end_run()
    return run.info.run_id


@pytest.mark.slow
def test_export_trace_writes_a_parsable_file(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = record_run(trace, mlflow, experiment_id)

    output = export_trace(run_id, output_path=str(tmp_path / "out.pftrace"))

    assert output.exists()
    parsed = parse(output.read_bytes())
    assert len(events_of(parsed, TrackEvent.TYPE_SLICE_BEGIN)) == 5
    assert len(events_of(parsed, TrackEvent.TYPE_COUNTER)) == 1


@pytest.mark.slow
def test_export_trace_defaults_the_filename_to_the_run_id(trace, tracking, tmp_path, monkeypatch):
    mlflow, experiment_id = tracking
    run_id = record_run(trace, mlflow, experiment_id)
    monkeypatch.chdir(tmp_path)

    assert export_trace(run_id).name == f"{run_id}.pftrace"


@pytest.mark.slow
def test_upload_stores_the_trace_under_the_name_the_server_expects(trace, tracking, tmp_path):
    """The radT mlflow UI recognises a converted trace by this exact filename,
    so uploading must not use the caller's output name."""
    from mlflow.tracking import MlflowClient

    from radt.run.trace_export import TRACE_NAME

    mlflow, experiment_id = tracking
    run_id = record_run(trace, mlflow, experiment_id)

    export_trace(run_id, output_path=str(tmp_path / "named-differently.pftrace"), upload=True)

    uploaded = {
        entry.path.split("/")[-1]
        for entry in MlflowClient().list_artifacts(run_id, trace.ARTIFACT_DIR)
    }
    assert TRACE_NAME in uploaded


@pytest.mark.slow
def test_metrics_can_be_omitted(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = record_run(trace, mlflow, experiment_id)

    output = export_trace(
        run_id, output_path=str(tmp_path / "nm.pftrace"), include_metrics=False
    )
    assert events_of(parse(output.read_bytes()), TrackEvent.TYPE_COUNTER) == []
