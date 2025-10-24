SELECT r.run_uuid,
       r.name,
       r.experiment_id,
       r.status,
       r.start_time,
       (
              COALESCE(
                     NULLIF(r.end_time, 0),
                     EXTRACT(
                            epoch
                            FROM (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'::text)
                     )::bigint * 1000
              ) - r.start_time
       )::numeric AS duration,
       (
              SELECT COALESCE(jsonb_object_agg(p.key, p.value), '{}'::jsonb)
              FROM params p
              WHERE p.run_uuid::text = r.run_uuid::text
       ) AS params
FROM runs r
WHERE r.lifecycle_stage::text = 'active'::text;
