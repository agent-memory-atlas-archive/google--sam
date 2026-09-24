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
        # A provider record can outlive its member; the first that answers is used.
        for provider in providers:
            try:
                await session.connect(provider)
                break
            except (ConnectionError, PermissionError) as err:
                print(f"{provider.peer_id}: {err}", file=sys.stderr)
        else:
            raise SystemExit(f"no provider of {service} is reachable")
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
