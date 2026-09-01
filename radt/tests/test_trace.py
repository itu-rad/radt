import threading

import pytest

from conftest import batch_event_counts, read_trace_artifacts


def emit_spans(trace, count, attributes=None, nested=False):
    for i in range(count):
        with trace.span(f"step-{i}", dict(attributes or {}, i=i)):
            if nested:
                with trace.span(f"inner-{i}", attributes):
                    pass


def run_workload(trace, mlflow, experiment_id, emit, backend="radt", set_run=True):
    """Starts an exporter, emits spans, and drains it. Returns the run id."""
    run = mlflow.start_run(experiment_id=experiment_id)
    trace.start(experiment_id=experiment_id, backend=backend)
    if set_run:
        trace.set_run(run.info.run_id)
    emit()
    trace.shutdown()
    mlflow.end_run()
    return run.info.run_id


# --- encoding -------------------------------------------------------------


def test_start_record_matches_manifest_layout(trace):
    event = ("s", 7, 3, 1, "name", {"a": 1}, 1234)
    assert trace._encode(event) == ["s", 7, 3, 1, "name", {"a": 1}, 1234]


def test_end_record_drops_the_unused_middle_fields(trace):
    assert trace._encode(("e", 7, None, None, None, None, 9999)) == ["e", 7, 9999]


def test_start_record_without_attributes_encodes_none(trace):
    assert trace._encode(("s", 1, None, 1, "n", None, 5))[5] is None


@pytest.mark.parametrize("value", [1, 1.5, True, "text", None])
def test_scalars_survive_encoding_untouched(trace, value):
    # Perfetto debug annotations are typed, so unlike the mlflow path these must
    # not be stringified.
    assert trace._jsonable(value) is value


def test_non_scalars_fall_back_to_string(trace):
    assert trace._jsonable([1, 2]) == "[1, 2]"


def test_attribute_keys_are_coerced_to_strings(trace):
    encoded = trace._encode(("s", 1, None, 1, "n", {2: "x"}, 5))
    assert encoded[5] == {"2": "x"}


# --- lifecycle ------------------------------------------------------------


def test_span_is_inert_before_start(trace):
    with trace.span("no-exporter") as span_id:
        assert span_id is None


def test_unknown_backend_is_rejected(trace):
    with pytest.raises(ValueError, match="backend must be one of"):
        trace.start(experiment_id="1", backend="nonsense")


@pytest.mark.slow
@pytest.mark.parametrize(
    ("env", "argument", "expected"),
    [
        ("mlflow", None, "_TraceExporter"),
        ("radt", None, "_RadtBatchExporter"),
        ("mlflow", "radt", "_RadtBatchExporter"),
        ("radt", "mlflow", "_TraceExporter"),
    ],
)
def test_explicit_backend_beats_detection(trace, tracking, monkeypatch, env, argument, expected):
    """An explicit choice must win, so detection can never override an operator."""
    _, experiment_id = tracking
    monkeypatch.setattr(trace, "_detect_backend", lambda: "radt" if env == "mlflow" else "mlflow")
    monkeypatch.setenv("RADT_TRACE_BACKEND", env)
    trace.start(experiment_id=experiment_id, backend=argument)
    assert type(trace._proc).__name__ == expected


@pytest.mark.slow
def test_detection_decides_when_nothing_is_specified(trace, tracking, monkeypatch):
    _, experiment_id = tracking
    monkeypatch.delenv("RADT_TRACE_BACKEND", raising=False)
    monkeypatch.setattr(trace, "_detect_backend", lambda: "mlflow")
    trace.start(experiment_id=experiment_id)
    assert type(trace._proc).__name__ == "_TraceExporter"


# --- server detection -----------------------------------------------------


def test_local_store_needs_no_probe(trace, tracking):
    """A sqlite/file store has no server to ask, and artifacts work regardless."""
    assert trace._detect_backend() == "radt"


def test_stock_server_is_identified_by_a_missing_endpoint(trace, monkeypatch):
    _patch_probe(trace, monkeypatch, status=404)
    assert trace._detect_backend() == "mlflow"


def test_radt_server_keeps_batch_tracing(trace, monkeypatch):
    _patch_probe(trace, monkeypatch, status=200)
    assert trace._detect_backend() == "radt"


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_ambiguous_responses_keep_the_default(trace, monkeypatch, status):
    """Only a definite 404 switches away; anything else leaves batch tracing on."""
    _patch_probe(trace, monkeypatch, status=status)
    assert trace._detect_backend() == "radt"


def test_unreachable_server_keeps_the_default(trace, monkeypatch):
    _patch_probe(trace, monkeypatch, raises=OSError("connection refused"))
    assert trace._detect_backend() == "radt"


def _patch_probe(trace, monkeypatch, status=None, raises=None):
    """Fakes an http tracking store and its probe response."""
    import types

    def fake_client():
        store = types.SimpleNamespace(get_host_creds=lambda: object())
        return types.SimpleNamespace(_tracking_client=types.SimpleNamespace(store=store))

    monkeypatch.setattr("mlflow.tracking.MlflowClient", fake_client)

    def fake_request(*args, **kwargs):
        if raises:
            raise raises
        return types.SimpleNamespace(status_code=status)

    monkeypatch.setattr("mlflow.utils.rest_utils.http_request", fake_request)


# --- radt backend ---------------------------------------------------------


@pytest.mark.slow
def test_uploads_every_event_with_a_manifest(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = run_workload(
        trace, mlflow, experiment_id, lambda: emit_spans(trace, 20, nested=True)
    )

    manifest, events, filenames = read_trace_artifacts(
        tmp_path, run_id, trace.ARTIFACT_DIR
    )
    assert manifest["schema_version"] == trace.SCHEMA_VERSION
    assert manifest["run_id"] == run_id
    assert manifest["event_count"] == len(events)
    assert all(batch in filenames for batch in manifest["batches"])

    starts = [e for e in events if e[0] == "s"]
    ends = [e for e in events if e[0] == "e"]
    assert len(starts) == len(ends) == 40
    assert {e[1] for e in starts} == {e[1] for e in ends}


@pytest.mark.slow
def test_batches_roll_on_event_count(trace, tracking, tmp_path, monkeypatch):
    mlflow, experiment_id = tracking
    monkeypatch.setenv("RADT_TRACE_BATCH_EVENTS", "10")
    run_id = run_workload(trace, mlflow, experiment_id, lambda: emit_spans(trace, 30))

    manifest, events, _ = read_trace_artifacts(tmp_path, run_id, trace.ARTIFACT_DIR)
    assert len(manifest["batches"]) > 1
    assert len(events) == 60


@pytest.mark.slow
def test_no_empty_batch_is_published(trace, tracking, tmp_path, monkeypatch):
    """The batch opened after the final roll receives nothing and must not ship.

    5 spans is exactly 10 events, so the roll lands on the last event and the
    replacement batch is still empty when shutdown closes it.
    """
    mlflow, experiment_id = tracking
    monkeypatch.setenv("RADT_TRACE_BATCH_EVENTS", "10")
    run_id = run_workload(trace, mlflow, experiment_id, lambda: emit_spans(trace, 5))

    counts = batch_event_counts(tmp_path, run_id, trace.ARTIFACT_DIR)
    assert counts and all(count > 0 for count in counts)


@pytest.mark.slow
def test_spans_emitted_before_set_run_are_still_uploaded(
    trace, tracking, tmp_path, monkeypatch
):
    """Batches that roll before the run exists must be held, not dropped.

    The small batch size is load-bearing: at the default the early spans would
    still sit in the open spool file and never reach the pending path at all.
    """
    mlflow, experiment_id = tracking
    monkeypatch.setenv("RADT_TRACE_BATCH_EVENTS", "4")
    run = mlflow.start_run(experiment_id=experiment_id)

    trace.start(experiment_id=experiment_id, backend="radt")
    emit_spans(trace, 10)
    trace.set_run(run.info.run_id)
    emit_spans(trace, 10)
    trace.shutdown()
    mlflow.end_run()

    _, events, _ = read_trace_artifacts(
        tmp_path, run.info.run_id, trace.ARTIFACT_DIR
    )
    assert len([e for e in events if e[0] == "s"]) == 20


@pytest.mark.slow
def test_attribute_types_survive_the_round_trip(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    attributes = {"thread_id": 3, "ok": True, "ratio": 0.5, "label": "x"}
    run_id = run_workload(
        trace, mlflow, experiment_id, lambda: emit_spans(trace, 5, attributes)
    )

    _, events, _ = read_trace_artifacts(tmp_path, run_id, trace.ARTIFACT_DIR)
    recorded = next(e[5] for e in events if e[0] == "s")
    assert recorded["thread_id"] == 3
    assert recorded["ok"] is True
    assert recorded["ratio"] == 0.5
    assert recorded["label"] == "x"


@pytest.mark.slow
def test_nesting_and_trace_grouping_are_recorded(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = run_workload(
        trace, mlflow, experiment_id, lambda: emit_spans(trace, 3, nested=True)
    )

    _, events, _ = read_trace_artifacts(tmp_path, run_id, trace.ARTIFACT_DIR)
    starts = [e for e in events if e[0] == "s"]
    roots = [e for e in starts if e[2] is None]
    children = [e for e in starts if e[2] is not None]
    assert len(roots) == len(children) == 3
    # A child's parent is a root, and both carry that root's trace id.
    by_id = {e[1]: e for e in starts}
    for child in children:
        assert by_id[child[2]][2] is None
        assert child[3] == by_id[child[2]][3]


@pytest.mark.slow
def test_threads_produce_independent_root_traces(trace, tracking, tmp_path):
    """Threads do not inherit contextvar state, so each starts its own trace."""
    mlflow, experiment_id = tracking

    def emit():
        threads = [
            threading.Thread(target=emit_spans, args=(trace, 5), kwargs={"attributes": {"thread_id": t}})
            for t in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    run_id = run_workload(trace, mlflow, experiment_id, emit)

    _, events, _ = read_trace_artifacts(tmp_path, run_id, trace.ARTIFACT_DIR)
    starts = [e for e in events if e[0] == "s"]
    assert len(starts) == 15
    assert all(e[2] is None for e in starts)
    assert len({e[3] for e in starts}) == 15


# --- mlflow backend -------------------------------------------------------


@pytest.mark.slow
def test_mlflow_backend_exports_traces_linked_to_the_run(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = run_workload(
        trace,
        mlflow,
        experiment_id,
        lambda: emit_spans(trace, 3, nested=True),
        backend="mlflow",
    )

    traces = mlflow.search_traces(locations=[experiment_id], return_type="list")
    assert traces
    assert all(
        t.info.request_metadata.get("mlflow.sourceRun") == run_id for t in traces
    )
    assert sum(len(t.data.spans) for t in traces) == 6


@pytest.mark.slow
def test_mlflow_backend_writes_no_trace_artifacts(trace, tracking, tmp_path):
    mlflow, experiment_id = tracking
    run_id = run_workload(
        trace, mlflow, experiment_id, lambda: emit_spans(trace, 3), backend="mlflow"
    )

    directory = tmp_path / "artifacts" / run_id / "artifacts" / trace.ARTIFACT_DIR
    assert not directory.exists()
