# agent-mesh (Python)

Native Python SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written in Python.
Import it as `agent_mesh`.

Status: **milestone 2** (identity, enrollment, credential refresh, joining
the mesh over libp2p with mutual authentication). Discovering and calling
tools are the next milestones; see [../README.md](../README.md) for the plan.

## Install

```bash
pip install -e 'sdk/python[test]'
```

Runtime dependencies are `cryptography`, `protobuf`, `biscuit-python`,
`libp2p` (py-libp2p 0.7, trio-based) and `multiaddr`. Python 3.11 or later.

## Use

```python
import trio
from agent_mesh import AgentMesh

mesh = AgentMesh.enroll(
    "https://hub.sam-mesh.dev",
    bootstrap_token_path="/run/secrets/sam-bootstrap-token",
    state_dir="~/.config/sam-mesh/agent",
)
print(mesh.peer_id)                      # 12D3Koo...


async def main():
    # On the mesh: authenticated with a router, reachable through it,
    # credential kept fresh for as long as the block is open.
    async with mesh.join() as session:
        print(session.relay_addresses)
        # Reach another member (directly or through a router) and verify it.
        peer = await session.authenticate("/ip4/.../p2p/<router>/p2p-circuit/p2p/<peer>")
        print(peer.roles, peer.labels, peer.expiration)


trio.run(main)

# Later, in a new process:
mesh = AgentMesh.load("~/.config/sam-mesh/agent")
```

py-libp2p runs on trio, so `join()` is a trio async context manager; under
asyncio use it through `anyio` with the trio backend.

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
- `agent_mesh.biscuit`: verification of a peer's credential with
  biscuit-python, as `internal/identity.verifyBiscuit` does.
- `agent_mesh.host`, `agent_mesh.auth`, `agent_mesh.relay`,
  `agent_mesh.session`: the libp2p host, the `/sam/auth/1.0.0` handshake on
  both sides, the circuit relay v2 client (reservation, dial, accept) and
  `MeshSession` with the refresh loop.
- `agent_mesh._proto`: generated from `api/sam.proto` and
  `sdk/python/proto/circuit.proto` by `hack/gen-sdk-proto.sh`.

## Test

```bash
pytest sdk/python/tests                          # unit tests, fake control plane and router
go test ./tests/integration -run TestNativeSDKs  # real control plane, router and sam-node
```

The integration tests skip unless `agent_mesh` imports in
`sdk/python/.venv/bin/python` or `python3`.
