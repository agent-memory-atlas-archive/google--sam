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

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { ControlPlaneClient, ROLE_NODE, type Enrollment } from "./controlplane.ts";
import { credentialFromJSON, credentialToJSON, encodeAuthFrame, type MeshCredential } from "./credential.ts";
import { Identity } from "./identity.ts";

const IDENTITY_FILE = "identity.key";
const CREDENTIAL_FILE = "credential.json";

export interface AgentMeshOptions {
  /** Base URL of the control plane, e.g. https://hub.sam-mesh.dev. */
  controlPlaneUrl: string;
  /** Accept plaintext http:// to a non-loopback control plane. Off by default. */
  allowInsecure?: boolean;
  /**
   * Directory that keeps the identity key and the credential across
   * restarts. Without it the identity lives only in this process.
   */
  stateDir?: string;
  /** Use this identity instead of the persisted or a freshly generated one. */
  identity?: Identity;
  /** Role to enroll as. Defaults to ROLE_NODE. */
  role?: string;
  /** Operator-declared labels, e.g. { region: "eu" }. */
  labels?: Record<string, string>;
  /** Injection point for tests. */
  fetch?: typeof fetch;
}

export interface EnrollOptions extends AgentMeshOptions {
  /** A bootstrap token value, when the caller already holds it in memory. */
  bootstrapToken?: string;
  /** Path of a file holding the bootstrap token. Preferred over a value. */
  bootstrapTokenPath?: string;
  /** An OIDC ID token, for meshes that enroll identities interactively. */
  jwt?: string;
  /** Bounds the wait for an operator to approve a pending enrollment. */
  signal?: AbortSignal;
  /** Overrides the control plane's suggested poll interval while pending. */
  pollIntervalMs?: number;
}

/**
 * A member of the mesh: an identity, the credential the control plane minted
 * for it, and the client that keeps that credential fresh.
 *
 * This is the enrollment half of the SDK. Joining the mesh over libp2p and
 * serving or calling MCP tools build on top of it (see sdk/README.md).
 */
export class AgentMesh {
  readonly identity: Identity;
  readonly controlPlane: ControlPlaneClient;
  #credential: MeshCredential;
  readonly #stateDir: string | undefined;

  private constructor(identity: Identity, controlPlane: ControlPlaneClient, credential: MeshCredential, stateDir: string | undefined) {
    this.identity = identity;
    this.controlPlane = controlPlane;
    this.#credential = credential;
    this.#stateDir = stateDir;
  }

  get peerId(): string {
    return this.identity.peerId;
  }

  get credential(): MeshCredential {
    return this.#credential;
  }

  /**
   * Enrolls with the control plane and returns a member holding a credential.
   * Exactly one of bootstrapToken, bootstrapTokenPath or jwt must be given.
   */
  static async enroll(options: EnrollOptions): Promise<AgentMesh> {
    const given = [options.bootstrapToken, options.bootstrapTokenPath, options.jwt].filter((v) => v !== undefined).length;
    if (given !== 1) {
      throw new Error("exactly one of bootstrapToken, bootstrapTokenPath or jwt is required");
    }
    const identity = options.identity ?? (await loadIdentity(options.stateDir)) ?? Identity.generate();
    const controlPlane = newClient(options);
    const role = options.role ?? ROLE_NODE;

    let enrollment: Enrollment;
    if (options.jwt !== undefined) {
      enrollment = await controlPlane.register({ identity, jwt: options.jwt, role, ...labelsOf(options) });
    } else {
      const bootstrapToken = options.bootstrapTokenPath !== undefined ? (await readFile(options.bootstrapTokenPath, "utf8")).trim() : (options.bootstrapToken as string);
      enrollment = await controlPlane.enrollBootstrap({
        identity,
        bootstrapToken,
        role,
        ...labelsOf(options),
        ...(options.pollIntervalMs !== undefined ? { pollIntervalMs: options.pollIntervalMs } : {}),
        ...(options.signal !== undefined ? { signal: options.signal } : {}),
      });
    }

    // Widen trust from the one key the enrollment carries to every key the
    // control plane currently signs with, so peers holding credentials from
    // a retiring key still verify. Best effort, as in sam-node.
    let controlPlaneKeys = [enrollment.controlPlanePublicKey];
    try {
      controlPlaneKeys = await controlPlane.keys(controlPlaneKeys);
    } catch {
      // The enrollment key alone still works until the next sync.
    }

    const mesh = new AgentMesh(
      identity,
      controlPlane,
      {
        controlPlaneUrl: controlPlane.url.toString(),
        biscuit: enrollment.biscuit,
        expiration: enrollment.expiration,
        controlPlaneKeys,
        routerAddresses: enrollment.routerAddresses,
      },
      options.stateDir,
    );
    await mesh.save();
    return mesh;
  }

  /** Resumes a member from a state directory written by an earlier enroll(). */
  static async load(options: AgentMeshOptions & { stateDir: string }): Promise<AgentMesh> {
    const identity = options.identity ?? (await loadIdentity(options.stateDir));
    if (!identity) {
      throw new Error(`no identity in ${options.stateDir}; enroll first`);
    }
    const credential = credentialFromJSON(await readFile(join(options.stateDir, CREDENTIAL_FILE), "utf8"));
    return new AgentMesh(identity, newClient({ ...options, controlPlaneUrl: credential.controlPlaneUrl }), credential, options.stateDir);
  }

  /**
   * Trades the current biscuit for a fresh one and persists it. The control
   * plane redeems only the last biscuit it issued, so a lost refresh result
   * means re-enrolling; persisting before returning keeps that rare.
   */
  async refresh(): Promise<MeshCredential> {
    const result = await this.controlPlane.refresh({ identity: this.identity, biscuit: this.#credential.biscuit });
    let controlPlaneKeys = this.#credential.controlPlaneKeys;
    try {
      controlPlaneKeys = await this.controlPlane.keys(controlPlaneKeys);
    } catch {
      // Keep the previous set; a failed /keys sync must not cost the new biscuit.
    }
    this.#credential = { ...this.#credential, biscuit: result.biscuit, expiration: result.expiration, controlPlaneKeys };
    await this.save();
    return this.#credential;
  }

  /**
   * The frame that opens every stream to a peer: this member's biscuit plus
   * the service it wants (e.g. "mcp://calculator") and the agent it speaks for.
   */
  authFrame(targetService = "", agent = ""): Uint8Array {
    return encodeAuthFrame(this.#credential.biscuit, targetService, agent);
  }

  /** Writes identity and credential to the state directory, if one is configured. */
  async save(): Promise<void> {
    if (this.#stateDir === undefined) {
      return;
    }
    await mkdir(this.#stateDir, { recursive: true, mode: 0o700 });
    await writeAtomic(join(this.#stateDir, IDENTITY_FILE), this.identity.toLibp2pPrivateKey(), 0o600);
    await writeAtomic(join(this.#stateDir, CREDENTIAL_FILE), credentialToJSON(this.#credential), 0o600);
  }
}

function newClient(options: AgentMeshOptions): ControlPlaneClient {
  return new ControlPlaneClient({
    url: options.controlPlaneUrl,
    allowInsecure: options.allowInsecure ?? false,
    ...(options.fetch !== undefined ? { fetch: options.fetch } : {}),
  });
}

function labelsOf(options: AgentMeshOptions): { labels?: Record<string, string> } {
  return options.labels !== undefined ? { labels: options.labels } : {};
}

async function loadIdentity(stateDir: string | undefined): Promise<Identity | undefined> {
  if (stateDir === undefined) {
    return undefined;
  }
  try {
    return Identity.fromLibp2pPrivateKey(new Uint8Array(await readFile(join(stateDir, IDENTITY_FILE))));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return undefined;
    }
    throw err;
  }
}

async function writeAtomic(path: string, data: Uint8Array | string, mode: number): Promise<void> {
  const tmp = `${path}.tmp`;
  await writeFile(tmp, data, { mode });
  await rename(tmp, path);
}
