from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor
ENROLLMENT_STATUS_APPROVED: EnrollmentStatus
ENROLLMENT_STATUS_PENDING: EnrollmentStatus
ENROLLMENT_STATUS_REJECTED: EnrollmentStatus
ENROLLMENT_STATUS_UNSPECIFIED: EnrollmentStatus
SERVICE_TYPE_A2A: ServiceType
SERVICE_TYPE_INFERENCE: ServiceType
SERVICE_TYPE_MCP: ServiceType
SERVICE_TYPE_UNSPECIFIED: ServiceType

class AgentAttachRequest(_message.Message):
    __slots__ = ["bundle"]
    BUNDLE_FIELD_NUMBER: _ClassVar[int]
    bundle: AgentBundle
    def __init__(self, bundle: _Optional[_Union[AgentBundle, _Mapping]] = ...) -> None: ...

class AgentAttachResponse(_message.Message):
    __slots__ = ["egress_socket", "error", "ingress_socket"]
    EGRESS_SOCKET_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    INGRESS_SOCKET_FIELD_NUMBER: _ClassVar[int]
    egress_socket: str
    error: str
    ingress_socket: str
    def __init__(self, egress_socket: _Optional[str] = ..., ingress_socket: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class AgentBundle(_message.Message):
    __slots__ = ["agent_id", "credential_path", "egress", "external_id", "ingress", "version"]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_PATH_FIELD_NUMBER: _ClassVar[int]
    EGRESS_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    INGRESS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    credential_path: str
    egress: AgentEgress
    external_id: str
    ingress: _containers.RepeatedCompositeFieldContainer[AgentIngress]
    version: str
    def __init__(self, version: _Optional[str] = ..., agent_id: _Optional[str] = ..., external_id: _Optional[str] = ..., credential_path: _Optional[str] = ..., egress: _Optional[_Union[AgentEgress, _Mapping]] = ..., ingress: _Optional[_Iterable[_Union[AgentIngress, _Mapping]]] = ...) -> None: ...

class AgentDetachRequest(_message.Message):
    __slots__ = ["agent_id"]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    def __init__(self, agent_id: _Optional[str] = ...) -> None: ...

class AgentDetachResponse(_message.Message):
    __slots__ = ["error", "success"]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    error: str
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class AgentEgress(_message.Message):
    __slots__ = ["allow", "secrets"]
    ALLOW_FIELD_NUMBER: _ClassVar[int]
    SECRETS_FIELD_NUMBER: _ClassVar[int]
    allow: _containers.RepeatedScalarFieldContainer[str]
    secrets: _containers.RepeatedCompositeFieldContainer[AgentSecret]
    def __init__(self, allow: _Optional[_Iterable[str]] = ..., secrets: _Optional[_Iterable[_Union[AgentSecret, _Mapping]]] = ...) -> None: ...

class AgentIngress(_message.Message):
    __slots__ = ["description", "name", "port", "type"]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    description: str
    name: str
    port: int
    type: ServiceType
    def __init__(self, type: _Optional[_Union[ServiceType, str]] = ..., name: _Optional[str] = ..., port: _Optional[int] = ..., description: _Optional[str] = ...) -> None: ...

class AgentRefreshRequest(_message.Message):
    __slots__ = ["agent_id", "credential_path"]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_PATH_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    credential_path: str
    def __init__(self, agent_id: _Optional[str] = ..., credential_path: _Optional[str] = ...) -> None: ...

class AgentRefreshResponse(_message.Message):
    __slots__ = ["error", "expires_at", "success"]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    error: str
    expires_at: int
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ..., expires_at: _Optional[int] = ...) -> None: ...

class AgentSecret(_message.Message):
    __slots__ = ["header_name", "host", "kind", "value_path"]
    HEADER_NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    VALUE_PATH_FIELD_NUMBER: _ClassVar[int]
    header_name: str
    host: str
    kind: str
    value_path: str
    def __init__(self, host: _Optional[str] = ..., kind: _Optional[str] = ..., header_name: _Optional[str] = ..., value_path: _Optional[str] = ...) -> None: ...

class AgentStatus(_message.Message):
    __slots__ = ["agent_id", "attached", "credential_expires_at", "ingress"]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    ATTACHED_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    INGRESS_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    attached: bool
    credential_expires_at: int
    ingress: _containers.RepeatedCompositeFieldContainer[AgentIngress]
    def __init__(self, agent_id: _Optional[str] = ..., attached: bool = ..., ingress: _Optional[_Iterable[_Union[AgentIngress, _Mapping]]] = ..., credential_expires_at: _Optional[int] = ...) -> None: ...

class AgentStatusRequest(_message.Message):
    __slots__ = ["agent_id"]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    def __init__(self, agent_id: _Optional[str] = ...) -> None: ...

class AgentStatusResponse(_message.Message):
    __slots__ = ["agents", "error"]
    AGENTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    agents: _containers.RepeatedCompositeFieldContainer[AgentStatus]
    error: str
    def __init__(self, agents: _Optional[_Iterable[_Union[AgentStatus, _Mapping]]] = ..., error: _Optional[str] = ...) -> None: ...

class AuthFrame(_message.Message):
    __slots__ = ["agent", "biscuit", "target_service"]
    AGENT_FIELD_NUMBER: _ClassVar[int]
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    TARGET_SERVICE_FIELD_NUMBER: _ClassVar[int]
    agent: str
    biscuit: bytes
    target_service: str
    def __init__(self, biscuit: _Optional[bytes] = ..., target_service: _Optional[str] = ..., agent: _Optional[str] = ...) -> None: ...

class AuthResponse(_message.Message):
    __slots__ = ["biscuit", "error", "success"]
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    biscuit: bytes
    error: str
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ..., biscuit: _Optional[bytes] = ...) -> None: ...

class BootstrapEnrollRequest(_message.Message):
    __slots__ = ["bootstrap_token", "challenge_signature", "labels", "peer_id", "public_key", "requested_role", "timestamp"]
    class LabelsEntry(_message.Message):
        __slots__ = ["key", "value"]
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    BOOTSTRAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_ROLE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    bootstrap_token: str
    challenge_signature: bytes
    labels: _containers.ScalarMap[str, str]
    peer_id: str
    public_key: bytes
    requested_role: str
    timestamp: int
    def __init__(self, bootstrap_token: _Optional[str] = ..., peer_id: _Optional[str] = ..., public_key: _Optional[bytes] = ..., requested_role: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., timestamp: _Optional[int] = ..., challenge_signature: _Optional[bytes] = ...) -> None: ...

class BootstrapEnrollResponse(_message.Message):
    __slots__ = ["biscuit_token", "control_plane_public_key", "error_message", "expiration", "poll_interval_seconds", "router_addresses", "status"]
    BISCUIT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CONTROL_PLANE_PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_FIELD_NUMBER: _ClassVar[int]
    POLL_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    ROUTER_ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    biscuit_token: bytes
    control_plane_public_key: bytes
    error_message: str
    expiration: int
    poll_interval_seconds: int
    router_addresses: _containers.RepeatedScalarFieldContainer[str]
    status: EnrollmentStatus
    def __init__(self, status: _Optional[_Union[EnrollmentStatus, str]] = ..., biscuit_token: _Optional[bytes] = ..., poll_interval_seconds: _Optional[int] = ..., error_message: _Optional[str] = ..., control_plane_public_key: _Optional[bytes] = ..., router_addresses: _Optional[_Iterable[str]] = ..., expiration: _Optional[int] = ...) -> None: ...

class CommandBackend(_message.Message):
    __slots__ = ["command", "env"]
    class EnvEntry(_message.Message):
        __slots__ = ["key", "value"]
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    ENV_FIELD_NUMBER: _ClassVar[int]
    command: _containers.RepeatedScalarFieldContainer[str]
    env: _containers.ScalarMap[str, str]
    def __init__(self, command: _Optional[_Iterable[str]] = ..., env: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ControlPlaneInfoResponse(_message.Message):
    __slots__ = ["audience", "banned_peer_ids", "client_id", "oidc_issuer", "router_addresses"]
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    BANNED_PEER_IDS_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    OIDC_ISSUER_FIELD_NUMBER: _ClassVar[int]
    ROUTER_ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    audience: str
    banned_peer_ids: _containers.RepeatedScalarFieldContainer[str]
    client_id: str
    oidc_issuer: str
    router_addresses: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, oidc_issuer: _Optional[str] = ..., client_id: _Optional[str] = ..., audience: _Optional[str] = ..., router_addresses: _Optional[_Iterable[str]] = ..., banned_peer_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class DiscoveredProvider(_message.Message):
    __slots__ = ["local_proxy_url", "peer_id", "srv_description", "srv_name"]
    LOCAL_PROXY_URL_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    SRV_DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    SRV_NAME_FIELD_NUMBER: _ClassVar[int]
    local_proxy_url: str
    peer_id: str
    srv_description: str
    srv_name: str
    def __init__(self, peer_id: _Optional[str] = ..., local_proxy_url: _Optional[str] = ..., srv_name: _Optional[str] = ..., srv_description: _Optional[str] = ...) -> None: ...

class EnrollRequest(_message.Message):
    __slots__ = ["challenge_signature", "jwt", "labels", "peer_id", "public_key", "requested_role", "timestamp"]
    class LabelsEntry(_message.Message):
        __slots__ = ["key", "value"]
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CHALLENGE_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    JWT_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_ROLE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    challenge_signature: bytes
    jwt: str
    labels: _containers.ScalarMap[str, str]
    peer_id: str
    public_key: bytes
    requested_role: str
    timestamp: int
    def __init__(self, jwt: _Optional[str] = ..., peer_id: _Optional[str] = ..., public_key: _Optional[bytes] = ..., requested_role: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., timestamp: _Optional[int] = ..., challenge_signature: _Optional[bytes] = ...) -> None: ...

class EnrollResponse(_message.Message):
    __slots__ = ["biscuit_token", "control_plane_public_key", "error_message", "expiration", "router_addresses"]
    BISCUIT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CONTROL_PLANE_PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_FIELD_NUMBER: _ClassVar[int]
    ROUTER_ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    biscuit_token: bytes
    control_plane_public_key: bytes
    error_message: str
    expiration: int
    router_addresses: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, biscuit_token: _Optional[bytes] = ..., error_message: _Optional[str] = ..., control_plane_public_key: _Optional[bytes] = ..., router_addresses: _Optional[_Iterable[str]] = ..., expiration: _Optional[int] = ...) -> None: ...

class IdentityEvidenceResponse(_message.Message):
    __slots__ = ["biscuit", "biscuit_expires_at", "checked_at", "control_plane_url", "peer_id", "trusted_control_plane_keys"]
    BISCUIT_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    CHECKED_AT_FIELD_NUMBER: _ClassVar[int]
    CONTROL_PLANE_URL_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    TRUSTED_CONTROL_PLANE_KEYS_FIELD_NUMBER: _ClassVar[int]
    biscuit: bytes
    biscuit_expires_at: int
    checked_at: int
    control_plane_url: str
    peer_id: str
    trusted_control_plane_keys: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, peer_id: _Optional[str] = ..., biscuit: _Optional[bytes] = ..., biscuit_expires_at: _Optional[int] = ..., control_plane_url: _Optional[str] = ..., trusted_control_plane_keys: _Optional[_Iterable[bytes]] = ..., checked_at: _Optional[int] = ...) -> None: ...

class KeysResponse(_message.Message):
    __slots__ = ["public_keys", "signatures", "timestamp"]
    PUBLIC_KEYS_FIELD_NUMBER: _ClassVar[int]
    SIGNATURES_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    public_keys: _containers.RepeatedScalarFieldContainer[bytes]
    signatures: _containers.RepeatedScalarFieldContainer[bytes]
    timestamp: int
    def __init__(self, public_keys: _Optional[_Iterable[bytes]] = ..., timestamp: _Optional[int] = ..., signatures: _Optional[_Iterable[bytes]] = ...) -> None: ...

class MemberCredential(_message.Message):
    __slots__ = ["biscuit", "control_plane_url", "expire_time", "issued_under_keys", "oidc_session", "router_addresses", "trusted_keys"]
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    CONTROL_PLANE_URL_FIELD_NUMBER: _ClassVar[int]
    EXPIRE_TIME_FIELD_NUMBER: _ClassVar[int]
    ISSUED_UNDER_KEYS_FIELD_NUMBER: _ClassVar[int]
    OIDC_SESSION_FIELD_NUMBER: _ClassVar[int]
    ROUTER_ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    TRUSTED_KEYS_FIELD_NUMBER: _ClassVar[int]
    biscuit: bytes
    control_plane_url: str
    expire_time: _timestamp_pb2.Timestamp
    issued_under_keys: _containers.RepeatedScalarFieldContainer[bytes]
    oidc_session: OIDCSession
    router_addresses: _containers.RepeatedScalarFieldContainer[str]
    trusted_keys: _containers.RepeatedCompositeFieldContainer[TrustedSigningKey]
    def __init__(self, control_plane_url: _Optional[str] = ..., biscuit: _Optional[bytes] = ..., expire_time: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., trusted_keys: _Optional[_Iterable[_Union[TrustedSigningKey, _Mapping]]] = ..., issued_under_keys: _Optional[_Iterable[bytes]] = ..., router_addresses: _Optional[_Iterable[str]] = ..., oidc_session: _Optional[_Union[OIDCSession, _Mapping]] = ...) -> None: ...

class MeshEvent(_message.Message):
    __slots__ = ["new_public_key", "peer_id", "signature", "timestamp", "type"]
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    BANNED: MeshEvent.Type
    KEY_ROTATION: MeshEvent.Type
    NEW_PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    POLICY_UPDATE: MeshEvent.Type
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    new_public_key: bytes
    peer_id: str
    signature: bytes
    timestamp: int
    type: MeshEvent.Type
    def __init__(self, type: _Optional[_Union[MeshEvent.Type, str]] = ..., peer_id: _Optional[str] = ..., timestamp: _Optional[int] = ..., new_public_key: _Optional[bytes] = ..., signature: _Optional[bytes] = ...) -> None: ...

class NodeCatalogReport(_message.Message):
    __slots__ = ["services"]
    SERVICES_FIELD_NUMBER: _ClassVar[int]
    services: _containers.RepeatedCompositeFieldContainer[ServiceInfo]
    def __init__(self, services: _Optional[_Iterable[_Union[ServiceInfo, _Mapping]]] = ...) -> None: ...

class OIDCSession(_message.Message):
    __slots__ = ["audience", "client_id", "issuer", "refresh_token"]
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    ISSUER_FIELD_NUMBER: _ClassVar[int]
    REFRESH_TOKEN_FIELD_NUMBER: _ClassVar[int]
    audience: str
    client_id: str
    issuer: str
    refresh_token: str
    def __init__(self, issuer: _Optional[str] = ..., client_id: _Optional[str] = ..., audience: _Optional[str] = ..., refresh_token: _Optional[str] = ...) -> None: ...

class PeerEvidenceResponse(_message.Message):
    __slots__ = ["biscuit", "checked_at", "expiration", "labels", "peer_id", "revocation_ids", "roles", "verifying_key"]
    class LabelsEntry(_message.Message):
        __slots__ = ["key", "value"]
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    CHECKED_AT_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    REVOCATION_IDS_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    VERIFYING_KEY_FIELD_NUMBER: _ClassVar[int]
    biscuit: bytes
    checked_at: int
    expiration: int
    labels: _containers.ScalarMap[str, str]
    peer_id: str
    revocation_ids: _containers.RepeatedScalarFieldContainer[str]
    roles: _containers.RepeatedScalarFieldContainer[str]
    verifying_key: bytes
    def __init__(self, peer_id: _Optional[str] = ..., biscuit: _Optional[bytes] = ..., verifying_key: _Optional[bytes] = ..., roles: _Optional[_Iterable[str]] = ..., labels: _Optional[_Mapping[str, str]] = ..., expiration: _Optional[int] = ..., revocation_ids: _Optional[_Iterable[str]] = ..., checked_at: _Optional[int] = ...) -> None: ...

class PolicyBinding(_message.Message):
    __slots__ = ["members", "role"]
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    members: _containers.RepeatedScalarFieldContainer[str]
    role: str
    def __init__(self, role: _Optional[str] = ..., members: _Optional[_Iterable[str]] = ...) -> None: ...

class PolicyConfig(_message.Message):
    __slots__ = ["bindings", "roles"]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    bindings: _containers.RepeatedCompositeFieldContainer[PolicyBinding]
    roles: _containers.RepeatedCompositeFieldContainer[PolicyRole]
    def __init__(self, roles: _Optional[_Iterable[_Union[PolicyRole, _Mapping]]] = ..., bindings: _Optional[_Iterable[_Union[PolicyBinding, _Mapping]]] = ...) -> None: ...

class PolicyConfigGetRequest(_message.Message):
    __slots__ = []
    def __init__(self) -> None: ...

class PolicyConfigGetResponse(_message.Message):
    __slots__ = ["datalog_rules"]
    DATALOG_RULES_FIELD_NUMBER: _ClassVar[int]
    datalog_rules: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, datalog_rules: _Optional[_Iterable[str]] = ...) -> None: ...

class PolicyConfigUpdateResponse(_message.Message):
    __slots__ = ["error", "success"]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    error: str
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class PolicyRole(_message.Message):
    __slots__ = ["allowed_agents", "allowed_labels", "allowed_services", "allowed_targets", "custom_datalog", "name"]
    ALLOWED_AGENTS_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_LABELS_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_SERVICES_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_TARGETS_FIELD_NUMBER: _ClassVar[int]
    CUSTOM_DATALOG_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    allowed_agents: _containers.RepeatedScalarFieldContainer[str]
    allowed_labels: _containers.RepeatedScalarFieldContainer[str]
    allowed_services: _containers.RepeatedScalarFieldContainer[str]
    allowed_targets: _containers.RepeatedScalarFieldContainer[str]
    custom_datalog: _containers.RepeatedScalarFieldContainer[str]
    name: str
    def __init__(self, name: _Optional[str] = ..., allowed_targets: _Optional[_Iterable[str]] = ..., allowed_services: _Optional[_Iterable[str]] = ..., custom_datalog: _Optional[_Iterable[str]] = ..., allowed_agents: _Optional[_Iterable[str]] = ..., allowed_labels: _Optional[_Iterable[str]] = ...) -> None: ...

class RegisterServiceRequest(_message.Message):
    __slots__ = ["command", "service", "target_url"]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    TARGET_URL_FIELD_NUMBER: _ClassVar[int]
    command: CommandBackend
    service: ServiceInfo
    target_url: str
    def __init__(self, service: _Optional[_Union[ServiceInfo, _Mapping]] = ..., target_url: _Optional[str] = ..., command: _Optional[_Union[CommandBackend, _Mapping]] = ...) -> None: ...

class RouterLeaseRequest(_message.Message):
    __slots__ = ["addresses", "biscuit", "challenge_signature", "connected_peers", "dht_size", "peer_id", "timestamp"]
    ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    BISCUIT_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_PEERS_FIELD_NUMBER: _ClassVar[int]
    DHT_SIZE_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    addresses: _containers.RepeatedScalarFieldContainer[str]
    biscuit: bytes
    challenge_signature: bytes
    connected_peers: _containers.RepeatedScalarFieldContainer[str]
    dht_size: int
    peer_id: str
    timestamp: int
    def __init__(self, peer_id: _Optional[str] = ..., addresses: _Optional[_Iterable[str]] = ..., biscuit: _Optional[bytes] = ..., connected_peers: _Optional[_Iterable[str]] = ..., dht_size: _Optional[int] = ..., timestamp: _Optional[int] = ..., challenge_signature: _Optional[bytes] = ...) -> None: ...

class RouterLeaseResponse(_message.Message):
    __slots__ = ["error", "expires_at", "success"]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    error: str
    expires_at: int
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ..., expires_at: _Optional[int] = ...) -> None: ...

class ServiceAnnounce(_message.Message):
    __slots__ = ["active_requests", "keys", "labels", "latency_ewma_ms", "peer_id", "service_name", "timestamp", "type"]
    class LabelsEntry(_message.Message):
        __slots__ = ["key", "value"]
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ACTIVE_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    LATENCY_EWMA_MS_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    active_requests: int
    keys: _containers.RepeatedScalarFieldContainer[str]
    labels: _containers.ScalarMap[str, str]
    latency_ewma_ms: float
    peer_id: str
    service_name: str
    timestamp: int
    type: ServiceType
    def __init__(self, peer_id: _Optional[str] = ..., type: _Optional[_Union[ServiceType, str]] = ..., service_name: _Optional[str] = ..., keys: _Optional[_Iterable[str]] = ..., labels: _Optional[_Mapping[str, str]] = ..., active_requests: _Optional[int] = ..., latency_ewma_ms: _Optional[float] = ..., timestamp: _Optional[int] = ...) -> None: ...

class ServiceInfo(_message.Message):
    __slots__ = ["description", "name", "type"]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    description: str
    name: str
    type: ServiceType
    def __init__(self, type: _Optional[_Union[ServiceType, str]] = ..., name: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class TokenRefreshRequest(_message.Message):
    __slots__ = ["challenge_signature", "peer_id", "timestamp"]
    CHALLENGE_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    challenge_signature: bytes
    peer_id: str
    timestamp: int
    def __init__(self, challenge_signature: _Optional[bytes] = ..., timestamp: _Optional[int] = ..., peer_id: _Optional[str] = ...) -> None: ...

class TokenRefreshResponse(_message.Message):
    __slots__ = ["biscuit_token", "error_message", "expires_at"]
    BISCUIT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    biscuit_token: bytes
    error_message: str
    expires_at: int
    def __init__(self, biscuit_token: _Optional[bytes] = ..., expires_at: _Optional[int] = ..., error_message: _Optional[str] = ...) -> None: ...

class TokenRevokeRequest(_message.Message):
    __slots__ = ["peer_id"]
    PEER_ID_FIELD_NUMBER: _ClassVar[int]
    peer_id: str
    def __init__(self, peer_id: _Optional[str] = ...) -> None: ...

class TokenRevokeResponse(_message.Message):
    __slots__ = ["error", "success"]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    error: str
    success: bool
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class TrustedSigningKey(_message.Message):
    __slots__ = ["public_key", "receive_time"]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    RECEIVE_TIME_FIELD_NUMBER: _ClassVar[int]
    public_key: bytes
    receive_time: _timestamp_pb2.Timestamp
    def __init__(self, public_key: _Optional[bytes] = ..., receive_time: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class EnrollmentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []

class ServiceType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
