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

"""The provider side: /sam/mcp/1.0.0 for MCP services and /libp2p-http for
inference and A2A services, both gated by the authorizer the way sam-node
gates them (WithBiscuitAuth and StartIngressServer in internal/node)."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Mapping, Optional, Sequence, Union

import anyio
import h11
import httpx
import mcp_types
import trio
from libp2p.abc import IHost, INetStream
from libp2p.custom_types import TProtocol
from libp2p.peer.id import ID
from libp2p.utils.varint import encode_varint_prefixed, read_varint_prefixed_bytes
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from ._proto import sam_pb2 as pb
from .auth import AUTH_HANDSHAKE_TIMEOUT, MAX_AUTH_FRAME_BYTES
from .authorizer import AuthorizationError, AuthorizeRequest, ProviderAuthorizerOptions, authorize_caller
from .biscuit import VerifiedBiscuit
from .discovery import ServiceType
from .mcp_client import MAX_MCP_MESSAGE_BYTES

logger = logging.getLogger("agent_mesh")

# go-libp2p-http's protocol: plain HTTP/1.1 on a stream, one request per stream.
HTTP_PROTOCOL = TProtocol("/libp2p-http")

# Headers of the mesh HTTP datapath (api/network.go).
HEADER_SAM_BISCUIT = "x-sam-biscuit"
HEADER_SAM_AGENT = "x-sam-agent"
HEADER_PEER_ID = "x-peer-id"
HEADER_SAM_NO_TRAILING_SLASH = "x-sam-no-trailing-slash"

_MAX_INGRESS_BODY_BYTES = 8 * 1024 * 1024
_READ_CHUNK = 64 * 1024
_REQUEST_TIMEOUT = 60.0


@dataclass(frozen=True)
class HTTPRequest:
    """What an in-process HTTP handler receives: the request as it reached
    the service, path relative to /<type>/<name>."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):  # type: ignore[no-untyped-def]
        return json.loads(self.body)


HTTPHandler = Callable[[HTTPRequest, VerifiedBiscuit], Awaitable[HTTPResponse]]


@dataclass(frozen=True)
class MCPService:
    """An MCP service: one server per session, since an MCP server run holds one transport."""

    name: str
    create_server: Callable[[], MCPServer]
    description: str = ""
    type: Literal["mcp"] = "mcp"


@dataclass(frozen=True)
class HTTPService:
    """An inference or A2A service: authorized requests are forwarded to
    target, a base URL of a local backend or a handler in this process. The
    biscuit and agent headers are stripped and X-Peer-Id names the verified
    caller, as sam-node does."""

    type: Literal["inference", "a2a"]
    name: str
    target: Union[str, HTTPHandler]
    description: str = ""


ServiceSpec = Union[MCPService, HTTPService]


@dataclass(frozen=True)
class ProviderOptions:
    authorizer: ProviderAuthorizerOptions
    # This provider's current biscuit, for the mutual AuthResponse.
    own_biscuit: Callable[[], bytes]
    # Called after a caller is authorized for a service, e.g. for the session's admitted set.
    on_authorized: Optional[Callable[[str, VerifiedBiscuit, str], None]] = None
    # Peers the control plane has banned; refused before their token is looked at.
    is_banned: Optional[Callable[[str], bool]] = None


class ServiceRegistry:
    """Services registered on a member, keyed by type and name."""

    def __init__(self) -> None:
        self._services: dict[str, ServiceSpec] = {}

    @staticmethod
    def key(service_type: str, name: str) -> str:
        return f"{service_type}://{name}"

    def add(self, spec: ServiceSpec) -> None:
        key = self.key(spec.type, spec.name)
        if key in self._services:
            raise ValueError(f"service {key} is already registered")
        self._services[key] = spec

    def remove(self, service_type: ServiceType, name: str) -> bool:
        return self._services.pop(self.key(service_type, name), None) is not None

    def get(self, service_type: str, name: str) -> Optional[ServiceSpec]:
        return self._services.get(self.key(service_type, name))

    def list(self) -> list[tuple[str, str, str]]:
        """(type, name, description) of every registered service."""
        return [(s.type, s.name, s.description) for s in self._services.values()]


def _catalog_server(registry: ServiceRegistry) -> MCPServer:
    """The member's own catalog over MCP, as sam-node's list_local_services."""
    server = MCPServer("agent-mesh-sdk")

    @server.tool(description="List the services this member publishes on the mesh")
    def list_local_services() -> str:
        return json.dumps([{"type": t, "name": n, "description": d} for t, n, d in registry.list()])

    return server


async def _refuse(stream: INetStream, peer_id: str, reason: str) -> None:
    logger.info("refusing %s: %s", peer_id, reason)
    try:
        with trio.fail_after(AUTH_HANDSHAKE_TIMEOUT):
            await stream.write(encode_varint_prefixed(pb.AuthResponse(success=False, error=reason).SerializeToString()))
    except Exception:  # noqa: BLE001 - the caller may already be gone
        pass


def mcp_stream_handler(registry: ServiceRegistry, options: ProviderOptions, protocol: str):  # type: ignore[no-untyped-def]
    """Server side of /sam/mcp/1.0.0, as sam-node's WithBiscuitAuth(HandleMCPStream):
    read the AuthFrame, authorize the caller for the named service, answer with
    our own credential, then hand the stream to that service's MCP server. A
    denied caller gets AuthResponse{success: false}; an unknown service closes
    the stream after a successful answer, which is what sam-node does."""

    async def handle(stream: INetStream) -> None:
        peer_id = str(stream.muxed_conn.peer_id)
        try:
            with trio.fail_after(AUTH_HANDSHAKE_TIMEOUT):
                data = await read_varint_prefixed_bytes(stream)
            if len(data) > MAX_AUTH_FRAME_BYTES:
                await stream.close()
                return
            frame = pb.AuthFrame.FromString(data)
            if options.is_banned is not None and options.is_banned(peer_id):
                await _refuse(stream, peer_id, "peer is revoked")
                await stream.close()
                return
            try:
                verified = await trio.to_thread.run_sync(
                    authorize_caller,
                    AuthorizeRequest(biscuit=frame.biscuit, peer_id=peer_id, target_service=frame.target_service, protocol=protocol, agent=frame.agent),
                    options.authorizer,
                )
            except AuthorizationError as err:
                await _refuse(stream, peer_id, str(err))
                await stream.close()
                return
            if options.on_authorized:
                options.on_authorized(peer_id, verified, frame.target_service)
            with trio.fail_after(AUTH_HANDSHAKE_TIMEOUT):
                await stream.write(encode_varint_prefixed(pb.AuthResponse(success=True, biscuit=options.own_biscuit()).SerializeToString()))

            if frame.target_service == "":
                server = _catalog_server(registry)
            else:
                spec = registry.get("mcp", frame.target_service.removeprefix("mcp://")) if frame.target_service.startswith("mcp://") else None
                if not isinstance(spec, MCPService):
                    logger.info("no MCP service %s for %s", frame.target_service, peer_id)
                    await stream.close()
                    return
                server = spec.create_server()
            await _serve_mcp(stream, server)
        except Exception as err:  # noqa: BLE001 - one caller's failure must not take the handler down
            logger.debug("mcp stream from %s ended: %s", peer_id, err)
            await stream.close()

    return handle


async def _serve_mcp(stream: INetStream, server: MCPServer) -> None:
    """Runs one MCP server over the stream until the caller goes away."""
    low = server._lowlevel_server  # noqa: SLF001 - the only way to run it over custom streams
    read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def pump_in() -> None:
        try:
            async with read_writer:
                while True:
                    data = await read_varint_prefixed_bytes(stream)
                    if len(data) > MAX_MCP_MESSAGE_BYTES:
                        raise ValueError("oversized MCP frame")
                    try:
                        message = mcp_types.jsonrpc_message_adapter.validate_json(data, by_name=False)
                    except ValueError as exc:
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))
        except Exception:  # noqa: BLE001 - the caller went away
            pass

    async def pump_out() -> None:
        try:
            async with write_reader:
                async for message in write_reader:
                    await stream.write(encode_varint_prefixed(message.message.model_dump_json(by_alias=True, exclude_unset=True).encode()))
        except Exception:  # noqa: BLE001
            pass

    async with trio.open_nursery() as nursery:
        nursery.start_soon(pump_in)
        nursery.start_soon(pump_out)
        try:
            await low.run(read_stream, write_stream, low.create_initialization_options())
        except Exception:  # noqa: BLE001 - the session ended with the stream
            pass
        finally:
            await stream.close()
            nursery.cancel_scope.cancel()


def _has_dot_segment(path: str) -> bool:
    return any(seg in (".", "..") for seg in path.split("/"))


async def _read_http_request(stream: INetStream, conn: h11.Connection, limit: int) -> tuple[h11.Request, bytes]:
    request: Optional[h11.Request] = None
    body = bytearray()
    while True:
        event = conn.next_event()
        if event is h11.NEED_DATA:
            try:
                data = await stream.read(_READ_CHUNK)
            except Exception:  # noqa: BLE001 - EOF or reset: the parser sees end of input
                data = b""
            conn.receive_data(data)
            continue
        if isinstance(event, h11.Request):
            request = event
        elif isinstance(event, h11.Data):
            body.extend(event.data)
            if len(body) > limit:
                raise ValueError(f"body exceeds {limit} bytes")
        elif isinstance(event, h11.EndOfMessage):
            if request is None:
                raise ValueError("end of message before request")
            return request, bytes(body)
        elif isinstance(event, (h11.ConnectionClosed, h11.PAUSED)):
            raise ValueError("connection closed before a request")


async def _write_http_response(stream: INetStream, conn: h11.Connection, status: int, headers: Mapping[str, str], body: bytes) -> None:
    out = [(k.encode(), v.encode()) for k, v in headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection")]
    out.append((b"content-length", str(len(body)).encode()))
    out.append((b"connection", b"close"))
    data = conn.send(h11.Response(status_code=status, headers=out)) or b""
    data += conn.send(h11.Data(data=body)) or b""
    data += conn.send(h11.EndOfMessage()) or b""
    await stream.write(data)


def http_ingress_handler(registry: ServiceRegistry, options: ProviderOptions):  # type: ignore[no-untyped-def]
    """Server side of /libp2p-http, as sam-node's StartIngressServer: the path
    is /<type>/<name>[/<upstream>], the caller's biscuit is X-Sam-Biscuit, and
    the request is authorized for <type>://<name> before anything is forwarded."""

    async def handle(stream: INetStream) -> None:
        peer_id = str(stream.muxed_conn.peer_id)
        conn = h11.Connection(h11.SERVER)

        async def reply(status: int, text: str) -> None:
            await _write_http_response(stream, conn, status, {"content-type": "text/plain; charset=utf-8"}, (text + "\n").encode())

        try:
            try:
                with trio.fail_after(AUTH_HANDSHAKE_TIMEOUT):
                    request, body = await _read_http_request(stream, conn, _MAX_INGRESS_BODY_BYTES)
            except (ValueError, h11.RemoteProtocolError, trio.TooSlowError) as err:
                logger.debug("http ingress from %s: %s", peer_id, err)
                return
            status, headers, out = await _handle_ingress(request, body, peer_id, registry, options)
            await _write_http_response(stream, conn, status, headers, out)
        except Exception as err:  # noqa: BLE001 - one caller's failure must not take the handler down
            logger.debug("http ingress from %s failed: %s", peer_id, err)
            try:
                await reply(500, f"ingress error: {err}")
            except Exception:  # noqa: BLE001
                pass
        finally:
            await stream.close()

    return handle


async def _handle_ingress(
    request: h11.Request, body: bytes, peer_id: str, registry: ServiceRegistry, options: ProviderOptions
) -> tuple[int, dict[str, str], bytes]:
    def plain(status: int, text: str) -> tuple[int, dict[str, str], bytes]:
        return status, {"content-type": "text/plain; charset=utf-8"}, (text + "\n").encode()

    target = request.target.decode("latin-1")
    raw_path, _, query = target.partition("?")
    # Policy is decided on the /<type>/<name> prefix; a dot segment in what
    # follows could resolve to a sibling service on a shared backend.
    if _has_dot_segment(raw_path):
        return plain(400, "Invalid path")
    parts = raw_path.lstrip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return plain(400, "Invalid path")
    service_type, service_name, rest = parts[0], parts[1], parts[2:]
    if service_type not in ("inference", "a2a", "mcp"):
        return plain(400, "Invalid service type")
    upstream_path = "/".join(rest)

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in request.headers}
    biscuit_b64 = headers.get(HEADER_SAM_BISCUIT, "")
    if not biscuit_b64:
        return plain(401, "Missing X-Sam-Biscuit header")
    try:
        biscuit = base64.b64decode(biscuit_b64, validate=True)
        if not biscuit:
            raise ValueError("empty")
    except (ValueError, TypeError):
        return plain(400, "Invalid X-Sam-Biscuit encoding")

    target_service = f"{service_type}://{service_name}"
    if options.is_banned is not None and options.is_banned(peer_id):
        return plain(403, "Authorization failed")
    try:
        verified = await trio.to_thread.run_sync(
            authorize_caller,
            AuthorizeRequest(
                biscuit=biscuit,
                peer_id=peer_id,
                target_service=target_service,
                protocol=str(HTTP_PROTOCOL),
                agent=headers.get(HEADER_SAM_AGENT, ""),
            ),
            options.authorizer,
        )
    except AuthorizationError as err:
        logger.info("http ingress denied %s: %s", peer_id, err)
        return plain(403, "Authorization failed")
    if options.on_authorized:
        options.on_authorized(peer_id, verified, target_service)

    # Under the type the policy was evaluated on.
    spec = registry.get(service_type, service_name)
    if not isinstance(spec, HTTPService):
        return plain(404, "Service not found")

    # The biscuit and the agent are for policy, not for the backend; X-Peer-Id
    # is set, not added, so an inbound value cannot pose as the verified peer.
    forwarded = {
        k: v
        for k, v in headers.items()
        if k not in (HEADER_SAM_BISCUIT, HEADER_SAM_AGENT, HEADER_SAM_NO_TRAILING_SLASH, HEADER_PEER_ID, "host", "connection", "transfer-encoding", "content-length")
    }
    forwarded[HEADER_PEER_ID] = peer_id
    if upstream_path == "" and not rest:
        forwarded[HEADER_SAM_NO_TRAILING_SLASH] = "true"
    method = request.method.decode()
    path = "/" + upstream_path + (f"?{query}" if query else "")

    if callable(spec.target):
        response = await spec.target(HTTPRequest(method=method, path=path, headers=forwarded, body=body), verified)
        return response.status, dict(response.headers), response.body

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=False) as client:
        upstream = await client.request(method, spec.target.rstrip("/") + path, headers=forwarded, content=body or None)
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection")}
    return upstream.status_code, out_headers, upstream.content


async def http_request_over_stream(
    host: IHost,
    peer_id: ID,
    biscuit: bytes,
    target_service: str,
    path: str,
    *,
    method: str = "GET",
    headers: Optional[Mapping[str, str]] = None,
    body: Union[bytes, str, None] = None,
    agent: str = "",
    timeout: float = _REQUEST_TIMEOUT,
) -> HTTPResponse:
    """Client side of /libp2p-http, as go-libp2p-http's RoundTripper: one
    stream per request, plain HTTP/1.1 with Host set to the peer ID, the
    biscuit in X-Sam-Biscuit and the path /<type>/<name>/<path>."""
    scheme, sep, name = target_service.partition("://")
    if not sep or scheme not in ("inference", "a2a", "mcp") or not name:
        raise ValueError(f"service target must look like inference://<name>, got {target_service!r}")
    payload = body.encode() if isinstance(body, str) else (body or b"")
    out = [(k.lower(), v) for k, v in (headers or {}).items() if k.lower() not in ("host", "content-length")]
    out.append(("host", str(peer_id)))
    out.append((HEADER_SAM_BISCUIT, base64.b64encode(biscuit).decode()))
    if agent:
        out.append((HEADER_SAM_AGENT, agent))
    out.append(("content-length", str(len(payload))))

    conn = h11.Connection(h11.CLIENT)
    stream = await host.new_stream(peer_id, [HTTP_PROTOCOL])
    try:
        with trio.fail_after(timeout):
            data = conn.send(h11.Request(method=method, target=f"/{scheme}/{name}" + (path if path.startswith("/") else "/" + path), headers=out)) or b""
            data += conn.send(h11.Data(data=payload)) or b""
            data += conn.send(h11.EndOfMessage()) or b""
            await stream.write(data)

            status = 0
            resp_headers: dict[str, str] = {}
            resp_body = bytearray()
            while True:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    try:
                        chunk = await stream.read(_READ_CHUNK)
                    except Exception:  # noqa: BLE001 - EOF ends the response
                        chunk = b""
                    conn.receive_data(chunk)
                    continue
                if isinstance(event, (h11.Response, h11.InformationalResponse)):
                    if isinstance(event, h11.Response):
                        status = event.status_code
                        resp_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in event.headers}
                elif isinstance(event, h11.Data):
                    resp_body.extend(event.data)
                    if len(resp_body) > _MAX_INGRESS_BODY_BYTES:
                        raise ValueError("response body too large")
                elif isinstance(event, h11.EndOfMessage):
                    return HTTPResponse(status=status, headers=resp_headers, body=bytes(resp_body))
                elif isinstance(event, (h11.ConnectionClosed, h11.PAUSED)):
                    if status:
                        return HTTPResponse(status=status, headers=resp_headers, body=bytes(resp_body))
                    raise ConnectionError(f"peer {peer_id} closed the stream before answering")
    finally:
        await stream.close()


__all__: Sequence[str] = (
    "HTTP_PROTOCOL",
    "HTTPHandler",
    "HTTPRequest",
    "HTTPResponse",
    "HTTPService",
    "MCPService",
    "ProviderOptions",
    "ServiceRegistry",
    "http_ingress_handler",
    "http_request_over_stream",
    "mcp_stream_handler",
)
