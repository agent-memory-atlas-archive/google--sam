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
import { decodeBase58, encodeBase58 } from "./base58.ts";
import { enrollChallenge } from "./challenges.ts";
import { Identity, libp2pPublicKey, peerIdFromPublicKey, verifyEd25519 } from "./identity.ts";

interface Vector {
  seed: string;
  public_key: string;
  peer_id: string;
  libp2p_private_key: string;
  libp2p_public_key: string;
  challenge: string;
  signature: string;
}

const vectors = (JSON.parse(readFileSync(new URL("../../testdata/identity_vectors.json", import.meta.url), "utf8")) as { vectors: Vector[] }).vectors;
const hex = (s: string) => new Uint8Array(Buffer.from(s, "hex"));
const toHex = (b: Uint8Array) => Buffer.from(b).toString("hex");

test("identity matches go-libp2p for every vector", () => {
  for (const v of vectors) {
    const id = Identity.fromSeed(hex(v.seed));
    assert.equal(toHex(id.publicKeyRaw), v.public_key);
    assert.equal(id.peerId, v.peer_id);
    assert.equal(toHex(id.libp2pPublicKey), v.libp2p_public_key);
    assert.equal(toHex(id.toLibp2pPrivateKey()), v.libp2p_private_key);
    assert.equal(peerIdFromPublicKey(hex(v.public_key)), v.peer_id);
    // ed25519 signatures are deterministic, so the Go signature is reproducible.
    const [, , peer, ts] = v.challenge.split(":");
    assert.equal(toHex(id.sign(enrollChallenge(peer as string, Number(ts)))), v.signature);
    assert.ok(verifyEd25519(id.publicKeyRaw, new TextEncoder().encode(v.challenge), hex(v.signature)));
  }
});

test("libp2p private key round-trips and rejects a mismatched public half", () => {
  const id = Identity.generate();
  const again = Identity.fromLibp2pPrivateKey(id.toLibp2pPrivateKey());
  assert.equal(again.peerId, id.peerId);

  const tampered = id.toLibp2pPrivateKey();
  tampered[tampered.length - 1] = (tampered[tampered.length - 1] ?? 0) ^ 0xff;
  assert.throws(() => Identity.fromLibp2pPrivateKey(tampered), /public half does not match/);
  assert.throws(() => Identity.fromLibp2pPrivateKey(tampered.subarray(1)), /not a libp2p ed25519 private key/);
});

test("generated identities are distinct and self-verify", () => {
  const a = Identity.generate();
  const b = Identity.generate();
  assert.notEqual(a.peerId, b.peerId);
  const msg = new TextEncoder().encode("hello");
  const sig = a.sign(msg);
  assert.ok(a.verify(msg, sig));
  assert.ok(!b.verify(msg, sig));
  assert.ok(!verifyEd25519(new Uint8Array(31), msg, sig));
});

test("libp2pPublicKey refuses the wrong size", () => {
  assert.throws(() => libp2pPublicKey(new Uint8Array(33)), /32 bytes/);
});

test("base58btc round-trips and keeps leading zeros", () => {
  const cases: Uint8Array[] = [new Uint8Array(0), Uint8Array.of(0), Uint8Array.of(0, 0, 1, 2), hex("00ff"), hex("deadbeef")];
  for (const c of cases) {
    assert.deepEqual(decodeBase58(encodeBase58(c)), c);
  }
  assert.equal(encodeBase58(new TextEncoder().encode("Hello World!")), "2NEpo7TZRRrLZSi2U");
  assert.throws(() => decodeBase58("0OIl"), /invalid base58 character/);
});
