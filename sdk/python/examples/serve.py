"""Publishes services on the mesh and answers callers until stopped: an MCP
tool, an A2A endpoint and, when OLLAMA_URL is set, the Ollama server running
beside this program as an inference service. The mesh policy decides which
members may call; the SDK turns the others away before anything reaches this
code or Ollama.

    python serve.py

SAM_CONTROL_PLANE_URL names the mesh. SAM_BOOTSTRAP_TOKEN_PATH is the file
holding the token the mesh operator gave you; the first run spends it and
keeps the identity and credential in SAM_STATE_DIR, later runs resume from
there without it.
"""

import json
import os

import trio
from agent_mesh import AgentMesh, HTTPRequest, HTTPResponse, HTTPService, MCPService, VerifiedBiscuit
from mcp.server.mcpserver import MCPServer

mesh = AgentMesh.enroll(
    os.environ.get("SAM_CONTROL_PLANE_URL", "https://mesh.example.com"),
    bootstrap_token_path=os.environ.get("SAM_BOOTSTRAP_TOKEN_PATH"),
    state_dir=os.environ.get("SAM_STATE_DIR", "~/.config/sam-mesh/greeter"),
)


def create_server() -> MCPServer:
    server = MCPServer("greeter")

    @server.tool(description="Greets someone by name")
    def greet(name: str) -> str:
        return f"hello {name}"

    return server


async def card(request: HTTPRequest, caller: VerifiedBiscuit) -> HTTPResponse:
    body = json.dumps({"name": "greeter", "path": request.path, "caller": caller.peer_id})
    return HTTPResponse(status=200, headers={"content-type": "application/json"}, body=body.encode())


async def main() -> None:
    async with mesh.join() as session:
        await session.serve(MCPService(name="greeter", create_server=create_server))
        await session.serve(HTTPService(type="a2a", name="greeter", target=card))
        if "OLLAMA_URL" in os.environ:
            await session.serve(HTTPService(type="inference", name="ollama", target=os.environ["OLLAMA_URL"]))

        served = ", ".join(f"{t}://{n}" for t, n, _ in session.services.list())
        print(f"serving {served} as {session.peer_id}", flush=True)
        await trio.sleep_forever()


trio.run(main)
