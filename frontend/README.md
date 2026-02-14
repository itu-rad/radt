# Data Management and Visualization Frontend
This frontend is a React app served behind nginx and paired with a small Node API.
The recommended way to deploy is via the repository-level Docker Compose stack.

## Quick start (Docker Compose)

From the repository root:

```bash
docker compose up -d
```

The stack exposes nginx on port 80. Endpoints:

- Frontend UI: http://<host>/radt/
- API: http://<host>/api/
- MLflow: http://<host>/mlflow/
- MinIO console: http://<host>/minio/

The nginx service uses basic auth. The credentials live in `.htpasswd` at the repo root.

## How the frontend is built

The Compose service named `visual` uses `node:25-alpine` and builds the app at container start:

```bash
npm install && REACT_APP_API_URL=/api PUBLIC_URL=/radt npm run build && npx serve -s build -l 80
```

Notes:

- `REACT_APP_API_URL=/api` location for the API that is used by the UI
- `PUBLIC_URL=/radt` ensures correct asset paths when served under `/radt/`.

If you change these values, rebuild the `visual` service:

```bash
docker compose up -d --force-recreate visual
```

## Development (optional)

For local development without Docker:

```bash
npm install
REACT_APP_API_URL=http://localhost:3000 npm start
```

This runs the React dev server on port 3000 and expects the API to be available at the same host.
