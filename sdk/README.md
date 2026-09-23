# Native SDKs

This directory holds the SDKs that let an agent join a SAM mesh from inside
its own process, without a `sam-node` sidecar. Two languages are in scope:

- [`js/`](js/) — `@agent-mesh/sdk`, TypeScript for Node.js, built on
  [js-libp2p](https://github.com/libp2p/js-libp2p).
- [`python/`](python/) — `agent-mesh` (import `agent_mesh`), built on
  [py-libp2p](https://github.com/libp2p/py-libp2p).

The motivation is [issue #480](https://github.com/google/sam/issues/480). The
sidecar works, but it makes every agent deployment manage a second process,
a local port and a token, which does not fit serverless platforms and splits
tracing between the agent and the network layer. An earlier attempt at a
Python package (`sam-mcp-python`) only wrapped the sidecar's HTTP API and was
removed in favour of this work.

Both SDKs speak the mesh protocol directly. There is no SDK-specific
endpoint anywhere in the mesh; a node built with an SDK is, to every other
node and to the control plane, a `sam-node`.

## What exists today

Milestone 1 is implemented and tested in both languages:

| Capability | JS | Python |
| --- | --- | --- |
| ed25519 identity, libp2p key encodings, peer ID | yes | yes |
| Enrollment with a bootstrap token (`POST /enroll`, `GET /enroll/status` polling) | yes | yes |
| Enrollment with an OIDC token (`POST /register`) | yes | yes |
| Credential refresh (`POST /refresh`) | yes | yes |
| Signed key-set sync (`GET /keys`) | yes | yes |
| Persisted state (identity and credential, owner-only files) | yes | yes |
| `AuthFrame` encoding | yes | yes |

A member built this way holds a valid mesh credential bound to its peer ID
and knows the router addresses. It cannot yet open a libp2p connection.

## Wire contract

Everything below is what the Go implementation does today, with the source
file that defines it. The SDKs must match it byte for byte; when a Go change
alters one of these, the SDKs change with it.

### Identity

- The identity is an ed25519 key pair. The public key travels in the libp2p
  protobuf encoding `PublicKey{Type: Ed25519, Data: <32 bytes>}`, which is
  the bytes `08 01 12 20` followed by the key. Private keys persist as
  `PrivateKey{Type: Ed25519, Data: <seed || public>}`, bytes `08 01 12 40`
  followed by 64 bytes; this is what `sam-node` writes to its store.
- The peer ID is the identity multihash (`00 24`) of the public key encoding,
  written in base58btc. The control plane refuses an enrollment whose
  `peer_id` is not derived from `public_key`
  (`internal/controlplane/server.go`, `pID.MatchesPublicKey`).

### Control plane (protobuf over HTTP)

All requests and responses are `application/x-protobuf` bodies of the
messages in `api/sam.proto`. Bodies are capped at 1 MiB on both sides.

| Endpoint | Request | Response | Proof of possession |
| --- | --- | --- | --- |
| `GET /info` | — | `ControlPlaneInfoResponse` | none |
| `POST /enroll` | `BootstrapEnrollRequest` | `BootstrapEnrollResponse` | sign `sam:enroll:<peer_id>:<ts>` |
| `GET /enroll/status?peer_id=` | headers `X-Sam-Challenge-Ts`, `X-Sam-Challenge-Sig` (base64url, unpadded) | `BootstrapEnrollResponse` | sign `sam:enroll-status:<peer_id>:<ts>` |
| `POST /register` | `EnrollRequest` | `EnrollResponse` | sign `sam:register:<peer_id>:<ts>` |
| `POST /refresh` | `TokenRefreshRequest`, header `Authorization: Bearer <base64 biscuit>` | `TokenRefreshResponse` | sign `sam:refresh:<peer_id>:<ts>` |
| `GET /keys` | — | `KeysResponse` | none; see below |

- `<ts>` is unix milliseconds and must be within 5 minutes of the control
  plane's clock (`challengeMaxAge`). Challenges are defined in
  `api/network.go`.
- A bootstrap enrollment answers `PENDING` until an operator approves it,
  unless the control plane runs with auto-approval. The client polls
  `/enroll/status` at the returned `poll_interval_seconds`.
- `/refresh` redeems only the last biscuit the control plane issued for the
  peer. A client must persist the new biscuit before using it; losing it
  means re-enrolling.
- `/keys` signatures cover the deterministic protobuf encoding of
  `KeysResponse{public_keys, timestamp}` with `signatures` cleared, one
  signature per key by that key. The receiver accepts the set when a key it
  already trusts (the enrollment key) vouches for it and the timestamp is
  within 5 minutes (`api/trust.go`). Rotation keeps several keys valid, so
  a member must trust the whole set to verify peers enrolled under a
  retiring key.
- A plaintext `http://` control plane URL is accepted only for a loopback
  host (`api.ValidateControlPlaneTransport`). The SDKs expose the same
  explicit opt-in (`allowInsecure` / `allow_insecure`).
- Bootstrap tokens are secrets. The SDKs read them from a file or a value the
  caller already holds; the conformance runners take a path in the
  environment. Nothing takes a token as a command-line argument.

### libp2p host (`internal/node/node.go`)

- Transports: `libp2p.DefaultTransports` (TCP, QUIC, WebSocket).
- Security: **TLS only** (`libp2p.Security(libp2ptls.ID, libp2ptls.New)`).
  There is no Noise on `sam-node` or `sam-router`. js-libp2p has TLS;
  py-libp2p gained it in
  [libp2p/py-libp2p#831](https://github.com/libp2p/py-libp2p/pull/831) and
  has passed the libp2p transport interoperability suite against the other
  implementations since
  [libp2p/test-plans#798](https://github.com/libp2p/test-plans/pull/798).
- Muxer: yamux (libp2p default).
- Relay: circuit relay v2, with the routers as static relays; hole punching
  is on. A node behind NAT is reached through a router.
- DHT: Kademlia with protocol prefix `/sam`, mode auto.

### Streams

Two protocols, both starting with a length-prefixed protobuf frame. Framing
is the go-msgio varint style (unsigned varint byte length, then the bytes),
with a 64 KiB cap on the first frame.

- `/sam/auth/1.0.0` (`HandleAuthHandshake`, and the router's equivalent).
  Client sends `AuthFrame{biscuit}`; server verifies the biscuit against the
  control plane keys, checks it is bound to the connection's peer ID and not
  revoked, and answers `AuthResponse{success, biscuit}` with its own
  credential. The client verifies that credential the same way and, for a
  router, requires the router role. This is how a member joins: connect to a
  router, run this handshake, and the router admits it to the relay and
  gossip.
- `/sam/mcp/1.0.0` (`WithBiscuitAuth` then `HandleMCPStream`). Client sends
  `AuthFrame{biscuit, target_service: "mcp://<service>", agent}`; server
  authorizes the request with the Biscuit authorizer described below and
  answers `AuthResponse`. The stream then carries MCP JSON-RPC messages, each
  one varint-length-prefixed (`StreamTransport` in `internal/node/gate.go`).
  An empty `target_service` selects the node's own catalog tools.

### Discovery (`internal/node/service.go`)

A provider announces a service as a DHT provider record for
`cidv1(raw, sha256("sam:service:<type>[:<name>]"))`, once for the type and
once for the name (`<type>` is `mcp`, `inference` or `a2a`). A consumer
looks up providers for the same CID, dials one and opens `/sam/mcp/1.0.0`.
Gossip topics under `/sam/discovery/v1/...` carry `ServiceAnnounce` for
interest-scoped updates; the DHT is the source of truth.

### Authorization on the provider side (`internal/node/middleware.go`)

A node that serves a tool evaluates the caller's biscuit with an authorizer
that has, in this order: the biscuit's own authority facts (`node`, `role`,
`label`, `agent`), injected facts `service(<type>, <name>)`,
`connection_peer_id(<peer>)` and optionally `agent(<claim>)`, the baseline
checks, rules and policies from `api/datalog.go`, the facts from the
provider's own biscuit, and the mesh policy rules the control plane
publishes (`BuildPolicyRules` over `PolicyConfigGetResponse`). Deny by
default.

## Plan

Each milestone lands in both languages with its tests before the next one
starts. Where a milestone needs a Go change, the Go change lands first.

### Milestone 1 — identity and enrollment (done)

Described above. Tested by unit tests in each SDK against a fake control
plane and by `tests/integration/sdk_enroll_test.go`, which runs each SDK's
conformance runner against a real control plane in manual-approval mode,
approves the request through `/admin`, and checks the biscuits the SDK
holds against the control plane's records.

### Milestone 2 — join the mesh

- libp2p host per SDK: TCP, TLS, yamux, identify, circuit relay v2 client,
  Kademlia client with prefix `/sam`, dial-through-relay to peers.
  JS: `libp2p`, `@libp2p/tcp`, `@libp2p/tls`, `@chainsafe/libp2p-yamux`,
  `@libp2p/circuit-relay-v2`, `@libp2p/kad-dht`, `@libp2p/identify`.
  Python: `libp2p>=0.7` (`security.tls`, `stream_muxer.yamux`,
  `relay.circuit_v2`, `kad_dht`); it is trio-based, so the Python SDK's
  networking API is `anyio`-compatible async.
- Biscuit verification of the peer's `AuthResponse`: signature under a
  trusted control plane key, expiry, authority binding to the connection
  peer ID, and the router role for routers. JS:
  `@biscuit-auth/biscuit-wasm`; Python: `biscuit-python`.
- `/sam/auth/1.0.0` client handshake with a router; the router addresses come
  from the credential.
- Background credential refresh, driven by the biscuit's expiration as
  `StartRenewalLoop` does.
- Test: a Go integration test starts a real router (`startRouter`) and
  checks, through the control plane's lease report, that the SDK member is
  among the router's authenticated peers.

### Milestone 3 — call tools

- DHT lookup by service type and name; `/sam/mcp/1.0.0` client: send the
  `AuthFrame`, verify the provider's biscuit, then run an MCP client over
  the varint-framed stream. JS: a `Transport` for
  `@modelcontextprotocol/sdk`; Python: a read/write stream pair for `mcp`.
- Caller-side label requirements (`checkPeerLabels`) on the provider's
  biscuit.
- Public API: `mesh.discover(type, name)`, `mesh.callTool("mcp://svc/tool",
  args)`, `mesh.listTools(peer, service)`.
- Test: the SDK calls a tool served by a `sam-node` started with
  `startBackgroundNode` and a registered MCP backend, through a real router.

### Milestone 4 — serve tools

- `/sam/mcp/1.0.0` server: read the `AuthFrame`, run the authorizer, answer,
  then hand the stream to an in-process MCP server.
- The authorizer needs the baseline Datalog from `api/datalog.go` and the
  mesh policy from `GET /policies`. Before this milestone the baseline
  Datalog moves out of Go string constants into a language-neutral artifact
  that the Go build and both SDKs embed, so it is written once.
- DHT provide for each registered service, reprovided on the node's
  interval; `POST /nodes/catalog` self-report.
- Test: a `sam-node` calls a tool served by the SDK member, and a denied
  caller is refused at the `AuthResponse`.

### Milestone 5 — parity and release

- Control plane sync loop (`/keys`, bans, router addresses, policy) and
  gossip event handling for bans and key rotation.
- Inference and A2A egress over libp2p HTTP (`libp2phttp`), following the
  sidecar's `/sam/<peer>/<type>/<name>/` proxy.
- Publishing: `@agent-mesh/sdk` to npm and `agent-mesh` to PyPI from the
  release workflow, versioned with the repository.
- Docs: a guide under `site/content/docs/guides/` and the connector
  interface for platforms (issue #480).

### Non-goals

- Rewriting the control plane, router or sandbox components; they stay Go.
- A browser build. Node.js only until the transport story for browsers
  (WebTransport or WebRTC to routers) is designed.
- Any SDK-only wire protocol. If an SDK needs something the Go node does not
  speak, the Go node learns it first.

## Working on the SDKs

```bash
# Regenerate protobuf bindings after editing api/sam.proto
./hack/gen-sdk-proto.sh

# JavaScript
cd sdk/js && npm ci && npm test && npm run build

# Python
python3 -m venv sdk/python/.venv
sdk/python/.venv/bin/pip install -e 'sdk/python[test]'
sdk/python/.venv/bin/pytest sdk/python/tests

# Both against a real control plane
go test ./tests/integration -run TestNativeSDKs -v
```

`make sdk-test` runs all of the above. The integration test skips an SDK
whose toolchain is missing, and CI (`.github/workflows/sdk.yml`) installs
both so nothing skips there.

Interoperability facts that the tests pin: `sdk/testdata/identity_vectors.json`
holds key encodings, peer IDs and challenge signatures produced with
go-libp2p, and every SDK's identity tests reproduce them.
