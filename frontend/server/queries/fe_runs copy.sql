SELECT r.run_uuid,
       r.experiment_id,
       wo.workload,
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
       da.data,
       le.letter,
       mo.model,
       replace(
              replace(pa.params::text, '"'::text, ''::text),
              ''''::text,
              ''::text
       ) AS params
FROM runs r
       LEFT JOIN (
              SELECT params.value AS data,
                     params.run_uuid
              FROM params
              WHERE params.key::text = 'data'::text
       ) da ON da.run_uuid::text = r.run_uuid::text
       LEFT JOIN (
              SELECT params.value AS letter,
                     params.run_uuid
              FROM params
              WHERE params.key::text = 'letter'::text
       ) le ON le.run_uuid::text = r.run_uuid::text
       LEFT JOIN (
              SELECT params.value AS model,
                     params.run_uuid
              FROM params
              WHERE params.key::text = 'model'::text
       ) mo ON mo.run_uuid::text = r.run_uuid::text
       LEFT JOIN (
              SELECT params.value AS params,
                     params.run_uuid
              FROM params
              WHERE params.key::text = 'params'::text
       ) pa ON pa.run_uuid::text = r.run_uuid::text
       LEFT JOIN (
              SELECT params.value AS workload,
                     params.run_uuid
              FROM params
              WHERE params.key::text = 'workload'::text
       ) wo ON wo.run_uuid::text = r.run_uuid::text
WHERE r.lifecycle_stage::text = 'active'::text;
