RADT frontend API server

This lightweight server provides the API endpoints the React frontend expects and queries a Postgres database.

Requirements
- Node 14+ and npm
- A Postgres database reachable via the `DATABASE_URL` environment variable

Environment variables
- DATABASE_URL - Postgres connection string (e.g. postgres://user:pass@host:5432/db)
- QUERY_EXPERIMENTS - (optional) SQL to return experiments (default: SELECT experiment_id, name FROM fe_experiments LIMIT 100)
- QUERY_RUNS - (optional) SQL to return runs (default: SELECT * FROM fe_runs LIMIT 500)
- QUERY_RUNS_BY_EXPERIMENT - (optional) SQL with single $1 placeholder for experiment_id
- QUERY_METRICS_AVAILABLE - (optional) SQL to select keys by run_uuid placeholders
- QUERY_METRICS_BY_RUN_AND_KEY - (optional) SQL with run placeholders and final $N for metric
- QUERY_METRICS_BY_RUN - (optional) SQL to select metrics for runs

Run locally (PowerShell example):

```powershell
cd server
npm install
$env:DATABASE_URL = 'postgres://user:pass@localhost:5432/db'
npm run start
```

During development you can run `npm run dev` (nodemon).

Notes
- The server expects the same endpoints as the frontend: `/fe_experiments`, `/fe_runs`, `/fe_metrics_available`, `/fe_metrics`.
- The frontend uses `/api/` as the base path. To proxy requests in development, update the root `package.json` or serve the server behind a proxy in production.
