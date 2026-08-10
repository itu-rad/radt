"""Resource-Aware Data systems Tracker (radT) for automatically tracking and training machine learning software."""

__version__ = "0.2.29"

from .radt import cli, schedule_external
from .run import (
    log_metric,
    log_metrics,
    listeners,
    mlflow_capture,
    shutdown,
    trace,
    trace_export,
    _patch_mlflow_async_export_queue,
)
from .run.trace_export import export_trace

_patch_mlflow_async_export_queue()

# Installs hooks only; whether mlflow spans are diverted into radT artifact
# batches is decided on the first exported span, once the tracking server can be
# probed. Against a stock server the async-upload patch above stays in charge.
mlflow_capture.enable()
