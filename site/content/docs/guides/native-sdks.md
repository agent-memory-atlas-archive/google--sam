---
title: "Native SDKs"
linkTitle: "Native SDKs"
weight: 6
---

An agent written in JavaScript or Python can be a mesh member itself, with no
`sam-node` beside it. The SDKs under `sdk/` speak the same protocols the Go
components speak: they enroll with the control plane, join through a router,
find services in the mesh DHT, call MCP tools and inference or A2A endpoints,
and publish services of their own, authorizing every caller with the same
Datalog a `sam-node` evaluates.

This guide shows the whole journey with each SDK. It assumes a running mesh:
a control plane, at least one router, and a bootstrap token minted for the
agent (see [Headless enrollment](../headless-enrollment/)).

## Install

```bash
npm install @sam-mesh/sdk          # Node.js 22 or later
pip install sam-mesh               # Python 3.11 or later
```

Both packages are published from the repository's release workflow, at the
version of the release tag. The Python package runs on trio; under asyncio,
use it through `anyio` with the trio backend.

## Enroll and join

Read the bootstrap token from a file or the environment. Do not put it on a
command line; it would sit in `ps` output and shell history. The state
directory keeps the identity key and the credential across restarts, so an
agent enrolls once and resumes with `load` afterwards.

JavaScript:

```ts
import { AgentMesh } from "@sam-mesh/sdk";

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: "https://hub.sam-mesh.dev",
  bootstrapTokenPath: "/run/secrets/sam-bootstrap-token",
  stateDir: `${process.env.HOME}/.config/sam-mesh/reviewer`,
});
const session = await mesh.join();
console.log(session.peerId, session.relayAddresses.map(String));
```

Python:

```python
import trio
from agent_mesh import AgentMesh

mesh = AgentMesh.enroll(
    "https://hub.sam-mesh.dev",
    bootstrap_token_path="/run/secrets/sam-bootstrap-token",
    state_dir="~/.config/sam-mesh/reviewer",
)

async def main():
    async with mesh.join() as session:
        print(session.peer_id, session.relay_addresses)
        ...

trio.run(main)
```

`join` connects to the routers named in the credential, runs the
`/sam/auth/1.0.0` handshake with each, requires the router role on the
credential the router answers with, and reserves a relay slot so other
members reach the agent through the router. While the session is open the
SDK keeps the credential fresh, pulls keys, bans and router addresses from
the control plane on `sam-node`'s interval, and listens to the control
plane's gossip events, so a ban or a key rotation reaches the agent within
seconds.

## Call services

Discovery is a DHT lookup by service type and name. A provider found there is
only a peer that announced itself; the call verifies its credential before
sending anything.

```ts
const [provider] = await session.discover("mcp", "calc");
const addr = `${session.routers[0].addr}/p2p-circuit/p2p/${provider.peerId}`;
const tools = await session.listTools(addr, "mcp://calc");
const result = await session.callTool(addr, "mcp://calc", "add", { a: 1, b: 2 }, { requiredLabels: { region: "eu" } });
console.log(result.text);

const models = await session.request(addr, "inference://llm", "/v1/models");
console.log(models.status, models.text());
```

```python
provider = (await session.discover("mcp", "calc"))[0]
addr = f"{session.routers[0].addr}/p2p-circuit/p2p/{provider.peer_id}"
tools = await session.list_tools(addr, "mcp://calc")
result = await session.call_tool(addr, "mcp://calc", "add", {"a": 1, "b": 2}, required_labels={"region": "eu"})
print(result.text)

models = await session.request(addr, "inference://llm", "/v1/models")
print(models.status, models.text)
```

`requiredLabels` refuses a provider whose control-plane-attested labels do not
carry the requested values, the way `sam-node`'s label gate does.
`request` speaks `/libp2p-http` for inference and A2A services, one request
per stream, the way `sam-node`'s egress proxy does for
`/sam/<peer>/<type>/<name>/<path>`.

## Publish services

`serve` registers a service, announces it in the DHT and reports it to the
control plane's catalog. The first call fetches the mesh policy. From then on
every caller is authorized before anything reaches your code or your backend:
its credential must be signed by a trusted control plane key, bound to the
connection's peer, unexpired, and its role must be granted the service by the
mesh policy, evaluated on the same Datalog text a `sam-node` evaluates.

An MCP service takes a factory for an MCP server, one per session:

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

await session.serve({
  type: "mcp",
  name: "echo",
  createServer: () => {
    const server = new McpServer({ name: "echo", version: "1.0.0" });
    server.tool("echo", { text: z.string() }, async ({ text }) => ({ content: [{ type: "text", text }] }));
    return server;
  },
});
```

```python
from agent_mesh import MCPService
from mcp.server.mcpserver import MCPServer

def create_server() -> MCPServer:
    server = MCPServer("echo")

    @server.tool()
    def echo(text: str) -> str:
        return text

    return server

await session.serve(MCPService(name="echo", create_server=create_server))
```

An inference or A2A service forwards authorized requests to a local URL or to
a handler in the process. The caller's biscuit never reaches the backend;
`X-Peer-Id` names the verified caller.

```ts
await session.serve({ type: "inference", name: "llm", target: "http://127.0.0.1:8000" });
await session.serve({
  type: "a2a",
  name: "reviewer",
  target: (request, caller) => new Response(`hello ${caller.peerId}`),
});
```

```python
from agent_mesh import HTTPRequest, HTTPResponse, HTTPService, VerifiedBiscuit

await session.serve(HTTPService(type="inference", name="llm", target="http://127.0.0.1:8000"))

async def card(request: HTTPRequest, caller: VerifiedBiscuit) -> HTTPResponse:
    return HTTPResponse(status=200, body=f"hello {caller.peer_id}".encode())

await session.serve(HTTPService(type="a2a", name="reviewer", target=card))
```

The mesh policy decides who may call. A role must be granted the service
(`allowed_services: ["mcp://echo"]`, or a pattern) and a target that matches
the agent's identity, exactly as for a service behind a `sam-node`; see
[Mesh policy](../../reference/policy/). A caller whose role grants nothing
receives `AuthResponse{success: false}` on the MCP stream and `403` on the
HTTP path.

## What the SDKs do not do

- They do not run a sidecar API or a sandbox. An agent that needs the egress
  policy enforcement of `sam-box` runs beside a `sam-node`.
- They do not serve the DHT. A member is a DHT client; the routers hold the
  records.
- They do not run in a browser. Node.js and CPython only, until the transport
  to routers from a browser is designed.

The wire contract, the interoperability facts the tests pin, and the plan are
in [`sdk/README.md`](https://github.com/google/sam/blob/main/sdk/README.md).
