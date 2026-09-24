---
title: "Getting started"
linkTitle: "Getting started"
weight: 1
---

To connect an agent or machine to a SAM mesh, `sam-node` only needs one thing:
a **Control Plane URL** (`https://...`).

## How to obtain a Control Plane URL

Pick the option that matches what you want to do right now:

| Option | Command to get your URL | Cost & Setup | Best for |
|---|---|---|---|
| **1. Public Testnet** | Use **`https://bananas.sam-mesh.dev`** directly | **$0** — zero setup, already running | Trying SAM and calling your first remote tool or model in 60 seconds ([Quick start](quickstart/)). |
| **2. Your Laptop (Instant Public HTTPS)** | `sam-one --data-dir ~/sam-one --tunnel cloudflare --tunnel-install` | **$0** — no cloud account needed | Running your own private mesh across laptops, VMs, and phones in 10 seconds. Prints your `https://*.trycloudflare.com` URL, join token, and QR code in the terminal ([Your own mesh](your-own-mesh/)). |
| **3. Your Own Cloud (1 Command)** | **SkyPilot**: `sky launch -c sam-hub deploy/skypilot/sam-one.yaml`<br>**Fly.io**: `fly launch --copy-config --config deploy/fly/fly.toml --ha=false`<br>**Cloud Run**: `gcloud run deploy sam-one --image ghcr.io/google/sam-one:latest ...` | **Free tiers** (GCP `e2-micro`, OCI, Cloudflare Named Tunnel, or serverless credits) | An always-on sovereign mesh in your own cloud account—without writing Terraform, building Docker images, or managing Kubernetes ([Cloud & Free-Tier Hosting](../guides/cloud-run/)). |

---

## Walkthroughs

1. **[Quick start](quickstart/)**: Join the public `bananas.sam-mesh.dev`
   testnet, discover remote MCP tools and inference models, and give your AI
   agent the SAM skill.
2. **[Your own mesh](your-own-mesh/)**: Start `sam-one` in one command, grab
   the Control Plane URL from the startup banner, publish a local Ollama model,
   and call it from another node.
