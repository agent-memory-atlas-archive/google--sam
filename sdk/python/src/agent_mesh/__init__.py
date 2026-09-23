# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Native SDK for joining a SAM agent mesh from inside a Python process."""

from .challenges import enroll_challenge, enroll_status_challenge, refresh_challenge, register_challenge
from .controlplane import (
    ROLE_NODE,
    ControlPlaneClient,
    ControlPlaneError,
    Enrollment,
    EnrollmentRejectedError,
    InsecureControlPlaneURLError,
    RefreshResult,
    validate_control_plane_url,
    verify_keys_response,
)
from .credential import MeshCredential, decode_auth_response, encode_auth_frame
from .identity import Identity, libp2p_public_key, peer_id_from_public_key, verify_ed25519
from .mesh import AgentMesh

__version__ = "0.1.0"

__all__ = [
    "AgentMesh",
    "ControlPlaneClient",
    "ControlPlaneError",
    "Enrollment",
    "EnrollmentRejectedError",
    "Identity",
    "InsecureControlPlaneURLError",
    "MeshCredential",
    "ROLE_NODE",
    "RefreshResult",
    "decode_auth_response",
    "encode_auth_frame",
    "enroll_challenge",
    "enroll_status_challenge",
    "libp2p_public_key",
    "peer_id_from_public_key",
    "refresh_challenge",
    "register_challenge",
    "validate_control_plane_url",
    "verify_ed25519",
    "verify_keys_response",
]
