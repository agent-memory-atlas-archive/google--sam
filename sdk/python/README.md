# agent-mesh (Python)

Native Python SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written in Python.
Import it as `agent_mesh`.

Status: **milestone 1** (identity, enrollment, credential refresh). The
libp2p transport, service discovery and MCP over the mesh are the next
milestones; see [../README.md](../README.md) for the plan.

## Install

```bash
pip install -e 'sdk/python[test]'
```

Runtime dependencies are `cryptography` (ed25519) and `protobuf`.

## Use

```python
from agent_mesh import AgentMesh

mesh = AgentMesh.enroll(
    "https://hub.sam-mesh.dev",
    bootstrap_token_path="/run/secrets/sam-bootstrap-token",
    state_dir="~/.config/sam-mesh/agent",
)
print(mesh.peer_id)                      # 12D3Koo...
print(mesh.credential.time_to_live_seconds())
mesh.refresh()                           # trades the biscuit for a fresh one

# Later, in a new process:
mesh = AgentMesh.load("~/.config/sam-mesh/agent")
frame = mesh.auth_frame("mcp://calculator")  # first frame on a mesh stream
```

`enroll` takes exactly one of `bootstrap_token_path`, `bootstrap_token` or
`jwt`. Read tokens from a file or the environment; do not put them on a
command line.

A plaintext `http://` control plane is accepted only on loopback. Pass
`allow_insecure=True` for a network you trust.

## Layout

- `agent_mesh.identity`: ed25519 key pair, libp2p key encodings, peer ID.
- `agent_mesh.controlplane`: `/info`, `/keys`, `/enroll`, `/enroll/status`,
  `/register`, `/refresh`, with the proof-of-possession challenges from
  `api/network.go`.
- `agent_mesh.credential`: what a member holds, `AuthFrame` encoding.
- `agent_mesh.mesh`: `AgentMesh`, persistence under a state directory
  (`identity.key` in the libp2p private key encoding, `credential.json`).
- `agent_mesh._proto`: generated from `api/sam.proto` by
  `hack/gen-sdk-proto.sh`.

## Test

```bash
pytest sdk/python/tests                       # unit tests, fake control plane
go test ./tests/integration -run TestNativeSDKs  # against a real control plane
```

The integration test skips unless `agent_mesh` imports in `python3`.
