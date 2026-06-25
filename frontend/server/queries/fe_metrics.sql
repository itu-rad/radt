SELECT metrics.run_uuid,
    metrics.key,
    metrics.value,
    metrics."timestamp",
    metrics.step
FROM metrics
WHERE run_uuid IN ($IN_RUNS$)
    AND key = $EQ_METRIC$
ORDER BY run_uuid,
    step;
