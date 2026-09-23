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
from cryptography import x509
from cryptography.x509.oid import NameOID
from libp2p import new_host
from libp2p.abc import IHost
from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.custom_types import TProtocol
from libp2p.security.tls.transport import PROTOCOL_ID as TLS_PROTOCOL_ID
from libp2p.security.tls.transport import IdentityConfig, TLSTransport
from libp2p.stream_muxer.yamux.yamux import PROTOCOL_ID as YAMUX_PROTOCOL_ID
from libp2p.stream_muxer.yamux.yamux import Yamux

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
