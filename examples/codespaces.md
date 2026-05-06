# Deploying radT on GitHub Codespaces

GitHub Codespaces provides a zero-provisioning way to run the radT server stack (MLflow, PostgreSQL, MinIO, nginx, and the radT frontend). Codespaces does not expose GPUs, so only the **server stack** runs here — training clients, which perform hardware-metric collection, run wherever the GPU hardware is available and connect to the Codespace over HTTPS.

## 1. Open the repository in a Codespace

The **2-core / 8 GB** machine type is sufficient. Expect the initial React build of the `visual` service to take several minutes on first start; subsequent starts reuse the built artifacts.

The browser-based VS Code workspace opens with the repository checked out.

## 2. Start the stack

From the Codespace terminal:

```bash
docker compose up -d
docker compose ps
```

All services should eventually report a healthy state. The `visual` service is the slowest to come up on first start because of the React build noted above.

nginx listens on port **80**, which Codespaces forwards automatically.

## 3. Expose port 80

In the VS Code **PORTS** panel, set port **80** to **Public**. The forwarded URL has the form `https://<codespace-name>-80.app.github.dev`.

nginx enforces basic authentication (`radt` / `radt_password` from `.htpasswd`), so the public URL is not an open endpoint. The default credentials should be replaced before sharing the URL beyond a trusted group; see [Section 5](#5-replace-the-default-credentials).

A smoke test from any HTTP client confirms the deployment:

```bash
curl -u radt:radt_password https://<your-url>/mlflow/
```

The response should be the MLflow web application.

## 4. Configure clients

On each machine that will submit runs:

```bash
pip install radt

export MLFLOW_TRACKING_URI=https://<your-url>/mlflow/
export MLFLOW_TRACKING_USERNAME=radt
export MLFLOW_TRACKING_PASSWORD=radt_password

radt -e demo train.py
```

The following endpoints are served behind the same URL:

| Path | Service |
|---|---|
| `/mlflow/` | MLflow UI |
| `/radt/` | radT frontend |
| `/minio/` | MinIO console |

For artifact uploads (log files, Nsight traces), clients must also point their S3 client at the public MinIO endpoint, because MLflow returns `s3://` URIs that clients resolve directly:

```bash
export MLFLOW_S3_ENDPOINT_URL=https://<your-url>/minio/
```

Scalar metric logging works without this setting.

## 5. Replace the default credentials

The committed `.htpasswd` is public and should not be relied on for access control. To replace it:

```bash
htpasswd -c .htpasswd <username>
docker compose restart nginx
```

Clients must then be updated to use the new credentials.
