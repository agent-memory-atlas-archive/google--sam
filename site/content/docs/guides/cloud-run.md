---
title: "Cloud Run & Free-Tier Hosting"
linkTitle: "Cloud Run & Free Tiers"
weight: 5
aliases:
  - /docs/user/cloud-run-deployment/
---

`sam-one` serves its HTTP API, the web console, and the router's WebSocket
transport on a single port (`8080`), and ships as a pre-built container image
(`ghcr.io/google/sam-one:latest`). You can deploy a private control plane to
your own cloud account in **one command**—without building Docker images,
configuring container registries, or writing Terraform.

## Pick a deployment path

| Platform | Command | Free Tier & Persistence | How you obtain the Control Plane URL |
|---|---|---|---|
| **1. SkyPilot** *(Any cloud: GCP, AWS, Azure, OCI, k8s)* | `sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run` | Uses your cloud's cheapest or **Always-Free VM** (`e2-micro`, `t4g.micro`) + persistent disk + free Cloudflare HTTPS tunnel | Run `sky logs sam-hub 1 --no-follow` and copy `API URL: https://...trycloudflare.com` |
| **2. Fly.io** | `fly launch --copy-config --config deploy/fly/fly.toml --ha=false` | 1 GB persistent `/data` volume (SQLite + keys survive restarts) + automatic TLS/WSS | `https://<your-app>.fly.dev` (also printed in `fly logs`) |
| **3. Google Cloud Run** | `gcloud run deploy sam-one --image ghcr.io/google/sam-one:latest ...` *(below)* | Serverless container; pair with a free Postgres (Neon / Supabase) for durable state across restarts | Printed at the end of `gcloud run deploy` (`Service URL: https://...a.run.app`) |

---

## Option 1: Google Cloud Run (1 Command)

Because `sam-one` automatically detects its public `wss://` address from Cloud
Run's `Host` / `X-Forwarded-Proto` headers on `/info` and `/enroll` and derives
a deterministic router `PeerID` from `SAM_ADMIN_TOKEN`, deploying to Cloud Run
takes a single command using the pre-built image:

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

### Obtain your URL and join a node

`gcloud run deploy` outputs your `Service URL` directly:

```text
Service [sam-one] revision [sam-one-00001-xxx] has been deployed and is serving 100 percent of traffic.
Service URL: https://sam-one-628944397724.us-central1.run.app
```

Verify readiness and enroll a node from anywhere:

```bash
URL=$(gcloud run services describe sam-one --project "$PROJECT" --region "$REGION" \
  --format='value(status.url)')

curl -s "$URL/readyz"              # {"status":"ready"}

echo -n "$JOIN_TOKEN" > join-token
sam-node join "$URL" --bootstrap-token-path join-token
sam-node run --daemonize
```

> [!TIP]
> **Free-tier persistent database for Cloud Run:** By default, Cloud Run's
> filesystem is in-memory, so when an instance restarts it keeps its router
> `PeerID` and pinned tokens (`SAM_TOKEN`, `SAM_ADMIN_TOKEN`), while enrolled
> nodes automatically re-enroll. To persist enrolled nodes, minted tokens, and
> policies across restarts for **$0/mo**, create a free serverless Postgres
> database (for example on [Neon](https://neon.tech) or
> [Supabase](https://supabase.com)) and add
> `--args="--data-dir=/data,--port=8080,--db-driver=postgres,--db-dsn=<YOUR_POSTGRES_URI>"`
> to `gcloud run deploy`.

Why each Cloud Run flag matters:

- `--min-instances 1 --max-instances 1`: the router's DHT and relay state
  live in the single process. Two instances would be two separate meshes behind
  one URL.
- `--no-cpu-throttling`: the router runs background loops (lease renewal,
  key sync, DHT maintenance) between HTTP requests.
- `--timeout 3600`: Cloud Run limits the lifetime of a streaming request,
  and each node's WebSocket connection is one. Nodes reconnect seamlessly when
  the limit closes a connection.
- `--allow-unauthenticated`: the mesh authenticates its own callers with
  Biscuit credentials and tokens.

---

## Option 2: SkyPilot (Multi-Cloud & Always-Free VMs)

If you use [SkyPilot](https://docs.skypilot.co/) (`sky check`) with your GCP,
AWS, Azure, OCI, or Kubernetes account, launch the included recipe in the
background:

```bash
sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run
```

SkyPilot provisions the cheapest available VM (including `e2-micro` / `t4g.micro`
Always-Free instances), installs `sam-one`, and opens an outbound Cloudflare
HTTPS tunnel (requiring zero inbound firewall ports or DNS setup).

### Obtain your URL and join token from SkyPilot logs

Read the startup banner from job `1`:

```bash
sky logs sam-hub 1 --no-follow
```

```text
══════════════════════════════════════════════════════════════════
SAM standalone mesh is ready!

API URL:      https://distinct-kent-bradford-elderly.trycloudflare.com
Tunnel:       https://distinct-kent-bradford-elderly.trycloudflare.com -> http://0.0.0.0:8080
Web Console:  https://distinct-kent-bradford-elderly.trycloudflare.com/console
Router Peer:  12D3KooWScRWXVx2zSaPWC7NectYnuyhaGxpLYkNgCkp2g2tkfkY
Admin Token:  sam_adm_e4075529d72bb22d78446799f683dd89
Join Token:   sam_tok_05a5d01cf091187c980e68e1bd7d7d7a

To enroll a node:
  sam-node join https://distinct-kent-bradford-elderly.trycloudflare.com --bootstrap-token-path /home/gcpuser/sam-one/join-token
══════════════════════════════════════════════════════════════════
```

Save the `Join Token` on your local machine and join:

```bash
echo -n "sam_tok_..." > join-token
sam-node join https://distinct-kent-bradford-elderly.trycloudflare.com --bootstrap-token-path join-token
sam-node run --daemonize
```

To use a **permanent custom domain** for **$0/mo** via a Cloudflare Named
Tunnel instead of a random `trycloudflare.com` hostname:

```bash
sky launch -y -c sam-hub deploy/skypilot/sam-one.yaml --detach-run \
  --env SAM_EXTERNAL_URL=https://mesh.example.com \
  --secret CLOUDFLARE_TUNNEL_TOKEN
```

To tear down the SkyPilot hub when done:

```bash
sky down -y sam-hub
```

---

## Option 3: Fly.io (1 Command + Persistent Volume)

The repository includes [`deploy/fly/fly.toml`](https://github.com/google/sam/blob/main/deploy/fly/fly.toml),
which deploys `ghcr.io/google/sam-one:latest` with a 1 GB persistent volume
mounted at `/data` (so SQLite, `router.key`, `join-token`, and `admin-token`
persist across restarts):

```bash
fly launch --copy-config --config deploy/fly/fly.toml --ha=false
```

Once deployed, run `fly logs` to read your `Admin Token`, `Join Token`, and
`sam-node join https://<your-app>.fly.dev` command from the startup banner.

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
