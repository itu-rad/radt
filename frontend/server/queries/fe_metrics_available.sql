SELECT DISTINCT latest_metrics.key,
  latest_metrics.run_uuid
FROM latest_metrics
WHERE run_uuid in ($IN_RUNS$);
