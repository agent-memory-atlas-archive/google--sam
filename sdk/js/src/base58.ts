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

// base58btc, the alphabet libp2p peer IDs are written in.

const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const BASE = BigInt(ALPHABET.length);

const INDEX = new Map<string, number>();
for (let i = 0; i < ALPHABET.length; i++) {
  INDEX.set(ALPHABET.charAt(i), i);
}

export function encodeBase58(bytes: Uint8Array): string {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) {
    zeros++;
  }
  let n = 0n;
  for (const b of bytes) {
    n = (n << 8n) | BigInt(b);
  }
  let out = "";
  while (n > 0n) {
    const digit = Number(n % BASE);
    out = ALPHABET.charAt(digit) + out;
    n /= BASE;
  }
  return "1".repeat(zeros) + out;
}

export function decodeBase58(text: string): Uint8Array {
  let zeros = 0;
  while (zeros < text.length && text.charAt(zeros) === "1") {
    zeros++;
  }
  let n = 0n;
  for (const ch of text) {
    const digit = INDEX.get(ch);
    if (digit === undefined) {
      throw new Error(`invalid base58 character ${JSON.stringify(ch)}`);
    }
    n = n * BASE + BigInt(digit);
  }
  const body: number[] = [];
  while (n > 0n) {
    body.unshift(Number(n & 0xffn));
    n >>= 8n;
  }
  return new Uint8Array([...new Array<number>(zeros).fill(0), ...body]);
}
