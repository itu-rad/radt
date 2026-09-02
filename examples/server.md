# Deploying radT on a server

For a radT stack that other people reach over the network: TLS, credentials of
your own, and only one port open. For a throwaway instance on a laptop or in a
Codespace, [codespaces.md](codespaces.md) is simpler and skips all of this.

The stack is one compose file plus two overrides, each doing one thing:

| file | what it adds |
|---|---|
| `docker-compose.yml` | the services |
| `docker-compose.server.yml` | binds every port except nginx's to the loopback |
| `docker-compose.tls.yml` | serves 443, redirects 80, reads certbot's certificates |

## Before you start

- A DNS name pointing at the host, reachable from the internet on ports 80 and
  443. Let's Encrypt validates over both.
- Docker with the compose plugin: `curl -fsSL https://get.docker.com | sh`, then
  `sudo usermod -aG docker $USER` and log in again.
- 2 vCPU / 4 GB is enough. The dashboard's React build is the memory spike.

## 1. Certificate

certbot issues and renews it; the stack only reads the result.

```bash
sudo apt install -y certbot apache2-utils
sudo certbot certonly --standalone -d radt.example.itu.dk
sudo mkdir -p /var/lib/radt/certbot
```

`--standalone` binds port 80 itself, so do this before starting the stack (or
`docker compose stop nginx` first).

## 2. Credentials

Three of them, read at different moments, which is why they are all set before
the first start:

| | read | changing it later |
|---|---|---|
| `POSTGRES_PASSWORD` | once, when the database is initialised | ignored until the volume is recreated |
| `MINIO_ROOT_PASSWORD` | every start | takes effect on restart, data intact |
| `.htpasswd` | every request | takes effect immediately |

```bash
git clone https://github.com/itu-rad/radt.git && cd radt

htpasswd -Bc .htpasswd <your-user>          # nginx basic auth, prompts twice

cat > .env <<ENV
RADT_DOMAIN=radt.example.itu.dk
MLFLOW_IMAGE=ghcr.io/itu-rad/mlflow:latest
POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
MINIO_ROOT_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
ENV
chmod 600 .env
```

`RADT_DOMAIN` must match the name on the certificate: it is substituted into the
certificate paths nginx opens at startup. Avoid `$` in the passwords, which
compose would interpret.

The defaults these replace are committed in this repository and therefore
public. `.env` itself is plaintext, and anyone who can run `docker` on the host
can read the same values from `docker inspect` regardless, so treat membership
of the `docker` group as equivalent to database access.

## 3. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml \
               -f docker-compose.tls.yml up -d
```

First start takes several minutes: the `visual` service builds the dashboard
from source every time it starts, and `/radt/` returns 502 until it finishes.
`docker compose logs -f visual` ends with `Accepting connections` when it is
ready.

## 4. Check it

```bash
curl -u <user>:<pass> https://radt.example.itu.dk/mlflow/health     # OK
curl -sI http://radt.example.itu.dk/mlflow/health | head -1         # 301
curl -so /dev/null -w '%{http_code}\n' \
     https://radt.example.itu.dk/radt/api/fe_experiments            # 401
```

From off-campus if you can, which also exercises the certificate chain.

To confirm the database really took the new password, connect from another
container. Do not test this from inside the postgres container: its
`pg_hba.conf` trusts the local socket and loopback, so the old password appears
to work there whatever it is set to.

```bash
docker exec -e PW=mlflow_password mlflow_server python -c "
import os, psycopg2
psycopg2.connect(host='postgres', dbname='mlflow_db', user='mlflow_user',
                 password=os.environ['PW'], connect_timeout=5)"
# expect: FATAL: password authentication failed
```

## What is exposed

Only nginx's ports 80 and 443. Postgres, MinIO and the dashboard's API listen on
the loopback, reachable through nginx or not at all.

A host firewall does not substitute for that: Docker inserts its own iptables
rules ahead of ufw's, so a published port stays reachable from the internet
whatever ufw reports.

| path | auth |
|---|---|
| `/mlflow/` | basic auth |
| `/radt/` | basic auth |
| `/radt/api/` | basic auth |
| `/minio/` | MinIO console's own login |
| `/.well-known/acme-challenge/` | open, for renewal |

## Clients

```bash
export MLFLOW_TRACKING_URI=https://radt.example.itu.dk/mlflow/
export MLFLOW_TRACKING_USERNAME=<your-user>
export MLFLOW_TRACKING_PASSWORD=<pass>
radt -e demo train.py
```

Artifacts need nothing further: the server proxies them, so clients never
address the object store directly.

## Running it

**Renewing the certificate** needs no downtime, because port 80 keeps serving
the challenge path unauthenticated:

```bash
sudo certbot renew --webroot -w /var/lib/radt/certbot
docker compose -f docker-compose.yml -f docker-compose.server.yml \
               -f docker-compose.tls.yml restart nginx
```

nginx reads the certificate at startup, so the restart is what picks up a
renewed one. Worth a monthly cron.

**Updating the MLflow server:** `docker compose ... pull mlflow` then
`up -d mlflow`. The `latest` tag moves whenever the fork's master does.

**Data** lives in the `radt_pgdata` and `radt_minio_data` volumes. `down` is
safe; `down -v` destroys them.

## When something is wrong

- **`permission denied ... /var/run/docker.sock`** -- not in the `docker` group,
  or the shell predates being added. `newgrp docker`, or log in again. Detached
  `screen` sessions keep the old group list and need restarting.
- **`sudo docker compose` ignores your settings** -- sudo strips the
  environment, so `RADT_DOMAIN=... sudo docker compose` passes nothing. Keeping
  `RADT_DOMAIN` in `.env` avoids the problem entirely.
- **`/radt/` returns 502** -- the dashboard is still building. See step 3.
- **`set RADT_DOMAIN to the name on the certificate`** -- compose could not read
  `.env`; you are probably not in the directory holding it.

## Migrating an older deployment into this one

[radt_move](https://github.com/itu-rad/radt_move) copies experiments, runs,
metrics and artifacts between deployments over the REST API, resumably. Point it
at the `https://` address of each: both ends redirect plain HTTP, and a redirect
turns the API's POST requests into GETs.
