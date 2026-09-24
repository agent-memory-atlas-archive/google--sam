# sam-mesh (Python)

Native Python SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written in Python:
the agent enrolls with the control plane, joins the mesh through a router,
finds services and calls them, publishes services of its own, and follows
the control plane's keys, bans and policy while it runs. Import it as
`agent_mesh`.

Guide: [sam-mesh.dev/docs/guides/native-sdks](https://sam-mesh.dev/docs/guides/native-sdks/).
Source: [github.com/google/sam/tree/main/sdk/python](https://github.com/google/sam/tree/main/sdk/python).

## Install

```bash
pip install sam-mesh
```

Python 3.11 or later. The SDK runs on trio (py-libp2p is trio-based); under
asyncio, use it through `anyio` with the trio backend. One dependency,
`fastecdsa`, builds from source against GMP (`libgmp-dev` on Debian).

## Use

```python
import trio
from agent_mesh import AgentMesh, HTTPRequest, HTTPResponse, HTTPService, MCPService, VerifiedBiscuit
from mcp.server.mcpserver import MCPServer

mesh = AgentMesh.enroll(
    "https://hub.sam-mesh.dev",
    bootstrap_token_path="/run/secrets/sam-bootstrap-token",
    state_dir="~/.config/sam-mesh/agent",
)
print(mesh.peer_id)                      # 12D3Koo...


async def main():
    # On the mesh: authenticated with a router, reachable through it,
    # credential kept fresh, keys, bans and policy followed from the control
    # plane for as long as the block is open.
    async with mesh.join() as session:
        print(session.relay_addresses)
        # Reach another member (directly or through a router) and verify it.
        peer = await session.authenticate("/ip4/.../p2p/<router>/p2p-circuit/p2p/<peer>")
        print(peer.roles, peer.labels, peer.expiration)

        # Find a service in the mesh DHT and call one of its tools.
        provider = (await session.discover("mcp", "calc"))[0]
        addr = f"{session.routers[0].addr}/p2p-circuit/p2p/{provider.peer_id}"
        print(await session.list_tools(addr, "mcp://calc"))
        result = await session.call_tool(addr, "mcp://calc", "add", {"a": 1, "b": 2}, required_labels={"region": "eu"})
        print(result.text)

        # Publish services of your own. Callers are authorized with the mesh
        # policy before anything reaches your code or your backend.
        def create_server() -> MCPServer:
            server = MCPServer("echo")

            @server.tool()
            def echo(text: str) -> str:
                return text

            return server

        await session.serve(MCPService(name="echo", create_server=create_server))
        await session.serve(HTTPService(type="inference", name="llm", target="http://127.0.0.1:8000"))

        async def card(request: HTTPRequest, caller: VerifiedBiscuit) -> HTTPResponse:
            return HTTPResponse(status=200, body=f"hello {caller.peer_id}".encode())

        await session.serve(HTTPService(type="a2a", name="reviewer", target=card))

        # Call an inference or A2A service on another member.
        models = await session.request(addr, "inference://llm", "/v1/models")
        print(models.status, models.text)


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

## License

Apache-2.0. Issues and contributions at
[github.com/google/sam](https://github.com/google/sam).
