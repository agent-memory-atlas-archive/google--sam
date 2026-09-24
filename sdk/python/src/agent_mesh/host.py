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

"""The libp2p host a member joins the mesh with, configured the way sam-node's
is (internal/node/node.go): TLS is the only security protocol and yamux the
muxer. py-libp2p is trio-based, so everything here is trio async."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Sequence

import multiaddr
import trio
from cryptography import x509
from cryptography.x509.oid import NameOID
from libp2p import new_host
from libp2p.abc import IHost
from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.custom_types import TProtocol
from libp2p.peer.peerinfo import PeerInfo, info_from_p2p_addr
from libp2p.security.tls.transport import PROTOCOL_ID as TLS_PROTOCOL_ID
from libp2p.security.tls.transport import IdentityConfig, TLSTransport
from libp2p.stream_muxer.yamux.yamux import PROTOCOL_ID as YAMUX_PROTOCOL_ID
from libp2p.stream_muxer.yamux.yamux import Yamux
from multiaddr.resolvers import DNSResolver

from .identity import Identity


def _certificate_template() -> x509.CertificateBuilder:
    """The libp2p TLS certificate with nothing but the libp2p extension.
    py-libp2p's default template also adds BasicConstraints and KeyUsage,
    which the spec allows, but js-libp2p (@libp2p/tls) reads the libp2p
    extension from `extensions[0]` and refuses the certificate otherwise."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "libp2p")])
    not_before = datetime.now(timezone.utc) - timedelta(hours=1)
    return (
        x509.CertificateBuilder()
        .serial_number(int.from_bytes(os.urandom(8), "big"))
        .not_valid_before(not_before)
        .not_valid_after(not_before + timedelta(days=365 * 100))
        .subject_name(name)
        .issuer_name(name)
    )


def create_mesh_host(identity: Identity, listen_addrs: Sequence[str] = ()) -> tuple[IHost, list[multiaddr.Multiaddr]]:
    """Builds the host for `identity`. Run it with `async with host.run(addrs)`.
    listen_addrs are for direct connections, e.g. "/ip4/0.0.0.0/tcp/0"; an
    agent is normally reached through a router's relay instead."""
    key_pair = create_new_key_pair(identity.seed)
    # No ALPN muxer list: py-libp2p 0.7 advertises early muxer negotiation
    # but does not complete it, and go-libp2p then refuses the mux upgrade.
    # Without it the muxer is negotiated with multistream-select as before.
    tls = TLSTransport(key_pair, identity_config=IdentityConfig(cert_template=_certificate_template()))
    host = new_host(
        key_pair=key_pair,
        sec_opt={TLS_PROTOCOL_ID: tls},
        muxer_opt={TProtocol(YAMUX_PROTOCOL_ID): Yamux},
    )
    if str(host.get_id()) != identity.peer_id:
        raise RuntimeError(f"libp2p derived peer {host.get_id()} for identity {identity.peer_id}")
    return host, [multiaddr.Multiaddr(a) for a in listen_addrs]


_DNS_PROTOCOLS = frozenset({"dnsaddr", "dns", "dns4", "dns6"})
# py-libp2p dials TCP only; a resolved address on another transport is noise.
_UNDIALABLE_PROTOCOLS = frozenset({"quic", "quic-v1", "ws", "wss", "webtransport", "webrtc", "webrtc-direct"})
_DNS_TIMEOUT = 10.0


async def dial_addrs(addr: multiaddr.Multiaddr) -> list[multiaddr.Multiaddr]:
    """The concrete addresses this host can dial for addr. A control plane
    hands out router addresses as `/dnsaddr/<host>/p2p/<id>`, resolved here
    through the host's `_dnsaddr` TXT records (and `/dns4`, `/dns6`, `/dns`
    through A and AAAA records) the way go-libp2p and js-libp2p do before
    dialing; py-libp2p does not, and would report no transport for them.
    Addresses on transports this host lacks are left out."""
    protocols = [p.name for p in addr.protocols()]
    if protocols and protocols[0] in _DNS_PROTOCOLS:
        resolved: list[multiaddr.Multiaddr] = []
        with trio.move_on_after(_DNS_TIMEOUT):
            resolved = list(await DNSResolver().resolve(addr))
        if not resolved:
            raise RuntimeError(f"{addr} resolved to no address")
    else:
        resolved = [addr]
    dialable: list[multiaddr.Multiaddr] = []
    for m in resolved:
        names = {p.name for p in m.protocols()}
        if "tcp" in names and not names & _UNDIALABLE_PROTOCOLS:
            dialable.append(m)
    if not dialable:
        raise RuntimeError(f"{addr} offers no TCP address; this host dials TCP only")
    return dialable


async def peer_info(addr: multiaddr.Multiaddr) -> PeerInfo:
    """The peer an address names and the concrete addresses to reach it on."""
    addrs = await dial_addrs(addr)
    return PeerInfo(info_from_p2p_addr(addrs[0]).peer_id, addrs)
