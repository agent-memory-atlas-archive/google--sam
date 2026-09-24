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

// The provider side in one process: this SDK's handlers for /sam/mcp/1.0.0
// and /libp2p-http behind a js-libp2p host, called with this SDK's clients.
// The real sam-node as a caller is exercised by tests/integration.

import { create, toBinary } from "@bufbuild/protobuf";
import { yamux } from "@chainsafe/libp2p-yamux";
import { identify } from "@libp2p/identify";
import type { Connection, Libp2p } from "@libp2p/interface";
import { tcp } from "@libp2p/tcp";
import { tls } from "@libp2p/tls";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createLibp2p } from "libp2p";
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";
import { z } from "zod";
import { AuthRejectedError, MCP_PROTOCOL } from "./auth.ts";
import { loadBiscuit } from "./biscuit.ts";
import { ROLE_NODE } from "./controlplane.ts";
import { AuthFrameSchema } from "./gen/sam_pb.ts";
import { openMCPSession } from "./mcp.ts";
import { HTTP_PROTOCOL, ServiceRegistry, httpIngressHandler, httpRequestOverStream, mcpStreamHandler, type ProviderOptions } from "./serve.ts";

type Wasm = Awaited<ReturnType<typeof loadBiscuit>>;

let wasm: Wasm;
let cpKeyPair: InstanceType<Wasm["KeyPair"]>;
let cpKey: Uint8Array;
let provider: Libp2p;
let providerBiscuit: Uint8Array;
let caller: Libp2p;
let callerBiscuit: Uint8Array;
let guestBiscuit: Uint8Array;
let backend: http.Server;
let backendURL: string;
const backendSeen: { method: string; url: string; peer: string | undefined; body: string }[] = [];
const authorized: string[] = [];

function mint(peerId: string, role: string, extra: string[] = []): Uint8Array {
  const b = wasm.Biscuit.builder();
  b.addFact(wasm.Fact.fromString(`node(${JSON.stringify(peerId)})`));
  b.addFact(wasm.Fact.fromString(`client_peer_id(${JSON.stringify(peerId)})`));
  b.addFact(wasm.Fact.fromString("expiration(2035-01-01T00:00:00Z)"));
  b.addFact(wasm.Fact.fromString(`role(${JSON.stringify(role)})`));
  for (const f of extra) {
    b.addFact(wasm.Fact.fromString(f));
  }
  return b.build(cpKeyPair.getPrivateKey()).toBytes();
}

function newHost(): Promise<Libp2p> {
  return createLibp2p({
    addresses: { listen: ["/ip4/127.0.0.1/tcp/0"] },
    transports: [tcp()],
    connectionEncrypters: [tls()],
    streamMuxers: [yamux()],
    services: { identify: identify() },
  });
}

// What the control plane renders for a policy granting the node role calc,
// echo and every inference service on any target.
const POLICY_RULES = [
  `granted_service_set("mcp", ["calc"]) <- role("sam:role:node")`,
  `granted_service_all("inference") <- role("sam:role:node")`,
  `granted_service_all("sam:system") <- role("sam:role:node")`,
  `target_unrestricted(true) <- role("sam:role:node")`,
];

before(async () => {
  wasm = await loadBiscuit();
  cpKeyPair = new wasm.KeyPair(wasm.SignatureAlgorithm.Ed25519);
  cpKey = new Uint8Array(Buffer.from(cpKeyPair.getPublicKey().toString().replace(/^ed25519\//, ""), "hex"));

  backend = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c: Buffer) => (body += c.toString()));
    req.on("end", () => {
      backendSeen.push({ method: req.method ?? "", url: req.url ?? "", peer: req.headers["x-peer-id"] as string | undefined, body });
      assert.equal(req.headers["x-sam-biscuit"], undefined, "biscuit leaked to the backend");
      res.writeHead(200, { "content-type": "application/json", "x-backend": "fake" });
      res.end(JSON.stringify({ path: req.url, echo: body }));
    });
  });
  await new Promise<void>((resolve) => backend.listen(0, "127.0.0.1", resolve));
  const addr = backend.address() as { port: number };
  backendURL = `http://127.0.0.1:${addr.port}`;

  provider = await newHost();
  providerBiscuit = mint(provider.peerId.toString(), ROLE_NODE);
  const registry = new ServiceRegistry();
  registry.add({
    type: "mcp",
    name: "calc",
    createServer: () => {
      const server = new McpServer({ name: "calc", version: "0.0.1" });
      server.tool("add", { a: z.number(), b: z.number() }, async ({ a, b }) => ({ content: [{ type: "text", text: String(a + b) }] }));
      return server;
    },
  });
  registry.add({ type: "inference", name: "llm", target: backendURL });
  registry.add({
    type: "inference",
    name: "inproc",
    target: (request, verified) => new Response(`hello ${verified.peerId} ${request.method} ${new URL(request.url).pathname}`, { status: 201 }),
  });
  const options: ProviderOptions = {
    trustedKeys: () => [cpKey],
    ownBiscuit: () => providerBiscuit,
    policyRules: () => POLICY_RULES,
    onAuthorized: (peerId, _v, target) => authorized.push(`${peerId} ${target}`),
  };
  await provider.handle(MCP_PROTOCOL, mcpStreamHandler(registry, options, MCP_PROTOCOL), { runOnLimitedConnection: true });
  await provider.handle(HTTP_PROTOCOL, httpIngressHandler(registry, options), { runOnLimitedConnection: true });

  caller = await newHost();
  callerBiscuit = mint(caller.peerId.toString(), ROLE_NODE);
  guestBiscuit = mint(caller.peerId.toString(), "sam:role:guest");
});

after(async () => {
  await caller.stop();
  await provider.stop();
  await new Promise<void>((resolve) => backend.close(() => resolve()));
});

function frame(target: string, biscuit = callerBiscuit): Uint8Array {
  return toBinary(AuthFrameSchema, create(AuthFrameSchema, { biscuit, targetService: target }));
}

async function dial(): Promise<Connection> {
  return caller.dial(provider.getMultiaddrs()[0] as Parameters<typeof caller.dial>[0]);
}

test("an MCP service is served to an authorized caller", async () => {
  const mcp = await openMCPSession(await dial(), frame("mcp://calc"), [cpKey]);
  try {
    assert.equal(mcp.provider.peerId, provider.peerId.toString());
    assert.deepEqual((await mcp.client.listTools()).tools.map((t) => t.name), ["add"]);
    const result = await mcp.client.callTool({ name: "add", arguments: { a: 2, b: 3 } });
    assert.deepEqual(result.content, [{ type: "text", text: "5" }]);
  } finally {
    await mcp.close();
  }
  assert.ok(authorized.includes(`${caller.peerId.toString()} mcp://calc`));
});

test("the empty target is the member's own catalog", async () => {
  const mcp = await openMCPSession(await dial(), frame(""), [cpKey]);
  try {
    const result = await mcp.client.callTool({ name: "list_local_services", arguments: {} });
    const text = (result.content as Array<{ text: string }>)[0]?.text ?? "";
    const services = JSON.parse(text) as Array<{ type: string; name: string }>;
    assert.deepEqual(
      services.map((s) => `${s.type}://${s.name}`).sort(),
      ["inference://inproc", "inference://llm", "mcp://calc"],
    );
  } finally {
    await mcp.close();
  }
});

test("a caller whose role grants nothing gets AuthResponse{success: false}", async () => {
  await assert.rejects(openMCPSession(await dial(), frame("mcp://calc", guestBiscuit), [cpKey]), (err: unknown) => {
    assert.ok(err instanceof AuthRejectedError, String(err));
    assert.match(err.message, /not authorized/);
    return true;
  });
});

test("a service the role was not granted is denied before the service is looked up", async () => {
  await assert.rejects(openMCPSession(await dial(), frame("mcp://secret"), [cpKey]), AuthRejectedError);
});

test("a granted service the member does not have closes the stream after the answer", async () => {
  // echo is granted by the policy, not registered: the answer is success, then nothing.
  await assert.rejects(openMCPSession(await dial(), frame("mcp://echo"), [cpKey]));
});

test("an inference service is proxied to its backend with the caller's identity", async () => {
  const conn = await dial();
  const res = await httpRequestOverStream(conn, callerBiscuit, "inference://llm", "/v1/models?x=1", { headers: { "x-sam-biscuit": "spoof" } });
  assert.equal(res.status, 200);
  assert.equal(res.headers["x-backend"], "fake");
  assert.deepEqual(JSON.parse(res.text()), { path: "/v1/models?x=1", echo: "" });
  const seen = backendSeen.at(-1);
  assert.equal(seen?.peer, caller.peerId.toString());

  const post = await httpRequestOverStream(conn, callerBiscuit, "inference://llm", "/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: "m" }),
  });
  assert.equal(post.status, 200);
  assert.equal(JSON.parse(post.text()).echo, JSON.stringify({ model: "m" }));
});

test("an in-process handler sees the verified caller", async () => {
  const res = await httpRequestOverStream(await dial(), callerBiscuit, "inference://inproc", "/agent/card");
  assert.equal(res.status, 201);
  assert.equal(res.text(), `hello ${caller.peerId.toString()} GET /agent/card`);
});

test("HTTP ingress refuses what the policy does not grant", async () => {
  const conn = await dial();
  // The guest role has no grants: 403 before anything reaches the backend.
  const before = backendSeen.length;
  const denied = await httpRequestOverStream(conn, guestBiscuit, "inference://llm", "/v1/models");
  assert.equal(denied.status, 403);
  assert.equal(backendSeen.length, before);
  // A2A is not granted to the node role either.
  const a2a = await httpRequestOverStream(conn, callerBiscuit, "a2a://llm", "/");
  assert.equal(a2a.status, 403);
  // Granted but not registered: 404 after authorization.
  const missing = await httpRequestOverStream(conn, callerBiscuit, "inference://nope", "/");
  assert.equal(missing.status, 404);
});

test("HTTP ingress rejects a request without a biscuit and a dotted path", async () => {
  const conn = await dial();
  const noBiscuit = await httpRequestOverStream(conn, new Uint8Array(), "inference://llm", "/v1/models").catch((err: Error) => err);
  // An empty biscuit encodes to an empty header, which the server treats as missing.
  assert.ok(!(noBiscuit instanceof Error) && noBiscuit.status === 401, String(noBiscuit instanceof Error ? noBiscuit : noBiscuit.status));
  const dotted = await httpRequestOverStream(conn, callerBiscuit, "inference://llm", "/../other/x");
  assert.equal(dotted.status, 400);
});
