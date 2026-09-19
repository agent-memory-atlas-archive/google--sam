---
title: "Your own mesh"
linkTitle: "Your own mesh"
weight: 2
aliases:
  - /docs/user/device-enrollment/
---

The quick start joined a mesh that someone else runs. This page runs one for
you: a control plane, a router and a web console on your laptop, in one
process, with two nodes talking through it. One node publishes a model that
runs on your machine, the other calls it. `sam-one` runs the same code as a
Kubernetes deployment, so what you learn here applies there too.

You need the `sam-one` and `sam-node` binaries. The
[install script](../quickstart/#1-install) provides both. The model is served
by [Ollama](https://ollama.com); any OpenAI-compatible server works in its
place.

## 1. Start the control plane

```bash
sam-one --data-dir ~/sam-one
```

`--data-dir` holds the database, the router's key and the generated tokens.
If you delete it, you get a new mesh. After a moment `sam-one` prints a
banner:

```text
══════════════════════════════════════════════════════════════════
SAM standalone mesh is ready!

API URL:      http://0.0.0.0:33775
Web Console:  http://0.0.0.0:33775/console
Router Peer:  12D3KooWBzUDQCkZhz2rWrYBhpjcCH8VnrRNcwCW6DoF36iADYrY
Admin Token:  sam_adm_…
Join Token:   sam_tok_…

To enroll a node:
  sam-node join http://0.0.0.0:33775 --bootstrap-token-path /home/you/sam-one/join-token
══════════════════════════════════════════════════════════════════
```

`sam-one` picked a free port. Pass `--port 8080` for a fixed one. The **join
token** is a standing bootstrap token that lets nodes enroll without an
identity provider. The **admin token** authenticates the console and the
admin API. Both are also written to files in the data directory, and the rest
of this page reads them from there.

On first boot, `sam-one` seeds an open development policy and logs a warning
about it: any enrolled node may register any service and call any service.
This is a reasonable default for a laptop and a bad one for anything shared.
The last section explains how to replace it.

## 2. Publish a model from one node

A node publishes backends that speak one of three protocols, and the
service `type` says which: `inference` for an OpenAI-compatible API, `mcp`
for an MCP server, `a2a` for an A2A agent. The type is a contract, not a
label. The node speaks that protocol to the backend and to nobody else: an
`inference` backend is offered for the models it lists on `/v1/models`, an
`mcp` server is advertised once the node has completed an MCP session with
it, an `a2a` agent once it has served its agent card. A plain web server
declared under any of these types is never advertised. The mesh is not a
generic HTTP tunnel.

Pull a small model and declare Ollama as an `inference` service. The
`target_url` is the backend's root, without `/v1`; the node adds the prefix:

```bash
ollama pull gemma3:1b

cat > ~/node-a.yaml <<'EOF'
version: "v1alpha1"
services:
  - type: inference
    name: laptop-llm
    description: "Ollama on my laptop"
    target_url: "http://127.0.0.1:11434"
EOF
```

Then run a node with it. Substitute the port from your banner:

```bash
URL=http://127.0.0.1:33775

sam-node run --control-plane $URL \
  --bootstrap-token-path ~/sam-one/join-token \
  --config ~/node-a.yaml \
  --data-dir ~/node-a --bind-addr= --allow-loopback --listen /ip4/127.0.0.1/tcp/0
```

Three of these flags are only needed because both nodes run on one machine.
`--bind-addr=` (an empty value) keeps the node's local API on its Unix
socket, so the two nodes do not compete for port 8080. `--allow-loopback`
lets the nodes advertise and dial `127.0.0.1`. `--listen .../tcp/0` picks a
free peer-to-peer port. On separate machines you would not pass any of them.

The node enrolls with the join token, connects to the router and prints its
peer ID:

```text
SAM Node Online.
PeerID: 12D3KooWSCnbUoZ8Jv3EKGv17LqEWtnTMfZ3XYJUg2WTm5Gz2hUK
```

Ollama is never exposed on the network. It listens on loopback, and the only
way to it from another machine is through this node, which checks the
caller's credential and the mesh policy on every request.

## 3. Call it from another node

In a second terminal, start a node with no services:

```bash
URL=http://127.0.0.1:33775

sam-node run --control-plane $URL \
  --bootstrap-token-path ~/sam-one/join-token \
  --data-dir ~/node-b --bind-addr= --allow-loopback --listen /ip4/127.0.0.1/tcp/0
```

Every node's local API includes an OpenAI-compatible endpoint. `/v1/models`
lists the models that every reachable `inference` provider serves, and a
completion request is routed to a provider of the model it names. Ask node
B:

```bash
SOCK=~/node-b/sam.sock

curl -s --unix-socket $SOCK http://localhost/v1/models

curl -s --unix-socket $SOCK http://localhost/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma3:1b","messages":[{"role":"user","content":"Say hello in five words."}]}'
```

The model list shows `gemma3:1b` with node A's peer ID as `owned_by`. Allow
a few seconds after A starts for B to learn about it. Nodes announce their
services in a discovery table that the router hosts, and the announcement
takes a moment to arrive.

The request went from B's socket to B, then over an authenticated connection
to A, through A's policy check, to Ollama, and back. Both nodes verified the
other's credential before any data moved. If A and B cannot reach each other
directly, the connection is relayed through the router inside `sam-one`,
which carries ciphertext and learns only that the two are talking.

The socket needs no token: only your user can open it. To point an OpenAI
SDK at the mesh instead, give node B a TCP port with
`--bind-addr 127.0.0.1:8081` and a token as in the
[quick start](../quickstart/#3-run-the-node), then use
`http://127.0.0.1:8081/v1` as `base_url` and the token as `api_key`.

## 4. Look at it in the console

Open `http://127.0.0.1:33775/console` and paste the admin token. The console
shows the enrolled nodes, the router, the services each node reports, the
bootstrap tokens with their remaining uses, and the mesh policy. You can edit
the policy in place.

The same operations are available from the command line. `sam-one` is also
an admin client for a running server. It reads the admin token from the data
directory, from `SAM_ADMIN_TOKEN`, or from `--admin-token-path`, never from a
flag value:

```bash
sam-one token list   --server $URL --data-dir ~/sam-one
sam-one token create --server $URL --data-dir ~/sam-one --description "node c" --max-usages 1
sam-one token revoke <token-id> --server $URL --data-dir ~/sam-one
sam-one admin ban <peer-id>      --server $URL --data-dir ~/sam-one
```

## MCP servers and A2A agents

The other two service types are declared the same way and reached through
the same node. An MCP server gives agents tools; the quick start
[calls one](../quickstart/#4-call-a-tool-on-the-mesh) on the testnet through
the node's MCP endpoint. A stdio server is declared with `command` and the
node runs it; a Streamable HTTP server with `target_url`:

```yaml
  - type: mcp
    name: filesystem
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/srv/docs"]
```

An A2A agent is another agent, spoken to with the A2A protocol. It is
reached at `/sam/<peer-id>/a2a/<name>/` on the caller's node, which
regenerates the agent card so that a stock A2A client works unchanged:

```yaml
  - type: a2a
    name: triage
    target_url: "http://127.0.0.1:9999"
```

[Exposing services](../../guides/exposing-services/) covers all three types
and what the policy must grant. The [A2A chat](../../use-cases/chat-a2a/)
and [Gemini Buddy](../../use-cases/gemini-buddy/) use cases run a complete
agent behind each of the other two types.

## Reaching it from other machines

Everything above used `http://127.0.0.1`. `sam-node` accepts a plain `http`
URL only when the control plane is on the same host. A node on another
machine needs an `https` URL, because the control plane is the node's trust
root and SAM refuses to fetch it over plaintext from a remote address.

On a laptop behind NAT, the quickest way to get an `https` URL is a temporary
tunnel:

```bash
sam-one --data-dir ~/sam-one --tunnel cloudflare
```

This publishes the port on a random `trycloudflare.com` hostname, with no
account needed, and prints that URL in the banner. If `cloudflared` is not
installed, `sam-one` offers to download a pinned, checksum-verified release
into the data directory. `--tunnel-install` accepts the download without
asking. Nodes on any network then enroll with the same
`sam-node run --control-plane <url> --bootstrap-token-path <file>` command,
without the loopback flags.

If you have a real hostname and a reverse proxy in front, pass
`--external-url https://mesh.example.com` instead. The
[Cloud Run guide](../../guides/cloud-run/) shows a hosted variant.

With `--tunnel` or any other `https` URL, `sam-one` also prints a QR code
that enrolls a phone running the SAM Connect app. That app is in
[preview](../../preview/mobile/).

## Before you rely on it

- **Policy**: replace the open development policy. Write a
  [mesh policy](../../reference/policy/) file and start `sam-one` with
  `--policy-file` on a fresh data directory, or edit the policy in the
  console. Once the database has a policy, the database is the source of
  truth.
- **Enrollment**: run with `--no-join-token` so nodes can only enroll with
  tokens you mint (`sam-one token create`, single-use by default), or give
  the mesh an identity provider with `--issuer` and let people log in.
- **State**: keep the data directory on durable storage, or point
  `--db-driver postgres --db-dsn ...` at a database.
- **Tokens in logs**: read the admin token from `SAM_ADMIN_TOKEN` or
  `--admin-token-path` instead of the banner, and pass `--enroll-qr=false`
  in non-interactive environments.

The [sam-one reference](../../reference/sam-one/) lists every flag.
