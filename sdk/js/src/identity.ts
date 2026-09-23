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

// A mesh identity is an ed25519 key pair. Its peer ID is the one libp2p
// derives, so the same key works in the SDK, in sam-node and on the wire.

import { createPrivateKey, createPublicKey, sign, verify, type KeyObject } from "node:crypto";
import { encodeBase58 } from "./base58.ts";

const PUBLIC_KEY_SIZE = 32;
const SEED_SIZE = 32;

// libp2p crypto.proto PublicKey{Type: Ed25519 (1), Data: <32 bytes>}.
const LIBP2P_PUBLIC_KEY_PREFIX = Uint8Array.of(0x08, 0x01, 0x12, 0x20);
// libp2p crypto.proto PrivateKey{Type: Ed25519 (1), Data: <seed || public>}.
const LIBP2P_PRIVATE_KEY_PREFIX = Uint8Array.of(0x08, 0x01, 0x12, 0x40);
// multihash: identity function (0x00), digest length 36.
const IDENTITY_MULTIHASH_PREFIX = Uint8Array.of(0x00, 0x24);

// PKCS#8 wrapper for a raw ed25519 seed (RFC 8410): what node:crypto imports.
const PKCS8_ED25519_PREFIX = Uint8Array.of(
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
);
// SubjectPublicKeyInfo wrapper for a raw ed25519 public key (RFC 8410).
const SPKI_ED25519_PREFIX = Uint8Array.of(0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00);

function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}

function startsWith(bytes: Uint8Array, prefix: Uint8Array): boolean {
  return prefix.every((b, i) => bytes[i] === b);
}

function publicKeyObject(publicKeyRaw: Uint8Array): KeyObject {
  return createPublicKey({ key: Buffer.from(concat(SPKI_ED25519_PREFIX, publicKeyRaw)), format: "der", type: "spki" });
}

/** The libp2p protobuf encoding of an ed25519 public key. */
export function libp2pPublicKey(publicKeyRaw: Uint8Array): Uint8Array {
  if (publicKeyRaw.length !== PUBLIC_KEY_SIZE) {
    throw new Error(`ed25519 public key must be ${PUBLIC_KEY_SIZE} bytes, got ${publicKeyRaw.length}`);
  }
  return concat(LIBP2P_PUBLIC_KEY_PREFIX, publicKeyRaw);
}

/** The peer ID libp2p derives from an ed25519 public key (base58btc, "12D3Koo..."). */
export function peerIdFromPublicKey(publicKeyRaw: Uint8Array): string {
  return encodeBase58(concat(IDENTITY_MULTIHASH_PREFIX, libp2pPublicKey(publicKeyRaw)));
}

export class Identity {
  readonly #privateKey: KeyObject;
  readonly #publicKey: KeyObject;
  readonly #seed: Uint8Array;
  /** Raw 32-byte ed25519 public key. */
  readonly publicKeyRaw: Uint8Array;
  readonly peerId: string;

  private constructor(seed: Uint8Array) {
    if (seed.length !== SEED_SIZE) {
      throw new Error(`ed25519 seed must be ${SEED_SIZE} bytes, got ${seed.length}`);
    }
    this.#seed = new Uint8Array(seed);
    this.#privateKey = createPrivateKey({
      key: Buffer.from(concat(PKCS8_ED25519_PREFIX, this.#seed)),
      format: "der",
      type: "pkcs8",
    });
    this.#publicKey = createPublicKey(this.#privateKey);
    const spki = new Uint8Array(this.#publicKey.export({ format: "der", type: "spki" }));
    if (spki.length !== SPKI_ED25519_PREFIX.length + PUBLIC_KEY_SIZE || !startsWith(spki, SPKI_ED25519_PREFIX)) {
      throw new Error("unexpected ed25519 public key encoding");
    }
    this.publicKeyRaw = spki.slice(SPKI_ED25519_PREFIX.length);
    this.peerId = peerIdFromPublicKey(this.publicKeyRaw);
  }

  static generate(): Identity {
    const seed = new Uint8Array(SEED_SIZE);
    crypto.getRandomValues(seed);
    return new Identity(seed);
  }

  static fromSeed(seed: Uint8Array): Identity {
    return new Identity(seed);
  }

  /** Loads the libp2p protobuf private key encoding, the format sam-node persists. */
  static fromLibp2pPrivateKey(bytes: Uint8Array): Identity {
    if (bytes.length !== LIBP2P_PRIVATE_KEY_PREFIX.length + 64 || !startsWith(bytes, LIBP2P_PRIVATE_KEY_PREFIX)) {
      throw new Error("not a libp2p ed25519 private key");
    }
    const seed = bytes.subarray(LIBP2P_PRIVATE_KEY_PREFIX.length, LIBP2P_PRIVATE_KEY_PREFIX.length + SEED_SIZE);
    const pub = bytes.subarray(LIBP2P_PRIVATE_KEY_PREFIX.length + SEED_SIZE);
    const id = new Identity(seed);
    if (!pub.every((b, i) => id.publicKeyRaw[i] === b)) {
      throw new Error("libp2p private key: public half does not match the seed");
    }
    return id;
  }

  /** The libp2p protobuf private key encoding (seed || public key). */
  toLibp2pPrivateKey(): Uint8Array {
    return concat(LIBP2P_PRIVATE_KEY_PREFIX, this.#seed, this.publicKeyRaw);
  }

  /** The libp2p protobuf public key encoding, what the control plane stores. */
  get libp2pPublicKey(): Uint8Array {
    return libp2pPublicKey(this.publicKeyRaw);
  }

  sign(data: Uint8Array): Uint8Array {
    return new Uint8Array(sign(null, data, this.#privateKey));
  }

  verify(data: Uint8Array, signature: Uint8Array): boolean {
    return verify(null, data, this.#publicKey, signature);
  }
}

/** Verifies an ed25519 signature with a raw 32-byte public key. */
export function verifyEd25519(publicKeyRaw: Uint8Array, data: Uint8Array, signature: Uint8Array): boolean {
  if (publicKeyRaw.length !== PUBLIC_KEY_SIZE) {
    return false;
  }
  return verify(null, data, publicKeyObject(publicKeyRaw), signature);
}
