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

"""The provider side in one process: this SDK's handlers for /sam/mcp/1.0.0
and /libp2p-http behind a py-libp2p host, called with this SDK's clients.
The real sam-node as a caller is exercised by tests/integration."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import biscuit_auth as ba
import multiaddr
import pytest
import trio
from libp2p.peer.peerinfo import info_from_p2p_addr
from mcp.server.mcpserver import MCPServer

from agent_mesh._proto import sam_pb2 as pb
from agent_mesh.auth import MCP_PROTOCOL, AuthRejectedError
from agent_mesh.authorizer import ProviderAuthorizerOptions
from agent_mesh.controlplane import ROLE_NODE
from agent_mesh.identity import Identity
from agent_mesh.mcp_client import open_mcp_session, tool_call_result
from agent_mesh.serve import (
    HTTP_PROTOCOL,
    HTTPRequest,
    HTTPResponse,
    HTTPService,
    MCPService,
    ProviderOptions,
    ServiceRegistry,
    http_ingress_handler,
    http_request_over_stream,
    mcp_stream_handler,
)

from .test_session import CP, CP_KEY, libp2p_host

# What the control plane renders for a policy granting the node role calc,
# echo and every inference service on any target.
POLICY_RULES = [
    'granted_service_set("mcp", ["calc", "echo"]) <- role("sam:role:node")',
    'granted_service_all("inference") <- role("sam:role:node")',
    'granted_service_all("sam:system") <- role("sam:role:node")',
    'target_unrestricted(true) <- role("sam:role:node")',
]


def mint(peer_id: str, role: str) -> bytes:
    return ba.BiscuitBuilder(
        "node({p}); client_peer_id({p}); expiration(2035-01-01T00:00:00Z); role({r});", {"p": peer_id, "r": role}
    ).build(CP.private_key).to_bytes()


def calc_server() -> MCPServer:
    server = MCPServer("calc")

    @server.tool()
    def add(a: int, b: int) -> str:
        return str(a + b)

    return server


class FakeBackend(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def _answer(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        FakeBackend.seen.append({"method": self.command, "path": self.path, "peer": self.headers.get("x-peer-id"), "body": body.decode(), "biscuit": self.headers.get("x-sam-biscuit")})
        payload = json.dumps({"path": self.path, "echo": body.decode()}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-backend", "fake")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *_args) -> None:  # noqa: D102 - quiet
        pass


@pytest.fixture(scope="module")
def backend_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def start_provider(nursery, backend_url: str, authorized: list[str]):
    identity = Identity.generate()
    host = libp2p_host(identity)
    biscuit = mint(identity.peer_id, ROLE_NODE)
    registry = ServiceRegistry()
    registry.add(MCPService(name="calc", create_server=calc_server))
    registry.add(HTTPService(type="inference", name="llm", target=backend_url))

    async def inproc(request: HTTPRequest, caller) -> HTTPResponse:
        return HTTPResponse(status=201, body=f"hello {caller.peer_id} {request.method} {request.path}".encode())

    registry.add(HTTPService(type="inference", name="inproc", target=inproc))
    options = ProviderOptions(
        authorizer=ProviderAuthorizerOptions(trusted_keys=lambda: [CP_KEY], own_biscuit=lambda: biscuit, policy_rules=lambda: POLICY_RULES),
        own_biscuit=lambda: biscuit,
        on_authorized=lambda peer, _v, target: authorized.append(f"{peer} {target}"),
    )
    host.set_stream_handler(MCP_PROTOCOL, mcp_stream_handler(registry, options, str(MCP_PROTOCOL)))
    host.set_stream_handler(HTTP_PROTOCOL, http_ingress_handler(registry, options))
    started = trio.Event()
    addr_box = []

    async def run():
        async with host.run(listen_addrs=[multiaddr.Multiaddr("/ip4/127.0.0.1/tcp/0")]):
            addr_box.append(multiaddr.Multiaddr(f"{host.get_addrs()[0]}"))
            started.set()
            await trio.sleep_forever()

    nursery.start_soon(run)
    await started.wait()
    return host, addr_box[0]


def frame(biscuit: bytes, target: str) -> bytes:
    return pb.AuthFrame(biscuit=biscuit, target_service=target).SerializeToString()


def test_services_are_served_to_authorized_callers(backend_url):
    authorized: list[str] = []

    async def main():
        async with trio.open_nursery() as nursery:
            provider, addr = await start_provider(nursery, backend_url, authorized)
            caller_identity = Identity.generate()
            caller = libp2p_host(caller_identity)
            caller_biscuit = mint(caller_identity.peer_id, ROLE_NODE)
            guest_biscuit = mint(caller_identity.peer_id, "sam:role:guest")
            async with caller.run(listen_addrs=[]):
                await caller.connect(info_from_p2p_addr(addr))
                pid = provider.get_id()

                # An MCP service, then the member's own catalog.
                async with open_mcp_session(caller, pid, frame(caller_biscuit, "mcp://calc"), [CP_KEY]) as (mcp, verified):
                    assert verified.peer_id == str(pid)
                    assert [t.name for t in (await mcp.list_tools()).tools] == ["add"]
                    assert tool_call_result(await mcp.call_tool("add", {"a": 2, "b": 3})).text == ["5"]
                assert f"{caller_identity.peer_id} mcp://calc" in authorized
                async with open_mcp_session(caller, pid, frame(caller_biscuit, ""), [CP_KEY]) as (mcp, _):
                    listed = json.loads(tool_call_result(await mcp.call_tool("list_local_services", {})).text[0])
                    assert sorted(f"{s['type']}://{s['name']}" for s in listed) == ["inference://inproc", "inference://llm", "mcp://calc"]

                # A role that grants nothing is refused at the AuthResponse.
                with pytest.raises(AuthRejectedError, match="not authorized"):
                    async with open_mcp_session(caller, pid, frame(guest_biscuit, "mcp://calc"), [CP_KEY]):
                        pass
                # A service the role was not granted, likewise.
                with pytest.raises(AuthRejectedError):
                    async with open_mcp_session(caller, pid, frame(caller_biscuit, "mcp://secret"), [CP_KEY]):
                        pass
                # Granted but not registered: success, then the stream closes.
                with pytest.raises(Exception):  # noqa: B017 - the MCP initialize fails on the closed stream
                    async with open_mcp_session(caller, pid, frame(caller_biscuit, "mcp://echo"), [CP_KEY]):
                        pass

                # An inference service is proxied with the caller's identity and without its biscuit.
                res = await http_request_over_stream(caller, pid, caller_biscuit, "inference://llm", "/v1/models?x=1", headers={"x-sam-biscuit": "spoof"})
                assert res.status == 200
                assert res.headers.get("x-backend") == "fake"
                assert res.json() == {"path": "/v1/models?x=1", "echo": ""}
                assert FakeBackend.seen[-1]["peer"] == caller_identity.peer_id
                assert FakeBackend.seen[-1]["biscuit"] is None
                post = await http_request_over_stream(
                    caller, pid, caller_biscuit, "inference://llm", "/v1/chat/completions", method="POST", headers={"content-type": "application/json"}, body='{"model": "m"}'
                )
                assert post.status == 200 and post.json()["echo"] == '{"model": "m"}'

                # An in-process handler sees the verified caller.
                res = await http_request_over_stream(caller, pid, caller_biscuit, "inference://inproc", "/agent/card")
                assert res.status == 201 and res.text == f"hello {caller_identity.peer_id} GET /agent/card"

                # What the policy does not grant is refused before the backend is reached.
                before = len(FakeBackend.seen)
                assert (await http_request_over_stream(caller, pid, guest_biscuit, "inference://llm", "/v1/models")).status == 403
                assert (await http_request_over_stream(caller, pid, caller_biscuit, "a2a://llm", "/")).status == 403
                assert len(FakeBackend.seen) == before
                assert (await http_request_over_stream(caller, pid, caller_biscuit, "inference://nope", "/")).status == 404
                assert (await http_request_over_stream(caller, pid, b"", "inference://llm", "/v1/models")).status == 401
                assert (await http_request_over_stream(caller, pid, caller_biscuit, "inference://llm", "/../other/x")).status == 400
            nursery.cancel_scope.cancel()

    async def with_timeout():
        with trio.fail_after(30):
            await main()

    trio.run(with_timeout)
