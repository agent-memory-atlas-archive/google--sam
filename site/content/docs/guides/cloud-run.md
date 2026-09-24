---
title: "Cloud Deployment"
linkTitle: "Cloud Deployment"
weight: 5
aliases:
  - /docs/user/cloud-run-deployment/
---

`sam-one` serves its HTTP API, the web console, and the router's WebSocket
transport on a single port (`8080`), and ships as a pre-built container image
(`ghcr.io/google/sam-one:latest`). You can deploy a production-ready standalone
control plane and router to your cloud account in a single command—without
building custom Docker images, configuring container registries, or maintaining
Kubernetes manifests.

## Pick a deployment path

| Platform | Command | State & Persistence | How you obtain the Control Plane URL |
|---|---|---|---|
| **1. Google Cloud Run** | `gcloud run deploy sam-one --image ghcr.io/google/sam-one:latest ...` *(below)* | Pair with PostgreSQL (`--db-driver=postgres`) for durable state across container revisions | Printed at the end of `gcloud run deploy` (`Service URL: https://...a.run.app`) |
| **2. SkyPilot** *(GCP, AWS, Azure, OCI, k8s)* | `sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run` | Dedicated VM disk (`~/sam-one`) + Cloudflare Named Tunnel (or custom domain) | Run `sky logs sam-hub 1 --no-follow` and read `API URL:` from the banner |
| **3. Fly.io** | `fly launch --copy-config --config deploy/fly/fly.toml --ha=false` | Persistent `/data` volume (SQLite, keys, and tokens survive restarts) + managed TLS/WSS | `https://<your-app>.fly.dev` (also printed in `fly logs`) |

*(Note: For evaluation or testing, each of these options can also run within cloud provider free tiers—such as Cloud Run with a free serverless Postgres instance, or SkyPilot with `cpus: 0.25+` on an `e2-micro` / `t4g.micro` instance.)*

---

## Option 1: Google Cloud Run

Because `sam-one` automatically infers its public `wss://` router address from
Cloud Run's `Host` and `X-Forwarded-Proto` headers on `/info` and `/enroll` and
derives a deterministic router `PeerID` from `SAM_ADMIN_TOKEN`, deploying to
Cloud Run takes a single command using the official image:

```bash
PROJECT=my-gcp-project
REGION=us-central1
JOIN_TOKEN="sam_tok_$(openssl rand -hex 16)"
ADMIN_TOKEN="sam_adm_$(openssl rand -hex 16)"

gcloud run deploy sam-one \
  --project "$PROJECT" --region "$REGION" \
  --image ghcr.io/google/sam-one:latest \
  --allow-unauthenticated \
  --min-instances 1 --max-instances 1 \
  --port 8080 \
  --no-cpu-throttling \
  --timeout 3600 \
  --set-env-vars "SAM_TOKEN=${JOIN_TOKEN},SAM_ADMIN_TOKEN=${ADMIN_TOKEN}"
```

For production durability across container restarts and new revisions, attach a
PostgreSQL database (such as Cloud SQL, AlloyDB, or Neon) by adding:

```bash
  --args="--data-dir=/data,--port=8080,--db-driver=postgres,--db-dsn=${POSTGRES_DSN}"
```

### Obtain your URL

`gcloud run deploy` prints your `Service URL` when deployment completes:

```text
Service [sam-one] revision [sam-one-00001-xxx] has been deployed and is serving 100 percent of traffic.
Service URL: https://sam-one-628944397724.us-central1.run.app
```

You can also query it at any time:

```bash
URL=$(gcloud run services describe sam-one --project "$PROJECT" --region "$REGION" \
  --format='value(status.url)')
curl -s "$URL/readyz"   # {"status":"ready"}
```

Why each Cloud Run flag matters:

- `--min-instances 1 --max-instances 1`: the router's DHT and relay state
  live in the single process. Two instances would form two separate meshes
  behind one URL.
- `--no-cpu-throttling`: the router runs background loops (lease renewal,
  key sync, DHT maintenance) between HTTP requests.
- `--timeout 3600`: Cloud Run bounds the lifetime of a streaming request,
  and each node's WebSocket connection is one. Nodes reconnect automatically
  when the limit closes a connection.
- `--allow-unauthenticated`: the mesh authenticates its own callers using
  Biscuit credentials and tokens.

---

## Option 2: SkyPilot (Multi-Cloud VMs)

If you use [SkyPilot](https://docs.skypilot.co/) (`sky check`) with your GCP,
AWS, Azure, OCI, or Kubernetes credentials, launch [`deploy/skypilot/sam-one.yaml`](https://github.com/google/sam/blob/main/deploy/skypilot/sam-one.yaml)
with a permanent custom domain backed by a Cloudflare Named Tunnel:

```bash
sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run \
  --env SAM_EXTERNAL_URL=https://mesh.example.com \
  --secret CLOUDFLARE_TUNNEL_TOKEN
```

For quick testing without a custom domain, omit `--env` and `--secret` to open
an automatic `trycloudflare.com` HTTPS tunnel:

```bash
sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run
sky logs sam-hub 1 --no-follow
```

Read the `API URL`, `Admin Token`, and `Join Token` printed in the banner from
`sky logs sam-hub 1 --no-follow`.

---

## Option 3: Fly.io (Persistent Volume)

The repository includes [`deploy/fly/fly.toml`](https://github.com/google/sam/blob/main/deploy/fly/fly.toml),
which deploys `ghcr.io/google/sam-one:latest` with a persistent volume mounted
at `/data` so SQLite, `router.key`, `join-token`, and `admin-token` survive
restarts:

```bash
fly launch --copy-config --config deploy/fly/fly.toml --ha=false
```

Run `fly logs` to read your `Admin Token`, `Join Token`, and
`sam-node join https://<your-app>.fly.dev` command from the startup banner.

---

## Verify the dataplane with two nodes

Once your control plane URL (`$URL`) is ready, verify end-to-end service
discovery, Biscuit authorization, and WebSocket relay routing across two nodes:

1. **Mint a bootstrap token** (or use `$JOIN_TOKEN`):
   ```bash
   export SAM_ADMIN_TOKEN="$ADMIN_TOKEN"
   sam-one token create --server "$URL" --description "smoke-test" --max-usages 2
   ```
2. **Start Node A (exposing a service)**:
   ```bash
   cat > node-a.yaml <<'EOF'
   version: "v1alpha1"
   services:
     - type: inference
       name: prod-llm
       target_url: "http://127.0.0.1:11434"
   EOF

   sam-node run --control-plane "$URL" \
     --bootstrap-token-path join-token \
     --config node-a.yaml --data-dir ~/node-a --bind-addr=
   ```
3. **Start Node B and call Node A's service through the mesh**:
   ```bash
   sam-node run --control-plane "$URL" \
     --bootstrap-token-path join-token \
     --data-dir ~/node-b --bind-addr=

   curl -s --unix-socket ~/node-b/sam.sock http://localhost/v1/models
   curl -s --unix-socket ~/node-b/sam.sock http://localhost/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"gemma3:1b","messages":[{"role":"user","content":"Ping across the mesh"}]}'
   ```

---

## Administer from your workstation

`sam-one` is also the admin client. With `SAM_ADMIN_TOKEN` exported:

```bash
sam-one token create --server "$URL" --role sam:role:node --max-usages 1
sam-one token qr     --server "$URL"
sam-one token list   --server "$URL"
sam-one token revoke <token-id> --server "$URL"
sam-one admin ban <peer-id>     --server "$URL"
```
