---
title: "Native SDKs"
linkTitle: "Native SDKs"
weight: 6
---

An agent written in JavaScript or Python can be a member of the mesh itself,
with no `sam-node` beside it. The SDK enrolls with the control plane, joins
through a router, finds services, calls MCP tools and inference or A2A
endpoints, and publishes services of its own. Every caller of those services
is checked against the mesh policy before anything reaches your code.

This guide takes you from nothing to two programs on a mesh: one that
publishes a tool and an agent card, and one that finds them and calls them.
Both programs are in the repository under
[`sdk/js/examples`](https://github.com/google/sam/tree/main/sdk/js/examples)
and
[`sdk/python/examples`](https://github.com/google/sam/tree/main/sdk/python/examples),
and the repository's tests run them against a real mesh, so what you read
here is what runs.

## 1. Get a mesh

A program needs the URL of a control plane and a token that lets it enroll.
You get them in one of two ways.

### Run your own

`sam-one` runs a control plane, a router and a web console in one process.
The [install script](../../getting-started/quickstart/#1-install) provides
it. Start it with a directory for its state:

```bash
sam-one --data-dir ~/sam-one
```

The banner it prints has the URL, and the token is written to
`~/sam-one/join-token`:

```text
API URL:      http://0.0.0.0:33775
Join Token:   sam_tok_…
```

On first boot `sam-one` seeds an open development policy, so any enrolled
member may publish and call any service. That is right for trying the
programs below on your laptop. Before you share the mesh, replace it, as
[Your own mesh](../../getting-started/your-own-mesh/#5-before-you-share-it)
explains.

This mesh is reachable from your machine only. To let programs on other
machines join, start `sam-one` with `--tunnel cloudflare`, which publishes
the port on a temporary public `https` hostname and prints that URL in the
banner instead. Use that URL as the control plane URL everywhere below. [Your
own mesh](../../getting-started/your-own-mesh/) covers the tunnel, a real
hostname and the console.

### Join a mesh someone else runs

Ask the operator for the control plane URL and a bootstrap token. Operators
mint tokens in the console or with `sam-one token create`; a mesh with an
identity provider lets you mint your own from the console after logging in.
Save the token in a file that only you can read. Do not put it on a command
line: it would sit in `ps` output and shell history.

### Tell the programs about it

The programs below read the mesh from two environment variables. Set them in
every terminal you use:

```bash
export SAM_CONTROL_PLANE_URL=http://127.0.0.1:33775   # the API URL from the banner
export SAM_BOOTSTRAP_TOKEN_PATH=~/sam-one/join-token
```

A plain `http://` URL is accepted only for a control plane on the same
machine. Anything else needs `https://`, because the control plane is the
trust root of every member and the SDK refuses to fetch it over plaintext
from a remote address. Inside a network you already trust, such as a
Kubernetes cluster where the control plane is a cluster-local service, set
`SAM_INSECURE_CONTROL_PLANE=true` (`allowInsecure` in code), the same choice
as `sam-node --insecure-control-plane`.

On a platform that issues workload identity tokens, a program needs no
bootstrap token. A mesh whose control plane trusts the platform's issuer
enrolls the program from that token instead; on Kubernetes that is a
projected service account token, as [Headless enrollment](../headless-enrollment/)
shows for `sam-node`. Set `SAM_JWT_PATH` to the token file rather than
`SAM_BOOTSTRAP_TOKEN_PATH`. That is how the public testnets run these same
programs as canaries beside the `sam-node` ones.

## 2. Install the SDK

```bash
npm install @sam-mesh/sdk @modelcontextprotocol/sdk zod   # Node.js 22 or later
```

```bash
pip install sam-mesh                                       # Python 3.11 or later
```

The JavaScript package depends on the MCP SDK and zod; the install line
names them so your project can import them directly. The Python package is
imported as `agent_mesh`. It runs on trio, because py-libp2p does; under
asyncio, use it through `anyio` with the trio backend.

## 3. Publish a service

This program publishes an MCP tool named `greet` under the service
`mcp://greeter` and an A2A endpoint under `a2a://greeter`, and stays on the
mesh answering callers until you stop it. With `OLLAMA_URL` set, it also
publishes the Ollama server running beside it as `inference://ollama`, so a
model on your laptop becomes a model on the mesh.

JavaScript, `serve.js`:

<!-- embed: sdk/js/examples/serve.ts -->
```ts
// Publishes services on the mesh and answers callers until stopped: an MCP
// tool, an A2A endpoint and, when OLLAMA_URL is set, the Ollama server running
// beside this program as an inference service. The mesh policy decides which
// members may call; the SDK turns the others away before anything reaches
// this code or Ollama.
//
//   node serve.js            # publishes mcp://greeter and a2a://greeter
//   node serve.js greeter-2  # the same under another name
//
// SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
// SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or
// SAM_JWT_PATH (a workload identity token your platform issues, such as a
// Kubernetes projected service account token), and keeps the identity and
// credential in SAM_STATE_DIR; later runs resume from there without it.
import { homedir } from "node:os";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { AgentMesh } from "@sam-mesh/sdk";
import { z } from "zod";

const [name = "greeter"] = process.argv.slice(2);

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: process.env.SAM_CONTROL_PLANE_URL ?? "https://mesh.example.com",
  bootstrapTokenPath: process.env.SAM_BOOTSTRAP_TOKEN_PATH,
  jwtPath: process.env.SAM_JWT_PATH,
  stateDir: process.env.SAM_STATE_DIR ?? `${homedir()}/.config/sam-mesh/${name}`,
  // A plaintext http:// control plane is otherwise accepted only on loopback.
  allowInsecure: process.env.SAM_INSECURE_CONTROL_PLANE === "true",
});
const session = await mesh.join();

await session.serve({
  type: "mcp",
  name,
  createServer: () => {
    const server = new McpServer({ name, version: "1.0.0" });
    server.registerTool("greet", { description: "Greets someone by name", inputSchema: { name: z.string() } }, async ({ name: who }) => ({
      content: [{ type: "text", text: `hello ${who}` }],
    }));
    return server;
  },
});

await session.serve({
  type: "a2a",
  name,
  target: (request, caller) => Response.json({ name, path: new URL(request.url).pathname, caller: caller.peerId }),
});

if (process.env.OLLAMA_URL !== undefined) {
  await session.serve({ type: "inference", name: "ollama", target: process.env.OLLAMA_URL });
}

console.log(`serving ${session.servedServices.map((s) => `${s.type}://${s.name}`).join(", ")} as ${session.peerId}`);

const stop = () => void session.close().then(() => process.exit(0));
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
```
<!-- /embed -->

Python, `serve.py`:

<!-- embed: sdk/python/examples/serve.py -->
```python
"""Publishes services on the mesh and answers callers until stopped: an MCP
tool, an A2A endpoint and, when OLLAMA_URL is set, the Ollama server running
beside this program as an inference service. The mesh policy decides which
members may call; the SDK turns the others away before anything reaches this
code or Ollama.

    python serve.py            # publishes mcp://greeter and a2a://greeter
    python serve.py greeter-2  # the same under another name

SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or SAM_JWT_PATH
(a workload identity token your platform issues, such as a Kubernetes
projected service account token), and keeps the identity and credential in
SAM_STATE_DIR; later runs resume from there without it.
"""

import json
import os
import sys

import trio
from agent_mesh import AgentMesh, HTTPRequest, HTTPResponse, HTTPService, MCPService, VerifiedBiscuit
from mcp.server.mcpserver import MCPServer

service_name = sys.argv[1] if len(sys.argv) > 1 else "greeter"

mesh = AgentMesh.enroll(
    os.environ.get("SAM_CONTROL_PLANE_URL", "https://mesh.example.com"),
    bootstrap_token_path=os.environ.get("SAM_BOOTSTRAP_TOKEN_PATH"),
    jwt_path=os.environ.get("SAM_JWT_PATH"),
    state_dir=os.environ.get("SAM_STATE_DIR", f"~/.config/sam-mesh/{service_name}"),
    # A plaintext http:// control plane is otherwise accepted only on loopback.
    allow_insecure=os.environ.get("SAM_INSECURE_CONTROL_PLANE") == "true",
)


def create_server() -> MCPServer:
    server = MCPServer(service_name)

    @server.tool(description="Greets someone by name")
    def greet(name: str) -> str:
        return f"hello {name}"

    return server


async def card(request: HTTPRequest, caller: VerifiedBiscuit) -> HTTPResponse:
    body = json.dumps({"name": service_name, "path": request.path, "caller": caller.peer_id})
    return HTTPResponse(status=200, headers={"content-type": "application/json"}, body=body.encode())


async def main() -> None:
    async with mesh.join() as session:
        await session.serve(MCPService(name=service_name, create_server=create_server))
        await session.serve(HTTPService(type="a2a", name=service_name, target=card))
        if "OLLAMA_URL" in os.environ:
            await session.serve(HTTPService(type="inference", name="ollama", target=os.environ["OLLAMA_URL"]))

        served = ", ".join(f"{t}://{n}" for t, n, _ in session.services.list())
        print(f"serving {served} as {session.peer_id}", flush=True)
        await trio.sleep_forever()


trio.run(main)
```
<!-- /embed -->

Run one of them:

```bash
node serve.js
# or
python serve.py
```

```text
serving mcp://greeter, a2a://greeter as 12D3KooWQmB5…
```

The program spent the token, saved its identity and credential under
`~/.config/sam-mesh/greeter`, joined through the router and announced its
services. Run it again and it resumes from that directory; the token is no
longer needed. The service is reachable from every member of the mesh:
another program written with an SDK, a `sam-node` on any machine, or a
phone.

## 4. Call it

This program finds a service by name and calls it: a tool of an MCP service,
or a path of an inference or A2A service.

JavaScript, `call.js`:

<!-- embed: sdk/js/examples/call.ts -->
```ts
// Finds a service on the mesh and calls it: a tool of an MCP service, or a
// path of an inference or A2A service.
//
//   node call.js mcp://greeter greet '{"name": "Ada"}'
//   node call.js a2a://greeter /card
//   node call.js inference://ollama /v1/models
//
// SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
// SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or
// SAM_JWT_PATH (a workload identity token your platform issues, such as a
// Kubernetes projected service account token), and keeps the identity and
// credential in SAM_STATE_DIR; later runs resume from there without it.
import { homedir } from "node:os";
import { AgentMesh } from "@sam-mesh/sdk";

const [service = "mcp://greeter", toolOrPath = "greet", args = '{"name": "world"}'] = process.argv.slice(2);

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: process.env.SAM_CONTROL_PLANE_URL ?? "https://mesh.example.com",
  bootstrapTokenPath: process.env.SAM_BOOTSTRAP_TOKEN_PATH,
  jwtPath: process.env.SAM_JWT_PATH,
  stateDir: process.env.SAM_STATE_DIR ?? `${homedir()}/.config/sam-mesh/caller`,
  // A plaintext http:// control plane is otherwise accepted only on loopback.
  allowInsecure: process.env.SAM_INSECURE_CONTROL_PLANE === "true",
});
const session = await mesh.join();
console.log(`on the mesh as ${session.peerId}`);

const [provider] = await session.discover(service);
if (provider === undefined) {
  throw new Error(`no member of the mesh serves ${service}`);
}
console.log(`${service} is served by ${provider.peerId}`);

if (service.startsWith("mcp://")) {
  const tools = await session.listTools(provider, service);
  console.log(`tools: ${tools.map((t) => t.name).join(", ")}`);
  const result = await session.callTool(provider, service, toolOrPath, JSON.parse(args));
  console.log(result.text.join("\n"));
} else {
  const response = await session.request(provider, service, toolOrPath);
  console.log(response.status, response.text());
}

await session.close();
```
<!-- /embed -->

Python, `call.py`:

<!-- embed: sdk/python/examples/call.py -->
```python
"""Finds a service on the mesh and calls it: a tool of an MCP service, or a
path of an inference or A2A service.

    python call.py mcp://greeter greet '{"name": "Ada"}'
    python call.py a2a://greeter /card
    python call.py inference://ollama /v1/models

SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or SAM_JWT_PATH
(a workload identity token your platform issues, such as a Kubernetes
projected service account token), and keeps the identity and credential in
SAM_STATE_DIR; later runs resume from there without it.
"""

import json
import os
import sys

import trio
from agent_mesh import AgentMesh

argv = sys.argv[1:]
service = argv[0] if len(argv) > 0 else "mcp://greeter"
tool_or_path = argv[1] if len(argv) > 1 else "greet"
args = json.loads(argv[2]) if len(argv) > 2 else {"name": "world"}

mesh = AgentMesh.enroll(
    os.environ.get("SAM_CONTROL_PLANE_URL", "https://mesh.example.com"),
    bootstrap_token_path=os.environ.get("SAM_BOOTSTRAP_TOKEN_PATH"),
    jwt_path=os.environ.get("SAM_JWT_PATH"),
    state_dir=os.environ.get("SAM_STATE_DIR", "~/.config/sam-mesh/caller"),
    # A plaintext http:// control plane is otherwise accepted only on loopback.
    allow_insecure=os.environ.get("SAM_INSECURE_CONTROL_PLANE") == "true",
)


async def main() -> None:
    async with mesh.join() as session:
        print(f"on the mesh as {session.peer_id}")

        providers = await session.discover(service)
        if not providers:
            raise SystemExit(f"no member of the mesh serves {service}")
        provider = providers[0]
        print(f"{service} is served by {provider.peer_id}")

        if service.startswith("mcp://"):
            tools = await session.list_tools(provider, service)
            print("tools:", ", ".join(t.name for t in tools))
            result = await session.call_tool(provider, service, tool_or_path, args)
            print("\n".join(result.text))
        else:
            response = await session.request(provider, service, tool_or_path)
            print(response.status, response.text)


trio.run(main)
```
<!-- /embed -->

In a second terminal, with the same two environment variables set, call the
tool and the agent card. Either language finds a service published from the
other:

```bash
node call.js mcp://greeter greet '{"name": "Ada"}'
```

```text
on the mesh as 12D3KooWHZ2M…
mcp://greeter is served by 12D3KooWQmB5…
tools: greet
hello Ada
```

```bash
python call.py a2a://greeter /card
```

```text
on the mesh as 12D3KooWHZ2M…
a2a://greeter is served by 12D3KooWQmB5…
200 {"name": "greeter", "path": "/card", "caller": "12D3KooWHZ2M…"}
```

If you started the server with `OLLAMA_URL=http://127.0.0.1:11434`, ask it
for its models:

```bash
node call.js inference://ollama /v1/models
```

The caller and the server are two identities on the mesh with their own
state directories, `caller` and `greeter`. Each spent a token on its first
run; the standing join token of `sam-one` admits any number of members.

## What happened

`enroll` sent the token and the program's public key to the control plane
and received a credential: a signed token that names the member, its role
and the routers it may use. The identity key and the credential live in the
state directory, so the next run resumes without a token, and the SDK
renews the credential before it expires. Delete the directory to enroll
afresh, for instance with other labels. The directory has the same layout
in both SDKs and in `sam-node`: a program in one language resumes a
directory written by the other, and `sam-node state import` runs the same
identity as a node (see the [sam-node reference](../../reference/sam-node/#state)).

`join` connected to the routers named in the credential, proved the
program's identity to each and verified the router's own credential, and
reserved a relay slot so that other members reach the program through the
router. The program opens no port of its own. While the session is open,
the SDK follows the control plane: keys, bans and router addresses on
`sam-node`'s schedule, and sooner when the control plane announces a change
over the mesh.

`discover` looked the service up by name in a table the routers host. Every
call then dials the addresses the provider advertised and the path through
the router, and verifies the provider's credential before sending anything.
`requiredLabels` (`required_labels` in Python) refuses a provider whose
control-plane-attested labels do not carry the values you ask for.

`serve` fetched the mesh policy and started answering. Every caller must
present a credential signed by a trusted control plane key, bound to the
connection's peer and unexpired, and its role must be granted the service
by the policy. The check runs on the same Datalog text a `sam-node`
evaluates. A caller the policy does not admit is turned away before the
request reaches your handler or your backend. Your handler sees the verified
caller as `caller.peerId` (`caller.peer_id`) and, for a forwarded backend
such as Ollama, as the `X-Peer-Id` header; the caller's credential is never
forwarded.

Under the open development policy of `sam-one`, every member holds the
`node` role and that role may call any service. On a shared mesh the policy
grants a role the services it may call (`allowed_services: ["mcp://greeter"]`
or a pattern) and the members it may reach; see
[Mesh policy](../../reference/policy/). A refused MCP caller receives
`AuthResponse{success: false}` on the stream; a refused HTTP caller receives
`403`.

## Beyond the examples

- **Naming a peer.** `callTool`, `listTools`, `request`, `authenticate` and
  `connect` accept a provider from `discover`, a peer id, or a multiaddr.
  With a peer id alone the SDK goes through the routers.
- **Every service of a type.** `discover("mcp")` lists every MCP provider
  on the mesh; `discover("mcp://greeter")` those of one service.
- **A member's own catalog.** `listTools(peer, "")` returns the provider's
  `list_local_services` tool, the same one a `sam-node` offers.
- **Forwarding to a local server.** `serve({ type: "inference", name,
  target: "http://127.0.0.1:8000" })` and
  `HTTPService(type="inference", name=..., target="http://...")` forward
  authorized requests to a URL; a function instead of a URL handles them in
  the process.
- **Reading the policy.** `session.policyRules` (`session.policy_rules`)
  holds the Datalog the session enforces, for logging or tests.

## What the SDKs do not do

- They do not run a sidecar API or a sandbox. An agent that needs the egress
  policy enforcement of `sam-box` runs beside a `sam-node`.
- They do not serve the discovery table. A member is a client of it; the
  routers hold the records.
- They do not run in a browser. Node.js and CPython only, until the transport
  to routers from a browser is designed.

The wire contract and the interoperability facts the tests pin are in
[`sdk/README.md`](https://github.com/google/sam/blob/main/sdk/README.md).
