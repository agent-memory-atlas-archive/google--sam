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

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { BiscuitVerificationError, ROLE_ROUTER, loadBiscuit, requireRole, verifyPeerBiscuit } from "./biscuit.ts";
import { ROLE_NODE } from "./controlplane.ts";

interface Vector {
  name: string;
  biscuit: string;
  peer_id: string;
  trusted_keys: string[];
  valid: boolean;
  reason?: string;
  expiration?: number;
  roles?: string[];
  has_router_role: boolean;
}

const fixture = JSON.parse(readFileSync(new URL("../../testdata/biscuit_vectors.json", import.meta.url), "utf8")) as {
  control_plane_key: string;
  node_token_labels: Record<string, string>;
  vectors: Vector[];
};
const hex = (s: string) => new Uint8Array(Buffer.from(s, "hex"));
const b64 = (s: string) => new Uint8Array(Buffer.from(s, "base64"));

// The vectors expire in 2035; pin "now" so they stay valid for the test's lifetime.
const NOW = new Date("2026-09-23T00:00:00Z");

for (const v of fixture.vectors) {
  test(`go-minted biscuit: ${v.name}`, async () => {
    const trusted = v.trusted_keys.map(hex);
    if (!v.valid) {
      await assert.rejects(verifyPeerBiscuit(b64(v.biscuit), v.peer_id, trusted, NOW), BiscuitVerificationError);
      return;
    }
    const verified = await verifyPeerBiscuit(b64(v.biscuit), v.peer_id, trusted, NOW);
    assert.equal(verified.peerId, v.peer_id);
    assert.equal(verified.expiration.getTime() / 1000, v.expiration);
    assert.deepEqual(verified.roles, v.roles);
    assert.equal(Buffer.from(verified.verifyingKey).toString("hex"), fixture.control_plane_key);
    if (v.has_router_role) {
      requireRole(verified, ROLE_ROUTER);
      assert.throws(() => requireRole(verified, ROLE_NODE), BiscuitVerificationError);
    } else {
      requireRole(verified, ROLE_NODE);
      assert.throws(() => requireRole(verified, ROLE_ROUTER), BiscuitVerificationError);
      assert.deepEqual(verified.labels, fixture.node_token_labels);
    }
  });
}

test("a valid token is refused once the clock passes its expiration", async () => {
  const v = fixture.vectors.find((x) => x.valid) as Vector;
  const after = new Date((v.expiration as number) * 1000 + 1000);
  await assert.rejects(verifyPeerBiscuit(b64(v.biscuit), v.peer_id, v.trusted_keys.map(hex), after), /expired/);
});

test("no trusted keys means nothing verifies", async () => {
  const v = fixture.vectors.find((x) => x.valid) as Vector;
  await assert.rejects(verifyPeerBiscuit(b64(v.biscuit), v.peer_id, [], NOW), /no trusted control plane key/);
});

test("tokens minted here verify and report their facts", async () => {
  const wasm = await loadBiscuit();
  const kp = new wasm.KeyPair(wasm.SignatureAlgorithm.Ed25519);
  const builder = wasm.Biscuit.builder();
  builder.addFact(wasm.Fact.fromString('node("12D3KooWA4Xop1JaT3MHxwYMkCepYsv4iPVopMXwCz5iHYdBfeSB")'));
  builder.addFact(wasm.Fact.fromString("expiration(2035-01-01T00:00:00Z)"));
  builder.addFact(wasm.Fact.fromString("expiration(2034-06-01T00:00:00Z)"));
  builder.addFact(wasm.Fact.fromString('role("sam:role:node")'));
  builder.addFact(wasm.Fact.fromString('label("team", "plat\\"form")'));
  const token = builder.build(kp.getPrivateKey());
  const key = new Uint8Array(Buffer.from(kp.getPublicKey().toString().replace(/^ed25519\//, ""), "hex"));

  const verified = await verifyPeerBiscuit(token.toBytes(), "12D3KooWA4Xop1JaT3MHxwYMkCepYsv4iPVopMXwCz5iHYdBfeSB", [key], NOW);
  // The earliest expiration binds.
  assert.equal(verified.expiration.toISOString(), "2034-06-01T00:00:00.000Z");
  assert.deepEqual(verified.labels, { team: 'plat"form' });
  assert.deepEqual(verified.roles, ["sam:role:node"]);
});
