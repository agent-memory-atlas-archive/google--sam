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

export { AgentMesh, type AgentMeshOptions, type EnrollOptions } from "./mesh.ts";
export { Identity, peerIdFromPublicKey, libp2pPublicKey, verifyEd25519 } from "./identity.ts";
export {
  ControlPlaneClient,
  ControlPlaneError,
  EnrollmentRejectedError,
  InsecureControlPlaneURLError,
  ROLE_NODE,
  validateControlPlaneURL,
  verifyKeysResponse,
  type ControlPlaneClientOptions,
  type Enrollment,
  type EnrollBootstrapParams,
  type RegisterParams,
  type RefreshParams,
  type RefreshResult,
} from "./controlplane.ts";
export {
  credentialFromJSON,
  credentialTimeToLiveSeconds,
  credentialToJSON,
  decodeAuthResponse,
  encodeAuthFrame,
  type MeshCredential,
} from "./credential.ts";
export { enrollChallenge, enrollStatusChallenge, refreshChallenge, registerChallenge } from "./challenges.ts";
