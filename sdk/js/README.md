# @sam-mesh/sdk

Native JavaScript SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written for Node.js:
the agent enrolls with the control plane, joins the mesh through a router,
finds services and calls them, publishes services of its own, and follows
the control plane's keys, bans and policy while it runs.

Guide: [sam-mesh.dev/docs/guides/native-sdks](https://sam-mesh.dev/docs/guides/native-sdks/).
Source: [github.com/google/sam/tree/main/sdk/js](https://github.com/google/sam/tree/main/sdk/js).

## Install

```bash
npm install @sam-mesh/sdk
```

Requires Node.js 22.18 or later.

## Use

```ts
import { AgentMesh } from "@sam-mesh/sdk";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: "https://hub.sam-mesh.dev",
  bootstrapTokenPath: "/run/secrets/sam-bootstrap-token",
  stateDir: `${process.env.HOME}/.config/sam-mesh/agent`,
});
console.log(mesh.peerId); // 12D3Koo...

// On the mesh: authenticated with a router, reachable through it, credential
// kept fresh, keys, bans and policy followed from the control plane until close().
const session = await mesh.join();
console.log(session.relayAddresses.map(String));

// Reach another member (directly or through a router) and verify it.
const peer = await session.authenticate("/ip4/.../p2p/<router>/p2p-circuit/p2p/<peer>");
console.log(peer.roles, peer.labels, peer.expiration);

// Find a service in the mesh DHT and call one of its tools.
const [provider] = await session.discover("mcp", "calc");
const addr = `${session.routers[0].addr}/p2p-circuit/p2p/${provider.peerId}`;
console.log(await session.listTools(addr, "mcp://calc"));
const result = await session.callTool(addr, "mcp://calc", "add", { a: 1, b: 2 }, { requiredLabels: { region: "eu" } });
console.log(result.text);

// Publish services of your own. Callers are authorized with the mesh policy
// before anything reaches your code or your backend.
await session.serve({
  type: "mcp",
  name: "echo",
  createServer: () => {
    const server = new McpServer({ name: "echo", version: "1.0.0" });
    server.tool("echo", { text: z.string() }, async ({ text }) => ({ content: [{ type: "text", text }] }));
    return server;
  },
});
await session.serve({ type: "inference", name: "llm", target: "http://127.0.0.1:8000" });
await session.serve({ type: "a2a", name: "reviewer", target: (request, caller) => new Response(`hello ${caller.peerId}`) });

// Call an inference or A2A service on another member.
const models = await session.request(addr, "inference://llm", "/v1/models");
console.log(models.status, models.text());
await session.close();

// Later, in a new process:
const resumed = await AgentMesh.load({ controlPlaneUrl: "https://hub.sam-mesh.dev", stateDir: "..." });
```

`enroll` takes exactly one of `bootstrapTokenPath`, `bootstrapToken` or
`jwt`. Read tokens from a file or the environment; do not put them on a
command line.

A plaintext `http://` control plane is accepted only on loopback. Pass
`allowInsecure: true` for a network you trust.

## License

Apache-2.0. Issues and contributions at
[github.com/google/sam](https://github.com/google/sam).
