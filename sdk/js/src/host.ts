// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// The libp2p host a member joins the mesh with, configured the way
// sam-node's is (internal/node/node.go): TLS is the only security
// protocol, yamux the muxer, circuit relay v2 for reachability through
// the routers.

import { yamux } from "@chainsafe/libp2p-yamux";
import { circuitRelayTransport } from "@libp2p/circuit-relay-v2";
import { privateKeyFromProtobuf } from "@libp2p/crypto/keys";
import { identify } from "@libp2p/identify";
import type { Libp2p } from "@libp2p/interface";
import { tcp } from "@libp2p/tcp";
import { tls } from "@libp2p/tls";
import type { Multiaddr } from "@multiformats/multiaddr";
import { createLibp2p } from "libp2p";
import type { Identity } from "./identity.ts";

export interface MeshHostOptions {
  /**
   * Addresses to accept direct connections on, e.g. "/ip4/0.0.0.0/tcp/0".
   * Empty by default: an agent is reached through a router's relay.
   */
  listenAddrs?: string[];
}

export async function createMeshHost(identity: Identity, options: MeshHostOptions = {}): Promise<Libp2p> {
  return createLibp2p({
    privateKey: privateKeyFromProtobuf(identity.toLibp2pPrivateKey()),
    addresses: { listen: options.listenAddrs ?? [] },
    transports: [tcp(), circuitRelayTransport()],
    connectionEncrypters: [tls()],
    streamMuxers: [yamux()],
    services: { identify: identify() },
  });
}

/**
 * Starts listening on a relay after the caller has authenticated with it.
 * js-libp2p reserves a relay slot when it starts listening on
 * `<relay>/p2p-circuit`; a router refuses that until the peer has passed
 * the auth handshake, so the listen cannot be part of the host's config.
 * The transport manager is not on the public Libp2p interface.
 */
export async function listenThroughRelay(node: Libp2p, relayAddr: Multiaddr): Promise<void> {
  const internals = node as unknown as { components: { transportManager: { listen(addrs: Multiaddr[]): Promise<void> } } };
  await internals.components.transportManager.listen([relayAddr.encapsulate("/p2p-circuit")]);
}
