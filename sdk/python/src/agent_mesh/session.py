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

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncIterator, Mapping, Optional, Sequence

import multiaddr
import trio
from libp2p.abc import IHost
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import info_from_p2p_addr
from mcp import ClientSession

from ._proto import circuit_pb2 as circuit
from .auth import AUTH_PROTOCOL, MCP_PROTOCOL, auth_stream_handler, authenticate_with_peer
from .authorizer import ProviderAuthorizerOptions
from .biscuit import ROLE_ROUTER, VerifiedBiscuit, require_role
from .discovery import DiscoveredProvider, ServiceType, find_providers, provide, service_key
from .host import create_mesh_host
from .mcp_client import ToolCallResult, open_mcp_session, tool_call_result
from .relay import STOP_PROTOCOL, dial_through_relay, reserve_relay, split_circuit_address, stop_stream_handler
from .serve import (
    HTTP_PROTOCOL,
    HTTPResponse,
    ProviderOptions,
    ServiceRegistry,
    ServiceSpec,
    http_ingress_handler,
    http_request_over_stream,
    mcp_stream_handler,
)

if TYPE_CHECKING:
    from .mesh import AgentMesh

logger = logging.getLogger("agent_mesh")

DEFAULT_REFRESH_LEAD = 60 * 60.0
DEFAULT_REFRESH_RETRY = 30.0
MIN_REFRESH_DELAY = 2.0
# sam-node's --control-plane-sync-interval default.
DEFAULT_POLICY_SYNC = 15 * 60.0
# How often provider records are refreshed; go-libp2p-kad-dht expires them after 48h.
DEFAULT_PROVIDE_INTERVAL = 10 * 60.0


@dataclass(frozen=True)
class AdmittedRouter:
    peer_id: str
    addr: multiaddr.Multiaddr
    credential: VerifiedBiscuit
    reservation: circuit.Reservation | None = None


@dataclass
class MeshSession:
    """A member that is on the mesh: a libp2p host authenticated with at least
    one router, answering the auth handshake for peers that dial it, and
    keeping its credential fresh for as long as the `join` block is open."""

    mesh: "AgentMesh"
    host: IHost
    routers: list[AdmittedRouter]
    # Peers that passed the inbound auth handshake, with their credential's expiration.
    authenticated_peers: dict[str, datetime] = field(default_factory=dict)
    # The services this member publishes.
    services: ServiceRegistry = field(default_factory=ServiceRegistry)
    policy_sync_interval: float = DEFAULT_POLICY_SYNC
    provide_interval: float = DEFAULT_PROVIDE_INTERVAL
    _nursery: Optional[trio.Nursery] = field(default=None, repr=False)
    _policy_rules: Optional[list[str]] = field(default=None, repr=False)
    _serving: bool = field(default=False, repr=False)

    @property
    def peer_id(self) -> str:
        return str(self.host.get_id())

    @property
    def relay_addresses(self) -> list[str]:
        """The `.../p2p-circuit/p2p/<self>` addresses reserved on routers."""
        out = []
        for r in self.routers:
            if r.reservation is None:
                continue
            # A relay lists the addresses it wants advertised; go-libp2p keeps
            # private ones out, so a router on loopback lists none and the
            # address we reached it on is the one that works.
            relay_addrs = [multiaddr.Multiaddr(raw) for raw in r.reservation.addrs] or [r.addr]
            for ma in relay_addrs:
                text = str(ma)
                if f"/p2p/{r.peer_id}" not in text:
                    text = f"{text}/p2p/{r.peer_id}"
                out.append(f"{text}/p2p-circuit/p2p/{self.peer_id}")
        return out

    async def connect(self, addr: str | multiaddr.Multiaddr) -> ID:
        """Connects to a peer by address, through a relay when the address says
        `/p2p-circuit`, and returns its peer ID."""
        ma = multiaddr.Multiaddr(str(addr))
        if "/p2p-circuit" in str(ma):
            relay_addr, target = split_circuit_address(ma)
            relay = info_from_p2p_addr(relay_addr)
            if relay.peer_id not in self.host.get_connected_peers():
                await self.host.connect(relay)
            if target not in self.host.get_connected_peers():
                await dial_through_relay(self.host, relay.peer_id, target)
            return target
        info = info_from_p2p_addr(ma)
        await self.host.connect(info)
        return info.peer_id

    async def authenticate(self, addr: str | multiaddr.Multiaddr) -> VerifiedBiscuit:
        """Connects to a peer and runs the mutual auth handshake, returning the
        peer's verified credential."""
        peer_id = await self.connect(addr)
        return await authenticate_with_peer(self.host, peer_id, self.mesh.auth_frame(), self.mesh.credential.control_plane_keys)

    async def discover(self, service_type: ServiceType, name: str | None = None, limit: int = 20) -> list[DiscoveredProvider]:
        """Looks the DHT up for peers offering a service, by type and name, or by
        type alone. The routers we are connected to seed the walk."""
        seeds = [ID.from_base58(r.peer_id) for r in self.routers]
        return await find_providers(self.host, service_key(service_type, name), seeds, limit)

    def open_mcp(
        self,
        addr: str | multiaddr.Multiaddr,
        target_service: str,
        *,
        required_labels: Optional[Mapping[str, str]] = None,
        agent: str = "",
    ):  # type: ignore[no-untyped-def]
        """Opens an MCP session with the provider at addr for target_service
        ("mcp://<name>", or "" for the provider's own catalog tools):

            async with session.open_mcp(addr, "mcp://calc") as (mcp, provider): ...
        """

        @asynccontextmanager
        async def opened() -> AsyncIterator[tuple[ClientSession, VerifiedBiscuit]]:
            peer_id = await self.connect(addr)
            frame = self.mesh.auth_frame(target_service, agent)
            async with open_mcp_session(self.host, peer_id, frame, self.mesh.credential.control_plane_keys, required_labels=required_labels) as opened_session:
                yield opened_session

        return opened()

    async def list_tools(self, addr: str | multiaddr.Multiaddr, target_service: str, **options: Any) -> list[str]:
        """Lists the tools a provider serves for a service."""
        async with self.open_mcp(addr, target_service, **options) as (mcp, _):
            return [t.name for t in (await mcp.list_tools()).tools]

    async def call_tool(
        self, addr: str | multiaddr.Multiaddr, target_service: str, tool: str, args: Optional[Mapping[str, Any]] = None, **options: Any
    ) -> ToolCallResult:
        """Calls one tool on a provider's service."""
        async with self.open_mcp(addr, target_service, **options) as (mcp, _):
            result = await mcp.call_tool(tool, dict(args or {}))
            if not hasattr(result, "content"):
                raise RuntimeError(f"tool {tool} answered with {type(result).__name__}, not a result")
            return tool_call_result(result)  # type: ignore[arg-type]

    @property
    def policy_rules(self) -> list[str]:
        """The mesh policy rules this member evaluates for callers, as the
        control plane rendered them (PolicyConfigGetResponse.datalog_rules).
        Empty until the first serve() or sync_policy()."""
        return list(self._policy_rules or [])

    async def sync_policy(self) -> None:
        """Re-reads the mesh policy from the control plane."""
        self._policy_rules = await trio.to_thread.run_sync(self.mesh.control_plane.policy_rules, self.mesh.credential.biscuit)

    async def request(
        self,
        addr: str | multiaddr.Multiaddr,
        target_service: str,
        path: str,
        *,
        method: str = "GET",
        headers: Optional[Mapping[str, str]] = None,
        body: bytes | str | None = None,
        agent: str = "",
    ) -> HTTPResponse:
        """Calls an inference or A2A service on a provider over /libp2p-http,
        the way sam-node's egress proxy does for /sam/<peer>/<type>/<name>/<path>."""
        peer_id = await self.connect(addr)
        return await http_request_over_stream(
            self.host, peer_id, self.mesh.credential.biscuit, target_service, path, method=method, headers=headers, body=body, agent=agent
        )

    async def serve(self, spec: ServiceSpec) -> None:
        """Publishes a service on the mesh: registers it, announces it in the
        DHT and reports it to the control plane's catalog. The first call
        fetches the mesh policy and starts answering /sam/mcp/1.0.0 and
        /libp2p-http; a policy that cannot be read fails the call, since a
        provider without it could only authorize what callers carry in their
        own tokens."""
        if not self._serving:
            await self.sync_policy()
            options = ProviderOptions(
                authorizer=ProviderAuthorizerOptions(
                    trusted_keys=lambda: self.mesh.credential.control_plane_keys,
                    own_biscuit=lambda: self.mesh.credential.biscuit,
                    policy_rules=lambda: self._policy_rules or [],
                ),
                own_biscuit=lambda: self.mesh.credential.biscuit,
                on_authorized=lambda peer, verified, _target: self.authenticated_peers.__setitem__(peer, verified.expiration),
            )
            self.host.set_stream_handler(MCP_PROTOCOL, mcp_stream_handler(self.services, options, str(MCP_PROTOCOL)))
            self.host.set_stream_handler(HTTP_PROTOCOL, http_ingress_handler(self.services, options))
            if self._nursery is not None:
                self._nursery.start_soon(self._policy_loop)
                self._nursery.start_soon(self._provide_loop)
            self._serving = True
        self.services.add(spec)
        await self._provide(spec.type, spec.name)
        try:
            await self.report_catalog()
        except Exception as err:  # noqa: BLE001 - the catalog is display only
            logger.warning("catalog report failed: %s", err)

    async def provide_all(self) -> None:
        """Announces every registered service in the DHT again."""
        for service_type, name, _ in self.services.list():
            await self._provide(service_type, name)  # type: ignore[arg-type]

    async def _provide(self, service_type: ServiceType, name: str) -> None:
        # Once for the type and once for the name, as sam-node announces.
        seeds = [ID.from_base58(r.peer_id) for r in self.routers]
        addrs = [multiaddr.Multiaddr(a) for a in self.relay_addresses] + list(self.host.get_addrs())
        for key in (service_key(service_type), service_key(service_type, name)):
            await provide(self.host, key, seeds, addrs)

    async def report_catalog(self) -> None:
        """Reports the published services to the control plane's catalog (display only)."""
        await trio.to_thread.run_sync(self.mesh.control_plane.report_catalog, self.mesh.credential.biscuit, self.services.list())

    async def _policy_loop(self) -> None:
        while True:
            await trio.sleep(self.policy_sync_interval)
            try:
                await self.sync_policy()
            except Exception as err:  # noqa: BLE001 - the last good policy stays in force
                logger.warning("mesh policy sync failed: %s", err)

    async def _provide_loop(self) -> None:
        while True:
            await trio.sleep(self.provide_interval)
            try:
                await self.provide_all()
            except Exception as err:  # noqa: BLE001
                logger.warning("dht reprovide failed: %s", err)


async def _refresh_loop(mesh: "AgentMesh", lead: float, retry: float) -> None:
    while True:
        due = mesh.credential.expiration - lead - time.time()
        await trio.sleep(max(MIN_REFRESH_DELAY, due))
        try:
            # The control plane client is synchronous; keep the loop free.
            await trio.to_thread.run_sync(mesh.refresh)
        except Exception as err:  # noqa: BLE001 - a failed refresh is retried, the session stays up
            logger.warning("credential refresh failed, retrying in %.0fs: %s", retry, err)
            await trio.sleep(retry)


def _single_cause(group: BaseException) -> BaseException:
    """trio wraps a failure inside `host.run` in one ExceptionGroup per nursery.
    A join that failed for one reason should raise that reason."""
    while isinstance(group, BaseExceptionGroup) and len(group.exceptions) == 1:
        group = group.exceptions[0]
    return group


@asynccontextmanager
async def join_mesh(
    mesh: "AgentMesh",
    *,
    listen_addrs: Sequence[str] = (),
    reserve: bool = True,
    refresh_lead: float = DEFAULT_REFRESH_LEAD,
    refresh_retry: float = DEFAULT_REFRESH_RETRY,
    policy_sync_interval: float = DEFAULT_POLICY_SYNC,
    provide_interval: float = DEFAULT_PROVIDE_INTERVAL,
) -> AsyncIterator[MeshSession]:
    """Implements AgentMesh.join(); lives here to keep mesh.py free of libp2p."""
    router_addrs = [multiaddr.Multiaddr(a) for a in mesh.credential.router_addresses]
    if not router_addrs:
        raise RuntimeError("credential lists no router addresses; the control plane had no active router at enrollment")

    host, listen = create_mesh_host(mesh.identity, listen_addrs)
    authenticated: dict[str, datetime] = {}
    host.set_stream_handler(
        AUTH_PROTOCOL,
        auth_stream_handler(
            own_biscuit=lambda: mesh.credential.biscuit,
            trusted_keys=lambda: mesh.credential.control_plane_keys,
            on_authenticated=lambda peer, verified: authenticated.__setitem__(peer, verified.expiration),
        ),
    )
    host.set_stream_handler(STOP_PROTOCOL, stop_stream_handler(host))

    try:
        async with host.run(listen_addrs=listen):
            admitted = await _admit(host, mesh, router_addrs, reserve)
            async with trio.open_nursery() as nursery:
                nursery.start_soon(_refresh_loop, mesh, refresh_lead, refresh_retry)
                try:
                    yield MeshSession(
                        mesh=mesh,
                        host=host,
                        routers=admitted,
                        authenticated_peers=authenticated,
                        policy_sync_interval=policy_sync_interval,
                        provide_interval=provide_interval,
                        _nursery=nursery,
                    )
                finally:
                    nursery.cancel_scope.cancel()
    except BaseExceptionGroup as group:
        cause = _single_cause(group)
        if cause is group:
            raise
        raise cause from None


async def _admit(host: IHost, mesh: "AgentMesh", router_addrs: list[multiaddr.Multiaddr], reserve: bool) -> list[AdmittedRouter]:
    admitted: list[AdmittedRouter] = []
    failures: list[str] = []
    for addr in router_addrs:
        try:
            info = info_from_p2p_addr(addr)
            await host.connect(info)
            credential = await authenticate_with_peer(host, info.peer_id, mesh.auth_frame(), mesh.credential.control_plane_keys)
            # Enforced under the key that verified the token; a relay that
            # is not a router must not become our way onto the mesh.
            require_role(credential, ROLE_ROUTER)
            reservation = await reserve_relay(host, info.peer_id) if reserve else None
            admitted.append(AdmittedRouter(peer_id=str(info.peer_id), addr=addr, credential=credential, reservation=reservation))
            if reserve:
                break
        except Exception as err:  # noqa: BLE001 - every router is tried, the summary names each failure
            failures.append(f"{addr}: {err}")
    if not admitted:
        raise RuntimeError("no router admitted this member:\n  " + "\n  ".join(failures))
    return admitted
