import types

import pytest

from conftest import read_trace_artifacts
from radt.run import mlflow_capture, trace


@pytest.fixture
def capture():
    """A clean slate around each test.

    ``import radt`` already installs the hooks, so they are removed first --
    otherwise a test cannot observe its own ``enable()``.
    """
    mlflow_capture._restore()
    mlflow_capture._capturing = None
    yield mlflow_capture
    mlflow_capture._restore()
    mlflow_capture._capturing = None


def emit_mlflow_spans(mlflow, count, nested=True):
    """Instruments the way a workload using plain mlflow tracing does."""
    for i in range(count):
        with mlflow.start_span(name=f"stage-{i}") as span:
            span.set_attribute("thread_id", i % 2)
            if nested:
                with mlflow.start_span(name="inner"):
                    pass


# --- record conversion ----------------------------------------------------


def _readable_span(name="s", span_id=7, parent_id=None, start=100, end=900, attributes=None):
    parent = types.SimpleNamespace(span_id=parent_id) if parent_id is not None else None
    return types.SimpleNamespace(
        name=name,
        context=types.SimpleNamespace(span_id=span_id, trace_id=1234),
        parent=parent,
        start_time=start,
        end_time=end,
        attributes=attributes or {},
    )


def test_a_span_becomes_a_start_and_an_end_record():
    start, end = mlflow_capture._span_records(_readable_span(), trace_id=1234, run_id="r")

    assert start[0] == "s" and start[1] == 7 and start[4] == "s" and start[6] == 100
    assert end == ["e", 7, 900]


def test_parent_is_carried_through():
    start, _ = mlflow_capture._span_records(_readable_span(parent_id=3), 1234, "r")
    assert start[2] == 3


def test_unfinished_span_gets_a_zero_length_slice():
    start, end = mlflow_capture._span_records(_readable_span(end=None), 1234, "r")
    assert end[2] == start[6]


def test_attributes_are_preserved_with_their_types():
    span = _readable_span(attributes={"thread_id": 3, "ok": True, "ratio": 0.5})
    start, _ = mlflow_capture._span_records(span, 1234, "r")

    assert start[5]["thread_id"] == 3
    assert start[5]["ok"] is True
    assert start[5]["ratio"] == 0.5


def test_run_id_is_recorded_on_the_span():
    start, _ = mlflow_capture._span_records(_readable_span(), 1234, "run-abc")
    assert start[5]["mlflow.run_id"] == "run-abc"


# --- activation -----------------------------------------------------------


def test_hooks_are_installed_once(capture, monkeypatch):
    from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter

    original = MlflowV3SpanExporter.export
    capture.enable()
    patched = MlflowV3SpanExporter.export
    capture.enable()

    assert patched is not original
    assert MlflowV3SpanExporter.export is patched  # second call changed nothing


def test_a_stock_server_is_left_alone(capture, monkeypatch):
    """Spans must reach mlflow's own exporter when there is no radT server."""
    monkeypatch.setattr(trace, "_detect_backend", lambda: "mlflow")
    capture.enable()

    seen = []
    monkeypatch.setattr(capture, "_original_export", lambda self, spans: seen.append(spans))
    from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter

    MlflowV3SpanExporter.export(object(), ["span"])
    assert seen == [["span"]]


def test_the_decision_is_made_once(capture, monkeypatch):
    """The probe costs a network round trip, so it must not repeat per span."""
    calls = []
    monkeypatch.setattr(trace, "_detect_backend", lambda: calls.append(1) or "mlflow")

    capture.enable()
    for _ in range(5):
        capture._should_capture()

    assert len(calls) == 1


# --- end to end -----------------------------------------------------------


@pytest.mark.slow
def test_mlflow_spans_land_in_artifacts(capture, tracking, tmp_path, monkeypatch):
    mlflow, experiment_id = tracking
    monkeypatch.setattr(trace, "_detect_backend", lambda: "radt")
    capture.enable(experiment_id=experiment_id)

    run = mlflow.start_run(experiment_id=experiment_id)
    emit_mlflow_spans(mlflow, 4)
    mlflow.end_run()
    capture.shutdown()  # drains mlflow's queue before closing the spool

    manifest, events, _ = read_trace_artifacts(tmp_path, run.info.run_id, trace.ARTIFACT_DIR)
    starts = [e for e in events if e[0] == "s"]
    assert len(starts) == 8  # 4 outer + 4 inner
    assert manifest["run_id"] == run.info.run_id
    assert {e[1] for e in starts} == {e[1] for e in events if e[0] == "e"}


@pytest.mark.slow
def test_captured_spans_never_reach_mlflow_tracing(capture, tracking, monkeypatch):
    """The whole point: no per-span traffic to the tracking server."""
    mlflow, experiment_id = tracking
    monkeypatch.setattr(trace, "_detect_backend", lambda: "radt")
    capture.enable(experiment_id=experiment_id)

    run = mlflow.start_run(experiment_id=experiment_id)
    emit_mlflow_spans(mlflow, 3)
    mlflow.end_run()
    capture.shutdown()

    assert mlflow.search_traces(locations=[experiment_id], return_type="list") == []


@pytest.mark.slow
def test_flush_uploads_before_an_abrupt_exit(capture, tracking, tmp_path, monkeypatch):
    """Callers run os._exit(0) straight after flushing, which skips atexit and
    background threads -- so the upload has to finish inside the flush call."""
    mlflow, experiment_id = tracking
    monkeypatch.setattr(trace, "_detect_backend", lambda: "radt")
    capture.enable(experiment_id=experiment_id)

    run = mlflow.start_run(experiment_id=experiment_id)
    emit_mlflow_spans(mlflow, 3)
    mlflow.end_run()

    # No capture.shutdown() here: the patched flush must do it.
    mlflow.flush_trace_async_logging(terminate=True)

    manifest, events, _ = read_trace_artifacts(tmp_path, run.info.run_id, trace.ARTIFACT_DIR)
    assert manifest["event_count"] == len(events) == 12
