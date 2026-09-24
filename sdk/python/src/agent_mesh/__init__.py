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

from .auth import AUTH_PROTOCOL, MCP_PROTOCOL, AuthRejectedError, auth_stream_handler, authenticate_with_peer
from .authorizer import BASELINE_DATALOG, AuthorizationError, AuthorizeRequest, ProviderAuthorizerOptions, authorize_caller
from .biscuit import ROLE_ROUTER, BiscuitVerificationError, VerifiedBiscuit, require_role, verify_peer_biscuit
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
from .discovery import DHT_PROTOCOL, DiscoveredProvider, find_providers, parse_service_target, provide, service_key
from .identity import Identity, libp2p_public_key, peer_id_from_public_key, verify_ed25519
from .mcp_client import LabelsNotSatisfiedError, ToolCallResult, ToolInfo, open_mcp_session, require_labels
from .mesh import AgentMesh, ControlPlaneSync
from .relay import dial_through_relay, reserve_relay
from .serve import (
    HTTP_PROTOCOL,
    HTTPHandler,
    HTTPRequest,
    HTTPResponse,
    HTTPService,
    MCPService,
    ProviderOptions,
    ServiceRegistry,
    http_ingress_handler,
    http_request_over_stream,
    mcp_stream_handler,
)
from .session import AdmittedRouter, MeshSession, Peer
from .sync import GOSSIP_EVENTS_TOPIC, BanSet, verify_mesh_event

__version__ = "0.1.0"

__all__ = [
    "AUTH_PROTOCOL",
    "AdmittedRouter",
    "AgentMesh",
    "AuthRejectedError",
    "AuthorizationError",
    "AuthorizeRequest",
    "BASELINE_DATALOG",
    "BanSet",
    "BiscuitVerificationError",
    "ControlPlaneClient",
    "ControlPlaneError",
    "ControlPlaneSync",
    "DHT_PROTOCOL",
    "DiscoveredProvider",
    "Enrollment",
    "EnrollmentRejectedError",
    "GOSSIP_EVENTS_TOPIC",
    "HTTP_PROTOCOL",
    "HTTPHandler",
    "HTTPRequest",
    "HTTPResponse",
    "HTTPService",
    "Identity",
    "InsecureControlPlaneURLError",
    "LabelsNotSatisfiedError",
    "MCP_PROTOCOL",
    "MCPService",
    "MeshCredential",
    "MeshSession",
    "Peer",
    "ProviderAuthorizerOptions",
    "ProviderOptions",
    "ROLE_NODE",
    "ROLE_ROUTER",
    "RefreshResult",
    "ServiceRegistry",
    "ToolCallResult",
    "ToolInfo",
    "VerifiedBiscuit",
    "auth_stream_handler",
    "authenticate_with_peer",
    "authorize_caller",
    "decode_auth_response",
    "dial_through_relay",
    "encode_auth_frame",
    "enroll_challenge",
    "enroll_status_challenge",
    "find_providers",
    "http_ingress_handler",
    "http_request_over_stream",
    "libp2p_public_key",
    "mcp_stream_handler",
    "open_mcp_session",
    "parse_service_target",
    "peer_id_from_public_key",
    "provide",
    "refresh_challenge",
    "register_challenge",
    "require_labels",
    "require_role",
    "reserve_relay",
    "service_key",
    "validate_control_plane_url",
    "verify_ed25519",
    "verify_keys_response",
    "verify_mesh_event",
    "verify_peer_biscuit",
]
