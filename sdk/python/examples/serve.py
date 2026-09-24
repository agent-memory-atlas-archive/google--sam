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
