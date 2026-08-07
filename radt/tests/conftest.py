import gzip
import json

import pytest


@pytest.fixture
def tracking(tmp_path, monkeypatch):
    """An isolated mlflow store, returning ``(mlflow, experiment_id)``.

    The URI goes through the environment as well as ``set_tracking_uri`` because
    the exporter child builds its own ``MlflowClient`` and only sees the former.
    """
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)

    import mlflow

    mlflow.set_tracking_uri(uri)
    experiment_id = mlflow.create_experiment(
        "radt-tests", artifact_location=(tmp_path / "artifacts").as_uri()
    )
    yield mlflow, experiment_id
    while mlflow.active_run():
        mlflow.end_run()


@pytest.fixture
def trace(tmp_path):
    """The trace module with its process-global state reset around each test.

    ``start`` is idempotent on ``_proc``, so a leaked exporter from one test
    would silently disable every later one.
    """
    from radt.run import trace as trace_module

    yield trace_module

    try:
        if trace_module._proc is not None:
            trace_module.shutdown(timeout=60)
    except Exception:  # noqa: BLE001 - teardown must not mask a test failure
        pass
    if trace_module._proc is not None and trace_module._proc.is_alive():
        trace_module._proc.kill()
    trace_module._queue = None
    trace_module._proc = None
    trace_module._enabled = False
    trace_module._dropped = 0
    trace_module._experiment_id = None


def _trace_dir(tmp_path, run_id, artifact_dir):
    return tmp_path / "artifacts" / run_id / "artifacts" / artifact_dir


def read_trace_artifacts(tmp_path, run_id, artifact_dir):
    """Returns ``(manifest, events, filenames)`` for a run's uploaded batches."""
    directory = _trace_dir(tmp_path, run_id, artifact_dir)
    filenames = sorted(p.name for p in directory.iterdir())
    manifest = json.loads((directory / "manifest.json").read_text())
    events = []
    for name in manifest["batches"]:
        with gzip.open(directory / name, "rt", encoding="utf-8") as f:
            events.extend(json.loads(line) for line in f)
    return manifest, events, filenames


def batch_event_counts(tmp_path, run_id, artifact_dir):
    """Event count per uploaded batch, in manifest order."""
    directory = _trace_dir(tmp_path, run_id, artifact_dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    counts = []
    for name in manifest["batches"]:
        with gzip.open(directory / name, "rt", encoding="utf-8") as f:
            counts.append(sum(1 for _ in f))
    return counts
