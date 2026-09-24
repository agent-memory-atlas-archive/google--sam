# @agent-mesh/sdk

Native JavaScript SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written for Node.js.

Status: **milestone 3** (identity, enrollment, credential refresh, joining
the mesh over libp2p with mutual authentication, discovering services and
calling their tools). Serving tools is the next milestone; see
[../README.md](../README.md) for the plan.

## Install

```bash
cd sdk/js && npm ci && npm run build
```

Requires Node.js 22.18 or later. Runtime dependencies are
`@bufbuild/protobuf`, `@biscuit-auth/biscuit-wasm`, `@modelcontextprotocol/sdk`
and the js-libp2p packages (`libp2p`, `@libp2p/tcp`, `@libp2p/tls`,
`@chainsafe/libp2p-yamux`, `@libp2p/circuit-relay-v2`, `@libp2p/identify`,
`@libp2p/kad-dht`, `@libp2p/ping`).

## Use

```ts
import { AgentMesh } from "@agent-mesh/sdk";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: "https://hub.sam-mesh.dev",
  bootstrapTokenPath: "/run/secrets/sam-bootstrap-token",
  stateDir: `${process.env.HOME}/.config/sam-mesh/agent`,
});
console.log(mesh.peerId); // 12D3Koo...

// On the mesh: authenticated with a router, reachable through it, credential
// kept fresh until close().
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

## Layout

- `src/identity.ts`: ed25519 key pair, libp2p key encodings, peer ID.
- `src/controlplane.ts`: `/info`, `/keys`, `/enroll`, `/enroll/status`,
  `/register`, `/refresh`, with the proof-of-possession challenges from
  `api/network.go`.
- `src/credential.ts`: what a member holds, `AuthFrame` encoding.
- `src/mesh.ts`: `AgentMesh`, persistence under a state directory
  (`identity.key` in the libp2p private key encoding, `credential.json`).
- `src/biscuit.ts`: verification of a peer's credential with biscuit-wasm,
  as `internal/identity.verifyBiscuit` does.
- `src/host.ts`, `src/auth.ts`, `src/session.ts`: the libp2p host, the
  `/sam/auth/1.0.0` handshake on both sides, `MeshSession` with the relay
  reservation and the refresh loop.
- `src/discovery.ts`, `src/mcp.ts`: service keys for the mesh DHT, and MCP
  over `/sam/mcp/1.0.0` (a `Transport` for the official MCP client).
- `src/authorizer.ts`: the provider authorizer, as
  `internal/node.(*SamNode).Authorize`, over the generated baseline Datalog
  and the mesh policy rules from `GET /policies`.
- `src/serve.ts`: the `/sam/mcp/1.0.0` server, the `/libp2p-http` server
  and client, and the service registry behind `session.serve()`.
- `src/gen/`: generated from `api/sam.proto` and `api/datalog.go` by
  `hack/gen-sdk-proto.sh`.

## Test

```bash
npm test                                          # unit tests, fake control plane and router
go test ./tests/integration -run TestNativeSDKs   # real control plane, router and sam-node
```

The integration tests run `dist/conformance.js` and
`dist/conformance-join.js`, so build first. They skip when `dist/` or
`node_modules/` is missing.
