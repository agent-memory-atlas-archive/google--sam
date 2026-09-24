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

import type { Connection } from "@libp2p/interface";
import { timestampMs } from "@bufbuild/protobuf/wkt";
import { TopicValidatorResult } from "@libp2p/gossipsub";
import { peerIdFromString } from "@libp2p/peer-id";
import { isMultiaddr, multiaddr, type Multiaddr } from "@multiformats/multiaddr";
import { AUTH_HANDLER_OPTIONS, AUTH_PROTOCOL, MCP_PROTOCOL, authenticateWithPeer, authStreamHandler } from "./auth.ts";
import { ROLE_ROUTER, requireRole, type VerifiedBiscuit } from "./biscuit.ts";
import { isServiceType, parseServiceTarget, serviceCID, type ServiceType } from "./discovery.ts";
import { createMeshHost, listenThroughRelay, type MeshHost, type MeshHostOptions } from "./host.ts";
import { openMCPSession, type MCPSession, type MCPSessionOptions } from "./mcp.ts";
import type { AgentMesh, ControlPlaneSync } from "./mesh.ts";
import {
  HTTP_PROTOCOL,
  SERVE_HANDLER_OPTIONS,
  ServiceRegistry,
  httpIngressHandler,
  httpRequestOverStream,
  mcpStreamHandler,
  type HTTPRequestOptions,
  type HTTPResponse,
  type ProviderOptions,
  type ServedService,
  type ServiceSpec,
} from "./serve.ts";
import { BanSet, GOSSIP_EVENTS_TOPIC, MeshEvent_Type, verifyMeshEvent } from "./sync.ts";

export interface JoinOptions extends MeshHostOptions {
  /**
   * Reserve a relay slot on the first router that admits us, so peers can
   * reach this member through the router. On by default; a member that
   * only calls out can turn it off.
   */
  reserveRelay?: boolean;
  /** Refresh the credential this long before it expires. */
  refreshLeadMs?: number;
  /** Retry a failed refresh after this long. */
  refreshRetryMs?: number;
  /** How often the mesh policy is re-read from the control plane while serving. */
  policySyncIntervalMs?: number;
  /** How often published services are re-announced in the DHT. */
  provideIntervalMs?: number;
  /**
   * How often keys, bans and router addresses are pulled from the control
   * plane (sam-node's --control-plane-sync-interval). Gossip events bring a
   * pull forward; 0 disables the loop.
   */
  controlPlaneSyncIntervalMs?: number;
  /** Upper bound of the random delay before a pull an event triggered. */
  controlPlaneSyncJitterMs?: number;
  /** Bounds the whole join. */
  signal?: AbortSignal;
}

export interface AdmittedRouter {
  peerId: string;
  addr: Multiaddr;
  credential: VerifiedBiscuit;
}

/** A peer the DHT names as offering a service. */
export interface DiscoveredProvider {
  peerId: string;
  /** Addresses the provider advertised; may be empty when the record carried none. */
  addrs: string[];
}

/**
 * How a caller names the peer it wants to reach: a provider `discover`
 * returned, a peer id, or a multiaddr. For a provider or a peer id the SDK
 * dials the addresses the peer advertised and the relayed path through
 * every router that admitted this member, so the caller never assembles a
 * `/p2p-circuit` address. A multiaddr is dialed as given.
 */
export type Peer = DiscoveredProvider | string | Multiaddr;

/** A tool call's outcome, as MCP reports it. */
export interface ToolCallResult {
  isError: boolean;
  /** Text content blocks, in order; other block types are left out. */
  text: string[];
  /** The raw MCP result. */
  raw: unknown;
}

const DISCOVERY_TIMEOUT_MS = 5_000;

const DEFAULT_REFRESH_LEAD_MS = 60 * 60 * 1000;
const DEFAULT_REFRESH_RETRY_MS = 30 * 1000;
const MIN_REFRESH_DELAY_MS = 2_000;
/** sam-node's --control-plane-sync-interval default. */
const DEFAULT_POLICY_SYNC_MS = 15 * 60 * 1000;
/** How often provider records are refreshed; go-libp2p-kad-dht expires them after 48h. */
const DEFAULT_PROVIDE_INTERVAL_MS = 10 * 60 * 1000;
/** sam-node's --control-plane-sync-interval default, and its 2s first pull. */
const DEFAULT_CONTROL_PLANE_SYNC_MS = 15 * 60 * 1000;
const FIRST_CONTROL_PLANE_SYNC_MS = 2_000;
const DEFAULT_CONTROL_PLANE_SYNC_JITTER_MS = 2_000;

/**
 * A member that is on the mesh: a libp2p host authenticated with at least
 * one router, answering the auth handshake for peers that dial it, and
 * keeping its credential fresh. Close it to leave.
 */
export class MeshSession {
  readonly mesh: AgentMesh;
  readonly node: MeshHost;
  readonly routers: AdmittedRouter[];
  /** Peers that passed the inbound auth handshake, with their credential's expiration. */
  readonly authenticatedPeers: Map<string, Date>;
  /** Peers the control plane has banned; connections to and from them are refused. */
  readonly banned: BanSet;
  /** The services this member publishes. */
  readonly services = new ServiceRegistry();
  #refreshTimer: ReturnType<typeof setTimeout> | undefined;
  #policyTimer: ReturnType<typeof setInterval> | undefined;
  #provideTimer: ReturnType<typeof setInterval> | undefined;
  #syncTimer: ReturnType<typeof setTimeout> | undefined;
  #syncing: Promise<ControlPlaneSync> | undefined;
  readonly #refreshLeadMs: number;
  readonly #refreshRetryMs: number;
  readonly #policySyncMs: number;
  readonly #provideIntervalMs: number;
  readonly #syncIntervalMs: number;
  readonly #syncJitterMs: number;
  #policyRules: string[] | undefined;
  #serving = false;
  #closed = false;

  constructor(mesh: AgentMesh, node: MeshHost, routers: AdmittedRouter[], authenticatedPeers: Map<string, Date>, banned: BanSet, options: JoinOptions) {
    this.mesh = mesh;
    this.node = node;
    this.routers = routers;
    this.authenticatedPeers = authenticatedPeers;
    this.banned = banned;
    this.#refreshLeadMs = options.refreshLeadMs ?? DEFAULT_REFRESH_LEAD_MS;
    this.#refreshRetryMs = options.refreshRetryMs ?? DEFAULT_REFRESH_RETRY_MS;
    this.#policySyncMs = options.policySyncIntervalMs ?? DEFAULT_POLICY_SYNC_MS;
    this.#provideIntervalMs = options.provideIntervalMs ?? DEFAULT_PROVIDE_INTERVAL_MS;
    this.#syncIntervalMs = options.controlPlaneSyncIntervalMs ?? DEFAULT_CONTROL_PLANE_SYNC_MS;
    this.#syncJitterMs = options.controlPlaneSyncJitterMs ?? DEFAULT_CONTROL_PLANE_SYNC_JITTER_MS;
    this.#scheduleRefresh();
    this.#listenForEvents();
    if (this.#syncIntervalMs > 0) {
      this.#scheduleSync(Math.min(FIRST_CONTROL_PLANE_SYNC_MS, this.#syncIntervalMs));
    }
  }

  get peerId(): string {
    return this.node.peerId.toString();
  }

  /** Addresses peers can dial this member on, including relayed ones. */
  get addresses(): Multiaddr[] {
    return this.node.getMultiaddrs();
  }

  /** The `.../p2p-circuit/p2p/<self>` addresses reserved on routers. */
  get relayAddresses(): Multiaddr[] {
    return this.node.getMultiaddrs().filter((ma) => ma.toString().includes("/p2p-circuit"));
  }

  /**
   * Connects to a peer; see Peer for how it is named. Returns the
   * connection, reused if one is already open. A banned peer is refused
   * here and by the connection gater.
   */
  connect(peer: Peer, signal?: AbortSignal): Promise<Connection> {
    const { peerId, addrs } = this.dialTargets(peer);
    if (peerId !== undefined && this.banned.has(peerId)) {
      return Promise.reject(new Error(`peer ${peerId} is banned by the control plane`));
    }
    return this.node.dial(addrs, signal !== undefined ? { signal } : {});
  }

  /** The addresses connect() dials for a peer, in the order libp2p tries them. */
  dialTargets(peer: Peer): { peerId: string | undefined; addrs: Multiaddr[] } {
    if (typeof peer === "string" && !peer.startsWith("/")) {
      return { peerId: peer, addrs: this.relayedAddresses(peer) };
    }
    if (typeof peer === "string" || isMultiaddr(peer)) {
      const ma = typeof peer === "string" ? multiaddr(peer) : peer;
      return { peerId: targetPeerOf(ma), addrs: [ma] };
    }
    const advertised = peer.addrs.map((text) => {
      const ma = multiaddr(text);
      return targetPeerOf(ma) === undefined ? ma.encapsulate(`/p2p/${peer.peerId}`) : ma;
    });
    return { peerId: peer.peerId, addrs: [...advertised, ...this.relayedAddresses(peer.peerId)] };
  }

  /** `<router>/p2p-circuit/p2p/<peer>` through every router that admitted this member. */
  relayedAddresses(peerId: string): Multiaddr[] {
    return this.routers.map((r) => r.addr.encapsulate(`/p2p-circuit/p2p/${peerId}`));
  }

  /** Connects to a peer and runs the mutual auth handshake, returning its verified credential. */
  async authenticate(peer: Peer, signal?: AbortSignal): Promise<VerifiedBiscuit> {
    const conn = await this.connect(peer, signal);
    return authenticateWithPeer(conn, this.mesh.authFrame(), this.mesh.credential.controlPlaneKeys);
  }

  /**
   * Looks the DHT up for peers offering a service: `"mcp://calc"`, the
   * same string callTool and request take, or a type alone (`"mcp"`) for
   * every service of that type, or (type, name). Bounded by the timeout;
   * the DHT walk itself is what sam-node's discover does.
   */
  async discover(service: string, name?: string, options: { timeoutMs?: number; limit?: number } = {}): Promise<DiscoveredProvider[]> {
    const target = service.includes("://") ? parseServiceTarget(service) : { type: service, name };
    if (!isServiceType(target.type)) {
      throw new Error(`service type must be mcp, inference or a2a, got ${JSON.stringify(target.type)}`);
    }
    const cid = await serviceCID(target.type, target.name);
    const signal = AbortSignal.timeout(options.timeoutMs ?? DISCOVERY_TIMEOUT_MS);
    const found = new Map<string, DiscoveredProvider>();
    try {
      for await (const provider of this.node.contentRouting.findProviders(cid, { signal })) {
        const peerId = provider.id.toString();
        if (peerId === this.peerId) {
          continue;
        }
        const entry = found.get(peerId) ?? { peerId, addrs: [] };
        for (const ma of provider.multiaddrs) {
          const text = ma.toString();
          if (!entry.addrs.includes(text)) {
            entry.addrs.push(text);
          }
        }
        found.set(peerId, entry);
        if (found.size >= (options.limit ?? 20)) {
          break;
        }
      }
    } catch (err) {
      // The lookup ended on its deadline; what was found so far is the answer.
      if (!(err instanceof Error && err.name === "TimeoutError") && !signal.aborted) {
        throw err;
      }
    }
    return [...found.values()];
  }

  /**
   * Opens an MCP session with a provider for targetService ("mcp://<name>",
   * or "" for the provider's own catalog tools).
   */
  async openMCP(peer: Peer, targetService: string, options: MCPSessionOptions = {}): Promise<MCPSession> {
    const conn = await this.connect(peer, options.signal);
    return openMCPSession(conn, this.mesh.authFrame(targetService, options.agent ?? ""), this.mesh.credential.controlPlaneKeys, options);
  }

  /** Lists the tools a provider serves for a service. */
  async listTools(peer: Peer, targetService: string, options: MCPSessionOptions = {}): Promise<{ name: string; description?: string }[]> {
    const mcp = await this.openMCP(peer, targetService, options);
    try {
      const { tools } = await mcp.client.listTools();
      return tools.map((t) => (t.description !== undefined ? { name: t.name, description: t.description } : { name: t.name }));
    } finally {
      await mcp.close();
    }
  }

  /** Calls one tool on a provider's service. */
  async callTool(peer: Peer, targetService: string, tool: string, args: Record<string, unknown> = {}, options: MCPSessionOptions = {}): Promise<ToolCallResult> {
    const mcp = await this.openMCP(peer, targetService, options);
    try {
      const result = await mcp.client.callTool({ name: tool, arguments: args });
      const content = Array.isArray(result.content) ? (result.content as Array<{ type: string; text?: string }>) : [];
      return {
        isError: result.isError === true,
        text: content.filter((c) => c.type === "text" && typeof c.text === "string").map((c) => c.text as string),
        raw: result,
      };
    } finally {
      await mcp.close();
    }
  }

  /** Refreshes now and reschedules; exposed so a caller can force it. */
  async refresh(): Promise<void> {
    await this.mesh.refresh();
    this.#scheduleRefresh();
  }

  /**
   * Pulls keys, bans and router addresses from the control plane now, and
   * the mesh policy when serving, then applies them: a newly banned peer is
   * hung up on and dropped from the admitted set. Concurrent calls share
   * one pull. Errors of individual parts are in the result, not thrown.
   */
  sync(): Promise<ControlPlaneSync> {
    this.#syncing ??= this.#syncOnce().finally(() => {
      this.#syncing = undefined;
    });
    return this.#syncing;
  }

  async #syncOnce(): Promise<ControlPlaneSync> {
    const result = await this.mesh.syncControlPlane();
    if (result.bannedPeerIds !== undefined) {
      const { banned } = this.banned.reconcile(result.bannedPeerIds, result.fetchedAt);
      await Promise.all(banned.map((peerId) => this.#evict(peerId)));
    }
    if (this.#serving) {
      try {
        await this.syncPolicy();
      } catch (err) {
        result.errors.push(`policy: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return result;
  }

  /** Asks for a pull soon, after a random delay so a fleet told at once does not pull at once. */
  triggerSync(): void {
    if (this.#closed) {
      return;
    }
    this.#scheduleSync(Math.floor(Math.random() * (this.#syncJitterMs + 1)));
  }

  #scheduleSync(delayMs: number): void {
    clearTimeout(this.#syncTimer);
    this.#syncTimer = setTimeout(() => {
      this.sync()
        .catch(() => {})
        .finally(() => {
          if (!this.#closed && this.#syncIntervalMs > 0) {
            // Stretched by up to a tenth so a fleet started together does not pull together.
            this.#scheduleSync(this.#syncIntervalMs + Math.floor(Math.random() * (this.#syncIntervalMs / 10 + 1)));
          }
        });
    }, delayMs);
    this.#syncTimer.unref?.();
  }

  /** Drops a banned peer: its admission and its connections. */
  async #evict(peerId: string): Promise<void> {
    this.authenticatedPeers.delete(peerId);
    try {
      await this.node.hangUp(peerIdFromString(peerId));
    } catch {
      // Not connected, or already gone.
    }
  }

  /**
   * The control plane's gossip events, relayed by the routers. The topic
   * validator drops anything not signed by a trusted control plane key, so
   * a peer whose libp2p key signed the envelope still cannot get an
   * unsigned event through; a stale event is ignored, not penalized.
   */
  #listenForEvents(): void {
    const pubsub = this.node.services.pubsub;
    pubsub.topicValidators.set(GOSSIP_EVENTS_TOPIC, (_peer, message) => {
      if (message.type !== "signed") {
        return TopicValidatorResult.Reject;
      }
      return verifyMeshEvent(message.data, this.mesh.credential.controlPlaneKeys) !== undefined ? TopicValidatorResult.Accept : TopicValidatorResult.Reject;
    });
    pubsub.addEventListener("message", (evt) => {
      if (evt.detail.topic !== GOSSIP_EVENTS_TOPIC) {
        return;
      }
      const event = verifyMeshEvent(evt.detail.data, this.mesh.credential.controlPlaneKeys);
      if (event === undefined) {
        return;
      }
      switch (event.type) {
        case MeshEvent_Type.BANNED:
          // Not persisted: a restarted member picks the ban back up from /info.
          if (this.banned.add(event.peerId, timestampMs(event.eventTime))) {
            void this.#evict(event.peerId);
          }
          break;
        case MeshEvent_Type.KEY_ROTATION:
          if (event.newPublicKey.length === 32) {
            this.mesh.addTrustedKey(event.newPublicKey);
          }
          this.triggerSync();
          break;
        case MeshEvent_Type.POLICY_UPDATE:
          this.triggerSync();
          break;
      }
    });
    pubsub.subscribe(GOSSIP_EVENTS_TOPIC);
  }

  /**
   * The mesh policy rules this member evaluates for callers, as the control
   * plane rendered them (PolicyConfigGetResponse.datalog_rules). Empty until
   * the first serve() or syncPolicy().
   */
  get policyRules(): string[] {
    return this.#policyRules ?? [];
  }

  /** Re-reads the mesh policy from the control plane. */
  async syncPolicy(): Promise<void> {
    this.#policyRules = await this.mesh.controlPlane.policyRules(this.mesh.credential.biscuit);
  }

  /**
   * Calls an inference or A2A service on a provider over /libp2p-http, the
   * way sam-node's egress proxy does for /sam/<peer>/<type>/<name>/<path>.
   */
  async request(peer: Peer, targetService: string, path: string, options: HTTPRequestOptions = {}): Promise<HTTPResponse> {
    const conn = await this.connect(peer, options.signal);
    return httpRequestOverStream(conn, this.mesh.credential.biscuit, targetService, path, options);
  }

  /**
   * Publishes a service on the mesh: registers it, announces it in the DHT
   * and reports it to the control plane's catalog. The first call fetches
   * the mesh policy and starts answering /sam/mcp/1.0.0 and /libp2p-http;
   * a policy that cannot be read fails the call, since a provider without
   * it could only authorize what callers carry in their own tokens.
   */
  async serve(spec: ServiceSpec): Promise<void> {
    if (!this.#serving) {
      await this.syncPolicy();
      const providerOptions: ProviderOptions = {
        trustedKeys: () => this.mesh.credential.controlPlaneKeys,
        ownBiscuit: () => this.mesh.credential.biscuit,
        policyRules: () => this.policyRules,
        isBanned: (peerId) => this.banned.has(peerId),
        onAuthorized: (peerId, verified) => this.authenticatedPeers.set(peerId, verified.expiration),
      };
      await this.node.handle(MCP_PROTOCOL, mcpStreamHandler(this.services, providerOptions, MCP_PROTOCOL), SERVE_HANDLER_OPTIONS);
      await this.node.handle(HTTP_PROTOCOL, httpIngressHandler(this.services, providerOptions), SERVE_HANDLER_OPTIONS);
      this.#policyTimer = setInterval(() => void this.syncPolicy().catch(() => {}), this.#policySyncMs);
      this.#policyTimer.unref?.();
      this.#provideTimer = setInterval(() => void this.provideAll().catch(() => {}), this.#provideIntervalMs);
      this.#provideTimer.unref?.();
      this.#serving = true;
    }
    this.services.add(spec);
    await this.#provide(spec.type, spec.name);
    await this.reportCatalog().catch(() => {});
  }

  /** Announces every registered service in the DHT again. */
  async provideAll(): Promise<void> {
    for (const s of this.services.list()) {
      await this.#provide(s.type, s.name);
    }
  }

  async #provide(type: ServiceType, name: string): Promise<void> {
    // Once for the type and once for the name, as sam-node announces.
    const signal = AbortSignal.timeout(DISCOVERY_TIMEOUT_MS);
    for (const cid of [await serviceCID(type), await serviceCID(type, name)]) {
      try {
        await this.node.contentRouting.provide(cid, { signal });
      } catch (err) {
        // A provide that timed out on some peers still landed on the ones that answered.
        if (!(err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError"))) {
          throw err;
        }
      }
    }
  }

  /** Reports the published services to the control plane's catalog (display only). */
  async reportCatalog(): Promise<void> {
    await this.mesh.controlPlane.reportCatalog(this.mesh.credential.biscuit, this.services.list());
  }

  /** The services this member publishes. */
  get servedServices(): ServedService[] {
    return this.services.list();
  }

  #scheduleRefresh(): void {
    if (this.#closed) {
      return;
    }
    clearTimeout(this.#refreshTimer);
    const dueMs = this.mesh.credential.expiration * 1000 - this.#refreshLeadMs - Date.now();
    const delay = Math.max(MIN_REFRESH_DELAY_MS, dueMs);
    this.#refreshTimer = setTimeout(() => {
      this.mesh.refresh().then(
        () => this.#scheduleRefresh(),
        () => {
          if (!this.#closed) {
            this.#refreshTimer = setTimeout(() => this.#scheduleRefresh(), this.#refreshRetryMs);
            this.#refreshTimer.unref?.();
          }
        },
      );
    }, delay);
    // A pending refresh must not keep an otherwise finished process alive.
    this.#refreshTimer.unref?.();
  }

  async close(): Promise<void> {
    this.#closed = true;
    clearTimeout(this.#refreshTimer);
    clearTimeout(this.#syncTimer);
    clearInterval(this.#policyTimer);
    clearInterval(this.#provideTimer);
    await this.node.stop();
  }
}

/** Implements AgentMesh.join(); lives here to keep mesh.ts free of libp2p. */
export async function joinMesh(mesh: AgentMesh, options: JoinOptions = {}): Promise<MeshSession> {
  const routerAddrs = mesh.credential.routerAddresses.map((a) => multiaddr(a));
  if (routerAddrs.length === 0) {
    throw new Error("credential lists no router addresses; the control plane had no active router at enrollment");
  }

  const banned = new BanSet();
  const node = await createMeshHost(mesh.identity, { ...options, banned });
  const admitted: AdmittedRouter[] = [];
  const authenticatedPeers = new Map<string, Date>();
  try {
    await node.handle(
      AUTH_PROTOCOL,
      authStreamHandler({
        ownBiscuit: () => mesh.credential.biscuit,
        trustedKeys: () => mesh.credential.controlPlaneKeys,
        isBanned: (peerId) => banned.has(peerId),
        onAuthenticated: (peerId, verified) => authenticatedPeers.set(peerId, verified.expiration),
      }),
      AUTH_HANDLER_OPTIONS,
    );

    const failures: string[] = [];
    for (const addr of routerAddrs) {
      const routerPeer = targetPeerOf(addr);
      if (routerPeer === undefined) {
        failures.push(`${addr.toString()}: no /p2p/<peer id> component`);
        continue;
      }
      try {
        const conn = await node.dial(addr, options.signal !== undefined ? { signal: options.signal } : {});
        const credential = await authenticateWithPeer(conn, mesh.authFrame(), mesh.credential.controlPlaneKeys);
        // Enforced under the key that verified the token; a relay that is
        // not a router must not become our way onto the mesh.
        requireRole(credential, ROLE_ROUTER);
        admitted.push({ peerId: routerPeer, addr, credential });
      } catch (err) {
        failures.push(`${addr.toString()}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    if (admitted.length === 0) {
      throw new Error(`no router admitted this member:\n  ${failures.join("\n  ")}`);
    }

    if (options.reserveRelay ?? true) {
      await listenThroughRelay(node, (admitted[0] as AdmittedRouter).addr);
    }
  } catch (err) {
    await Promise.resolve(node.stop()).catch(() => {});
    throw err;
  }

  return new MeshSession(mesh, node, admitted, authenticatedPeers, banned, options);
}

/** The peer a multiaddr ends at: its trailing `/p2p/<id>`, or undefined for a relay address with no target yet. */
function targetPeerOf(ma: Multiaddr): string | undefined {
  const last = ma.getComponents().at(-1);
  return last?.name === "p2p" ? last.value : undefined;
}
