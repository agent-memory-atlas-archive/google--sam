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

import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { credentialTimeToLiveSeconds, decodeAuthResponse } from "./credential.ts";
import {
  AuthFrameSchema,
  AuthResponseSchema,
  BootstrapEnrollResponseSchema,
  EnrollmentStatus,
  KeysResponseSchema,
  TokenRefreshResponseSchema,
} from "./gen/sam_pb.ts";
import { Identity } from "./identity.ts";
import { AgentMesh } from "./mesh.ts";

const cpKey = Identity.generate();
const text = (s: string) => new TextEncoder().encode(s);

function proto(bytes: Uint8Array): Response {
  return new Response(Buffer.from(bytes), { status: 200, headers: { "Content-Type": "application/x-protobuf" } });
}

/** A control plane that approves everything and hands out numbered biscuits. */
function fakeControlPlane(keysOk = true): { fetch: typeof fetch; issued: number } {
  const state = { issued: 0 };
  const signedKeys = () => {
    const unsigned = create(KeysResponseSchema, { publicKeys: [cpKey.publicKeyRaw], timestamp: BigInt(Date.now()) });
    return create(KeysResponseSchema, { ...unsigned, signatures: [cpKey.sign(toBinary(KeysResponseSchema, unsigned))] });
  };
  const fetch: typeof globalThis.fetch = (async (input: Parameters<typeof globalThis.fetch>[0], init?: RequestInit) => {
    const req = new Request(input, init);
    const path = new URL(req.url).pathname;
    switch (`${req.method} ${path}`) {
      case "POST /enroll":
        state.issued++;
        return proto(
          toBinary(
            BootstrapEnrollResponseSchema,
            create(BootstrapEnrollResponseSchema, {
              status: EnrollmentStatus.APPROVED,
              biscuitToken: text(`biscuit-${state.issued}`),
              controlPlanePublicKey: cpKey.publicKeyRaw,
              routerAddresses: ["/dns4/router.example/tcp/4001/p2p/12D3KooWP8iKhDf3iCMo2H3butNVfdTUtYwYWYQ75jTGnynXPFMp"],
              expiration: BigInt(Math.floor(Date.now() / 1000) + 3600),
            }),
          ),
        );
      case "POST /refresh":
        state.issued++;
        return proto(toBinary(TokenRefreshResponseSchema, create(TokenRefreshResponseSchema, { biscuitToken: text(`biscuit-${state.issued}`), expiresAt: BigInt(Math.floor(Date.now() / 1000) + 7200) })));
      case "GET /keys":
        return keysOk ? proto(toBinary(KeysResponseSchema, signedKeys())) : new Response("boom", { status: 500 });
      default:
        return new Response(`no route for ${req.method} ${path}`, { status: 404 });
    }
  }) as typeof globalThis.fetch;
  return {
    fetch,
    get issued() {
      return state.issued;
    },
  };
}

test("enroll persists identity and credential, load resumes them, refresh rotates", async () => {
  const dir = await mkdtemp(join(tmpdir(), "sam-sdk-"));
  try {
    const cp = fakeControlPlane();
    const tokenPath = join(dir, "bootstrap.token");
    await writeFile(tokenPath, "sbt_secret\n");

    const mesh = await AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", stateDir: join(dir, "state"), bootstrapTokenPath: tokenPath, fetch: cp.fetch });
    assert.deepEqual(mesh.credential.biscuit, text("biscuit-1"));
    assert.deepEqual(mesh.credential.controlPlaneKeys, [cpKey.publicKeyRaw]);
    assert.equal(mesh.credential.routerAddresses.length, 1);
    assert.ok(credentialTimeToLiveSeconds(mesh.credential) > 3500);

    // Secrets on disk are owner-only.
    for (const f of ["identity.key", "credential.json"]) {
      assert.equal((await stat(join(dir, "state", f))).mode & 0o777, 0o600, f);
    }
    assert.equal((await stat(join(dir, "state"))).mode & 0o777, 0o700);

    const resumed = await AgentMesh.load({ controlPlaneUrl: "ignored://", stateDir: join(dir, "state"), fetch: cp.fetch });
    assert.equal(resumed.peerId, mesh.peerId);
    assert.deepEqual(resumed.credential.biscuit, text("biscuit-1"));
    assert.equal(resumed.controlPlane.url.toString(), "http://127.0.0.1:1/");

    await resumed.refresh();
    assert.deepEqual(resumed.credential.biscuit, text("biscuit-2"));
    const onDisk = JSON.parse(await readFile(join(dir, "state", "credential.json"), "utf8")) as { biscuit: string };
    assert.equal(Buffer.from(onDisk.biscuit, "base64").toString(), "biscuit-2");

    // Re-enrolling from the same directory keeps the identity.
    const again = await AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", stateDir: join(dir, "state"), bootstrapToken: "sbt_secret", fetch: cp.fetch });
    assert.equal(again.peerId, mesh.peerId);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("enroll works without a state directory and keeps the enrollment key when /keys fails", async () => {
  const cp = fakeControlPlane(false);
  const mesh = await AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", bootstrapToken: "sbt_secret", fetch: cp.fetch });
  assert.deepEqual(mesh.credential.controlPlaneKeys, [cpKey.publicKeyRaw]);
  await mesh.save();
  await mesh.refresh();
  assert.deepEqual(mesh.credential.biscuit, text("biscuit-2"));
});

test("enroll refuses ambiguous credentials", async () => {
  const cp = fakeControlPlane();
  await assert.rejects(AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", fetch: cp.fetch }), /exactly one of/);
  await assert.rejects(AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", bootstrapToken: "a", jwt: "b", fetch: cp.fetch }), /exactly one of/);
  assert.equal(cp.issued, 0);
});

test("authFrame is the AuthFrame protobuf with this member's biscuit", async () => {
  const cp = fakeControlPlane();
  const mesh = await AgentMesh.enroll({ controlPlaneUrl: "http://127.0.0.1:1", bootstrapToken: "sbt_secret", fetch: cp.fetch });
  const frame = fromBinary(AuthFrameSchema, mesh.authFrame("mcp://calculator", "agent:acme.example:bot"));
  assert.deepEqual(frame.biscuit, text("biscuit-1"));
  assert.equal(frame.targetService, "mcp://calculator");
  assert.equal(frame.agent, "agent:acme.example:bot");

  const resp = decodeAuthResponse(toBinary(AuthResponseSchema, create(AuthResponseSchema, { success: false, error: "denied" })));
  assert.equal(resp.success, false);
  assert.equal(resp.error, "denied");
});
