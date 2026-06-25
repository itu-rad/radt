SELECT experiments.experiment_id,
    experiments.name
FROM experiments
WHERE experiments.lifecycle_stage::text = 'active'::text;
