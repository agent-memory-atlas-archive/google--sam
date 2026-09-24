# sam-mesh (Python)

Native Python SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written in Python:
the agent enrolls with the control plane, joins the mesh through a router,
finds services and calls them, publishes services of its own, and follows
the control plane's keys, bans and policy while it runs. Import it as
`agent_mesh`.

Guide: [sam-mesh.dev/docs/guides/native-sdks](https://sam-mesh.dev/docs/guides/native-sdks/),
from an empty machine to two programs on a mesh.
Source: [github.com/google/sam/tree/main/sdk/python](https://github.com/google/sam/tree/main/sdk/python).

## Install

```bash
pip install sam-mesh
```

Python 3.11 or later. The SDK runs on trio (py-libp2p is trio-based); under
asyncio, use it through `anyio` with the trio backend. One dependency,
`fastecdsa`, builds from source against GMP (`libgmp-dev` on Debian).

## Use

Both programs below are in
[`examples/`](https://github.com/google/sam/tree/main/sdk/python/examples)
and run against a real mesh in the repository's tests. They read the mesh
from `SAM_CONTROL_PLANE_URL` and the enrollment token from
`SAM_BOOTSTRAP_TOKEN_PATH`; the guide shows how to get both from `sam-one`
or from the operator of an existing mesh.

Find a service and call it:

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

Publish services of your own:

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

`enroll` reuses the identity and credential saved in `state_dir` when they
are still valid for that control plane, and needs exactly one of
`bootstrap_token_path`, `bootstrap_token` or `jwt` otherwise. Read tokens
from a file or the environment; do not put them on a command line.

A plaintext `http://` control plane is accepted only on loopback. Pass
`allow_insecure=True` for a network you trust.

## License

Apache-2.0. Issues and contributions at
[github.com/google/sam](https://github.com/google/sam).
