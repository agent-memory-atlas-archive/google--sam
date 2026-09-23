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
import { AuthFrameSchema, AuthResponseSchema, type AuthResponse } from "./gen/sam_pb.ts";

/** What a member holds after enrolling: its biscuit and what it trusts. */
export interface MeshCredential {
  controlPlaneUrl: string;
  /** The biscuit the control plane minted for this identity. */
  biscuit: Uint8Array;
  /** Unix seconds at which the biscuit expires. */
  expiration: number;
  /** Every control plane signing key currently trusted (rotation keeps several valid). */
  controlPlaneKeys: Uint8Array[];
  /** Router multiaddrs, `/p2p/<peer id>` suffixed, as handed out at enrollment. */
  routerAddresses: string[];
}

/** Seconds of validity left on the biscuit; negative once expired. */
export function credentialTimeToLiveSeconds(c: MeshCredential, nowMs = Date.now()): number {
  return c.expiration - Math.floor(nowMs / 1000);
}

/**
 * The first frame on every mesh stream (/sam/auth/1.0.0, /sam/mcp/1.0.0):
 * the caller's biscuit, the service it wants and the agent it speaks for.
 * Framing (varint length prefix) is the transport's job.
 */
export function encodeAuthFrame(biscuit: Uint8Array, targetService = "", agent = ""): Uint8Array {
  return toBinary(AuthFrameSchema, create(AuthFrameSchema, { biscuit, targetService, agent }));
}

/** The peer's answer to an AuthFrame, carrying its own biscuit on success. */
export function decodeAuthResponse(bytes: Uint8Array): AuthResponse {
  return fromBinary(AuthResponseSchema, bytes);
}

interface CredentialJSON {
  control_plane_url: string;
  biscuit: string;
  expiration: number;
  control_plane_keys: string[];
  router_addresses: string[];
}

export function credentialToJSON(c: MeshCredential): string {
  const out: CredentialJSON = {
    control_plane_url: c.controlPlaneUrl,
    biscuit: Buffer.from(c.biscuit).toString("base64"),
    expiration: c.expiration,
    control_plane_keys: c.controlPlaneKeys.map((k) => Buffer.from(k).toString("base64")),
    router_addresses: c.routerAddresses,
  };
  return JSON.stringify(out, null, 2) + "\n";
}

export function credentialFromJSON(text: string): MeshCredential {
  const raw = JSON.parse(text) as Partial<CredentialJSON>;
  if (typeof raw.control_plane_url !== "string" || typeof raw.biscuit !== "string" || typeof raw.expiration !== "number") {
    throw new Error("malformed credential file");
  }
  return {
    controlPlaneUrl: raw.control_plane_url,
    biscuit: new Uint8Array(Buffer.from(raw.biscuit, "base64")),
    expiration: raw.expiration,
    controlPlaneKeys: (raw.control_plane_keys ?? []).map((k) => new Uint8Array(Buffer.from(k, "base64"))),
    routerAddresses: raw.router_addresses ?? [],
  };
}
