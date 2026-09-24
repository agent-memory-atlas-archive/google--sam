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

// The provider side: /sam/mcp/1.0.0 for MCP services and /libp2p-http for
// inference and A2A services, both gated by the authorizer the way sam-node
// gates them (WithBiscuitAuth and StartIngressServer in internal/node).

import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import type { Connection, Stream, StreamHandler } from "@libp2p/interface";
import { lpStream } from "@libp2p/utils";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import http from "node:http";
import { Duplex, Readable } from "node:stream";
import { AUTH_HANDSHAKE_TIMEOUT_MS, MAX_AUTH_FRAME_BYTES } from "./auth.ts";
import { AuthorizationError, authorizeCaller, type ProviderAuthorizerOptions } from "./authorizer.ts";
import type { VerifiedBiscuit } from "./biscuit.ts";
import type { ServiceType } from "./discovery.ts";
import { AuthFrameSchema, AuthResponseSchema } from "./gen/sam_pb.ts";
import { StreamTransport } from "./mcp.ts";

/** go-libp2p-http's protocol: plain HTTP/1.1 on a stream, one request per stream. */
export const HTTP_PROTOCOL = "/libp2p-http";

/** Headers of the mesh HTTP datapath (api/network.go). */
export const HEADER_SAM_BISCUIT = "x-sam-biscuit";
export const HEADER_SAM_AGENT = "x-sam-agent";
export const HEADER_PEER_ID = "x-peer-id";
export const HEADER_SAM_NO_TRAILING_SLASH = "x-sam-no-trailing-slash";

/** Options for libp2p.handle() so services are reachable over relayed connections. */
export const SERVE_HANDLER_OPTIONS = { runOnLimitedConnection: true };

/** Anything with the MCP SDK's connect(): McpServer or the low-level Server. */
export interface MCPServerLike {
  connect(transport: Transport): Promise<void>;
  close(): Promise<void>;
}

/** An MCP service: one server per session, since an MCP server holds one transport. */
export interface MCPServiceSpec {
  type: "mcp";
  name: string;
  description?: string;
  createServer(): MCPServerLike;
}

/** A fetch-style handler for an HTTP service. */
export type HTTPHandler = (request: Request, caller: VerifiedBiscuit) => Promise<Response> | Response;

/**
 * An inference or A2A service: authorized requests are forwarded to target,
 * a base URL of a local backend or a handler in this process. The request
 * path is what followed /<type>/<name>/; the biscuit and agent headers are
 * stripped and X-Peer-Id names the verified caller, as sam-node does.
 */
export interface HTTPServiceSpec {
  type: "inference" | "a2a";
  name: string;
  description?: string;
  target: string | HTTPHandler;
}

export type ServiceSpec = MCPServiceSpec | HTTPServiceSpec;

export interface ServedService {
  type: ServiceType;
  name: string;
  description: string;
}

/** The request as seen by an HTTP handler; the Node http types are hidden. */
interface StreamSocket extends Duplex {
  remotePeer: string;
}

/**
 * Bridges a libp2p stream to a Node Duplex so Node's own HTTP parser and
 * client can run over it.
 */
export function streamToNodeDuplex(stream: Stream, remotePeer: string): StreamSocket {
  const duplex = new Duplex({
    read() {
      stream.resume();
    },
    write(chunk: Uint8Array, _encoding, callback) {
      if (stream.send(chunk)) {
        callback();
      } else {
        stream.addEventListener("drain", () => callback(), { once: true });
      }
    },
    final(callback) {
      stream.close().then(
        () => callback(),
        (err: Error) => callback(err),
      );
    },
    destroy(err, callback) {
      if (err !== null) {
        stream.abort(err);
      } else {
        void stream.close().catch(() => {});
      }
      callback(err);
    },
  }) as StreamSocket;
  duplex.remotePeer = remotePeer;
  stream.addEventListener("message", (evt) => {
    if (!duplex.push(Buffer.from(evt.data.subarray()))) {
      stream.pause();
    }
  });
  const end = () => {
    if (!duplex.readableEnded) {
      duplex.push(null);
    }
  };
  stream.addEventListener("remoteCloseWrite", end);
  stream.addEventListener("close", end);
  return duplex;
}

export interface ProviderOptions extends ProviderAuthorizerOptions {
  /** This provider's current biscuit, for the mutual AuthResponse. */
  ownBiscuit(): Uint8Array;
  /** Peers the control plane has banned; refused before their token is looked at. */
  isBanned?(peerId: string): boolean;
  /** Called after a caller is authorized for a service, e.g. for the session's admitted set. */
  onAuthorized?(peerId: string, verified: VerifiedBiscuit, targetService: string): void;
}

/** Services registered on a member, keyed by type and name. */
export class ServiceRegistry {
  readonly #services = new Map<string, ServiceSpec>();

  static key(type: string, name: string): string {
    return `${type}://${name}`;
  }

  add(spec: ServiceSpec): void {
    const key = ServiceRegistry.key(spec.type, spec.name);
    if (this.#services.has(key)) {
      throw new Error(`service ${key} is already registered`);
    }
    this.#services.set(key, spec);
  }

  remove(type: ServiceType, name: string): boolean {
    return this.#services.delete(ServiceRegistry.key(type, name));
  }

  get(type: string, name: string): ServiceSpec | undefined {
    return this.#services.get(ServiceRegistry.key(type, name));
  }

  list(): ServedService[] {
    return [...this.#services.values()].map((s) => ({ type: s.type, name: s.name, description: s.description ?? "" }));
  }
}

async function refuse(stream: Stream, peerId: string, reason: string, signal: AbortSignal): Promise<void> {
  stream.log?.("refusing %s: %s", peerId, reason);
  const lp = lpStream(stream, { maxDataLength: MAX_AUTH_FRAME_BYTES });
  await lp.write(toBinary(AuthResponseSchema, create(AuthResponseSchema, { success: false, error: reason })), { signal }).catch(() => {});
}

/**
 * Server side of /sam/mcp/1.0.0, as sam-node's WithBiscuitAuth(HandleMCPStream):
 * read the AuthFrame, authorize the caller for the named service, answer with
 * our own credential, then hand the stream to that service's MCP server. A
 * denied caller gets AuthResponse{success: false}; an unknown service closes
 * the stream after a successful answer, which is what sam-node does.
 */
export function mcpStreamHandler(registry: ServiceRegistry, options: ProviderOptions, protocol: string): StreamHandler {
  return async (stream: Stream, connection: Connection) => {
    const peerId = connection.remotePeer.toString();
    const signal = AbortSignal.timeout(AUTH_HANDSHAKE_TIMEOUT_MS);
    let handedOff = false;
    try {
      const lp = lpStream(stream, { maxDataLength: MAX_AUTH_FRAME_BYTES });
      const frame = fromBinary(AuthFrameSchema, (await lp.read({ signal })).subarray());
      if (options.isBanned?.(peerId) === true) {
        await refuse(stream, peerId, "peer is revoked", signal);
        return;
      }
      let verified: VerifiedBiscuit;
      try {
        verified = await authorizeCaller({ biscuit: frame.biscuit, peerId, targetService: frame.targetService, protocol, agent: frame.agent }, options);
      } catch (err) {
        if (err instanceof AuthorizationError) {
          await refuse(stream, peerId, err.message, signal);
          return;
        }
        throw err;
      }
      options.onAuthorized?.(peerId, verified, frame.targetService);
      await lp.write(toBinary(AuthResponseSchema, create(AuthResponseSchema, { success: true, biscuit: options.ownBiscuit() })), { signal });

      if (frame.targetService === "") {
        // The member's own catalog: what sam-node serves for an empty target.
        handedOff = true;
        await serveMCP(stream, catalogServer(registry));
        return;
      }
      const m = /^mcp:\/\/(.+)$/.exec(frame.targetService);
      const spec = m !== null ? registry.get("mcp", m[1] as string) : undefined;
      if (spec === undefined || spec.type !== "mcp") {
        stream.log?.("no MCP service %s for %s", frame.targetService, peerId);
        return;
      }
      handedOff = true;
      await serveMCP(stream, spec.createServer());
    } finally {
      if (!handedOff) {
        await stream.close().catch(() => stream.abort(new Error("mcp stream close failed")));
      }
    }
  };
}

/** Runs one MCP server over the stream until the caller goes away. */
async function serveMCP(stream: Stream, server: MCPServerLike): Promise<void> {
  const transport = new StreamTransport(stream);
  const closed = new Promise<void>((resolve) => {
    // The MCP Protocol takes over transport.onclose; its own onclose fires after.
    const proto = ("server" in server ? (server as { server: { onclose?: () => void } }).server : server) as { onclose?: () => void };
    proto.onclose = () => resolve();
  });
  await server.connect(transport);
  await closed;
  await server.close().catch(() => {});
}

/**
 * The member's own catalog over MCP, as sam-node's list_local_services: the
 * services this member publishes. Built per session with the MCP SDK.
 */
function catalogServer(registry: ServiceRegistry): MCPServerLike {
  const server = new McpServer({ name: "agent-mesh-sdk", version: "0.1.0" });
  server.registerTool("list_local_services", { description: "List the services this member publishes on the mesh" }, () => ({
    content: [{ type: "text", text: JSON.stringify(registry.list()) }],
  }));
  return server;
}

const MAX_INGRESS_BODY_BYTES = 8 * 1024 * 1024;

/** Reads a whole body, bounded. */
async function readBody(readable: Readable, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of readable) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as Uint8Array);
    size += buf.length;
    if (size > limit) {
      throw new Error(`body exceeds ${limit} bytes`);
    }
    chunks.push(buf);
  }
  return Buffer.concat(chunks);
}

function hasDotSegment(path: string): boolean {
  return path.split("/").some((seg) => seg === "." || seg === "..");
}

/**
 * Server side of /libp2p-http, as sam-node's StartIngressServer: the path is
 * /<type>/<name>[/<upstream>], the caller's biscuit is X-Sam-Biscuit, and
 * the request is authorized for <type>://<name> before anything is forwarded.
 * Node's own HTTP server parses the stream.
 */
export function httpIngressHandler(registry: ServiceRegistry, options: ProviderOptions): StreamHandler {
  const server = http.createServer({ keepAlive: false }, (req, res) => {
    void handleIngress(req, res, registry, options).catch((err: unknown) => {
      if (!res.headersSent) {
        res.writeHead(500, { "content-type": "text/plain" });
      }
      res.end(`ingress error: ${err instanceof Error ? err.message : String(err)}\n`);
    });
  });
  server.headersTimeout = AUTH_HANDSHAKE_TIMEOUT_MS;
  return (stream: Stream, connection: Connection) => {
    server.emit("connection", streamToNodeDuplex(stream, connection.remotePeer.toString()));
  };
}

async function handleIngress(req: http.IncomingMessage, res: http.ServerResponse, registry: ServiceRegistry, options: ProviderOptions): Promise<void> {
  const remotePeer = (req.socket as unknown as StreamSocket).remotePeer;
  const reply = (status: number, text: string) => {
    res.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
    res.end(text + "\n");
  };

  const rawURL = req.url ?? "/";
  // Policy is decided on the /<type>/<name> prefix; a dot segment in what
  // follows could resolve to a sibling service on a shared backend. Checked
  // on the raw path, before URL parsing normalizes it away.
  if (hasDotSegment(rawURL.split("?")[0] as string)) {
    reply(400, "Invalid path");
    return;
  }
  const url = new URL(rawURL, "http://mesh.invalid");
  const parts = url.pathname.replace(/^\//, "").split("/");
  if (parts.length < 2 || parts[0] === "" || parts[1] === "") {
    reply(400, "Invalid path");
    return;
  }
  const [serviceType, serviceName, ...rest] = parts as [string, string, ...string[]];
  if (serviceType !== "inference" && serviceType !== "a2a" && serviceType !== "mcp") {
    reply(400, "Invalid service type");
    return;
  }
  const upstreamPath = rest.join("/");

  const biscuitB64 = req.headers[HEADER_SAM_BISCUIT];
  if (typeof biscuitB64 !== "string" || biscuitB64 === "") {
    reply(401, "Missing X-Sam-Biscuit header");
    return;
  }
  let biscuit: Uint8Array;
  try {
    biscuit = new Uint8Array(Buffer.from(biscuitB64, "base64"));
    if (biscuit.length === 0) {
      throw new Error("empty");
    }
  } catch {
    reply(400, "Invalid X-Sam-Biscuit encoding");
    return;
  }

  const targetService = `${serviceType}://${serviceName}`;
  if (options.isBanned?.(remotePeer) === true) {
    reply(403, "Authorization failed");
    return;
  }
  const agentHeader = req.headers[HEADER_SAM_AGENT];
  let verified: VerifiedBiscuit;
  try {
    verified = await authorizeCaller(
      { biscuit, peerId: remotePeer, targetService, protocol: HTTP_PROTOCOL, agent: typeof agentHeader === "string" ? agentHeader : "" },
      options,
    );
  } catch (err) {
    if (err instanceof AuthorizationError) {
      reply(403, "Authorization failed");
      return;
    }
    throw err;
  }
  options.onAuthorized?.(remotePeer, verified, targetService);

  // Under the type the policy was evaluated on.
  const spec = registry.get(serviceType, serviceName);
  if (spec === undefined || spec.type === "mcp") {
    reply(404, "Service not found");
    return;
  }

  // The biscuit and the agent are for policy, not for the backend; X-Peer-Id
  // is set, not added, so an inbound value cannot pose as the verified peer.
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (v === undefined || k === HEADER_SAM_BISCUIT || k === HEADER_SAM_AGENT || k === HEADER_SAM_NO_TRAILING_SLASH || k === HEADER_PEER_ID || k === "host" || k === "connection" || k === "transfer-encoding" || k === "content-length") {
      continue;
    }
    for (const value of Array.isArray(v) ? v : [v]) {
      headers.append(k, value);
    }
  }
  headers.set(HEADER_PEER_ID, remotePeer);
  if (upstreamPath === "" && rest.length === 0) {
    headers.set(HEADER_SAM_NO_TRAILING_SLASH, "true");
  }

  const method = req.method ?? "GET";
  const body: Uint8Array<ArrayBuffer> | undefined =
    method === "GET" || method === "HEAD" ? undefined : new Uint8Array(await readBody(req, MAX_INGRESS_BODY_BYTES));
  const path = "/" + upstreamPath + url.search;
  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    init.body = body;
  }

  let response: Response;
  if (typeof spec.target === "string") {
    const base = spec.target.replace(/\/$/, "");
    response = await fetch(base + path, { ...init, redirect: "manual" });
  } else {
    response = await spec.target(new Request("http://" + serviceName + path, init), verified);
  }

  const outHeaders: Record<string, string | string[]> = {};
  response.headers.forEach((value, key) => {
    if (key === "content-length" || key === "transfer-encoding" || key === "connection") {
      return;
    }
    outHeaders[key] = value;
  });
  res.writeHead(response.status, outHeaders);
  if (response.body === null) {
    res.end();
    return;
  }
  await new Promise<void>((resolve, reject) => {
    Readable.fromWeb(response.body as import("node:stream/web").ReadableStream)
      .on("error", reject)
      .pipe(res)
      .on("finish", resolve)
      .on("error", reject);
  });
}

export interface HTTPRequestOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: Uint8Array | string;
  /** The agent this request is made for. */
  agent?: string;
  signal?: AbortSignal;
}

export interface HTTPResponse {
  status: number;
  headers: Record<string, string>;
  body: Uint8Array;
  text(): string;
}

/**
 * Client side of /libp2p-http, as go-libp2p-http's RoundTripper: one stream
 * per request, plain HTTP/1.1 with Host set to the peer ID, the biscuit in
 * X-Sam-Biscuit and the path /<type>/<name>/<path>.
 */
export async function httpRequestOverStream(
  conn: Connection,
  biscuit: Uint8Array,
  targetService: string,
  path: string,
  options: HTTPRequestOptions = {},
): Promise<HTTPResponse> {
  const m = /^(inference|a2a|mcp):\/\/(.+)$/.exec(targetService);
  if (m === null) {
    throw new Error(`service target must look like inference://<name>, got ${JSON.stringify(targetService)}`);
  }
  const signal = options.signal ?? AbortSignal.timeout(60_000);
  const stream = await conn.newStream(HTTP_PROTOCOL, { signal, runOnLimitedConnection: true });
  const peerId = conn.remotePeer.toString();
  const socket = streamToNodeDuplex(stream, peerId);
  return new Promise<HTTPResponse>((resolve, reject) => {
    const headers: Record<string, string> = { ...(options.headers ?? {}), [HEADER_SAM_BISCUIT]: Buffer.from(biscuit).toString("base64"), host: peerId };
    if (options.agent) {
      headers[HEADER_SAM_AGENT] = options.agent;
    }
    const req = http.request({
      method: options.method ?? "GET",
      path: `/${m[1]}/${m[2]}${path.startsWith("/") ? path : "/" + path}`,
      headers,
      createConnection: () => socket,
      signal,
    });
    req.on("error", reject);
    req.on("response", (res) => {
      readBody(res, MAX_INGRESS_BODY_BYTES).then((buf) => {
        const flat: Record<string, string> = {};
        for (const [k, v] of Object.entries(res.headers)) {
          if (typeof v === "string") {
            flat[k] = v;
          } else if (Array.isArray(v)) {
            flat[k] = v.join(", ");
          }
        }
        resolve({ status: res.statusCode ?? 0, headers: flat, body: new Uint8Array(buf), text: () => new TextDecoder().decode(buf) });
      }, reject);
    });
    if (options.body !== undefined) {
      req.write(options.body);
    }
    req.end();
  }).finally(() => {
    socket.destroy();
  });
}
