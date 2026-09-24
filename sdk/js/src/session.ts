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

import type { Connection, Libp2p } from "@libp2p/interface";
import { multiaddr, type Multiaddr } from "@multiformats/multiaddr";
import { AUTH_HANDLER_OPTIONS, AUTH_PROTOCOL, MCP_PROTOCOL, authenticateWithPeer, authStreamHandler } from "./auth.ts";
import { ROLE_ROUTER, requireRole, type VerifiedBiscuit } from "./biscuit.ts";
import { serviceCID, type ServiceType } from "./discovery.ts";
import { createMeshHost, listenThroughRelay, type MeshHostOptions } from "./host.ts";
import { openMCPSession, type MCPSession, type MCPSessionOptions } from "./mcp.ts";
import type { AgentMesh } from "./mesh.ts";
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

/**
 * A member that is on the mesh: a libp2p host authenticated with at least
 * one router, answering the auth handshake for peers that dial it, and
 * keeping its credential fresh. Close it to leave.
 */
export class MeshSession {
  readonly mesh: AgentMesh;
  readonly node: Libp2p;
  readonly routers: AdmittedRouter[];
  /** Peers that passed the inbound auth handshake, with their credential's expiration. */
  readonly authenticatedPeers: Map<string, Date>;
  /** The services this member publishes. */
  readonly services = new ServiceRegistry();
  #refreshTimer: ReturnType<typeof setTimeout> | undefined;
  #policyTimer: ReturnType<typeof setInterval> | undefined;
  #provideTimer: ReturnType<typeof setInterval> | undefined;
  readonly #refreshLeadMs: number;
  readonly #refreshRetryMs: number;
  readonly #policySyncMs: number;
  readonly #provideIntervalMs: number;
  #policyRules: string[] | undefined;
  #serving = false;
  #closed = false;

  constructor(mesh: AgentMesh, node: Libp2p, routers: AdmittedRouter[], authenticatedPeers: Map<string, Date>, options: JoinOptions) {
    this.mesh = mesh;
    this.node = node;
    this.routers = routers;
    this.authenticatedPeers = authenticatedPeers;
    this.#refreshLeadMs = options.refreshLeadMs ?? DEFAULT_REFRESH_LEAD_MS;
    this.#refreshRetryMs = options.refreshRetryMs ?? DEFAULT_REFRESH_RETRY_MS;
    this.#policySyncMs = options.policySyncIntervalMs ?? DEFAULT_POLICY_SYNC_MS;
    this.#provideIntervalMs = options.provideIntervalMs ?? DEFAULT_PROVIDE_INTERVAL_MS;
    this.#scheduleRefresh();
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
   * Connects to a peer by address; a `/p2p-circuit` address goes through
   * the named relay. Returns the connection, reused if one is already open.
   */
  connect(addr: string | Multiaddr, signal?: AbortSignal): Promise<Connection> {
    const ma = typeof addr === "string" ? multiaddr(addr) : addr;
    return this.node.dial(ma, signal !== undefined ? { signal } : {});
  }

  /** Connects to a peer and runs the mutual auth handshake, returning its verified credential. */
  async authenticate(addr: string | Multiaddr, signal?: AbortSignal): Promise<VerifiedBiscuit> {
    const conn = await this.connect(addr, signal);
    return authenticateWithPeer(conn, this.mesh.authFrame(), this.mesh.credential.controlPlaneKeys);
  }

  /**
   * Looks the DHT up for peers offering a service, by type and name, or by
   * type alone when name is omitted. Bounded by the timeout; the DHT walk
   * itself is what sam-node's discover does.
   */
  async discover(type: ServiceType, name?: string, options: { timeoutMs?: number; limit?: number } = {}): Promise<DiscoveredProvider[]> {
    const cid = await serviceCID(type, name);
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
   * Opens an MCP session with the provider at addr for targetService
   * ("mcp://<name>", or "" for the provider's own catalog tools).
   */
  async openMCP(addr: string | Multiaddr, targetService: string, options: MCPSessionOptions = {}): Promise<MCPSession> {
    const conn = await this.connect(addr, options.signal);
    return openMCPSession(conn, this.mesh.authFrame(targetService, options.agent ?? ""), this.mesh.credential.controlPlaneKeys, options);
  }

  /** Lists the tools a provider serves for a service. */
  async listTools(addr: string | Multiaddr, targetService: string, options: MCPSessionOptions = {}): Promise<{ name: string; description?: string }[]> {
    const mcp = await this.openMCP(addr, targetService, options);
    try {
      const { tools } = await mcp.client.listTools();
      return tools.map((t) => (t.description !== undefined ? { name: t.name, description: t.description } : { name: t.name }));
    } finally {
      await mcp.close();
    }
  }

  /** Calls one tool on a provider's service. */
  async callTool(addr: string | Multiaddr, targetService: string, tool: string, args: Record<string, unknown> = {}, options: MCPSessionOptions = {}): Promise<ToolCallResult> {
    const mcp = await this.openMCP(addr, targetService, options);
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
  async request(addr: string | Multiaddr, targetService: string, path: string, options: HTTPRequestOptions = {}): Promise<HTTPResponse> {
    const conn = await this.connect(addr, options.signal);
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

  const node = await createMeshHost(mesh.identity, options);
  const admitted: AdmittedRouter[] = [];
  const authenticatedPeers = new Map<string, Date>();
  try {
    await node.handle(
      AUTH_PROTOCOL,
      authStreamHandler({
        ownBiscuit: () => mesh.credential.biscuit,
        trustedKeys: () => mesh.credential.controlPlaneKeys,
        onAuthenticated: (peerId, verified) => authenticatedPeers.set(peerId, verified.expiration),
      }),
      AUTH_HANDLER_OPTIONS,
    );

    const failures: string[] = [];
    for (const addr of routerAddrs) {
      const routerPeer = addr.getComponents().findLast((c) => c.name === "p2p")?.value;
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

  return new MeshSession(mesh, node, admitted, authenticatedPeers, options);
}
