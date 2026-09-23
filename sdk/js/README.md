# @agent-mesh/sdk

Native JavaScript SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written for Node.js.

Status: **milestone 1** (identity, enrollment, credential refresh). The
libp2p transport, service discovery and MCP over the mesh are the next
milestones; see [../README.md](../README.md) for the plan.

## Install

```bash
cd sdk/js && npm ci && npm run build
```

Requires Node.js 22.18 or later. The only runtime dependency is
`@bufbuild/protobuf`.

## Use

```ts
import { AgentMesh } from "@agent-mesh/sdk";

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: "https://hub.sam-mesh.dev",
  bootstrapTokenPath: "/run/secrets/sam-bootstrap-token",
  stateDir: `${process.env.HOME}/.config/sam-mesh/agent`,
});
console.log(mesh.peerId); // 12D3Koo...
await mesh.refresh(); // trades the biscuit for a fresh one

// Later, in a new process:
const resumed = await AgentMesh.load({ controlPlaneUrl: "https://hub.sam-mesh.dev", stateDir: "..." });
const frame = resumed.authFrame("mcp://calculator"); // first frame on a mesh stream
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
- `src/gen/`: generated from `api/sam.proto` by `hack/gen-sdk-proto.sh`.

## Test

```bash
npm test                                          # unit tests, fake control plane
go test ./tests/integration -run TestNativeSDKs   # against a real control plane
```

The integration test runs `dist/conformance.js`, so build first. It skips
when `dist/` or `node_modules/` is missing.
