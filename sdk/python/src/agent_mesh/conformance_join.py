# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mesh member driven by tests/integration/sdk_mesh_test.go: enrolls, joins
the mesh through a real router, prints one JSON line describing the session,
then takes JSON commands on stdin, one per line, until stdin closes:

  {"cmd": "auth", "addr": "<multiaddr>"}  connect (through a relay when the
                                           address says /p2p-circuit) and run
                                           the auth handshake
  {"cmd": "discover", "type": "mcp", "name": "calc"}
                                           DHT lookup for a service's providers
  {"cmd": "tools", "addr": "<multiaddr>", "service": "mcp://calc"}
                                           list a provider's tools
  {"cmd": "call", "addr": "<multiaddr>", "service": "mcp://calc",
   "tool": "add", "args": {...}}          call one tool
  {"cmd": "serve", "type": "mcp", "name": "echo"}
                                           publish an MCP service with an echo
                                           tool served in this process
  {"cmd": "serve", "type": "inference", "name": "llm", "target": "<url>"}
                                           publish an HTTP service; without
                                           target, a fake in this process
  {"cmd": "http", "addr": "<multiaddr>", "service": "inference://llm",
   "path": "/v1/models", "method": "GET", "body": "..."}
                                           call an HTTP service over the mesh
  {"cmd": "peers"}                          peers that authenticated to us
  {"cmd": "quit"}                           leave the mesh and exit

Each command gets one JSON line back. Same environment as conformance.py; the
JavaScript SDK ships the same runner (dist/conformance-join.js).
"""

import base64
import json
import logging
import os
import sys
import traceback

import trio
from mcp.server.mcpserver import MCPServer

from .mesh import AgentMesh
from .serve import HTTPRequest, HTTPResponse, HTTPService, MCPService
from .session import MeshSession


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _root_cause(err: BaseException) -> BaseException:
    # trio wraps a failure in one ExceptionGroup per nursery it crossed.
    while isinstance(err, BaseExceptionGroup) and len(err.exceptions) == 1:
        err = err.exceptions[0]
    return err


async def _handle(session: MeshSession, command: dict) -> dict:
    cmd = command.get("cmd")
    try:
        if cmd == "auth":
            verified = await session.authenticate(command["addr"])
            return {
                "cmd": cmd,
                "ok": True,
                "peer_id": verified.peer_id,
                "roles": verified.roles,
                "labels": verified.labels,
                "expiration": int(verified.expiration.timestamp()),
            }
        if cmd == "discover":
            providers = await session.discover(command.get("type", "mcp"), command.get("name"))
            return {"cmd": cmd, "ok": True, "providers": [{"peer_id": p.peer_id, "addrs": p.addrs} for p in providers]}
        if cmd == "tools":
            with trio.fail_after(15):
                tools = await session.list_tools(command["addr"], command.get("service", ""))
            return {"cmd": cmd, "ok": True, "tools": tools}
        if cmd == "call":
            with trio.fail_after(15):
                result = await session.call_tool(command["addr"], command.get("service", ""), command["tool"], command.get("args") or {})
            return {"cmd": cmd, "ok": True, "is_error": result.is_error, "text": result.text}
        if cmd == "peers":
            return {"cmd": cmd, "authenticated_peers": sorted(session.authenticated_peers)}
        if cmd == "serve":
            name = command.get("name", "")
            service_type = command.get("type")
            if service_type == "mcp":

                def create_server() -> MCPServer:
                    server = MCPServer(name)

                    @server.tool()
                    def echo(text: str) -> str:
                        return f"python:{text}"

                    return server

                await session.serve(MCPService(name=name, create_server=create_server, description="echo served by the python SDK"))
            elif service_type in ("inference", "a2a"):
                target = command.get("target")
                if not target:

                    async def fake(request: HTTPRequest, caller) -> HTTPResponse:  # type: ignore[no-untyped-def]
                        body = json.dumps({"sdk": "python", "peer": caller.peer_id, "method": request.method, "path": request.path.split("?")[0]})
                        return HTTPResponse(status=200, headers={"content-type": "application/json"}, body=body.encode())

                    target = fake
                await session.serve(HTTPService(type=service_type, name=name, target=target, description=f"{service_type} served by the python SDK"))
            else:
                return {"cmd": cmd, "ok": False, "error": f"unknown service type {service_type!r}"}
            return {"cmd": cmd, "ok": True, "services": [f"{t}://{n}" for t, n, _ in session.services.list()]}
        if cmd == "http":
            with trio.fail_after(15):
                res = await session.request(
                    command["addr"],
                    command.get("service", ""),
                    command.get("path", "/"),
                    method=command.get("method", "GET"),
                    headers=command.get("headers"),
                    body=command.get("body"),
                )
            return {"cmd": cmd, "ok": True, "status": res.status, "headers": res.headers, "body": res.text}
        return {"cmd": cmd, "ok": False, "error": f"unknown command {cmd!r}"}
    except BaseException as err:  # noqa: BLE001 - the driver wants the failure, not a dead runner
        if isinstance(err, (KeyboardInterrupt, SystemExit, trio.Cancelled)):
            raise
        cause = _root_cause(err)
        traceback.print_exception(err, file=sys.stderr)
        return {"cmd": cmd, "ok": False, "error": f"{type(cause).__name__}: {cause}"}


async def main() -> None:
    # stdout carries the protocol lines only; every log goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    control_plane_url = _require_env("SAM_CONTROL_PLANE_URL")
    bootstrap_token_path = _require_env("SAM_BOOTSTRAP_TOKEN_PATH")
    state_dir = _require_env("SAM_SDK_STATE_DIR")
    allow_insecure = os.environ.get("SAM_INSECURE_CONTROL_PLANE") == "1"
    listen = [a for a in os.environ.get("SAM_SDK_LISTEN_ADDRS", "").split(",") if a]

    mesh = AgentMesh.enroll(
        control_plane_url,
        bootstrap_token_path=bootstrap_token_path,
        state_dir=state_dir,
        allow_insecure=allow_insecure,
        poll_interval=0.2,
    )
    async with mesh.join(listen_addrs=listen) as session:
        _emit(
            {
                "sdk": "python",
                "peer_id": session.peer_id,
                "routers": [{"peer_id": r.peer_id, "addr": str(r.addr), "roles": r.credential.roles} for r in session.routers],
                "relay_addresses": session.relay_addresses,
                "direct_addresses": [str(a) for a in session.host.get_addrs()],
                "biscuit": base64.b64encode(mesh.credential.biscuit).decode(),
            }
        )
        while True:
            line = await trio.to_thread.run_sync(sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError as err:
                _emit({"ok": False, "error": f"not JSON: {err}"})
                continue
            if command.get("cmd") == "quit":
                _emit({"cmd": "quit", "ok": True})
                break
            _emit(await _handle(session, command))


if __name__ == "__main__":
    trio.run(main)
