---
title: "Getting started"
linkTitle: "Getting started"
weight: 1
---

To connect an agent or machine to a SAM mesh, `sam-node` only needs one thing:
a **Control Plane URL** (`https://...`).

## How to obtain a Control Plane URL

Pick the option that matches what you want to do right now:

| Option | Command to get your URL | Architecture & Ingress | Best for |
|---|---|---|---|
| **1. Public Testnet** | Use **`https://bananas.sam-mesh.dev`** directly | Hosted shared playground (no setup required) | Trying SAM and calling your first remote tool or model in 60 seconds ([Quick start](quickstart/)). |
| **2. Local / Workstation (`sam-one`)** | `sam-one --data-dir ~/sam-one --tunnel cloudflare --tunnel-install` | Single binary with SQLite + automatic HTTPS tunnel | Running a private mesh across workstations, VMs, and phones in seconds. Prints your HTTPS URL, join token, and QR code in the terminal ([Your own mesh](your-own-mesh/)). |
| **3. Cloud Deployment (1 Command)** | **Cloud Run**: `gcloud run deploy sam-one --image ghcr.io/google/sam-one:latest ...`<br>**SkyPilot**: `sky launch -c sam-hub deploy/skypilot/sam-one.yaml`<br>**Fly.io**: `fly launch --copy-config --config deploy/fly/fly.toml --ha=false` | Managed TLS/WSS ingress + PostgreSQL or persistent volume *(can also run on cloud free tiers for testing)* | An always-on production control plane in your own cloud account without custom Docker builds or Kubernetes ([Cloud Deployment](../guides/cloud-run/)). |

---

## Walkthroughs

1. **[Quick start](quickstart/)**: Join the public `bananas.sam-mesh.dev`
   testnet, discover remote MCP tools and inference models, and give your AI
   agent the SAM skill.
2. **[Your own mesh](your-own-mesh/)**: Start `sam-one` in one command, grab
   the Control Plane URL from the startup banner, publish a local Ollama model,
   and call it from another node.
