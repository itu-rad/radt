# Deploying radT on GitHub Codespaces

A free, zero-setup way to run the radT server stack (MLflow + Postgres + MinIO + nginx + frontend) without provisioning a VM. Good for:

- Trying radT before deploying anywhere permanent
- Short demos, workshops, or lectures
- Sharing a tracking server with a handful of collaborators for a week

Codespaces runs only the **server stack**. Clients that log runs (your GPU machines) still execute locally — Codespaces does not provide GPUs.

## 1. Open the repo in a Codespace

1. On [github.com/itu-rad/radt](https://github.com/itu-rad/radt), click the green **Code** button
2. **Codespaces** tab → click the **⋯** menu next to "Create codespace on master"
3. **New with options** → machine type **4-core / 16 GB** (2-core works but is tight while the frontend builds)
4. **Create codespace**

After ~90 s you land in VS Code in the browser with radT checked out.

## 2. Bring the stack up

In the Codespace terminal:

```bash
docker compose up -d
docker compose ps
```

All services should report healthy after a minute or two (the `visual` service runs `npm install` and a React build on first start — this is the slowest step).

nginx listens on port **80**. Codespaces auto-forwards it.

## 3. Make port 80 public

In VS Code's bottom panel, open the **PORTS** tab. Find port 80:

- Right-click → **Port Visibility** → **Public**
- Copy the forwarded URL: `https://<codespace-name>-80.app.github.dev`

nginx still enforces basic auth (`radt` / `radt_password` from `.htpasswd`), which keeps the public URL from being an open server.

Smoke test from your laptop:

```bash
curl -u radt:radt_password https://<your-url>/mlflow/
```

Should return MLflow HTML.

## 4. Point clients at the Codespace

On each machine that will run training (the GPU boxes):

```bash
pip install radt

export MLFLOW_TRACKING_URI=https://<your-url>/mlflow/
export MLFLOW_TRACKING_USERNAME=radt
export MLFLOW_TRACKING_PASSWORD=radt_password

radt -e demo train.py
```

Dashboards:

- MLflow UI — `https://<your-url>/mlflow/`
- radT UI — `https://<your-url>/radt/`
- MinIO console — `https://<your-url>/minio/`

## 5. Know the limits

- **Idle shutdown**: Codespaces stops after 30 min of inactivity. Raise this in [github.com/settings/codespaces](https://github.com/settings/codespaces) → **Default idle timeout** (up to 240 min).
- **Monthly free budget**: 120 core-hours/month on personal accounts. A 4-core Codespace costs 4 core-hours per wall-clock hour.
- **Ephemeral storage**: Codespaces delete themselves after 30 days idle. Export experiments you want to keep, or move the stack to a permanent host once you're past the trial phase.
- **No GPU**: Codespaces are CPU-only, so only the server stack runs here. Hardware metrics are collected on the client side.
- **Artifact uploads via MinIO**: MLflow returns `s3://` URIs to clients. If a client's `MLFLOW_S3_ENDPOINT_URL` is not reachable (default config assumes the stack is local), large-artifact uploads may fail. Scalar metrics are unaffected. For remote clients, set:

  ```bash
  export MLFLOW_S3_ENDPOINT_URL=https://<your-url>/minio/
  ```

## 6. Change the default credentials

Before sharing the public URL with anyone outside your team, replace `.htpasswd` — the committed default is well-known:

```bash
# in the Codespace
htpasswd -c .htpasswd yourusername
docker compose restart nginx
```

Then update the `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` your clients export.

## 7. Stop and resume

- **Stop**: [github.com/codespaces](https://github.com/codespaces) → your codespace → **⋯** → **Stop**. State (Postgres, MinIO volumes) is preserved.
- **Resume**: click the codespace to reopen, then `docker compose up -d` again. The public-port visibility setting is remembered.
- **Delete**: only when you no longer need the run history.
